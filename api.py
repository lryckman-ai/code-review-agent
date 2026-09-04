#!/usr/bin/env python3
"""
FastAPI service — the "api" Deployment in the target architecture.

Currently exposes a health check, a review-trigger endpoint (used by the
docker-compose smoke test now, and by the GitHub webhook handler added in a
later phase), and the read endpoints the Vue dashboard (Phase C) queries.

Run locally:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
  GET  /healthz               — liveness check, no LLM/DB dependency
  POST /api/review            — run the pipeline against submitted code
  POST /api/webhook/github    — GitHub PR webhook; queues a review, see webhook.py
  GET  /api/runs               — recent runs (RunsTable)
  GET  /api/runs/{run_id}      — full detail for one run (RunDetail)
  GET  /api/stats/summary      — aggregate stats (StatsSummary)
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import webhook
from monitoring import db
from pipeline import run_pipeline

_log = logging.getLogger("codereview")


@asynccontextmanager
async def _lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="codereview-api", lifespan=_lifespan)

# Dev-only: Vite (:5173) and this API (:8000) are different origins locally.
# Not needed in prod — the ALB Ingress serves both on one domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


class ReviewRequest(BaseModel):
    code: str
    label: str = "unknown"


@app.post("/api/review")
async def trigger_review(req: ReviewRequest) -> dict:
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="code must not be empty")
    return await run_pipeline(code=req.code, label=req.label, verbose=False)


@app.post("/api/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """
    GitHub calls this on repo events. Verifies the HMAC signature, decides
    fast whether this delivery warrants a review, and — if so — returns
    immediately while the real work (fetch diff, run pipeline, post a PR
    comment) happens in a BackgroundTask. See webhook.py for why this can't
    be synchronous: GitHub times out a delivery at ~10s, a review takes 60-90s.
    """
    body = await request.body()
    if not webhook.verify_signature(body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    event = request.headers.get("x-github-event", "")
    if event == "ping":
        return {"status": "pong"}

    payload = json.loads(body)
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    action = payload.get("action")

    handle, reason = webhook.should_handle(event, action, repo_full_name)
    if not handle:
        _log.info("[webhook] ignored: %s", reason)
        return {"status": "ignored", "reason": reason}

    pr_number = payload["pull_request"]["number"]
    background_tasks.add_task(webhook.handle_pull_request_event, repo_full_name, pr_number)
    _log.info("[webhook] queued review for %s#%s", repo_full_name, pr_number)
    return {"status": "queued", "repo": repo_full_name, "pr_number": pr_number}


@app.get("/api/runs")
def get_runs(limit: int = 50) -> list:
    return db.list_runs(limit=limit)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run '{run_id}' not found")
    return run


@app.get("/api/stats/summary")
def get_stats_summary() -> dict:
    return db.get_stats_summary()
