# Phase E — Helm chart, app deploy, and a real ALB smoke test

Picks up where `PHASE_D_RUNBOOK.md` stops: cluster up, 2 nodes ready, backend
image in ECR, WireGuard tunnel proven pod → gx10.lan, ALB controller running.
No app pods yet.

**Scope of Phase E**: `Dockerfile.dashboard` (deferred from Phase C), the Helm
chart under `deploy/chart/`, the EBS CSI driver (needed for the SQLite PVC),
`helm install`, and a smoke test hitting `/healthz` and `/api/review` through a
real ALB hostname.

You run every command on your own machine with your own AWS creds. Paste
output back — especially errors.

---

## What the chart deploys

```
                      ALB  (internet-facing, from the AWS LB Controller)
                       │
        ┌──────────────┼─────────────────┐
      /healthz        /api              /            (path-based Ingress rules)
        │              │                 │
        ▼              ▼                 ▼
   ┌─────────┐   ┌─────────┐      ┌──────────────┐
   │  api    │◀──│  api    │      │  dashboard   │   nginx, static Vue bundle
   │ /healthz│   │ :8000   │      │  :80         │   calls /api same-origin
   └─────────┘   └────┬────┘      └──────────────┘
                      │ A2A (in-cluster Service DNS)
       ┌──────────────┼───────────────┐
       ▼              ▼               ▼
 security-       performance-    dependency-
 reviewer:8001   reviewer:8002   auditor:8003     serve_one.py, one agent each
       └──────────────┴───────────────┘
                      │  OPENAI_API_BASE = http://10.100.0.2:8084/v1
                      ▼
             WireGuard tunnel → gx10.lan:8084   (the LLM)
```

- **api** owns a `ReadWriteOnce` EBS volume for `logs/reviews.db` (SQLite).
  Always 1 replica, `Recreate` strategy — two writers, or a rolling update
  that briefly wants two pods, would deadlock on the volume.
- **reviewers** get `ADVERTISE_HOST` = their own Service name so the A2A agent
  card's RPC URL resolves in-cluster; the api's `SECURITY_HOST` / etc. (from
  the ConfigMap) point at the same names.
- **dashboard** is built with an empty `VITE_API_BASE`, so the SPA fetches
  `/api/...` same-origin and the Ingress path-routes it to the api Service.
  nginx also answers `/healthz` 200 for its own ALB target-group health check.

---

## 0. Preflight

Same shell exports as Phase D (re-run if this is a fresh terminal):

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export EKS_CLUSTER_NAME=codereview
aws eks update-kubeconfig --region $AWS_REGION --name $EKS_CLUSTER_NAME

kubectl get nodes                       # both Ready
kubectl -n kube-system get deploy aws-load-balancer-controller   # 2/2 from Phase D
helm version && docker version
```

Confirm the backend image from Phase D is still in ECR (teardown keeps the
repo, only the cluster goes):

```bash
aws ecr describe-images --region $AWS_REGION --repository-name codereview-backend \
  --query 'imageDetails[].imageTags' --output text
```

If it's gone (repo deleted, or you want a rebuild):

```bash
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
cd ~/agents/codereview
docker build -t $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend:latest .
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend:latest
```

---

## 1. EBS CSI driver — required for the SQLite PVC

The in-tree EBS provisioner was removed from upstream Kubernetes; on a stock
`eksctl` cluster a `PersistentVolumeClaim` sits `Pending` forever with no CSI
driver installed. This is a one-time addon per cluster (so: redo it each
session, since the cluster is recreated).

```bash
# IRSA role for the driver's controller pods
eksctl create iamserviceaccount \
  --name ebs-csi-controller-sa --namespace kube-system \
  --cluster $EKS_CLUSTER_NAME --region $AWS_REGION \
  --role-name AmazonEKS_EBS_CSI_DriverRole --role-only \
  --attach-policy-arn arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy \
  --approve

eksctl create addon --name aws-ebs-csi-driver \
  --cluster $EKS_CLUSTER_NAME --region $AWS_REGION \
  --service-account-role-arn arn:aws:iam::$AWS_ACCOUNT_ID:role/AmazonEKS_EBS_CSI_DriverRole \
  --force
