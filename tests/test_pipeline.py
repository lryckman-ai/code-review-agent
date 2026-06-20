"""
Integration tests — full pipeline including A2A agents + synthesis + remediation.

Requires A2A servers (auto-started via conftest or manually):
    python run_servers.py &
    pytest tests/test_pipeline.py -m integration -v

The combined/auth_with_db.py fixture is used here because it exercises
all three remote reviewers and the full synthesis + remediation chain.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.utils.validators import (
    check_keywords_present,
    check_no_high_severity,
    check_remediation_blocks_parse,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXPECTED_DIR = Path(__file__).parent / "expected"


async def _run_full_pipeline(code: str, label: str = "test") -> str:
    """Run the complete Workflow: supervisor → synthesis → remediation."""
    from google.adk.workflow import Workflow
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part
    from agents.supervisor import supervisor
    from agents.synthesis import synthesis_agent
    from agents.remediation import remediation_agent

    root = Workflow(
        name="test_pipeline",
        edges=[
            ("START", supervisor),
            (supervisor, synthesis_agent),
            (synthesis_agent, remediation_agent),
        ],
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="test", user_id="test")
    runner = Runner(agent=root, app_name="test", session_service=session_service)

    message = Content(
        role="user",
        parts=[Part(text=f"Review this code ({label}):\n\n```python\n{code}\n```")],
    )

    output = ""
    async for event in runner.run_async(
        user_id="test", session_id=session.id, new_message=message
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    output += part.text

    return output


# ── Integration: full pipeline on combined fixture ─────────────────────────────

@pytest.mark.integration
def test_pipeline_produces_output(a2a_servers):
    fixture = FIXTURES_DIR / "combined" / "auth_with_db.py"
    code = fixture.read_text()
    output = asyncio.run(_run_full_pipeline(code, label=fixture.name))
    assert len(output) >= 200, f"Pipeline output too short ({len(output)} chars)"


@pytest.mark.integration
def test_pipeline_report_has_sections(a2a_servers):
    """Synthesis report must contain all expected Markdown sections."""
    fixture = FIXTURES_DIR / "combined" / "auth_with_db.py"
    code = fixture.read_text()
    output = asyncio.run(_run_full_pipeline(code, label=fixture.name))

    required_sections = [
        "## Overview",
        "## Security Findings",
        "## Performance Findings",
        "## Priority Action Items",
        "## Overall Assessment",
    ]
    missing = [s for s in required_sections if s not in output]
    assert not missing, f"Report missing sections: {missing}"


@pytest.mark.integration
def test_pipeline_remediation_code_parses(a2a_servers):
    """All code blocks in the remediation section must be valid Python."""
    fixture = FIXTURES_DIR / "combined" / "auth_with_db.py"
    code = fixture.read_text()
    output = asyncio.run(_run_full_pipeline(code, label=fixture.name))

    result = check_remediation_blocks_parse(output)
    assert result.passed, "\n".join(result.failures)


@pytest.mark.integration
def test_pipeline_finds_sqli(a2a_servers):
    """Full pipeline must surface the SQL injection in auth_with_db.py."""
    fixture = FIXTURES_DIR / "combined" / "auth_with_db.py"
    code = fixture.read_text()
    output = asyncio.run(_run_full_pipeline(code, label=fixture.name))

    result = check_keywords_present(output, ["sql injection", "f-string", "username"])
    assert result.passed, "\n".join(result.failures)


@pytest.mark.integration
def test_pipeline_finds_n_plus_one(a2a_servers):
    """Full pipeline must surface the N+1 DB query pattern."""
    fixture = FIXTURES_DIR / "combined" / "auth_with_db.py"
    code = fixture.read_text()
    output = asyncio.run(_run_full_pipeline(code, label=fixture.name))

    result = check_keywords_present(output, ["n+1", "loop"])
    assert result.passed, "\n".join(result.failures)


@pytest.mark.integration
def test_pipeline_true_negative(a2a_servers):
    """Clean auth code should produce no HIGH/CRITICAL findings in the full pipeline."""
    fixture = FIXTURES_DIR / "security" / "clean_auth.py"
    code = fixture.read_text()
    output = asyncio.run(_run_full_pipeline(code, label=fixture.name))

    result = check_no_high_severity(output)
    assert result.passed, "\n".join(result.failures)


# ── Integration: LLM-as-judge on combined fixture ─────────────────────────────

@pytest.mark.integration
@pytest.mark.llm_judge
def test_pipeline_judge_combined(a2a_servers, judge):
    """Judge must confirm the full pipeline meets recall + precision thresholds."""
    fixture = FIXTURES_DIR / "combined" / "auth_with_db.py"
    expected = json.loads((EXPECTED_DIR / "combined" / "auth_with_db.json").read_text())
    code = fixture.read_text()

    output = asyncio.run(_run_full_pipeline(code, label=fixture.name))
    scores = judge.score(code=code, agent_output=output, expected=expected)

    print(f"\nJudge scores: {scores.summary()}")
    print(f"Recall details: {scores.recall_details}")
    if scores.false_positives:
        print(f"False positives: {scores.false_positives}")

    assert scores.recall >= 0.75, (
        f"Combined fixture recall {scores.recall:.2f} < 0.75\n"
        f"Details: {scores.recall_details}"
    )
    assert scores.precision >= 0.65, (
        f"Combined fixture precision {scores.precision:.2f} < 0.65\n"
        f"False positives: {scores.false_positives}"
    )
    assert scores.remediation_validity >= 0.70, (
        f"Combined remediation validity {scores.remediation_validity:.2f} < 0.70\n"
        f"Issues: {scores.remediation_issues}"
    )
