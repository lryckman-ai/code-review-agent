"""
10% shadow scoring — async, reference-free quality judge.

On each production review there is a SHADOW_SCORE_PROBABILITY chance
(default 10%) that a background task fires a Claude judge to evaluate
the review output independently of the golden dataset.

If the overall score falls below SHADOW_SCORE_THRESHOLD (default 70%)
a warning is logged prominently (no email — see ReviewLogger.log_shadow_score).

The task is fire-and-forget but main.py collects and awaits it with a
short timeout so the process does not exit before it completes.
"""

import asyncio
import json
import os
import random
import re
from typing import Optional

import litellm

SHADOW_SCORE_PROBABILITY = float(os.environ.get("SHADOW_SCORE_PROBABILITY", "0.10"))
SHADOW_SCORE_THRESHOLD   = float(os.environ.get("SHADOW_SCORE_THRESHOLD",   "0.70"))
_JUDGE_MODEL  = os.environ.get("OPENAI_JUDGE_MODEL", "gpt-oss-120b")
_API_BASE     = os.environ.get("OPENAI_API_BASE", "http://gx10.lan:8084/v1")

_JUDGE_SYSTEM = (
    "You are a code review quality evaluator. "
    "Assess review outputs for specificity, correctness, and actionability. "
    "Return only valid JSON — no prose, no markdown fences."
)

_JUDGE_PROMPT = """\
Evaluate this AI code review output for production quality.

CODE SUBMITTED (first 2 000 chars):
```python
{code}
```

REVIEW OUTPUT (first 4 000 chars):
{output}

Score each dimension 0–100 based solely on what is in the output above:

specificity (0-100)
  100 = every finding is pinpointed to a named function or line
  50  = some findings reference code locations, others are vague
  0   = all findings are generic with no code references

correctness (0-100)
  100 = all flagged issues are clearly visible in the submitted code
  50  = most issues real, a few questionable
  0   = findings don't match the code (hallucinated)

remediation_quality (0-100)
  100 = fixes are correct Python, resolve the flagged issue, parse without errors
  50  = fixes are conceptually right but incomplete or not runnable
  0   = fixes are absent, wrong, or introduce new bugs

structure (0-100)
  100 = report has all sections: Overview, Findings, Priority Items, Assessment
  50  = most sections present
  0   = unstructured or missing key sections

Compute overall as:
  specificity*0.30 + correctness*0.35 + remediation_quality*0.25 + structure*0.10

Return ONLY this JSON object:
{{
  "specificity": <int>,
  "correctness": <int>,
  "remediation_quality": <int>,
  "structure": <int>,
  "overall": <float>,
  "key_issues": ["<short issue description>"],
  "notes": "<1-2 sentence overall assessment>"
}}"""


async def _call_judge(code: str, output: str) -> dict:
    """Call the local LLM as a quality judge via LiteLLM (no Anthropic key needed)."""
    response = await litellm.acompletion(
        model=f"openai/{_JUDGE_MODEL}",
        api_base=_API_BASE,
        api_key=os.environ.get("OPENAI_API_KEY", "local"),
        max_tokens=512,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user",   "content": _JUDGE_PROMPT.format(
                code=code[:2000],
                output=output[:4000],
            )},
        ],
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


async def run_shadow_score(
    run_id: str,
    label: str,
    code: str,
    output: str,
    logger,                         # ReviewLogger — passed in to avoid circular import
    threshold: float = SHADOW_SCORE_THRESHOLD,
) -> None:
    """Run the quality judge and log the result (with a warning if below threshold)."""
    try:
        result = await _call_judge(code, output)

        score = float(result.get("overall", 0)) / 100.0
        breakdown = {
            "specificity":          result.get("specificity"),
            "correctness":          result.get("correctness"),
            "remediation_quality":  result.get("remediation_quality"),
            "structure":            result.get("structure"),
        }

        logger.log_shadow_score(
            score=score,
            passed=score >= threshold,
            breakdown=breakdown,
            notes=result.get("notes", ""),
            key_issues=result.get("key_issues", []),
        )

    except Exception as exc:
        print(f"[shadow] Scoring failed for run {run_id}: {exc}")


def maybe_queue_shadow_score(
    run_id: str,
    label: str,
    code: str,
    output: str,
    logger,
    probability: float = SHADOW_SCORE_PROBABILITY,
    threshold: float = SHADOW_SCORE_THRESHOLD,
) -> Optional[asyncio.Task]:
    """
    Roll the dice. If selected, schedule the shadow-score coroutine as an
    asyncio.Task and return it so the caller can await it before exiting.
    Returns None if not selected this run.
    """
    if random.random() >= probability:
        return None

    task = asyncio.create_task(
        run_shadow_score(run_id, label, code, output, logger, threshold),
        name=f"shadow_score_{run_id}",
    )
    print(f"[shadow] Quality check queued for run {run_id} (p={probability:.0%})", flush=True)
    return task
