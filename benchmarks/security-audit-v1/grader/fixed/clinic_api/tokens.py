"""Signed session tokens: ``<base64url(json payload)>.<base64url(hmac-sha256)>``.

The service issues these itself at login; there is no third-party identity
provider, so a stdlib HMAC is all a token needs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# Set CLINIC_TOKEN_SECRET in production; without it every process start gets a
# fresh random key, so tokens never outlive the process that issued them.
SECRET = os.environ.get("CLINIC_TOKEN_SECRET", "").encode() or secrets.token_bytes(32)
TTL_SECONDS = 3600


class InvalidToken(Exception):
    """The token is malformed, forged or expired."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())


def issue(user_id: int, role: str, now: float | None = None) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": int((now or time.time()) + TTL_SECONDS),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def verify(token: str, now: float | None = None) -> dict:
    """The token's payload, or InvalidToken."""
    try:
        body, signature = token.split(".")
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidToken("malformed token") from exc
    if not hmac.compare_digest(signature, _sign(body)):
        raise InvalidToken("bad signature")
    if payload.get("exp", 0) < (now or time.time()):
        raise InvalidToken("expired token")
    return payload
