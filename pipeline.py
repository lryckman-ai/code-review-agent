"""
Shared review pipeline logic, used by both the CLI (main.py) and the API
service (api.py) — one place that wires the ADK Workflow, waits for the
A2A servers, and drives logging + shadow scoring for a single review run.
"""

import asyncio
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Monitoring bootstrap — must happen before any agent imports
from monitoring.logging import ReviewLogger, new_run_id
from monitoring.tracing import setup_langsmith, trace_review
from monitoring.shadow_scorer import maybe_queue_shadow_score
from monitoring import pricing

LANGSMITH_ENABLED = setup_langsmith()

# Agent pipeline
from google.adk.workflow import Workflow
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.supervisor import supervisor, AGENT_CARDS
from agents.synthesis import synthesis_agent
from agents.remediation import remediation_agent

root_agent = Workflow(
    name="code_review_pipeline",
    edges=[
        ("START",         supervisor),
        (supervisor,      synthesis_agent),
        (synthesis_agent, remediation_agent),
    ],
)

APP_NAME = "codereview"
USER_ID  = "pipeline"

AGENT_LABELS = {
    "security_reviewer":    "[security]     reviewing via A2A...",
    "performance_reviewer": "[performance]  reviewing via A2A...",
    "dependency_auditor":   "[dependency]   auditing imports via A2A...",
    "synthesis":            "[synthesis]    building structured report...",
    "remediation":          "[remediation]  generating code fixes...\n",
}


async def wait_for_servers(timeout: int = 30, verbose: bool = True) -> bool:
    if verbose:
        print("  Waiting for A2A servers", end="", flush=True)
    deadline = time.monotonic() + timeout
    cards = list(AGENT_CARDS.values())
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                responses = await asyncio.gather(
                    *[client.get(url, timeout=2.0) for url in cards],
                    return_exceptions=True,
                )
                if all(not isinstance(r, Exception) and r.status_code == 200
                       for r in responses):
                    if verbose:
                        print(" ready.", flush=True)
                    return True
            except Exception:
                pass
            if verbose:
                print(".", end="", flush=True)
            await asyncio.sleep(1.0)
    if verbose:
        print(" timed out.", flush=True)
    return False


# @trace_review wraps this as a LangSmith root trace when the key is present
@trace_review
async def run_review(
    code: str, label: str, run_id: str, rlogger: ReviewLogger, verbose: bool = True,
) -> str:
    if not await wait_for_servers(verbose=verbose):
        raise RuntimeError(
            "A2A agent servers are not reachable. Start them with: python run_servers.py"
        )

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner  = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)

    message = Content(
        role="user",
        parts=[Part(text=f"Please review this code ({label}):\n\n```python\n{code}\n```")],
    )

    rlogger.log_start()
    if verbose:
        print(f"[run:{run_id}]   pipeline starting...", flush=True)

    last_author = ""
    final_text  = ""
    # Only counts tokens from this process (supervisor/synthesis/remediation) —
    # the 3 reviewer agents run in separate processes, so this is a lower bound.
    prompt_tokens     = 0
    completion_tokens = 0

    async for event in runner.run_async(
        user_id=USER_ID, session_id=session.id, new_message=message
    ):
        author = getattr(event, "author", None)

        # Log agent transitions
        if author and author != last_author:
            if last_author:
                rlogger.log_agent_complete(last_author, len(final_text))
            last_author = author
            rlogger.log_agent_start(author)

            if verbose and author in AGENT_LABELS:
                print(AGENT_LABELS[author], flush=True)

        usage = getattr(event, "usage_metadata", None)
        if usage:
            prompt_tokens     += usage.prompt_token_count or 0
            completion_tokens += usage.candidates_token_count or 0

        if event.is_final_response() and event.content:
            for part in event.content.parts:
                # ADK tags chain-of-thought parts (from LiteLLM's reasoning_content,
                # e.g. gpt-oss's Harmony analysis channel) with part.thought = True.
                # Without this check they were getting concatenated straight into
                # final_text — the raw scratchpad ended up in real GitHub PR comments.
                if getattr(part, "thought", False):
                    continue
                if hasattr(part, "text") and part.text:
                    final_text += part.text

    if last_author:
        rlogger.log_agent_complete(last_author, len(final_text))

    rlogger.log_cost_estimate(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        real_cost_usd=pricing.real_cost_usd(prompt_tokens, completion_tokens),
        reference_model=pricing.REFERENCE_MODEL,
        reference_cost_usd=pricing.reference_cost_usd(prompt_tokens, completion_tokens),
    )

    return final_text


async def run_pipeline(
    code: str,
    label: str,
    verbose: bool = True,
    repo: str | None = None,
    pr_number: int | None = None,
) -> dict:
    """
    Run one full review (pipeline + shadow scoring) and return a result dict.
    Shared entrypoint for the CLI and the webhook API service.

    repo/pr_number are optional provenance for a webhook-triggered run — they
    just ride along into the runs table (monitoring/db.py already has columns
    for them); a manual /api/review call or CLI run leaves them None.
    """
    run_id  = new_run_id()
    rlogger = ReviewLogger(run_id=run_id, code=code, label=label, repo=repo, pr_number=pr_number)

    output = await run_review(
        code=code, label=label, run_id=run_id, rlogger=rlogger, verbose=verbose,
    )

    # Queue shadow score (10% chance) — non-blocking
    shadow_task = maybe_queue_shadow_score(
        run_id=run_id,
        label=label,
        code=code,
        output=output,
        logger=rlogger,
    )

    rlogger.log_complete(output=output, shadow_queued=shadow_task is not None)

    if shadow_task:
        if verbose:
            print("\n[shadow] Waiting for quality score... (max 60s)", flush=True)
        try:
            await asyncio.wait_for(shadow_task, timeout=60)
        except asyncio.TimeoutError:
            if verbose:
                print("[shadow] Timed out — result will not be logged this run.")

    return {
        "run_id": run_id,
        "label": label,
        "output": output,
        "shadow_queued": shadow_task is not None,
    }
