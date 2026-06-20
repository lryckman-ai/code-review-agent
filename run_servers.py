#!/usr/bin/env python3
"""
A2A Agent Server Launcher
Starts the specialist reviewer agents as independent A2A HTTP microservices.

  Port 8001 — security_reviewer
  Port 8002 — performance_reviewer
  Port 8003 — dependency_auditor

Keep this running in a separate terminal while using main.py.
Stop with Ctrl+C.
"""

import asyncio
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from google.adk.a2a.utils.agent_to_a2a import to_a2a

from agents.security_reviewer import security_reviewer
from agents.performance_reviewer import performance_reviewer
from agents.dependency_auditor import dependency_auditor

SERVERS = [
    (security_reviewer,    8001),
    (performance_reviewer, 8002),
    (dependency_auditor,   8003),
]


async def serve(agent, port: int) -> None:
    app    = to_a2a(agent, host="localhost", port=port)
    config = uvicorn.Config(app, host="localhost", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    print("=" * 52)
    print("  A2A Code Review Servers")
    print("=" * 52)
    for agent, port in SERVERS:
        print(f"  {agent.name:<26} ::{port}")
    print()
    print("  Agent cards → http://localhost:<port>/.well-known/agent.json")
    print("  Press Ctrl+C to stop.\n")

    try:
        await asyncio.gather(*[serve(agent, port) for agent, port in SERVERS])
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nShutting down A2A servers.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
