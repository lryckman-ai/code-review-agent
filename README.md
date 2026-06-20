# Code Review Agent

A multi-agent code review system built with [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) using the Agent-to-Agent (A2A) protocol. Three specialist reviewer agents run as independent HTTP microservices and are coordinated by a supervisor agent, with LangSmith tracing and shadow scoring for production monitoring.

## Architecture

```
main.py
  └── supervisor (LlmAgent)
        ├──A2A──▶ security_reviewer    :8001
        ├──A2A──▶ performance_reviewer :8002
        └──A2A──▶ dependency_auditor   :8003
              │
              ▼ (local)
        synthesis_agent  →  remediation_agent
```

Each specialist runs as an independent A2A microservice with its own HTTP server and agent card (`/.well-known/agent.json`). The supervisor calls them via the A2A protocol and passes the full code to each relevant reviewer. Results are collected by the synthesis agent, which builds a structured report, and the remediation agent then generates concrete code fixes.

## Agents

| Agent | Role |
|---|---|
| `supervisor` | Assesses code complexity, routes to relevant specialist reviewers |
| `security_reviewer` | Auth flaws, injection, input validation, session handling |
| `performance_reviewer` | N+1 DB queries, loop inefficiencies, memory issues, async anti-patterns |
| `dependency_auditor` | CVEs in imports, dangerous usage patterns (pickle, yaml.load, etc.) |
| `synthesis` | Structures all findings into a consistent report format |
| `remediation` | Generates corrected code for each flagged issue |

## Monitoring

- **Structured logging** — every review run writes to `logs/reviews.jsonl` (one JSON event per line): run start, agent transitions with latency, completion
- **LangSmith tracing** — full trace with all LLM calls nested under a root span when `LANGSMITH_API_KEY` is set
- **Shadow scoring** — 10% of runs are asynchronously judged by a separate LLM on specificity, correctness, remediation quality, and structure; a warning is logged if the score falls below 70%

## Setup

```bash
# Install dependencies
pip install google-adk litellm langsmith python-dotenv

# Copy and fill in env vars
cp .env.example .env

# Terminal 1 — start the A2A specialist servers
python run_servers.py

# Terminal 2 — run a review
python main.py path/to/file.py

# Or pipe via stdin
cat myfile.py | python main.py
```

## Example Output

```
============================================================
  CODE REVIEW AGENT  (A2A + monitoring)
  File    : app/auth.py
  Lines   : 87
  Tracing : LangSmith ON
============================================================

[security]     reviewing via A2A...
[performance]  reviewing via A2A...
[dependency]   auditing imports via A2A...
[synthesis]    building structured report...
[remediation]  generating code fixes...

## Overview
...findings and fixes...
```

## Running Tests

```bash
pytest tests/ -v
```

Test fixtures in `tests/fixtures/` cover: SQL injection, command injection, hardcoded secrets, N+1 queries, nested loops, and unsafe dependency usage. Expected outputs in `tests/expected/` define the baseline for each case.

## Tech Stack

- [Google ADK](https://google.github.io/adk-docs/) — agent framework and A2A protocol
- [LiteLLM](https://github.com/BerriAI/litellm) — unified LLM API (any OpenAI-compatible endpoint)
- [LangSmith](https://smith.langchain.com/) — tracing and observability
- Python 3.11, asyncio, uvicorn
