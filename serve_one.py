#!/usr/bin/env python3
"""
Single-agent A2A server entrypoint — one reviewer per process/container.

Usage:
  python serve_one.py --agent security_reviewer
  python serve_one.py --agent performance_reviewer
  python serve_one.py --agent dependency_auditor

Unlike run_servers.py (which hosts all three reviewers in one local process
for CLI convenience), this starts exactly one. It's what each reviewer's
Dockerfile CMD / K8s Deployment runs, so each becomes its own container.

Env vars:
  PORT            — port to serve on (default: the agent's standard port, e.g. 8001)
  BIND_HOST       — address uvicorn binds to (default: 0.0.0.0, so other pods can reach it)
  ADVERTISE_HOST  — host baked into the agent card's RPC URL, i.e. how callers
                    (the supervisor) reach this service. Default "localhost" for
                    local dev; in K8s set to this service's own name, e.g.
                    "security-reviewer", to match the SECURITY_HOST the
                    supervisor is configured with (see agents/supervisor.py).
"""

import argparse
import asyncio
import importlib
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from google.adk.a2a.utils.agent_to_a2a import to_a2a
from monitoring.tracing import setup_langsmith

# Each reviewer runs in its own pod/process — pipeline.py's setup_langsmith()
# call (which covers the api pod's supervisor/synthesis/remediation calls)
# never executes here, so without this, this process's LLM calls have no
# LangSmith callback registered at all. Not nested under the api pod's root
# trace (LangSmith context doesn't cross the A2A HTTP boundary) — shows up
# as its own separate trace per reviewer instead.
setup_langsmith()

# name -> (module path, attribute name, default port)
AGENTS = {
    "security_reviewer":    ("agents.security_reviewer",    "security_reviewer",    8001),
    "performance_reviewer": ("agents.performance_reviewer", "performance_reviewer", 8002),
    "dependency_auditor":   ("agents.dependency_auditor",   "dependency_auditor",   8003),
}


def _load_agent(name: str):
    module_path, attr, _ = AGENTS[name]
    module = importlib.import_module(module_path)
    return getattr(module, attr)


async def _serve(name: str) -> None:
    _, _, default_port = AGENTS[name]
    bind_host      = os.environ.get("BIND_HOST", "0.0.0.0")
    advertise_host = os.environ.get("ADVERTISE_HOST", "localhost")
    port           = int(os.environ.get("PORT", default_port))

    agent = _load_agent(name)

    print(f"[{name}] binding {bind_host}:{port}  (advertised as {advertise_host}:{port})")
    print(f"  Agent card → http://{advertise_host}:{port}/.well-known/agent.json")

    app = to_a2a(agent, host=advertise_host, port=port)
    config = uvicorn.Config(app, host=bind_host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a single A2A reviewer agent.")
    parser.add_argument(
        "--agent", required=True, choices=sorted(AGENTS),
        help="Which reviewer agent to serve as this process's A2A server.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_serve(args.agent))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
