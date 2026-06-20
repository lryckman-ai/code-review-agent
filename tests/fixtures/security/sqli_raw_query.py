"""User lookup module — contains SQL injection vulnerabilities."""
import sqlite3


def get_user_by_name(db_path: str, username: str):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # VULN: f-string interpolation — attacker controls username
    query = f"SELECT id, email, role FROM users WHERE username = '{username}'"
    cursor.execute(query)
    return cursor.fetchone()


def get_user_posts(db_path: str, user_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # VULN: string concatenation with user-supplied id
    query = "SELECT title, body FROM posts WHERE user_id = " + str(user_id)
    cursor.execute(query)
    return cursor.fetchall()


def search_users(db_path: str, search_term: str, role_filter: str = "user"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # VULN: both fields injectable
    query = (
        f"SELECT id, username FROM users "
        f"WHERE (username LIKE '%{search_term}%' OR email LIKE '%{search_term}%') "
        f"AND role = '{role_filter}'"
    )
    cursor.execute(query)
    return cursor.fetchall()
