"""
Unit tests — run each specialist agent in isolation (no A2A servers needed).

Run:
    pytest tests/test_agents.py -v
    pytest tests/test_agents.py -v -k security          # filter by category
    pytest tests/test_agents.py -v --no-header -rN      # compact output
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.utils.runner import run_for_category
from tests.utils.validators import (
    check_code_parses,
    check_keywords_absent,
    check_keywords_present,
    check_no_high_severity,
    check_remediation_blocks_parse,
    run_bandit_on_remediation,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXPECTED_DIR = Path(__file__).parent / "expected"

# ── Parametrize over all fixture/expected pairs ────────────────────────────────

def _collect_cases():
    cases = []
    for fixture_path in sorted(FIXTURES_DIR.rglob("*.py")):
        category = fixture_path.parent.name
        if category == "combined":
            continue  # combined fixtures need the full pipeline — see test_pipeline.py
        expected_path = EXPECTED_DIR / category / f"{fixture_path.stem}.json"
        if not expected_path.exists():
            continue
        cases.append(
            pytest.param(
                fixture_path,
                expected_path,
                category,
                id=f"{category}/{fixture_path.stem}",
            )
        )
    return cases


# ── Layer 1: Programmatic tests (fast, deterministic) ─────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_path,expected_path,category", _collect_cases())
async def test_agent_produces_output(fixture_path, expected_path, category):
    """Agent must return a non-trivial response."""
    code = fixture_path.read_text()
    output = await run_for_category(category, code)
    assert len(output) >= 100, f"Output too short ({len(output)} chars) for {fixture_path.name}"


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_path,expected_path,category", _collect_cases())
async def test_remediation_code_parses(fixture_path, expected_path, category):
    """All Python code blocks in the agent output must be syntactically valid."""
    code = fixture_path.read_text()
    output = await run_for_category(category, code)
    result = check_remediation_blocks_parse(output)
    assert result.passed, "\n".join(result.failures)


@pytest.mark.asyncio
async def test_true_negative_no_high_severity():
    """clean_auth.py should produce no HIGH/CRITICAL findings."""
    fixture = FIXTURES_DIR / "security" / "clean_auth.py"
    code = fixture.read_text()
    output = await run_for_category("security", code)
    result = check_no_high_severity(output)
    assert result.passed, "\n".join(result.failures)


@pytest.mark.asyncio
async def test_sqli_remediation_uses_parameterized_queries():
    """SQL injection fix must use parameterized queries, not f-strings."""
    fixture = FIXTURES_DIR / "security" / "sqli_raw_query.py"
    expected = json.loads((EXPECTED_DIR / "security" / "sqli_raw_query.json").read_text())
    code = fixture.read_text()
    output = await run_for_category("security", code)

    present = check_keywords_present(output, expected["remediation"]["must_include_keywords"])
    assert present.passed, "\n".join(present.failures)

    absent = check_keywords_absent(output, expected["remediation"]["must_not_contain_keywords"])
    assert absent.passed, "\n".join(absent.failures)


@pytest.mark.asyncio
async def test_dependency_fix_removes_yaml_unsafe():
    """yaml.load() fix must replace it with yaml.safe_load()."""
    fixture = FIXTURES_DIR / "dependency" / "yaml_unsafe_pickle.py"
    expected = json.loads((EXPECTED_DIR / "dependency" / "yaml_unsafe_pickle.json").read_text())
    code = fixture.read_text()
    output = await run_for_category("dependency", code)

    present = check_keywords_present(output, expected["remediation"]["must_include_keywords"])
    assert present.passed, "\n".join(present.failures)


@pytest.mark.asyncio
async def test_bandit_findings_not_reintroduced():
    """Bandit should not flag the same HIGH issues in the remediated code."""
    fixture = FIXTURES_DIR / "security" / "command_injection.py"
    code = fixture.read_text()
    output = await run_for_category("security", code)
    result = run_bandit_on_remediation(code, output)
    assert result.passed, "\n".join(result.failures)


# ── Layer 2: LLM-as-judge tests (semantic, slower) ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.llm_judge
@pytest.mark.parametrize("fixture_path,expected_path,category", _collect_cases())
async def test_agent_recall(fixture_path, expected_path, category, judge):
    """Agent must identify at least 80% of expected findings."""
    expected = json.loads(expected_path.read_text())
    if not expected.get("must_find"):
        pytest.skip("No must_find entries — true negative fixture")

    code = fixture_path.read_text()
    output = await run_for_category(category, code)
    scores = judge.score(code=code, agent_output=output, expected=expected)

    assert scores.recall >= 0.80, (
        f"Recall {scores.recall:.2f} < 0.80\n"
        f"Details: {scores.recall_details}\n"
        f"Notes: {scores.overall_notes}"
    )


@pytest.mark.asyncio
@pytest.mark.llm_judge
@pytest.mark.parametrize("fixture_path,expected_path,category", _collect_cases())
async def test_agent_precision(fixture_path, expected_path, category, judge):
    """Agent must not flood the output with false positives."""
    expected = json.loads(expected_path.read_text())
    code = fixture_path.read_text()
    output = await run_for_category(category, code)
    scores = judge.score(code=code, agent_output=output, expected=expected)

    assert scores.precision >= 0.70, (
        f"Precision {scores.precision:.2f} < 0.70\n"
        f"False positives: {scores.false_positives}"
    )


@pytest.mark.asyncio
@pytest.mark.llm_judge
@pytest.mark.parametrize("fixture_path,expected_path,category", _collect_cases())
async def test_remediation_validity(fixture_path, expected_path, category, judge):
    """Proposed fixes must actually address the flagged issues."""
    expected = json.loads(expected_path.read_text())
    rem = expected.get("remediation", {})
    if not rem.get("must_include_keywords"):
        pytest.skip("No remediation keywords specified — skip remediation judge check")

    code = fixture_path.read_text()
    output = await run_for_category(category, code)
    scores = judge.score(code=code, agent_output=output, expected=expected)

    assert scores.remediation_validity >= 0.75, (
        f"Remediation validity {scores.remediation_validity:.2f} < 0.75\n"
        f"Issues: {scores.remediation_issues}\n"
        f"Notes: {scores.overall_notes}"
    )
