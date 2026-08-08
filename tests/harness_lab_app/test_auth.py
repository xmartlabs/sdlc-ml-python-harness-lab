from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from harness_lab_app import session_middleware as sm
from harness_lab_app.auth import (
    DEFAULT_SESSION_TTL,
    MAX_FAILURES,
    SESSION_COOKIE,
    AuthError,
    generate_secret,
    hash_password,
    issue_session,
    record_failure,
    record_success,
    reset_throttle,
    throttle_retry_after,
    verify_password,
    verify_session,
)

FAST_ITERATIONS = 1_000
SECRET = "test-session-secret"


@pytest.fixture(autouse=True)
def _clean_throttle():
    reset_throttle()
    yield
    reset_throttle()


# -- passwords --------------------------------------------------------------


def test_hash_password_round_trips():
    stored = hash_password("correct horse", iterations=FAST_ITERATIONS)

    assert verify_password("correct horse", stored) is True
    assert verify_password("wrong horse", stored) is False


def test_hash_password_salts_each_call():
    first = hash_password("same", iterations=FAST_ITERATIONS)
    second = hash_password("same", iterations=FAST_ITERATIONS)

    assert first != second


def test_hash_password_rejects_empty():
    with pytest.raises(AuthError):
        hash_password("")


@pytest.mark.parametrize(
    "stored",
    ["", "not-a-hash", "bcrypt$1$a$b", "pbkdf2_sha256$notanint$a$b"],
)
def test_verify_password_refuses_malformed_hash(stored):
    """A corrupt hash must fail closed, never raise into the request handler."""
    assert verify_password("anything", stored) is False


def test_generated_values_fit_the_env_format():
    """The hash and secret must survive the quote-free .env format."""
    from harness_lab_app.env_file import validate_assignment

    hashed = hash_password("pw", iterations=FAST_ITERATIONS)
    validate_assignment("HARNESS_LAB_ADMIN_PASSWORD_HASH", hashed)
    validate_assignment("HARNESS_LAB_SESSION_SECRET", generate_secret())


# -- sessions ---------------------------------------------------------------


def test_session_round_trips():
    token = issue_session(SECRET)

    assert verify_session(SECRET, token) is True


def test_session_rejects_other_secret():
    token = issue_session(SECRET)

    assert verify_session("rotated-secret", token) is False


def test_session_rejects_tampered_payload():
    token = issue_session(SECRET)
    payload, _, signature = token.partition(".")

    assert verify_session(SECRET, f"{payload}x.{signature}") is False


def test_session_expires():
    token = issue_session(SECRET, issued_at=1_000.0)

    assert verify_session(SECRET, token, max_age=60, now=1_030.0) is True
    assert verify_session(SECRET, token, max_age=60, now=1_120.0) is False


def test_session_rejects_future_token():
    """Clock skew or forgery; either way it is not something to trust."""
    token = issue_session(SECRET, issued_at=5_000.0)

    assert verify_session(SECRET, token, now=1_000.0) is False


@pytest.mark.parametrize("token", ["", "nodot", "a.b", "."])
def test_session_refuses_garbage(token):
    assert verify_session(SECRET, token) is False


def test_session_requires_a_secret():
    """Open mode must never accidentally validate a token."""
    assert verify_session("", issue_session(SECRET)) is False
    with pytest.raises(AuthError):
        issue_session("")


# -- throttle ---------------------------------------------------------------


def test_throttle_blocks_after_repeated_failures():
    for _ in range(MAX_FAILURES):
        record_failure("10.0.0.5", now=0.0)

    assert throttle_retry_after("10.0.0.5", now=0.0) > 0
    assert throttle_retry_after("10.0.0.6", now=0.0) == 0.0


def test_throttle_backs_off_further_on_each_extra_failure():
    for _ in range(MAX_FAILURES):
        record_failure("10.0.0.5", now=0.0)
    first = throttle_retry_after("10.0.0.5", now=0.0)
    record_failure("10.0.0.5", now=0.0)

    assert throttle_retry_after("10.0.0.5", now=0.0) > first


