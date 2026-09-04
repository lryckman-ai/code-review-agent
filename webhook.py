"""
GitHub webhook integration — Phase F.

api.py's POST /api/webhook/github verifies the signature and decides whether
to act (fast, synchronous); everything that talks to the network — fetching
the PR diff, running the review pipeline, posting the comment back — happens
here, as a FastAPI BackgroundTask kicked off *after* the response is sent.

Why background at all: a review takes 60-90s (3 reviewers + synthesis +
remediation, every LLM call over the tunnel to gx10.lan). GitHub considers a
webhook delivery failed if the endpoint doesn't respond within ~10s, and will
retry it — so the handler must return immediately and do the real work async,
the same reason /api/review needed the ALB idle-timeout bump to be usable
synchronously at all (see deploy/PHASE_E_RUNBOOK.md §7).

Required env vars (see deploy/chart's Secret + ConfigMap):
  GITHUB_WEBHOOK_SECRET  — shared secret GitHub HMAC-signs each delivery with
  GITHUB_TOKEN           — PAT with Pull requests: Read and write on the repo
  GITHUB_ALLOWED_REPO    — "owner/repo" to process; anything else is ignored
                            (so a leaked/guessed URL can't make this instance
                            spend LLM time reviewing an arbitrary repo)
"""

import hashlib
import hmac
import os

import httpx

from pipeline import run_pipeline

GITHUB_API = "https://api.github.com"

GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
GITHUB_TOKEN          = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ALLOWED_REPO   = os.environ.get("GITHUB_ALLOWED_REPO", "")

# A PR diff can run to megabytes; the LLM's context window can't, and neither
# should one review. Naive cut, not a diff-aware truncation — good enough for
# the PRs this project is tested against so far.
MAX_DIFF_CHARS = 20_000

# Only these actions represent new/changed code worth reviewing. "closed",
# "labeled", "assigned", etc. all also fire "pull_request" events.
HANDLED_ACTIONS = {"opened", "reopened", "synchronize"}


def verify_signature(body: bytes, signature_header: str | None) -> bool:
    """Constant-time check of GitHub's X-Hub-Signature-256 header."""
    if not GITHUB_WEBHOOK_SECRET or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature_header)


def should_handle(event: str, action: str | None, repo_full_name: str) -> tuple[bool, str]:
    """Decide whether this delivery gets a review. Returns (handle, reason)."""
    if GITHUB_ALLOWED_REPO and repo_full_name != GITHUB_ALLOWED_REPO:
        return False, f"repo '{repo_full_name}' not allowlisted"
    if event != "pull_request":
        return False, f"event '{event}' not handled"
    if action not in HANDLED_ACTIONS:
        return False, f"action '{action}' not handled"
    return True, "queued"


async def _fetch_pr_diff(repo_full_name: str, pr_number: int) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API}/repos/{repo_full_name}/pulls/{pr_number}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3.diff",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.text


async def _post_pr_comment(repo_full_name: str, pr_number: int, body: str) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GITHUB_API}/repos/{repo_full_name}/issues/{pr_number}/comments",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": body},
            timeout=15.0,
        )
        resp.raise_for_status()


async def handle_pull_request_event(repo_full_name: str, pr_number: int) -> None:
    """
    The whole webhook-triggered flow: fetch the diff, run the pipeline, post
    the result back as a PR comment. Runs as a BackgroundTask — nothing here
    can be awaited by the request that queued it, so every failure is caught
    and (best-effort) turned into a PR comment rather than lost silently.
    """
    label = f"{repo_full_name}#{pr_number}"
    try:
        diff = await _fetch_pr_diff(repo_full_name, pr_number)
    except Exception as exc:
        print(f"[webhook] failed to fetch diff for {label}: {exc}", flush=True)
        return

    truncated = len(diff) > MAX_DIFF_CHARS
    if truncated:
        diff = diff[:MAX_DIFF_CHARS]

    try:
        result = await run_pipeline(
            code=diff, label=label, verbose=False, repo=repo_full_name, pr_number=pr_number,
        )
        comment = result["output"]
        if truncated:
            comment += (
                f"\n\n---\n*Diff truncated to {MAX_DIFF_CHARS:,} characters "
                "for this review — some changed files may not be covered.*"
            )
    except Exception as exc:
        print(f"[webhook] pipeline failed for {label}: {exc}", flush=True)
        comment = (
            "⚠️ Automated code review failed to complete for this PR "
            f"(see the api pod's logs for `{label}`)."
        )

    try:
        await _post_pr_comment(repo_full_name, pr_number, comment)
    except Exception as exc:
        print(f"[webhook] failed to post comment for {label}: {exc}", flush=True)
