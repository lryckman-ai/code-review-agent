"""Programmatic (non-LLM) validators for agent outputs."""
import ast
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ValidationResult:
    passed: bool
    failures: list[str] = field(default_factory=list)

    def __bool__(self):
        return self.passed


_NON_PYTHON_LANGS = {
    "sql", "mysql", "postgresql", "sqlite",
    "bash", "sh", "shell", "zsh",
    "json", "yaml", "yml", "toml",
    "text", "txt", "diff", "patch",
    "markdown", "md", "html", "xml", "css",
    "javascript", "js", "typescript", "ts",
    "java", "go", "rust", "c", "cpp", "ruby",
}


def extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Return (language, code) pairs for every fenced block in text.
    Language is lowercased and may be empty string for untagged blocks.
    """
    return [
        (lang.lower(), body)
        for lang, body in re.findall(r"```(\w*)\n(.*?)```", text, re.DOTALL)
    ]


def extract_python_blocks(text: str) -> list[str]:
    """Return only code blocks that are explicitly tagged 'python' or 'py'."""
    return [
        body
        for lang, body in extract_code_blocks(text)
        if lang in ("python", "py")
    ]


def check_code_parses(code: str) -> tuple[bool, str]:
    """Return (ok, error_message). Only valid for Python."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as exc:
        return False, f"SyntaxError at line {exc.lineno}: {exc.msg}"


def check_remediation_blocks_parse(review_output: str) -> ValidationResult:
    """All Python code blocks in the remediation output must parse."""
    blocks = extract_code_blocks(review_output)
    if not blocks:
        return ValidationResult(passed=True)  # no code blocks is fine

    failures = []
    for i, block in enumerate(blocks, 1):
        ok, err = check_code_parses(block)
        if not ok:
            snippet = block[:120].replace("\n", " ")
            failures.append(f"Block {i}: {err} — `{snippet}…`")

    return ValidationResult(passed=not failures, failures=failures)


def check_keywords_present(text: str, keywords: list[str]) -> ValidationResult:
    """At least one keyword from the list must appear in text (case-insensitive)."""
    text_lower = text.lower()
    missing = [kw for kw in keywords if kw.lower() not in text_lower]
    if len(missing) == len(keywords):
        return ValidationResult(
            passed=False,
            failures=[f"None of the expected keywords found: {keywords}"],
        )
    return ValidationResult(passed=True)


def check_keywords_absent(text: str, banned: list[str]) -> ValidationResult:
    """None of the banned strings should appear in the remediation code blocks."""
    blocks = "\n".join(extract_code_blocks(text))
    found = [kw for kw in banned if kw.lower() in blocks.lower()]
    if found:
        return ValidationResult(
            passed=False,
            failures=[f"Banned patterns still present in fix: {found}"],
        )
    return ValidationResult(passed=True)


def check_no_high_severity(review_output: str) -> ValidationResult:
    """For true-negative fixtures: no HIGH or CRITICAL findings should appear."""
    pattern = re.compile(r"\b(CRITICAL|HIGH)\b", re.IGNORECASE)
    matches = pattern.findall(review_output)
    if matches:
        return ValidationResult(
            passed=False,
            failures=[f"Found {len(matches)} HIGH/CRITICAL ratings in a clean fixture"],
        )
    return ValidationResult(passed=True)


def run_bandit(code: str) -> Optional[dict]:
    """Run bandit on a code snippet. Returns parsed JSON or None if bandit unavailable."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(code)
            tmp = f.name

        result = subprocess.run(
            [sys.executable, "-m", "bandit", "-f", "json", tmp],
            capture_output=True,
            text=True,
            timeout=15,
        )
        import json
        return json.loads(result.stdout) if result.stdout.strip() else None
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None  # bandit not installed — skip silently
    finally:
        Path(tmp).unlink(missing_ok=True)


def run_bandit_on_remediation(original_code: str, review_output: str) -> ValidationResult:
    """
    Check that code blocks in the remediation output don't re-introduce
    HIGH-severity bandit findings that were in the original code.
    """
    original_report = run_bandit(original_code)
    if original_report is None:
        return ValidationResult(passed=True)  # bandit unavailable

    original_high = {
        issue["test_id"]
        for issue in original_report.get("results", [])
        if issue.get("issue_severity") in ("HIGH", "MEDIUM")
    }

    failures = []
    for i, block in enumerate(extract_code_blocks(review_output), 1):
        report = run_bandit(block)
        if report is None:
            continue
        for issue in report.get("results", []):
            tid = issue["test_id"]
            sev = issue.get("issue_severity", "")
            if tid in original_high and sev in ("HIGH", "MEDIUM"):
                failures.append(
                    f"Block {i} still has {sev} bandit issue {tid}: "
                    f"{issue.get('issue_text', '')}"
                )

    return ValidationResult(passed=not failures, failures=failures)
