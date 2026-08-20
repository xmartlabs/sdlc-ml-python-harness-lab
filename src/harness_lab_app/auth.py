"""Single-user authentication for the always-on lab (ADR-0015).

Stdlib only — ``uv.lock`` carries neither ``passlib`` nor ``itsdangerous``, and
one password plus one cookie does not justify either.

Three pieces:

* **Password hashing** — PBKDF2-HMAC-SHA256 with a per-password salt, stored in
  ``.env`` as ``HARNESS_LAB_ADMIN_PASSWORD_HASH``. Verification is constant-time.
* **Session tokens** — an HMAC-SHA256 signature over a timestamped payload,
  keyed by ``HARNESS_LAB_SESSION_SECRET``. Nothing is stored server-side, so a
  restart does not log the user out; rotating the secret does.
* **A login throttle** — in-memory, per client address, so a LAN neighbour
  cannot grind through a weak password.

**Open mode:** an empty password hash disables authentication entirely. That is
the default, keeps the dev flow and the existing test suite untouched, and is why
the UI shows a persistent banner until the user runs ``make set-password``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

# Cost tuned so a login costs ~100ms on the kind of host this lab runs on.
PBKDF2_ITERATIONS = 480_000
PBKDF2_PREFIX = "pbkdf2_sha256"
SESSION_COOKIE = "harness_lab_session"
DEFAULT_SESSION_TTL = 7 * 24 * 3600

# Throttle: after this many consecutive failures the address is blocked, with the
# block doubling up to the cap on each further failure.
MAX_FAILURES = 5
BASE_BLOCK_SECONDS = 30.0
MAX_BLOCK_SECONDS = 900.0

_failures: dict[str, int] = {}
_blocked_until: dict[str, float] = {}


class AuthError(ValueError):
    """Raised when a login attempt is refused."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# -- passwords --------------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return a self-describing ``pbkdf2_sha256$iters$salt$hash`` string."""
    if not password:
        raise AuthError("Password must not be empty.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{PBKDF2_PREFIX}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored hash.

    A malformed or empty hash returns False rather than raising: callers decide
    what an unconfigured password means (see :func:`auth_enabled`).
    """
    if not password or not stored:
        return False
    try:
        prefix, raw_iterations, salt_b64, digest_b64 = stored.split("$")
        if prefix != PBKDF2_PREFIX:
            return False
        iterations = int(raw_iterations)
        salt = _unb64(salt_b64)
        expected = _unb64(digest_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def generate_secret() -> str:
    """A fresh session-signing secret."""
    return secrets.token_urlsafe(48)


# -- sessions ---------------------------------------------------------------


def issue_session(secret: str, *, issued_at: float | None = None) -> str:
    """Sign a session token. ``issued_at`` is injectable for tests."""
    if not secret:
        raise AuthError("No session secret configured.")
    payload = json.dumps(
        {"iat": int(issued_at if issued_at is not None else time.time())},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_session(
    secret: str,
    token: str,
    *,
    max_age: int = DEFAULT_SESSION_TTL,
    now: float | None = None,
) -> bool:
    """True when ``token`` carries a valid, unexpired signature from ``secret``."""
    if not secret or not token or "." not in token:
        return False
    payload_b64, _, signature_b64 = token.partition(".")
    try:
        payload = _unb64(payload_b64)
        signature = _unb64(signature_b64)
    except (ValueError, TypeError):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        issued_at = int(json.loads(payload)["iat"])
    except (ValueError, KeyError, TypeError):
        return False
    age = (now if now is not None else time.time()) - issued_at
    # A token dated in the future is a clock skew or a forgery attempt; either
    # way it is not something to trust.
    return 0 <= age <= max_age


# -- throttle ---------------------------------------------------------------


def _block_seconds(failures: int) -> float:
    over = max(0, failures - MAX_FAILURES)
    return min(BASE_BLOCK_SECONDS * (2**over), MAX_BLOCK_SECONDS)


def throttle_retry_after(client: str, *, now: float | None = None) -> float:
    """Seconds the caller must wait, or 0.0 when it may attempt a login."""
    until = _blocked_until.get(client)
    if until is None:
        return 0.0
    remaining = until - (now if now is not None else time.monotonic())
    return max(0.0, remaining)


def record_failure(client: str, *, now: float | None = None) -> None:
    failures = _failures.get(client, 0) + 1
    _failures[client] = failures
    if failures >= MAX_FAILURES:
        clock = now if now is not None else time.monotonic()
        _blocked_until[client] = clock + _block_seconds(failures)


def record_success(client: str) -> None:
    _failures.pop(client, None)
    _blocked_until.pop(client, None)


def reset_throttle() -> None:
    """Clear all throttle state (used by tests)."""
    _failures.clear()
    _blocked_until.clear()
