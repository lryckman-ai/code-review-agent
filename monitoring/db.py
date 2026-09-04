"""
SQLite sink for review run history, alongside logs/reviews.jsonl.

JSONL stays the raw event log; this lets the dashboard/API query run
history (recent runs, one run's detail, aggregate stats) without
re-parsing a growing file. Written from the same ReviewLogger calls
that write JSONL.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "logs" / "reviews.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    label             TEXT NOT NULL,
    repo              TEXT,
    pr_number         INTEGER,
    code_hash         TEXT,
    code_lines        INTEGER,
    started_at        REAL NOT NULL,
    completed_at      REAL,
    total_latency_ms  INTEGER,
    output            TEXT,
    gate_passed       INTEGER,
    status            TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    agent         TEXT NOT NULL,
    latency_ms    INTEGER,
    output_chars  INTEGER,
    recorded_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_decisions (
    run_id      TEXT PRIMARY KEY REFERENCES runs(run_id),
    gate        TEXT NOT NULL,
    score       REAL NOT NULL,
    threshold   REAL NOT NULL,
    passed      INTEGER NOT NULL,
    breakdown   TEXT,
    notes       TEXT,
    key_issues  TEXT,
    recorded_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_estimates (
    run_id              TEXT PRIMARY KEY REFERENCES runs(run_id),
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    real_cost_usd       REAL NOT NULL,
    reference_model     TEXT,
    reference_cost_usd  REAL,
    recorded_at         REAL NOT NULL
);
"""


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


def insert_run_start(
    run_id: str,
    label: str,
    code_hash: str,
    code_lines: int,
    repo: str | None = None,
    pr_number: int | None = None,
) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id, label, repo, pr_number, code_hash, code_lines, started_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running')",
            (run_id, label, repo, pr_number, code_hash, code_lines, time.time()),
        )


def insert_agent_run(run_id: str, agent: str, latency_ms: int, output_chars: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO agent_runs (run_id, agent, latency_ms, output_chars, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, agent, latency_ms, output_chars, time.time()),
        )


def update_run_complete(run_id: str, total_latency_ms: int, output: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE runs SET completed_at = ?, total_latency_ms = ?, output = ?, status = 'complete' "
            "WHERE run_id = ?",
            (time.time(), total_latency_ms, output, run_id),
        )


def insert_gate_decision(
    run_id: str,
    gate: str,
    score: float,
    threshold: float,
    passed: bool,
    breakdown: dict,
    notes: str,
    key_issues: list,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO gate_decisions "
            "(run_id, gate, score, threshold, passed, breakdown, notes, key_issues, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, gate, score, threshold, int(passed),
                json.dumps(breakdown), notes, json.dumps(key_issues), time.time(),
            ),
        )
        conn.execute("UPDATE runs SET gate_passed = ? WHERE run_id = ?", (int(passed), run_id))


def insert_cost_estimate(
    run_id: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    real_cost_usd: float,
    reference_model: str | None,
    reference_cost_usd: float | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cost_estimates "
            "(run_id, prompt_tokens, completion_tokens, real_cost_usd, reference_model, "
            "reference_cost_usd, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, prompt_tokens, completion_tokens, real_cost_usd,
                reference_model, reference_cost_usd, time.time(),
            ),
        )


# Read side — backs the api.py /api/* endpoints

def list_runs(limit: int = 50) -> list[dict]:
    """Recent runs with per-agent latencies, shadow score, and cost — for RunsTable."""
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        runs = [
            dict(r) for r in conn.execute(
                "SELECT run_id, label, repo, pr_number, started_at, completed_at, "
                "total_latency_ms, gate_passed, status FROM runs "
                "ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]

        for run in runs:
            run["agents"] = [
                dict(a) for a in conn.execute(
                    "SELECT agent, latency_ms FROM agent_runs WHERE run_id = ? ORDER BY id",
                    (run["run_id"],),
                ).fetchall()
            ]

            gate = conn.execute(
                "SELECT score, threshold FROM gate_decisions WHERE run_id = ?",
                (run["run_id"],),
            ).fetchone()
            run["shadow_score"] = gate["score"] if gate else None

            cost = conn.execute(
                "SELECT real_cost_usd, reference_cost_usd FROM cost_estimates WHERE run_id = ?",
                (run["run_id"],),
            ).fetchone()
            run["real_cost_usd"]      = cost["real_cost_usd"] if cost else None
            run["reference_cost_usd"] = cost["reference_cost_usd"] if cost else None

    return runs


def get_run(run_id: str) -> dict | None:
    """Full detail for one run — report text, per-agent findings, gate breakdown, cost."""
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)

        result["agents"] = [
            dict(a) for a in conn.execute(
                "SELECT agent, latency_ms, output_chars, recorded_at FROM agent_runs "
                "WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        ]

        gate = conn.execute("SELECT * FROM gate_decisions WHERE run_id = ?", (run_id,)).fetchone()
        if gate:
            gate_dict = dict(gate)
            gate_dict["breakdown"]   = json.loads(gate_dict["breakdown"]) if gate_dict["breakdown"] else None
            gate_dict["key_issues"]  = json.loads(gate_dict["key_issues"]) if gate_dict["key_issues"] else []
            result["gate_decision"] = gate_dict
        else:
            result["gate_decision"] = None

        cost = conn.execute("SELECT * FROM cost_estimates WHERE run_id = ?", (run_id,)).fetchone()
        result["cost_estimate"] = dict(cost) if cost else None

    return result


def get_stats_summary(trend_days: int = 30) -> dict:
    """Aggregate stats for the dashboard's StatsSummary cards + trend chart."""
    init_db()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row

        totals = conn.execute(
            "SELECT COUNT(*) AS total_runs, AVG(total_latency_ms) AS avg_latency_ms "
            "FROM runs WHERE status = 'complete'"
        ).fetchone()

        gate_totals = conn.execute(
            "SELECT COUNT(*) AS scored, SUM(passed) AS passed FROM gate_decisions"
        ).fetchone()

        cost_totals = conn.execute(
            "SELECT AVG(real_cost_usd) AS avg_real_cost_usd, "
            "AVG(reference_cost_usd) AS avg_reference_cost_usd FROM cost_estimates"
        ).fetchone()

        trend = [
            dict(t) for t in conn.execute(
                "SELECT date(started_at, 'unixepoch') AS day, COUNT(*) AS runs, "
                "AVG(total_latency_ms) AS avg_latency_ms FROM runs "
                "WHERE status = 'complete' GROUP BY day ORDER BY day DESC LIMIT ?",
                (trend_days,),
            ).fetchall()
        ]

    scored = gate_totals["scored"] or 0
    passed = gate_totals["passed"] or 0

    return {
        "total_runs":             totals["total_runs"] or 0,
        "avg_latency_ms":         totals["avg_latency_ms"],
        "gate_pass_rate":         (passed / scored) if scored else None,
        "gate_scored_runs":       scored,
        "avg_real_cost_usd":      cost_totals["avg_real_cost_usd"],
        "avg_reference_cost_usd": cost_totals["avg_reference_cost_usd"],
        "trend":                  trend,
    }
