#!/usr/bin/env python3
"""
Code Review Agent — CLI  (A2A mode + production monitoring)

Usage:
  python main.py <file>          # review a source file
  cat file.py | python main.py   # pipe code via stdin

Requires A2A agent servers running first:
  python run_servers.py          # keep this open in a separate terminal

Pipeline:
  supervisor ──A2A──▶ security_reviewer   :8001
             ──A2A──▶ performance_reviewer :8002
             ──A2A──▶ dependency_auditor   :8003
       │
       ▼ (local)
  synthesis  →  remediation

Monitoring:
  logs/reviews.jsonl             — structured event log (one JSON per line)
  LangSmith                      — full trace if LANGSMITH_API_KEY is set
  Shadow scoring                 — 10% of runs judged async; warning logged if <70%
"""

import asyncio
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Monitoring bootstrap (must happen before any agent imports) ────────────────
from monitoring.logging import ReviewLogger, new_run_id
from monitoring.tracing import setup_langsmith, trace_review
from monitoring.shadow_scorer import maybe_queue_shadow_score

_langsmith_on = setup_langsmith()

# ── Agent pipeline ─────────────────────────────────────────────────────────────
from google.adk.workflow import Workflow
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.supervisor import supervisor
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
USER_ID  = "cli_user"

_A2A_CARDS = [
    "http://localhost:8001/.well-known/agent.json",
    "http://localhost:8002/.well-known/agent.json",
    "http://localhost:8003/.well-known/agent.json",
]

_AGENT_LABELS = {
    "security_reviewer":    "[security]     reviewing via A2A...",
    "performance_reviewer": "[performance]  reviewing via A2A...",
    "dependency_auditor":   "[dependency]   auditing imports via A2A...",
    "synthesis":            "[synthesis]    building structured report...",
    "remediation":          "[remediation]  generating code fixes...\n",
}


async def _wait_for_servers(timeout: int = 30) -> bool:
    print("  Waiting for A2A servers", end="", flush=True)
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                responses = await asyncio.gather(
                    *[client.get(url, timeout=2.0) for url in _A2A_CARDS],
                    return_exceptions=True,
                )
                if all(not isinstance(r, Exception) and r.status_code == 200
                       for r in responses):
                    print(" ready.", flush=True)
                    return True
            except Exception:
                pass
            print(".", end="", flush=True)
            await asyncio.sleep(1.0)
    print(" timed out.", flush=True)
    return False


# @trace_review wraps this as a LangSmith root trace when the key is present
@trace_review
async def run_review(code: str, label: str, run_id: str, rlogger: ReviewLogger) -> str:
    if not await _wait_for_servers():
        print(
            "\nERROR: A2A agent servers are not reachable.\n"
            "Start them with:  python run_servers.py\n",
            file=sys.stderr,
        )
        sys.exit(1)

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner  = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)

    message = Content(
        role="user",
        parts=[Part(text=f"Please review this code ({label}):\n\n```python\n{code}\n```")],
    )

    rlogger.log_start()
    print(f"[run:{run_id}]   pipeline starting...", flush=True)

    last_author = ""
    final_text  = ""

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

            if author in _AGENT_LABELS:
                print(_AGENT_LABELS[author], flush=True)

        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text += part.text

    if last_author:
        rlogger.log_agent_complete(last_author, len(final_text))

    return final_text


async def _main_async(code: str, label: str) -> None:
    run_id  = new_run_id()
    rlogger = ReviewLogger(run_id=run_id, code=code, label=label)

    result = await run_review(code=code, label=label, run_id=run_id, rlogger=rlogger)

    # Queue shadow score (10% chance) — non-blocking
    shadow_task = maybe_queue_shadow_score(
        run_id=run_id,
        label=label,
        code=code,
        output=result,
        logger=rlogger,
    )

    rlogger.log_complete(shadow_queued=shadow_task is not None)

    print(result if result else "Warning: pipeline produced no output.")

    # If a shadow task was queued, wait for it (max 60 s) before process exits
    if shadow_task:
        print("\n[shadow] Waiting for quality score... (max 60s)", flush=True)
        try:
            await asyncio.wait_for(shadow_task, timeout=60)
        except asyncio.TimeoutError:
            print("[shadow] Timed out — result will not be logged this run.")

    if _langsmith_on:
        print(
            f"\n[tracing] Trace available at https://smith.langchain.com "
            f"(project: codereview-agent, run_id tag: {run_id})"
        )


def main() -> None:
    code, label = None, "unknown"

    if len(sys.argv) > 1 and sys.argv[1] != "--stdin":
        path = Path(sys.argv[1])
        if not path.exists():
            print(f"Error: '{path}' not found.", file=sys.stderr)
            sys.exit(1)
        code  = path.read_text()
        label = str(path)
    elif not sys.stdin.isatty() or (len(sys.argv) > 1 and sys.argv[1] == "--stdin"):
        code  = sys.stdin.read().strip()
        label = "stdin"

    if not code:
        print(__doc__)
        sys.exit(1)

    print("=" * 60)
    print("  CODE REVIEW AGENT  (A2A + monitoring)")
    print(f"  File    : {label}")
    print(f"  Lines   : {len(code.splitlines())}")
    print(f"  Tracing : {'LangSmith ON' if _langsmith_on else 'off (set LANGSMITH_API_KEY)'}")
    print("=" * 60)
    print()

    asyncio.run(_main_async(code, label))


if __name__ == "__main__":
    main()
