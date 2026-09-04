# Phase E — how it all fits together (learning notes)

Companion to `PHASE_E_RUNBOOK.md`. The runbook is the *what to type*; this is
the *what it means and why*. Written to be read after the deploy is working,
to understand every file that was created and how the running system hangs
together.

---

## 1. The goal in one paragraph

We have a Python app (the code-review pipeline) and a Vue web dashboard. They
run fine on one machine with `docker compose`. Phase E moves them onto a
Kubernetes cluster in AWS (EKS) so they run as independent, restartable,
network-addressable services, reachable from the internet through a load
balancer — while the actual LLM stays on your home GPU box (`gx10.lan`),
reached over the WireGuard tunnel that Phase D built.

Nothing about the *application code* changes in Phase E. Everything we add is
packaging (Docker images) and deployment description (a Helm chart).

---

## 2. The layers, bottom to top

```
Your Python code  ──►  Docker images  ──►  Kubernetes objects  ──►  Helm chart
 (already existed)      (Dockerfile,        (Deployment, Service,    (templates +
                         Dockerfile.         ConfigMap, Secret,       values.yaml
                         dashboard)          PVC, Ingress)            that stamp them out)
```

### 2a. Docker images — "the app, frozen"

An image is a tarball of a filesystem + a default command. Two images here:

- **`Dockerfile`** (backend, from Phase C) — Python 3.10 + `requirements.txt` +
  the `agents/`, `monitoring/`, `pipeline.py`, `api.py`, `serve_one.py` code.
  **No default command** — the same image is run 4 different ways (3 reviewers
  + api), each deployment supplies its own command. This is why the compose
  file and the Helm chart both set `command:` explicitly.

- **`Dockerfile.dashboard`** (new in Phase E) — *two stages*:
  1. `node:24-slim`: run `npm ci` then `vite build`, producing static
     HTML/JS/CSS in `dist/`. This stage is thrown away.
  2. `nginx:1.27-alpine`: copy `dist/` in, serve it. This is the final image —
     ~50 MB, no Node, just a web server and static files.

  The trick with `.env.production` (`VITE_API_BASE=` empty): Vite bakes
  environment variables into the JavaScript *at build time*. An empty API base
  means the compiled dashboard calls `/api/runs` (relative) instead of
  `http://some-host:8000/api/runs` (absolute). Relative = "same host that
  served this page" = whatever the load balancer's hostname is. We don't need
  to know that hostname at build time.

### 2b. Kubernetes objects — "how to run the app"

Kubernetes is a control loop: you declare desired state (YAML), it makes
reality match. The objects we use:

| Object | Plain-English meaning | Where in the chart |
|---|---|---|
| **Deployment** | "keep N copies of this container running; restart them if they die; replace them on update" | `api.yaml`, `dashboard.yaml`, `reviewers.yaml` (×3) |
| **Service** | "a stable internal DNS name + IP that load-balances to a Deployment's pods" — pods are cattle with changing IPs, the Service name is constant | one next to each Deployment |
| **ConfigMap** | a bag of non-secret key/value config, injected as environment variables | `configmap.yaml` |
| **Secret** | same, but for sensitive values (base64, access-controlled) | `secret.yaml` |
| **PersistentVolumeClaim (PVC)** | "give me a disk that survives pod restarts" — provisions a real AWS EBS volume | `pvc.yaml` |
| **Ingress** | "an HTTP router from the internet to Services, by URL path" — the AWS controller turns this into a real ALB | `ingress.yaml` |

A **pod** is one running instance (one or more containers sharing a network
namespace). Deployments make pods. You rarely make pods directly.

**Probes** (on every Deployment):
- *readiness* — "is this pod ready for traffic?" Fails → pulled out of the
  Service's rotation, no traffic sent, but not restarted.
- *liveness* — "is this pod wedged?" Fails repeatedly → killed and restarted.

Our reviewers probe `/.well-known/agent.json` (the A2A agent card endpoint);
api and dashboard probe `/healthz`.

### 2c. Helm — "don't write the same YAML five times"

The three reviewers are identical except name/port/agent. Helm is a
templating engine: `deploy/chart/templates/*.yaml` are Go templates,
`values.yaml` supplies the variables, and `helm install` renders them into
plain Kubernetes YAML and applies it.

- `Chart.yaml` — chart metadata (name, version).
- `values.yaml` — all the knobs: image repos, the tunnel IP, model names,
  resource limits, the list of reviewers. **This is the file you edit.**
