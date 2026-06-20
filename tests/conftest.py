"""
Pytest configuration.

Unit tests  (test_agents.py)      — run with just: pytest tests/test_agents.py
Integration tests (test_pipeline.py) — require A2A servers:
    python run_servers.py &
    pytest tests/test_pipeline.py -m integration
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

# Put codereview/ root on the path so `from agents.xxx import yyy` works in tests
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


# ── Judge fixture ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def judge():
    """Shared LLM judge instance. Reused across the whole test session."""
    from tests.utils.judge import LLMJudge
    return LLMJudge()


# ── A2A server lifecycle (integration tests only) ──────────────────────────────

def _servers_ready(urls: list[str], timeout: int = 30) -> bool:
    """Block until all A2A server agent cards respond."""
    import httpx
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with httpx.Client() as client:
                responses = [client.get(u, timeout=2.0) for u in urls]
                if all(r.status_code == 200 for r in responses):
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


@pytest.fixture(scope="session")
def a2a_servers():
    """
    Start A2A agent servers for integration tests and stop them after the session.
    Skips if SKIP_A2A_SERVERS=1 env var is set (for CI where servers are pre-started).
    """
    if os.environ.get("SKIP_A2A_SERVERS") == "1":
        yield  # servers already running externally
        return

    import subprocess
    venv_python = Path(__file__).parent.parent / "venv" / "bin" / "python"
    server_script = Path(__file__).parent.parent / "run_servers.py"

    proc = subprocess.Popen(
        [str(venv_python), str(server_script)],
        cwd=str(Path(__file__).parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cards = [
        "http://localhost:8001/.well-known/agent.json",
        "http://localhost:8002/.well-known/agent.json",
        "http://localhost:8003/.well-known/agent.json",
    ]

    if not _servers_ready(cards, timeout=30):
        proc.terminate()
        pytest.skip("A2A servers failed to start within 30s")

    yield

    proc.terminate()
    proc.wait(timeout=5)


# ── Async event loop ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
