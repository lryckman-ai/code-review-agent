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
from pathlib import Path

from pipeline import run_pipeline, LANGSMITH_ENABLED


async def _main_async(code: str, label: str) -> None:
    try:
        result = await run_pipeline(code=code, label=label, verbose=True)
    except RuntimeError as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        sys.exit(1)

    output = result["output"]
    print(output if output else "Warning: pipeline produced no output.")

    if LANGSMITH_ENABLED:
        print(
            f"\n[tracing] Trace available at https://smith.langchain.com "
            f"(project: codereview-agent, run_id tag: {result['run_id']})"
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
    print(f"  Tracing : {'LangSmith ON' if LANGSMITH_ENABLED else 'off (set LANGSMITH_API_KEY)'}")
    print("=" * 60)
    print()

    asyncio.run(_main_async(code, label))


if __name__ == "__main__":
    main()
