# Phase E — progress tracker  ✅ COMPLETE (2026-09-03)

Full app deployed to EKS and verified end-to-end through the public ALB.

- [x] **0. Preflight** — 2 nodes Ready; ALB controller 2/2; backend image `latest`
      in ECR. Account 884404665002, region us-east-1, cluster `codereview`.
      (Cluster was NOT torn down after Phase D — 2d old.)
- [x] **1. EBS CSI driver** — was NOT installed. Created role
      `AmazonEKS_EBS_CSI_DriverRole` (`eksctl create iamserviceaccount --role-only`)
      + `eksctl create addon aws-ebs-csi-driver`. `ebs-csi-controller` 2/2,
      `ebs-csi-node` on both nodes. Existing `gp2` StorageClass (in-tree
      provisioner) works once the CSI driver is present.
- [x] **2. Dashboard image** — created ECR repo `codereview-dashboard`, built
      with `Dockerfile.dashboard` (context `./dashboard`), pushed `:latest`.
- [x] **3. Values** — passed `image.repository` / `dashboardImage.repository`
      via `--set` (kept `values.yaml` placeholders). Tunnel IP
      `10.100.0.2:8084` unchanged from Phase D.
- [x] **4. helm install** — `helm upgrade --install codereview deploy/chart`
      with 2× `--set` + `--set-string secrets.langsmithApiKey=...` from `.env`.
      Release `codereview` rev 1 (later rev 2, see notes).
- [x] **5. Rollout** — all 5 pods Running 1/1 in ~40s. Reviewer probe
      `/.well-known/agent.json` works (google-adk 2.1.0). PVC `codereview-data`
      Bound (1Gi gp2). Reviewer agent card advertises `http://security-reviewer:8001`
      (ADVERTISE_HOST correct).
- [x] **6. ALB** — Ingress reconciled, ALB
      `k8s-coderevi-coderevi-bce6e50f10-1184488268.us-east-1.elb.amazonaws.com`.
      `curl http://$ALB/healthz` → `{"status":"ok"}`.
- [x] **7. Smoke test** — `POST /api/review` returns full JSON through the ALB
      (~62s). `/api/runs`, `/api/stats/summary` OK. Dashboard renders 3 runs.

## Notes / deviations from the runbook

1. **LLM on gx10.lan was down.** Step 5c (`curl 10.100.0.2:8084/v1/models` from
   a pod) → `ConnectionRefusedError`. Not a networking problem — "refused" =
   the tunnel delivered the packet, nothing listening. Started the model
   server on gx10.lan, then 200. Lesson: check the gx10 LLM is up before
   smoke-testing; the tunnel itself was fine.

2. **ALB 504 on `POST /api/review`.** Review takes ~89s; ALB default idle
   timeout is 60s. Pipeline completed fine server-side (`/api/runs` showed
   `status: complete`) but the client got a 504 HTML page. Fix: added
   `alb.ingress.kubernetes.io/load-balancer-attributes:
   idle_timeout.timeout_seconds=300` to `ingress.yaml` +
   `ingress.idleTimeoutSeconds: 300` in `values.yaml`.

3. **`helm upgrade --reuse-values` silently dropped the new value.** After
   adding `idleTimeoutSeconds` to `values.yaml`, `helm upgrade ... --reuse-values`
   rendered the annotation as `idle_timeout.timeout_seconds=` (empty) — because
   `--reuse-values` ignores `values.yaml` and reuses the prior release's values
   as the base. Fix: re-ran the full `helm upgrade --install` command with the
   `--set` flags. Runbook §9 updated to warn about this.

4. **False alarm, corrected 2026-09-04**: earlier notes here said `output` was
   just the supervisor's handoff summary with no synthesis/remediation. That
   was wrong — caused by inspecting responses through
   `curl | tee file | head -c N`. When `head` exits after N bytes it closes
   the pipe, `tee` gets SIGPIPE and dies *before finishing the write to the
   file*, so `file` (and the terminal view) were both truncated. The full
   persisted run (`GET /api/runs/{id}`, read directly, no pipe into `head`)
   has the complete `# Code Review Report` + 4 remediation fix blocks with
   before/after code — the pipeline was working correctly the whole time. No
   model change was needed. **Lesson: never pipe `tee` into `head`/anything
   that exits early; write to a file with `curl -o` and read it separately.**
   `llm.chatModel` was bumped to `gpt-oss-120b` while chasing this — fine to
   keep, or revert to `gpt-oss-20b` (Phase D/E default) since it was likely
   never the problem; latency was similar either way (~62-65s total).

   Real, minor finding from the same investigation: `agent_runs` shows
   `remediation latency_ms: 1` despite `output_chars: 8509` (it did produce
   the full fix content) — the per-agent latency measurement for the last
   agent in the pipeline is wrong (cosmetic, dashboard-only; not a pipeline
   correctness bug).

## Current live state

- Namespace `codereview`, Helm release `codereview` (rev 2).
- ALB is public (`inboundCidrs: 0.0.0.0/0`) — lock to your IP or tear down
  when not in use.
- Cost clock running: EKS control plane + 2× t3.small + tunnel EC2 + 1 ALB +
  1 EBS volume (~1Gi).