- `templates/_helpers.tpl` — reusable snippets (naming, labels, the shared
  `envFrom` block).
- `templates/*.yaml` — one per object type. `reviewers.yaml` has a
  `{{- range .Values.reviewers }}` loop that stamps out all three.
- `templates/NOTES.txt` — the post-install message Helm prints.

`helm upgrade --install codereview deploy/chart` = "make the cluster match
this chart, whether or not it's already installed."

---

## 3. How one review request flows through the system

```
  browser ──1──► ALB ──2──► dashboard pod (nginx)         [serves the HTML/JS]
  browser JS ──3──► ALB /api/review ──4──► api pod
      api pod ──5──► security-reviewer / performance-reviewer / dependency-auditor
          each reviewer ──6──► 10.100.0.2:8084 ──tunnel──► gx10.lan LLM
      api pod ──7──► synthesis, remediation (in-process) ──8──► LLM again
      api pod ──9──► writes run to /app/logs/reviews.db (EBS volume)
  browser ──10──► ALB /api/runs ──► api pod ──► reads the DB ──► dashboard shows it
```

1–2. You open `http://<alb-hostname>/`. The Ingress rule `/` routes to the
   dashboard Service → nginx → the static Vue bundle loads in your browser.

3–4. The bundle's JS calls `fetch("/api/review", …)`. Same hostname, so it
   hits the ALB again; the Ingress rule `/api` routes it to the **api**
   Service → an api pod.

5. `api.py` calls `run_pipeline()`. The supervisor agent decides which
   reviewers to call and makes **A2A** (agent-to-agent HTTP) calls to them.
   It finds them via `SECURITY_HOST=security-reviewer` etc. (from the
   ConfigMap) — those are Kubernetes Service DNS names, resolvable only
   inside the cluster.

   *Why `ADVERTISE_HOST` matters*: each reviewer publishes an "agent card"
   (JSON) that contains the URL to call it back on. If a reviewer advertised
   `localhost:8001`, the supervisor would try to call itself. So each
   reviewer sets `ADVERTISE_HOST` to its own Service name — matching what the
   supervisor looks for.

6. Each reviewer needs the LLM. `agents/config.py` reads `OPENAI_API_BASE`
   (`http://10.100.0.2:8084/v1`, from the ConfigMap) and makes an
   OpenAI-compatible call. `10.100.0.2` is gx10.lan's address on the
   WireGuard overlay; pod traffic to `10.100.0.0/24` is routed through the
   tunnel-server EC2 instance → WireGuard → your home network (Phase D §4).

7–8. Reviewer results come back to the api pod; `synthesis` and `remediation`
   agents run *in the api process* (not separate services) and call the LLM
   again to format the report and generate fixes.

9. `monitoring/db.py` writes the run (timing, cost estimate, output) to
   `logs/reviews.db`, a SQLite file on the **EBS volume** mounted at
   `/app/logs`. Survives pod restarts.

10. Later, the dashboard's `fetchRuns()` / `fetchStatsSummary()` hit
   `/api/runs` and `/api/stats/summary`; the api reads the same SQLite DB and
   returns JSON the Vue components render.

---

## 4. Why each non-obvious decision was made

- **api is 1 replica, `Recreate` strategy.** SQLite is a single-writer file
  on a `ReadWriteOnce` volume (one node can mount it at a time). Two api pods
  → corruption or a stuck mount. `Recreate` = kill the old pod before
  starting the new one on deploy, so they never both want the volume.

- **One `/healthz` health-check path for the whole ALB.** An ALB health-checks
  each target group on one path. The api answers `/healthz` naturally; we
  added a matching `/healthz` to nginx (`dashboard/nginx.conf`) so the *same*
  path works for the dashboard target group. Without it, the dashboard
  targets would show "unhealthy" and the ALB would 502 the site root.

- **`target-type: ip` on the Ingress.** The ALB sends traffic straight to pod
  IPs (EKS pods get real VPC IPs via the AWS CNI). The alternative
  (`instance`) routes via a NodePort on every node — an extra hop and a
  node-port to manage. `ip` is simpler and the EKS default recommendation.

- **EBS CSI driver is a separate install step.** Upstream Kubernetes removed
  the built-in AWS EBS volume provisioner; without the CSI driver addon, the
  PVC never binds. It needs its own IAM role (IRSA) — same pattern as the ALB
  controller in Phase D.

