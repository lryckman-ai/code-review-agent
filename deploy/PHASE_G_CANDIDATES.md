# Phase G — candidates (not started)

Backlog of real gaps identified while building/running Phase E (Helm deploy)
and Phase F (GitHub webhook), 2026-09-03/04. Nothing here is built yet — this
is notes for picking one up in a future session, not a runbook.

## 1. HTTPS

The ALB is plain HTTP right now (`http://k8s-coderevi-...elb.amazonaws.com/`).
That means the GitHub webhook secret and every PR diff cross the internet
unencrypted, and the dashboard/API do too. Needs:
- an ACM certificate (can be for a real domain, or none — GitHub's webhook
  delivery doesn't require a "real" domain, just a valid cert)
- a domain pointed at the ALB (Route 53, or external DNS + a CNAME)
- `alb.ingress.kubernetes.io/certificate-arn` + a second `HTTPS: 443` entry
  in `listen-ports`, plus (usually) an HTTP→HTTPS redirect rule
- update the GitHub webhook's payload URL to `https://...`

## 2. `/api/review` is still synchronous

Works (ALB idle timeout bumped to 300s — see `deploy/PHASE_E_RUNBOOK.md` §7),
but the *right* shape long-term is the same async pattern Phase F's webhook
handler uses: `POST /api/review` returns a `run_id` immediately (202), the
dashboard/caller polls `GET /api/runs/{run_id}` for `status: complete`. Would
let the ALB idle-timeout workaround go away entirely and stop tying up an ALB
connection for 60-90s per request. `dashboard/src/api.ts` and the
`RunDetail.vue` component would need a small polling loop.

## 3. Immutable image tags

The whole `pullPolicy: Always` fix in Phase E (see runbook §9) exists because
`:latest` is mutable and `IfNotPresent` caches by tag name, not digest — hit
this directly, cost real debugging time. Tag images with the git SHA
(`codereview-backend:$(git rev-parse --short HEAD)`), template the tag through
`values.yaml` per deploy, and switch back to `pullPolicy: IfNotPresent` (fewer
registry round-trips, and rollbacks become "redeploy the old tag" instead of
"hope the node still has it cached").

## 4. GitHub App instead of a personal access token

`webhook.py` currently posts PR comments using a PAT (`GITHUB_TOKEN`), so
every automated review comment shows up authored by your own GitHub account,
not a bot identity. Fine solo; not great with other people on the repo. A
GitHub App would post as itself, use short-lived installation tokens instead
of a long-lived PAT, and could get narrower default permissions.

## 5. CI

Right now "deploy" is you running `docker build`/`push`/`helm upgrade` by
hand from `deploy/PHASE_E_RUNBOOK.md`. A GitHub Actions workflow (build +
push both images + `helm upgrade`) triggered on merge to `main` would close
the loop — including, with #3 above, allowing automated rollback to a known
image tag if a deploy goes bad.

## Also noted but not urgent

- `remediation` agent's logged `latency_ms` is ~1ms regardless of real work
  done (cosmetic, dashboard-only — see `deploy/PHASE_E_PROGRESS.md` note 4).
- SQLite on a single EBS volume is fine for this project's scale; if run
  history ever needs to survive a full cluster teardown/recreate (not just a
  pod restart), it'd need snapshotting or a move off in-cluster SQLite.
