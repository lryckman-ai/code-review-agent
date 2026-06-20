"""
Structured JSON-Lines logging for every review run.

Each event is one JSON object on its own line in logs/reviews.jsonl,
and also echoed to stdout via the standard logging module.

Event types:
  review_start     — a review was submitted
  agent_start      — an individual agent began processing
  agent_complete   — an individual agent finished
  review_complete  — the full pipeline finished
  shadow_score     — async quality judge result
"""

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

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

    def __init__(self, run_id: str, code: str, label: str):
        self.run_id = run_id
        self.label = label
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

    def log_agent_start(self, agent: str) -> None:
        self._agent_starts[agent] = time.monotonic()
        _emit({
            "event": "agent_start",
            "run_id": self.run_id,
            "agent": agent,
        })

    def log_agent_complete(self, agent: str, output_chars: int) -> None:
        t0 = self._agent_starts.get(agent, self._wall_start)
        _emit({
            "event": "agent_complete",
            "run_id": self.run_id,
            "agent": agent,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "output_chars": output_chars,
        })

    def log_complete(self, shadow_queued: bool = False) -> None:
        _emit({
            "event": "review_complete",
            "run_id": self.run_id,
            "total_latency_ms": int((time.monotonic() - self._wall_start) * 1000),
            "shadow_score_queued": shadow_queued,
        })

    def log_shadow_score(
        self,
        score: float,
        passed: bool,
        breakdown: dict,
        notes: str,
        key_issues: list,
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

        if not passed:
            # Prominent console warning — no email
            border = "=" * 60
            _log.warning(
                "\n%s\n  LOW QUALITY SCORE ALERT\n"
                "  run_id : %s\n"
                "  label  : %s\n"
                "  score  : %.0f%%  (threshold 70%%)\n"
                "  issues : %s\n"
                "  notes  : %s\n%s",
                border,
                self.run_id,
                self.label,
                score * 100,
                "; ".join(key_issues) if key_issues else "see breakdown",
                notes,
                border,
            )