```

Verify:

```bash
kubectl -n kube-system rollout status deployment/ebs-csi-controller
kubectl get storageclass          # expect a `gp2` (default) SC
```

**Gotcha**: the `gp2` StorageClass on EKS uses
`volumeBindingMode: WaitForFirstConsumer` — the PVC stays `Pending` with
`waiting for first consumer to be created before binding` until the api pod is
actually scheduled. That's normal; it binds when the pod starts. It only
stays `Pending` *after* the pod is scheduled if the CSI driver is missing or
its IRSA role is wrong (`kubectl -n kube-system logs deploy/ebs-csi-controller
-c csi-provisioner`).

---

## 2. Dashboard image → ECR

`Dockerfile.dashboard` (repo root) — multi-stage: `node` builds the Vite
bundle, `nginx:alpine` serves it. Build context is `./dashboard`.

```bash
cd ~/agents/codereview

aws ecr create-repository --region $AWS_REGION --repository-name codereview-dashboard

aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker build -f Dockerfile.dashboard -t $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard:latest ./dashboard
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard:latest
```

**Gotchas**:
- `-f Dockerfile.dashboard ... ./dashboard` is deliberate — the Dockerfile
  lives outside its own build context. All its `COPY` paths are relative to
  `./dashboard` (`nginx.conf` is `dashboard/nginx.conf`).
- The build writes `.env.production` with `VITE_API_BASE=` (empty) *inside the
  image* so `vite build` inlines a relative API base. Don't set
  `VITE_API_BASE` to the ALB hostname — you don't know it yet, and same-origin
  is what the Ingress is for.
- If `npm ci` fails on a node engine mismatch, check `dashboard/package.json`
  `engines` vs the `node:24-slim` base and bump the tag.

---

## 3. Fill in chart values

`deploy/chart/values.yaml` has placeholders. The only ones you *must* set are
the two image repositories (account id) and — if you want tracing — the
LangSmith key. Everything else has a working default.

Either edit `values.yaml`, or keep secrets out of git and pass overrides on
the CLI (below). Check the tunnel IP: `values.yaml`'s `llm.apiBase` is
`http://10.100.0.2:8084/v1` — that's gx10.lan's WireGuard overlay address from
Phase D Step 2.5. If you changed the overlay addressing, update it.

Dry-run the render:

```bash
helm template codereview deploy/chart \
  --set image.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend \
  --set dashboardImage.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard \
  | less
```

---

## 4. helm install

```bash
helm upgrade --install codereview deploy/chart \
  --namespace codereview --create-namespace \
  --set image.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend \
  --set dashboardImage.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard \
  --set-string secrets.langsmithApiKey="$(grep -s '^LANGSMITH_API_KEY=' .env | cut -d= -f2)" \
  --wait --timeout 5m
```

`--wait` blocks until every Deployment is Available (or times out — then go to
§6). Drop `--set-string secrets.langsmithApiKey=...` to run without tracing.

---

## 5. Watch it come up

```bash
kubectl -n codereview get pods -w
```

Expected steady state — 5 pods `Running`, api `1/1`:

```
codereview-api-...              1/1  Running
codereview-dashboard-...        1/1  Running
security-reviewer-...           1/1  Running
performance-reviewer-...        1/1  Running
dependency-auditor-...          1/1  Running
```

```bash
kubectl -n codereview get pvc      # codereview-data -> Bound
kubectl -n codereview get ingress  # ADDRESS fills in after ~2-4 min
```

---

## 6. If pods don't go Ready

| Symptom | Likely cause | Check |
|---|---|---|
| `ImagePullBackOff` | wrong repo in `--set`, or node role missing ECR read | `kubectl -n codereview describe pod <p>`; node role should have `AmazonEC2ContainerRegistryReadOnly` (eksctl default) |
| api `Pending`, PVC `Pending` after pod scheduled | EBS CSI driver / IRSA (§1) | `kubectl -n kube-system logs deploy/ebs-csi-controller -c csi-provisioner` |
| reviewer `Running` but never `Ready` | readiness path wrong for this google-adk version | `kubectl -n codereview exec deploy/security-reviewer -- python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8001/.well-known/agent.json').status)"` — if 404, try `/.well-known/agent-card.json` and update `deploy/chart/templates/reviewers.yaml` + `agents/supervisor.py`'s `_card()` |
| pods `Pending`, `Insufficient memory` | t3.small is 2 GiB; 3 reviewers @ 300Mi + api + kube-system is tight | `kubectl describe node \| grep -A6 Allocated`; lower `resources.reviewer.requests.memory` or drop a reviewer replica |
| api crashloop, `sqlite3.OperationalError` | volume not writable | `kubectl -n codereview exec deploy/codereview-api -- ls -ld /app/logs` |
| review request hangs then 500 | reviewers unreachable from api, or LLM tunnel down | see §8 |