def test_throttle_expires():
    for _ in range(MAX_FAILURES):
        record_failure("10.0.0.5", now=0.0)

    assert throttle_retry_after("10.0.0.5", now=10_000.0) == 0.0


def test_success_clears_the_throttle():
    for _ in range(MAX_FAILURES):
        record_failure("10.0.0.5", now=0.0)
    record_success("10.0.0.5")

    assert throttle_retry_after("10.0.0.5", now=0.0) == 0.0


# -- middleware -------------------------------------------------------------


def _request(path: str, method: str = "GET", cookies=None, headers=None) -> Request:
    merged = {"host": "lab.local:8080", **(headers or {})}
    if cookies:
        merged["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "server": ("lab.local", 8080),
            "path": path,
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in merged.items()],
            "client": ("10.0.0.5", 51234),
        }
    )


async def _ok(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _dispatch(request: Request):
    return asyncio.run(sm.SessionMiddleware(app=None).dispatch(request, _ok))


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(sm, "auth_enabled", lambda: True)
    monkeypatch.setattr(sm, "session_secret", lambda: SECRET)
    monkeypatch.setattr(sm, "session_ttl", lambda: DEFAULT_SESSION_TTL)


def test_open_mode_lets_everything_through(monkeypatch):
    monkeypatch.setattr(sm, "auth_enabled", lambda: False)

    assert _dispatch(_request("/api/runs")).status_code == 200


def test_protected_route_requires_a_session(auth_on):
    assert _dispatch(_request("/api/runs")).status_code == 401


def test_valid_cookie_passes(auth_on):
    request = _request("/api/runs", cookies={SESSION_COOKIE: issue_session(SECRET)})

    assert _dispatch(request).status_code == 200


def test_forged_cookie_is_refused(auth_on):
    request = _request(
        "/api/runs", cookies={SESSION_COOKIE: issue_session("other-secret")}
    )

    assert _dispatch(request).status_code == 401


@pytest.mark.parametrize(
    "path", ["/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/session"]
)
def test_public_paths_stay_open(auth_on, path):
    """The compose healthcheck and the login flow must work without a cookie."""
    assert _dispatch(_request(path, method="POST")).status_code == 200


def test_health_detail_is_not_public(auth_on):
    """It reports gateway, worker and drift state — that is not public."""
    assert _dispatch(_request("/api/health/detail")).status_code == 401


def test_non_api_paths_are_not_gated(auth_on):
    """The SPA itself must load so it can render the login form."""
    assert _dispatch(_request("/index.html")).status_code == 200


def test_preflight_is_not_gated(auth_on):
    assert _dispatch(_request("/api/runs", method="OPTIONS")).status_code == 200


def test_cross_origin_mutation_is_refused(auth_on):
    request = _request(
        "/api/runs",
        method="POST",
        cookies={SESSION_COOKIE: issue_session(SECRET)},
        headers={"origin": "http://evil.example"},
    )

    assert _dispatch(request).status_code == 403


def test_same_origin_mutation_is_allowed(auth_on):
    request = _request(
        "/api/runs",
        method="POST",
        cookies={SESSION_COOKIE: issue_session(SECRET)},
        headers={"origin": "http://lab.local:8080"},
    )

    assert _dispatch(request).status_code == 200


def test_auth_enabled_needs_both_hash_and_secret(monkeypatch):
    """A half-configured .env must stay in open mode, not lock the user out."""
    monkeypatch.setattr(sm, "admin_password_hash", lambda: "pbkdf2_sha256$1$a$b")
    monkeypatch.setattr(sm, "session_secret", lambda: "")

    assert sm.auth_enabled() is False


def test_session_ttl_falls_back_on_garbage(monkeypatch):
    monkeypatch.setattr(sm, "read_env_file_value", lambda _key: "not-a-number")

    assert sm.session_ttl() == DEFAULT_SESSION_TTL
