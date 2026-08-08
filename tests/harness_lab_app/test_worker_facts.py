"""The worker's answer to "is the run runtime usable?" (ADR-0018).

Docker facts have to come from the worker: the API container has no socket by
design (ADR-0002). These tests pin the contract the health panel reads — and
that a broken docker is *reported*, never raised.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from harness_lab_app import tasks


@pytest.fixture
def docker_backend(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            execution_backend="docker", runner_image="harness-lab-runner:latest"
        ),
    )


def _fake_docker(monkeypatch, *, version="27.0.1", image_present=True, boom=None):
    class _Images:
        def get(self, _name):
            if not image_present:
                raise RuntimeError("no such image")
            return object()

    class _Client:
        images = _Images()

        def version(self):
            return {"Version": version}

    def _from_env():
        if boom is not None:
            raise boom
        return _Client()

    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=_from_env))


def test_facts_report_docker_and_the_runner_image(docker_backend, monkeypatch):
    _fake_docker(monkeypatch)
    facts = tasks.harness_facts(None)

    assert facts["backend"] == "docker"
    assert facts["docker_ok"] is True
    assert facts["docker_version"] == "27.0.1"
    assert facts["runner_image_present"] is True
    assert facts["docker_error"] == ""


def test_a_missing_runner_image_is_reported_without_failing_docker(
    docker_backend, monkeypatch
):
    _fake_docker(monkeypatch, image_present=False)
    facts = tasks.harness_facts(None)

    assert facts["docker_ok"] is True
    assert facts["runner_image_present"] is False


def test_an_unusable_socket_is_reported_not_raised(docker_backend, monkeypatch):
    _fake_docker(monkeypatch, boom=PermissionError("/var/run/docker.sock"))
    facts = tasks.harness_facts(None)

    assert facts["docker_ok"] is False
    assert "PermissionError" in facts["docker_error"]


def test_the_subprocess_backend_never_touches_docker(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(execution_backend="subprocess", runner_image="x"),
    )

    def _explode():
        raise AssertionError("docker must not be imported for this backend")

    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=_explode))
    facts = tasks.harness_facts(None)

    assert facts["backend"] == "subprocess"
    assert facts["docker_ok"] is False


def test_reconcile_runs_task_delegates_to_the_boot_time_reconciler(monkeypatch):
    """Same body the worker runs on restart, exposed as a heal button."""
    calls: list[str] = []
    monkeypatch.setattr(tasks, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(
        tasks, "reconcile_stale_runs", lambda: (calls.append("r"), 3)[1]
    )

    assert tasks.reconcile_runs() == 3
    assert calls == ["init_db", "r"]