---

## 7. Smoke test through the ALB

```bash
ALB=$(kubectl -n codereview get ingress codereview -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "$ALB"
```

Wait until it resolves and the target groups are healthy (the LB controller
registers targets only once pods are Ready; a fresh ALB also takes a minute to
go from `provisioning` to `active`):

```bash
until curl -fsS "http://$ALB/healthz" 2>/dev/null; do echo -n .; sleep 10; done; echo
```

`{"status":"ok"}` = the ALB → api path works. Then a real review:

```bash
curl -fsS -X POST "http://$ALB/api/review" \
  -H 'Content-Type: application/json' \
  -d '{"code":"import os\npassword=\"hunter2\"\nos.system(\"echo \"+input())","label":"smoke.py"}' | tee /tmp/review.json
```

That drives supervisor → 3 reviewers (A2A) → synthesis → remediation, every
model call going out over the tunnel to gx10.lan. Takes 60-90s. You get back
`{"run_id": "...", "output": "...", ...}`.

**Gotcha (2026-09-03)**: `POST /api/review` is synchronous and routinely runs
past 60s, which is the ALB's *default* idle timeout — you get a
`504 Gateway Time-out` HTML page while the pipeline actually finishes fine
server-side (check `/api/runs` — the run shows `status: complete`). The chart
sets `idle_timeout.timeout_seconds=300` via
`alb.ingress.kubernetes.io/load-balancer-attributes` to fix this; confirm it
landed with
`aws elbv2 describe-load-balancer-attributes ... --query "Attributes[?Key=='idle_timeout.timeout_seconds']"`.
The real fix long-term is to make review submission async (return a run_id
immediately, poll for the result) — Phase F.

Then:

```bash
curl -fsS "http://$ALB/api/runs?limit=5" | head -c 400          # the run is persisted
open "http://$ALB/"                                             # dashboard (macOS; xdg-open on Linux)
```

The dashboard should list the run, and clicking it should load the report,
per-agent latencies, and cost estimate.

---

## 8. Debugging the request path (if a review fails)

Reuse the netshoot pod from Phase D's toolkit:

```bash
kubectl -n codereview run netshoot --image=nicolaka/netshoot --restart=Never -- sleep 7200
kubectl -n codereview exec -it netshoot -- bash
```

From inside, in order of depth:

```bash
# 1. LLM reachable through the tunnel? (same check as Phase D Step 4)
curl -sS http://10.100.0.2:8084/v1/models

# 2. reviewers reachable + advertising the right URL?
curl -sS http://security-reviewer:8001/.well-known/agent.json | python3 -m json.tool
#    the "url" field must be http://security-reviewer:8001/... — if it's
#    localhost, ADVERTISE_HOST didn't take (check the Deployment env)

# 3. api → reviewer directly
curl -sS -X POST http://codereview-api:8000/api/review \
  -H 'content-type: application/json' -d '{"code":"print(1)","label":"x"}'
```

api logs show which agent stalled:

```bash
kubectl -n codereview logs deploy/codereview-api --tail=100 -f
```