- **Secrets passed with `--set-string` at install, not committed.** The
  `values.yaml` `secrets:` block has placeholders. Real values (the LangSmith
  key) are pulled from your local `.env` at install time so they never enter
  git.

- **`:latest` image tags.** Fine for a learning project; a real one uses
  immutable tags (git SHA) so `helm upgrade` actually knows something
  changed and rollbacks are precise. Noted as Phase F work.

- **ALB idle timeout bumped to 300s** (`ingress.yaml`'s
  `load-balancer-attributes` annotation). `POST /api/review` holds the HTTP
  connection open for the whole 60-90s pipeline; the ALB's 60s default would
  cut it with a 504 (even though the review finishes fine server-side). The
  *proper* fix is async submission — return a run id immediately, poll
  `/api/runs/{id}` — which is Phase F.

---

## 5. File-by-file index (everything Phase E added)

```
Dockerfile.dashboard            two-stage build: vite build → nginx static serve
dashboard/nginx.conf            SPA fallback + /healthz 200 for the ALB check
dashboard/.dockerignore         keep node_modules/dist/.env out of the build context

deploy/chart/
  Chart.yaml                    chart name + version
  values.yaml                   ← the file you edit: images, tunnel IP, models, sizes
  .helmignore                   files Helm shouldn't package
  templates/
    _helpers.tpl                naming/label/envFrom snippets reused by the rest
    configmap.yaml              non-secret env: OPENAI_API_BASE, model names,
                                  reviewer host/port discovery
    secret.yaml                 OPENAI_API_KEY, optional LANGSMITH_API_KEY
    pvc.yaml                    the EBS volume claim for SQLite
    reviewers.yaml              range loop → 3× (Deployment + Service)
    api.yaml                    api Deployment (+ volume mount) + Service
    dashboard.yaml              nginx Deployment + Service
    ingress.yaml                the ALB: path rules /healthz + /api → api, / → dashboard;
                                  idle-timeout bumped to 300s for slow reviews
    NOTES.txt                   printed after `helm install`

deploy/PHASE_E_RUNBOOK.md        the commands
deploy/PHASE_E_EXPLAINER.md      this file
```

---

## 6. Mental model for debugging later

Work *out''-in* along the request flow in §3:

1. **Pods**: `kubectl -n codereview get pods` — all `Running` and `1/1`?
2. **The LLM tunnel**: exec into a pod, `curl http://10.100.0.2:8084/v1/models`.
3. **Reviewer discovery**: `curl http://security-reviewer:8001/.well-known/agent.json`
   from another pod — is the `url` field the Service name or `localhost`?
4. **api → reviewers**: `kubectl logs deploy/codereview-api` during a request.
5. **The ALB**: `kubectl get ingress codereview` — is `ADDRESS` populated?
   Is the target group healthy in the AWS console?
6. **The DB**: `kubectl exec deploy/codereview-api -- ls -l /app/logs`.

`curl -v` distinguishes the two failure classes: "connection timed out"
before "Connected to" = network/routing/firewall; "connection refused" after
= reached the host, nothing listening = application problem.

---

## 7. Glossary

- **EKS** — AWS's managed Kubernetes (they run the control plane).
- **eksctl** — CLI that creates EKS clusters + supporting AWS resources.
- **Pod** — smallest deployable unit; one running set of containers.
- **Deployment** — controller that maintains a set of identical pods.
- **Service** — stable virtual IP + DNS name load-balancing to pods.
- **Ingress** — HTTP routing rules; a controller turns them into a real LB.
- **ALB** — AWS Application Load Balancer (layer-7, path/host routing).
- **AWS Load Balancer Controller** — the in-cluster component that watches
  Ingress objects and provisions ALBs (installed in Phase D).
- **PVC / PV** — PersistentVolumeClaim (request) / PersistentVolume (the
  actual disk, here an EBS volume).
- **EBS** — AWS network block storage; survives instance termination.
- **CSI** — Container Storage Interface; the plugin API for storage drivers.
- **IRSA** — IAM Roles for Service Accounts; lets a pod assume an AWS IAM
  role via its Kubernetes identity instead of static keys.
- **Helm** — the Kubernetes package manager; charts = templated YAML bundles.
- **A2A** — agent-to-agent protocol; how the supervisor calls the reviewers
  over HTTP, discovering them via a published "agent card".
- **ConfigMap / Secret** — cluster-stored config, injected as env vars.
- **WireGuard** — the VPN tunnel (Phase D) carrying pod→gx10.lan LLM traffic.
