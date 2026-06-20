"""Auth module — TRUE NEGATIVE: no intentional vulnerabilities.
The agent should find no HIGH/CRITICAL issues here.
"""
import secrets
import hmac
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

import bcrypt
import jwt
from flask import request, jsonify

# Key loaded from environment, never hardcoded
import os
SECRET_KEY: str = os.environ["JWT_SECRET_KEY"]


def hash_password(password: str) -> str:
    """Hash with bcrypt (auto-salted, work-factor adjustable)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, expires_in: int = 3600) -> str:
    payload = {
        "sub": user_id,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(seconds=expires_in),
        "jti": secrets.token_hex(16),   # unique token ID to allow revocation
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing token"}), 401
        token = auth_header[7:]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(user_id=payload["sub"], *args, **kwargs)
    return decorated


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Constant-time comparison prevents timing attacks."""
    webhook_secret = os.environ["WEBHOOK_SECRET"].encode()
    expected = hmac.new(webhook_secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def generate_reset_token() -> str:
    """Cryptographically secure 32-byte token."""
    return secrets.token_urlsafe(32)