`curl: Connection timed out` before `Connected to` = routing/SG (the tunnel
route or the tunnel SG's port-8084 rule). `Connection refused` = something
*is* reachable but not listening (gx10.lan's LLM process down). Same split as
Phase D.

Clean up: `kubectl -n codereview delete pod netshoot`.

---

## 9. Iterating

Rebuild + roll a single component without touching the rest:

```bash
# backend code change
docker build -t $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend:latest .
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend:latest
kubectl -n codereview rollout restart deploy/codereview-api deploy/security-reviewer deploy/performance-reviewer deploy/dependency-auditor

# chart change only — re-run the FULL §4 command, not `--reuse-values`
helm upgrade --install codereview deploy/chart \
  --namespace codereview \
  --set image.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend \
  --set dashboardImage.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard \
  --set-string secrets.langsmithApiKey="$(grep -s '^LANGSMITH_API_KEY=' .env | cut -d= -f2)"

# dashboard change
docker build -f Dockerfile.dashboard -t $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard:latest ./dashboard
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard:latest
kubectl -n codereview rollout restart deploy/codereview-dashboard
```

**Corrected 2026-09-04** — the chart now sets `pullPolicy: Always` for this
exact reason: with `:latest` + `IfNotPresent`, once a node has pulled
`codereview-backend:latest` once, it keeps reusing that cached image forever,
even after a new image is pushed to the same tag — `IfNotPresent` checks the
node's local cache by tag name, not by digest. **Deleting/recreating the pod
does NOT force a re-pull either** — hit this directly: two rounds of
`kubectl delete pod` both silently kept serving the pre-Phase-F code with no
error anywhere, because the pod landed back on the same node both times. Only
`kubectl -n codereview rollout status` + actually inspecting the running
code (e.g. `kubectl exec ... -- python -c "import api; print([r.path for r in
api.app.routes])"`) caught it — the pod was `Running` and `1/1` throughout.
`pullPolicy: Always` re-checks the registry on every pod start and pulls if
the digest changed, at the cost of an extra registry round-trip each restart.
For a real project use immutable tags (git SHA) instead — Phase G.

**Gotcha, hit in the 2026-09-03 session**: do NOT use `helm upgrade
--reuse-values` for a chart change. `--reuse-values` takes the *previous
release's* values as the base and ignores `values.yaml` entirely — so any key
you *added* to `values.yaml` since the last install renders as empty/nil. The
symptom that session: a new `idleTimeoutSeconds` value came through as
`idle_timeout.timeout_seconds=` (no number). Always re-run the full §4 command
with the `--set` flags; that reads `values.yaml` fresh.

---

## 10. Teardown

Within a session, to drop just the app (keeps the cluster/tunnel/ALB
controller):

```bash
helm uninstall codereview --namespace codereview
kubectl delete namespace codereview        # also deletes the PVC...
```

**The EBS volume**: deleting the PVC deletes the `PersistentVolume` and the
underlying EBS volume (default `Delete` reclaim policy). That's the SQLite
history — fine for this project, but `aws ec2 describe-volumes --region
$AWS_REGION --filters Name=tag:kubernetes.io/created-for/pvc-name,Values=codereview-data`
to confirm it's gone and not silently billing.

**The ALB**: `helm uninstall` removes the Ingress, and the LB controller then
deletes the ALB + target groups + the SG it created. Confirm:

```bash
aws elbv2 describe-load-balancers --region $AWS_REGION --query 'LoadBalancers[?contains(LoadBalancerName, `k8s-codereview`)].LoadBalancerName' --output text
```

If it lingers after the namespace is gone, the controller lost its finalizer
chance — delete the Ingress explicitly *before* the namespace next time, or
clean the ALB up by hand.

Full session teardown (cluster + tunnel-server + everything) is still Phase
D's `deploy/teardown.sh` / `eksctl delete cluster`. Save the WireGuard keys
and Elastic IP allocation id first, per Phase D Step 2.

---

## Where this leaves us

**Done — verified 2026-09-03.** Full app on EKS: 3 A2A reviewers + api +
dashboard, SQLite on an EBS volume, path-based ALB Ingress, reviews running
end-to-end against gx10.lan's LLM over the WireGuard tunnel, all confirmed
through the public ALB hostname
(`k8s-coderevi-coderevi-bce6e50f10-...elb.amazonaws.com`). The dashboard lists
runs and renders detail.

Issues found and resolved:
- **(False alarm, corrected)** the `output` initially looked like just the
  supervisor's short summary with no synthesis/remediation content. That was
  an artifact of inspecting it via `curl | tee file | head -c N` — `head`
  exiting early SIGPIPEs `tee` before it finishes writing the file, truncating
  both the terminal view and the saved file. Read from the server directly
  (`GET /api/runs/{id}`, no pipe into `head`) and the report is complete: full
  `# Code Review Report` + findings + 4 remediation fix blocks with
  before/after code. **Never pipe `tee` into `head` or anything that exits
  early — use `curl -o file` and read the file separately.**
- One real minor finding from the same dig: `remediation`'s logged
  `latency_ms` is ~1 despite a real `output_chars` count — the per-agent
  latency measurement for the *last* agent in the pipeline is wrong
  (dashboard cosmetic issue only, not a correctness bug).
- **Sync request model**: see the `/api/review` 504 gotcha in §7 (real, fixed
  with the ALB idle-timeout bump).

Possible Phase F+: async review submission, HTTPS (ACM cert + real domain),
the GitHub webhook handler `api.py` mentions, HPA/resource tuning, immutable
image tags, secrets in AWS Secrets Manager, CI to build/push/upgrade on push.
