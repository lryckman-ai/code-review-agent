"""LLM-as-judge scorer for code review agent outputs.
Uses the local LLM via LiteLLM — no Anthropic API key required.
"""
import json
import os
import re
from dataclasses import dataclass, field

import litellm

_JUDGE_MODEL = os.environ.get("OPENAI_JUDGE_MODEL", "gpt-oss-120b")
_API_BASE    = os.environ.get("OPENAI_API_BASE",    "http://gx10.lan:8084/v1")
_API_KEY     = os.environ.get("OPENAI_API_KEY",     "local")

JUDGE_SYSTEM = """You are an expert code security and performance review evaluator.
You assess whether an AI code reviewer correctly identified vulnerabilities and
proposed valid fixes. You return only valid JSON — no prose, no markdown fences."""

JUDGE_PROMPT = """\
Evaluate this AI code review output against the ground-truth expected findings.

ORIGINAL CODE:
```python
{code}
```

AI REVIEWER OUTPUT (truncated to 4000 chars):
{output}

EXPECTED FINDINGS (ground truth JSON):
{expected}

Score each dimension from 0.0 to 1.0:

1. recall
   For each entry in must_find, check whether the agent output identifies the same
   vulnerability type at roughly the same location.
   - 1.0 per finding: type + location both correct
   - 0.5 per finding: type identified but location/detail vague or wrong
   - 0.0 per finding: not mentioned at all
   Final score = average across all must_find entries (1.0 if must_find is empty).

2. precision
   Start at 1.0. Subtract 0.15 for each agent finding that is clearly not a real
   issue (false positive). Consider must_not_flag as a list of known-good patterns
   the agent should not criticise. Floor at 0.0.

3. severity_accuracy
   For each finding the agent DID identify, compare its severity to the expected
   severity. 1.0=exact, 0.5=off by one level (e.g. MEDIUM vs HIGH), 0.0=off by two+.
   Average across identified findings only (1.0 if none found or must_find is empty).

4. remediation_validity
   Is the suggested fix correct, safe, and does it actually address the issue?
   - 1.0: fix fully addresses the issue with no new problems introduced
   - 0.75: fix mostly correct, minor omission
   - 0.5: partial fix or correct concept but missing details
   - 0.25: fix is on the right track but would not work as written
   - 0.0: fix is wrong, absent, or introduces new vulnerabilities
   If no remediation is expected (remediation.must_include_keywords is empty): return 1.0.

Return ONLY this JSON object (no markdown):
{{
  "recall": <float>,
  "precision": <float>,
  "severity_accuracy": <float>,
  "remediation_validity": <float>,
  "recall_details": {{"<finding_id>": "found|partial|missed"}},
  "false_positives": ["<description>"],
  "remediation_issues": "<short description or empty string>",
  "overall_notes": "<1-2 sentence summary>"
}}"""


@dataclass
class JudgeScores:
    recall: float
    precision: float
    severity_accuracy: float
    remediation_validity: float
    recall_details: dict = field(default_factory=dict)
    false_positives: list = field(default_factory=list)
    remediation_issues: str = ""
    overall_notes: str = ""

    def passed(
        self,
        min_recall: float = 0.80,
        min_precision: float = 0.70,
        min_remediation: float = 0.75,
    ) -> bool:
        return (
            self.recall >= min_recall
            and self.precision >= min_precision
            and self.remediation_validity >= min_remediation
        )

    def summary(self) -> str:
        return (
            f"recall={self.recall:.2f} precision={self.precision:.2f} "
            f"severity={self.severity_accuracy:.2f} remediation={self.remediation_validity:.2f}"
        )


class LLMJudge:
    """Scores agent outputs against golden expectations using the local LLM."""

    def score(self, code: str, agent_output: str, expected: dict) -> JudgeScores:
        """Synchronous scoring via LiteLLM — no Anthropic key needed."""
        prompt = JUDGE_PROMPT.format(
            code=code[:3000],
            output=agent_output[:4000],
            expected=json.dumps(expected, indent=2),
        )

        response = litellm.completion(
            model=f"openai/{_JUDGE_MODEL}",
            api_base=_API_BASE,
            api_key=_API_KEY,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user",   "content": prompt},
            ],
        )

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Judge returned non-JSON: {exc}\n\nRaw:\n{raw[:500]}")

        return JudgeScores(
            recall=float(data.get("recall", 0)),
            precision=float(data.get("precision", 1)),
            severity_accuracy=float(data.get("severity_accuracy", 1)),
            remediation_validity=float(data.get("remediation_validity", 1)),
            recall_details=data.get("recall_details", {}),
            false_positives=data.get("false_positives", []),
            remediation_issues=data.get("remediation_issues", ""),
            overall_notes=data.get("overall_notes", ""),
        )
