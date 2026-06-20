"""User dashboard data fetcher — contains N+1 query patterns."""
import sqlite3
from typing import List


def get_users_with_orders(db_path: str) -> List[dict]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT id, name, email FROM users")
    users = cursor.fetchall()

    result = []
    for user_id, name, email in users:
        # PERF: N+1 — one query per user for orders
        cursor.execute(
            "SELECT id, total, status FROM orders WHERE user_id = ?", (user_id,)
        )
        orders = cursor.fetchall()

        # PERF: N+1 — another query per user for address
        cursor.execute(
            "SELECT street, city, country FROM addresses WHERE user_id = ?", (user_id,)
        )
        address = cursor.fetchone()

        result.append(
            {
                "id": user_id,
                "name": name,
                "email": email,
                "orders": [{"id": o[0], "total": o[1], "status": o[2]} for o in orders],
                "address": address,
            }
        )

    conn.close()
    return result


def load_post_comments(db_path: str, post_ids: List[int]) -> dict:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    comments = {}
    for post_id in post_ids:
        # PERF: N+1 — separate SELECT per post
        cursor.execute(
            "SELECT author_id, body, created_at FROM comments WHERE post_id = ?",
            (post_id,),
        )
        rows = cursor.fetchall()

        enriched = []
        for author_id, body, created_at in rows:
            # PERF: nested N+1 — author lookup inside comment loop
            cursor.execute("SELECT username FROM users WHERE id = ?", (author_id,))
            author = cursor.fetchone()
            enriched.append({"author": author[0] if author else "?", "body": body})

        comments[post_id] = enriched

    conn.close()
    return comments
