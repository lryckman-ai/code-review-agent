"""Auth + data layer — intentionally contains issues across all three review tracks."""
import hashlib
import random
import sqlite3
import subprocess

import yaml

# SEC: hardcoded production credential
DB_URL = "postgresql://app_user:Prod$ecret99@db.prod.internal/appdb"


# ── Security issues ────────────────────────────────────────────────────────────

def authenticate(username: str, password: str, db_path: str) -> bool:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # SEC: SQL injection — username injected into f-string
    row = cursor.execute(
        f"SELECT password_hash FROM users WHERE username = '{username}'"
    ).fetchone()
    if not row:
        return False
    # DEP: MD5 for password verification
    return hashlib.md5(password.encode()).hexdigest() == row[0]


def run_maintenance_job(job_name: str) -> str:
    # SEC: command injection — job_name not sanitized
    result = subprocess.run(
        f"python jobs/{job_name}.py", shell=True, capture_output=True, text=True
    )
    return result.stdout


def load_app_config(config_path: str) -> dict:
    with open(config_path) as f:
        # DEP: unsafe yaml.load
        return yaml.load(f)


# ── Performance issues ─────────────────────────────────────────────────────────

def get_dashboard(user_id: int, db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT id, name FROM users")
    users = cursor.fetchall()

    data = []
    for uid, name in users:
        # PERF: N+1 — separate SELECT per user
        cursor.execute("SELECT id, amount, status FROM orders WHERE user_id = ?", (uid,))
        orders = cursor.fetchall()
        # PERF: second N+1 per user
        cursor.execute("SELECT tag FROM user_tags WHERE user_id = ?", (uid,))
        tags = [r[0] for r in cursor.fetchall()]
        data.append({"user": name, "orders": len(orders), "tags": tags})

    conn.close()
    return {"viewer": user_id, "data": data}


def find_flagged_users(user_list: list, flag_list: list) -> list:
    # PERF: O(n·m) — should convert flag_list to a set first
    return [u for u in user_list for f in flag_list if u["id"] == f]


# ── Dependency issues ──────────────────────────────────────────────────────────

def generate_session_token() -> str:
    # DEP: random is predictable — use secrets.token_hex
    return hex(random.getrandbits(128))[2:]


def load_user_model(path: str):
    with open(path, "rb") as f:
        # DEP: pickle on filesystem file
        import pickle
        return pickle.load(f)
