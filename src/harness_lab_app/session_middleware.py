"""Fail-closed session gate for the API (ADR-0015).

Middleware rather than a ``Depends``: a dependency has to be remembered on every
new route, and forgetting it is silent. A middleware covers routes added later by
construction, is one place to audit, and leaves the direct-call test suite (which
never goes through the HTTP stack) untouched.

Authentication material is read from ``.env`` on each request rather than from
the ``lru_cache``d settings object, so ``make set-password`` takes effect without
restarting the stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from harness_lab_app.auth import DEFAULT_SESSION_TTL, SESSION_COOKIE, verify_session
from harness_lab_app.config import read_env_file_value

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

HASH_KEY = "HARNESS_LAB_ADMIN_PASSWORD_HASH"
SECRET_KEY = "HARNESS_LAB_SESSION_SECRET"
TTL_KEY = "HARNESS_LAB_SESSION_TTL_SECONDS"

# ``/api/health`` is the compose healthcheck and must stay reachable without a
# session. ``/api/health/detail`` deliberately is NOT public: it reports gateway,
# worker and drift state.
PUBLIC_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
    }
)

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def admin_password_hash() -> str:
    return read_env_file_value(HASH_KEY)


def session_secret() -> str:
    return read_env_file_value(SECRET_KEY)


def session_ttl() -> int:
    raw = read_env_file_value(TTL_KEY)
    try:
        return int(raw) if raw else DEFAULT_SESSION_TTL
    except ValueError:
        return DEFAULT_SESSION_TTL


def auth_enabled() -> bool:
    """False in *open mode* — no password configured, so the gate is a no-op."""
    return bool(admin_password_hash() and session_secret())


def has_valid_session(request: Request) -> bool:
    return verify_session(
        session_secret(),
        request.cookies.get(SESSION_COOKIE, ""),
        max_age=session_ttl(),
    )


def _same_origin(request: Request) -> bool:
    """Reject a mutating request whose Origin is not this host.

    ``SameSite=Lax`` already stops a cross-site POST from carrying the cookie;
    this is the second lock, and the one that still holds if a future browser
    loosens that default.
    """
    origin = request.headers.get("origin")
    if not origin:
        # Browsers omit Origin only for same-origin navigations; non-browser
        # clients (curl, the CLI lane) never carry an ambient cookie anyway.
        return True
    host = request.headers.get("host", "")
    return urlsplit(origin).netloc == host


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or not path.startswith("/api/")
            or path in PUBLIC_PATHS
            or not auth_enabled()
        ):
            return await call_next(request)

        if request.method in MUTATING_METHODS and not _same_origin(request):
            return JSONResponse(
                {"detail": "Cross-origin request refused."}, status_code=403
            )
        if not has_valid_session(request):
            return JSONResponse({"detail": "Authentication required."}, status_code=401)
        return await call_next(request)
