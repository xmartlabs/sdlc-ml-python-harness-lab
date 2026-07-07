from __future__ import annotations

from harness_lab_app.api import create_app


def _route_endpoint(app, path: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"Route not found: {path}")


def test_health_endpoint():
    app = create_app()

    assert _route_endpoint(app, "/api/health")() == {"status": "ok"}


def test_login_endpoint_accepts_default_credentials():
    app = create_app()
    login = _route_endpoint(app, "/api/auth/login")

    assert login(type("Login", (), {"username": "admin", "password": "admin"})()) == {
        "authenticated": True
    }
