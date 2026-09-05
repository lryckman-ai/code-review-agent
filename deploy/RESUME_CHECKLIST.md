# Resume checklist — after a teardown

Condensed run-in-order version of Phase D → E → F for coming back after
`eksctl delete cluster`. Each step links to the full runbook for
troubleshooting detail — read this file top to bottom, only detour into the
linked doc if a step's "expect" doesn't match.

Prerequisite: you saved the WireGuard keys (2 private + 2 public) somewhere
outside this repo/chat before tearing down (see `deploy/PHASE_D_RUNBOOK.md`
"Why the tunnel-server lives inside the EKS VPC" + the teardown note at the
bottom). Without them this is "regenerate everything and reconfigure
gx10.lan" instead of "paste keys back in" — still doable, just longer.

**Known constants from the last session** (safe to reuse, not secrets):
- Elastic IP: `3.226.108.173`, AllocationId `eipalloc-0b79e26a4a5db8f58`
- SSH keypair: `codereview-tunnel-key` (`~/.ssh/codereview-tunnel-key.pem` —
  local file, untouched by teardown)
- ECR repos `codereview-backend` / `codereview-dashboard` — untouched by
  teardown, no rebuild needed unless the code changed since
- AWS account `884404665002`, region `us-east-1`, cluster name `codereview`

---

## 1. Cluster + tunnel + ALB controller — `PHASE_D_RUNBOOK.md`

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=884404665002
export EKS_CLUSTER_NAME=codereview
```

1. **Step 1** — `eksctl create cluster` (~15-20 min). `--node-type t3.small`,
   `--nodes 2`, `--vpc-cidr 10.0.0.0/16`.
2. **Step 2** — tunnel-server: find the public subnet, security group
   (re-create — SGs don't survive), launch a fresh t3.micro instance,
   **re-associate the saved Elastic IP** (`eipalloc-0b79e26a4a5db8f58` —
   don't allocate a new one, this is what keeps gx10.lan's `Endpoint =` line
   unchanged), then on the instance paste the **saved server private key**
   into `/etc/wireguard/wg0.conf` (skip `wg genkey`) with gx10.lan's **saved
   public key** already in the `[Peer]` block — this skips needing to touch
   gx10.lan's config at all if both sides' keys are reused.
3. Verify the tunnel: `curl http://10.100.0.2:8084/v1/models` from the
   tunnel-server → model JSON (needs gx10's LLM server actually running —
   separate check, see below).
4. **Step 3** — backend image already in ECR, skip unless it changed.
5. **Step 4** — route pod traffic to the tunnel-server (one route per route
   table, `create-route` — see runbook for the `replace-route` fallback if a
   stale route already exists).
6. **Step 5** — AWS Load Balancer Controller: full reinstall (IRSA, IAM
   policy, `helm install aws-load-balancer-controller`) — this does NOT
   survive teardown, it lived in the cluster.

## 2. EBS CSI driver + app deploy — `PHASE_E_RUNBOOK.md`

```bash
# EBS CSI driver (§1) — does NOT survive teardown, redo every time
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
kubectl -n kube-system rollout status deployment/ebs-csi-controller
```

Then the app itself — images are already in ECR, so straight to `helm
install` (no rebuild/push needed unless code changed since teardown):

```bash
cd ~/agents/codereview

helm upgrade --install codereview deploy/chart \
  --namespace codereview --create-namespace \
  --set image.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-backend \
  --set dashboardImage.repository=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/codereview-dashboard \
  --set-string secrets.langsmithApiKey="$(grep -s '^LANGSMITH_API_KEY=' .env | cut -d= -f2)" \
  --set-string secrets.githubToken="$(grep -s '^GITHUB_TOKEN=' .env | cut -d= -f2)" \
  --set-string secrets.githubWebhookSecret="$(grep -s '^GITHUB_WEBHOOK_SECRET=' .env | cut -d= -f2)"

kubectl -n codereview get pods -w   # 5 pods, all Running 1/1
kubectl -n codereview get ingress codereview   # wait for ADDRESS (~2-4 min)
```

## 3. Update the GitHub webhook — new ALB hostname

The Ingress gets a **new** ALB hostname every time (unless/until Phase G's
HTTPS+domain work is done). On GitHub → repo → Settings → Webhooks → the
existing webhook → edit **Payload URL** to
`http://<new-alb-hostname>/api/webhook/github`. Content type, secret, and
event selection stay the same.

## 4. Confirm the LLM is actually up

Separate from all of the above — check `curl http://localhost:8084/v1/models`
**on gx10.lan itself**. This bit us once already this session (tunnel was
fine, model server was just not running) — see `deploy/PHASE_E_RUNBOOK.md`
§8 for the full debug sequence if this fails.

## 5. Smoke test

```bash
ALB=$(kubectl -n codereview get ingress codereview -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
curl -fsS "http://$ALB/healthz"
curl -fsS -X POST "http://$ALB/api/review" -H 'Content-Type: application/json' \
  -d '{"code":"print(1)","label":"resume-check.py"}'
```

Full detail/troubleshooting for any step: `PHASE_D_RUNBOOK.md` (§0-5),
`PHASE_E_RUNBOOK.md` (§0-9), this session's corrections already folded into
both (EBS CSI requirement, ALB idle timeout, `pullPolicy: Always`).
