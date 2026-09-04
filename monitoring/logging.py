"""
Structured JSON-Lines logging for every review run.

One JSON object per line in logs/reviews.jsonl, also echoed to stdout,
and (aside from agent_start) mirrored into logs/reviews.db.

Event types:
  review_start     — a review was submitted
  agent_start      — an individual agent began processing
  agent_complete   — an individual agent finished
  review_complete  — the full pipeline finished
  shadow_score     — async quality judge result (full breakdown)
  gate_decision    — pass/fail distilled from shadow_score
  cost_estimate    — token usage + real/reference cost for one run
"""

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

from . import db

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "reviews.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [codereview] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("codereview")


def new_run_id() -> str:
    return uuid.uuid4().hex[:10]


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()[:12]


def _emit(record: dict) -> None:
    record.setdefault("ts", round(time.time(), 3))
    line = json.dumps(record, default=str)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    _log.info(line)


class ReviewLogger:
    """Tracks timing and emits structured events for one review run."""

    def __init__(
        self,
        run_id: str,
        code: str,
        label: str,
        repo: str | None = None,
        pr_number: int | None = None,
    ):
        self.run_id = run_id
        self.label = label
        self.repo = repo
        self.pr_number = pr_number
        self._hash = _code_hash(code)
        self._lines = len(code.splitlines())
        self._wall_start = time.monotonic()
        self._agent_starts: dict[str, float] = {}

    def log_start(self) -> None:
        _emit({
            "event": "review_start",
            "run_id": self.run_id,
            "label": self.label,
            "code_hash": self._hash,
            "code_lines": self._lines,
        })
        db.insert_run_start(
            run_id=self.run_id,
            label=self.label,
            code_hash=self._hash,
            code_lines=self._lines,
            repo=self.repo,
            pr_number=self.pr_number,
        )

    def log_agent_start(self, agent: str) -> None:
        self._agent_starts[agent] = time.monotonic()
        _emit({
            "event": "agent_start",
            "run_id": self.run_id,
            "agent": agent,
        })

    def log_agent_complete(self, agent: str, output_chars: int) -> None:
        t0 = self._agent_starts.get(agent, self._wall_start)
        latency_ms = int((time.monotonic() - t0) * 1000)
        _emit({
            "event": "agent_complete",
            "run_id": self.run_id,
            "agent": agent,
            "latency_ms": latency_ms,
            "output_chars": output_chars,
        })
        db.insert_agent_run(
            run_id=self.run_id, agent=agent, latency_ms=latency_ms, output_chars=output_chars,
        )

    def log_complete(self, output: str = "", shadow_queued: bool = False) -> None:
        total_latency_ms = int((time.monotonic() - self._wall_start) * 1000)
        _emit({
            "event": "review_complete",
            "run_id": self.run_id,
            "total_latency_ms": total_latency_ms,
            "shadow_score_queued": shadow_queued,
        })
        db.update_run_complete(run_id=self.run_id, total_latency_ms=total_latency_ms, output=output)

    def log_shadow_score(
        self,
        score: float,
        passed: bool,
        breakdown: dict,
        notes: str,
        key_issues: list,
        threshold: float,
    ) -> None:
        record: dict = {
            "event": "shadow_score",
            "run_id": self.run_id,
            "score": round(score, 3),
            "passed": passed,
            "breakdown": breakdown,
            "notes": notes,
        }
        if key_issues:
            record["key_issues"] = key_issues
        _emit(record)

        # Distilled pass/fail event — lets metric filters key on this directly.
        _emit({
            "event": "gate_decision",
            "run_id": self.run_id,
            "gate": "shadow_score_threshold",
            "score": round(score, 3),
            "threshold": threshold,
            "passed": passed,
        })
        db.insert_gate_decision(
            run_id=self.run_id,
            gate="shadow_score_threshold",
            score=score,
            threshold=threshold,
            passed=passed,
            breakdown=breakdown,
            notes=notes,
            key_issues=key_issues,
        )

        if not passed:
            # Prominent console warning — no email
            border = "=" * 60
            _log.warning(
                "\n%s\n  LOW QUALITY SCORE ALERT\n"
                "  run_id : %s\n"
                "  label  : %s\n"
                "  score  : %.0f%%  (threshold %.0f%%)\n"
                "  issues : %s\n"
                "  notes  : %s\n%s",
                border,
                self.run_id,
                self.label,
                score * 100,
                threshold * 100,
                "; ".join(key_issues) if key_issues else "see breakdown",
                notes,
                border,
            )

    def log_cost_estimate(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        real_cost_usd: float,
        reference_model: str | None = None,
        reference_cost_usd: float | None = None,
    ) -> None:
        _emit({
            "event": "cost_estimate",
            "run_id": self.run_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "real_cost_usd": round(real_cost_usd, 6),
            "reference_model": reference_model,
            "reference_cost_usd": round(reference_cost_usd, 6) if reference_cost_usd is not None else None,
        })
        db.insert_cost_estimate(
            run_id=self.run_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            real_cost_usd=real_cost_usd,
            reference_model=reference_model,
            reference_cost_usd=reference_cost_usd,
        )
