"""Improvement-proposals API: gating and artifact reading (no LLM, no celery)."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from harness_lab_app.api import create_app


def _endpoint(app, path: str):
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def test_get_improvements_404_when_absent(tmp_path, monkeypatch):
    from harness_lab_app import api as api_module

    class _Settings:
        data_runs_dir = tmp_path

    monkeypatch.setattr(api_module, "get_settings", lambda: _Settings())
    endpoint = _endpoint(create_app(), "/api/runs/{run_id}/improvements")
    with pytest.raises(HTTPException) as exc:
        endpoint("run-x")
    assert exc.value.status_code == 404


def test_get_improvements_returns_doc(tmp_path, monkeypatch):
    from harness_lab_app import api as api_module

    class _Settings:
        data_runs_dir = tmp_path

    doc = {"run_id": "run-x", "proposals": [{"title": "t", "body": "b"}]}
    (tmp_path / "run-x").mkdir()
    (tmp_path / "run-x" / "improvements.json").write_text(json.dumps(doc))
    monkeypatch.setattr(api_module, "get_settings", lambda: _Settings())
    endpoint = _endpoint(create_app(), "/api/runs/{run_id}/improvements")
    assert endpoint("run-x") == doc
