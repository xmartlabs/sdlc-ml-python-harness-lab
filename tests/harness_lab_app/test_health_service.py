"""Health and drift panel (ADR-0018).

Two properties matter more than any individual check. First, **a broken lab
must still render**: one probe raising cannot take the panel down, because the
panel is what you open precisely when things are broken. Second, **heal is an
allowlist**, not a command runner — an unknown action is refused before any
work happens.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import health_service as module
from harness_lab_app.api import create_app
from harness_lab_app.models import Run, RunState, utc_now
from harness_lab_app.schemas import HealRequest


def _route_endpoint(app, path: str, method: str | None = None):
    for route in app.router.routes:
        if getattr(route, "path", None) != path:
            continue
        if method is not None and method not in getattr(route, "methods", set()):
            continue
        return route.endpoint
    raise AssertionError(f"Route not found: {path} ({method})")


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No gateway, no broker, no worker — the panel must still answer."""
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(repo_root=tmp_path, docker_host_alias="host.docker"),
    )
    monkeypatch.setattr(module, "read_env_file_value", lambda _key: "")
    monkeypatch.setattr(module, "_worker_facts", dict)
    # Langfuse resolves its own config from the environment, so a developer
    # with tracing exported in their shell must not change this panel.
    for name in ("LANGFUSE_ENABLED", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _by_id(report: dict) -> dict:
    return {check["id"]: check for check in report["checks"]}


# -- the panel ---------------------------------------------------------------


def test_every_check_reports_even_with_nothing_running(isolated):
    with _session() as session:
        report = module.collect_health(session)

    checks = _by_id(report)
    assert set(checks) == {
        "gateway",
        "llm_transport",
        "redis",
        "worker",
        "docker",
        "skills_drift",
        "git_tree",
        "staging",
        "model_overlay",
        "langfuse",
        "orphan_runs",
    }
    assert all(check["latency_ms"] is not None for check in checks.values())
    # Nothing is configured, so this lab is not healthy — and says so.
    assert report["status"] != module.OK


def test_a_raising_probe_becomes_a_row_not_a_crash(isolated, monkeypatch):
    def _boom() -> None:
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(module, "_check_redis", _boom)
    with _session() as session:
        report = module.collect_health(session)

    redis = _by_id(report)["redis"]
    assert redis["status"] == module.UNKNOWN
    assert "socket exploded" in redis["detail"]


def test_worker_facts_failure_does_not_abort_the_panel(isolated, monkeypatch):
    def _boom() -> dict:
        raise TimeoutError("no broker")

    monkeypatch.setattr(module, "_worker_facts", _boom)
    with _session() as session:
        report = module.collect_health(session)

    assert _by_id(report)["worker"]["status"] == module.ERROR


def test_worst_status_sorts_first_and_wins_overall(isolated, monkeypatch):
    monkeypatch.setattr(
        module, "_check_git_tree", lambda: (module.ERROR, "everything is on fire", [])
    )
    with _session() as session:
        report = module.collect_health(session)

    assert report["status"] == module.ERROR
    assert report["checks"][0]["status"] == module.ERROR


def test_a_healthy_check_offers_no_heal_button(isolated, monkeypatch):
    monkeypatch.setattr(module, "_check_git_tree", lambda: (module.OK, "clean", []))
    with _session() as session:
        checks = _by_id(module.collect_health(session))

    assert checks["git_tree"]["heal"] is None


def test_a_failing_check_offers_its_heal_action(isolated, monkeypatch):
    monkeypatch.setattr(
        module, "_check_git_tree", lambda: (module.WARN, "mid-ablation", [])
    )
    with _session() as session:
        checks = _by_id(module.collect_health(session))

    assert checks["git_tree"]["heal"] == "skills_sync"


# -- individual probes -------------------------------------------------------


def test_docker_is_unknown_rather_than_ok_when_no_worker_replies():
    status, detail, _ = module._check_docker({})
    assert status == module.UNKNOWN
    assert "worker" in detail


def test_docker_reports_the_workers_error_verbatim():
    status, detail, _ = module._check_docker(
        {
            "backend": "docker",
            "docker_ok": False,
            "docker_error": "PermissionError: /var/run",
        }
    )
    assert status == module.ERROR
    assert "PermissionError" in detail


def test_a_missing_runner_image_is_a_warning_not_an_error():
    """The lab is usable — it just needs a build — so this must not read as
    'docker is broken'."""
    status, _, _ = module._check_docker(
        {
            "backend": "docker",
            "docker_ok": True,
            "runner_image_present": False,
            "runner_image": "harness-lab-runner:latest",
        }
    )
    assert status == module.WARN


def test_a_missing_runner_image_points_at_the_button_not_the_shell():
    """Self-management is the point: the remedy must be in the panel."""
    _, detail, _ = module._check_docker(
        {
            "backend": "docker",
            "docker_ok": True,
            "runner_image_present": False,
            "runner_image": "harness-lab-runner:latest",
        }
    )
    assert "Build runner image" in detail
    assert "make app-up" not in detail


def test_the_subprocess_backend_does_not_need_docker():
    status, _, _ = module._check_docker({"backend": "subprocess"})
    assert status == module.OK


def test_a_long_running_run_is_flagged_as_orphaned(isolated):
    with _session() as session:
        session.add(
            Run(
                run_id="k-001",
                run_config_id=1,
                state=RunState.running,
                started_at=utc_now() - timedelta(hours=9),
            )
        )
        session.add(
            Run(
                run_id="k-002",
                run_config_id=1,
                state=RunState.running,
                started_at=utc_now(),
            )
        )
        session.commit()
        status, _, items = module._check_orphan_runs(session)

    assert status == module.WARN
    assert items == ["k-001 (running)"]


def test_a_finished_run_is_never_orphaned(isolated):
    with _session() as session:
        session.add(
            Run(
                run_id="k-003",
                run_config_id=1,
                state=RunState.completed,
                started_at=utc_now() - timedelta(days=30),
            )
        )
        session.commit()
        status, _, _ = module._check_orphan_runs(session)

    assert status == module.OK


def test_gateway_without_credentials_warns_instead_of_erroring(isolated):
    """An unconfigured gateway is a setup step, not a fault."""
    status, detail, _ = module._check_gateway()
    assert status == module.WARN
    assert "GATEWAY_BASE_URL" in detail


# -- llm transport -----------------------------------------------------------


def test_a_subscription_only_lab_is_healthy_on_the_transport_row(monkeypatch):
    """No gateway is not the same as no judge: the CLI is a working answer."""
    from harness_lab_app import llm_transport

    monkeypatch.setattr(llm_transport, "available_transport", lambda: "claude_cli")
    status, detail, _ = module._check_llm_transport()
    assert status == module.OK
    assert "claude-" in detail


def test_a_credential_without_the_cli_installed_names_the_missing_piece(monkeypatch):
    """The api image may predate the CLI; say that rather than "no transport"."""
    from harness_lab_app import llm_transport

    monkeypatch.setattr(llm_transport, "available_transport", lambda: "none")
    monkeypatch.setattr(
        llm_transport, "claude_cli_credential", lambda: ("ANTHROPIC_API_KEY", "sk-x")
    )
    monkeypatch.setattr(llm_transport, "claude_cli_path", lambda: "")
    status, detail, _ = module._check_llm_transport()
    assert status == module.WARN
    assert "PATH" in detail


def test_no_credentials_at_all_warns_that_judging_is_unavailable(monkeypatch):
    from harness_lab_app import llm_transport

    monkeypatch.setattr(llm_transport, "available_transport", lambda: "none")
    monkeypatch.setattr(llm_transport, "claude_cli_credential", lambda: None)
    status, detail, _ = module._check_llm_transport()
    assert status == module.WARN
    assert "CLAUDE_CODE_OAUTH_TOKEN" in detail


# -- langfuse ----------------------------------------------------------------


def test_tracing_switched_off_is_ok_not_a_warning(isolated):
    """Most labs never turn Langfuse on; that is a choice, not a fault."""
    status, detail, _ = module._check_langfuse()
    assert status == module.OK
    assert "off" in detail


def test_tracing_asked_for_without_keys_warns(isolated, monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    status, detail, _ = module._check_langfuse()
    assert status == module.WARN
    assert "LANGFUSE_PUBLIC_KEY" in detail


def test_bad_credentials_are_an_error_not_a_warning(isolated, monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    import harness_lab_app.observability.client as client

    monkeypatch.setattr(
        client, "check_connection", lambda _config: client.SendResult(False, 401, "bad")
    )
    status, detail, _ = module._check_langfuse()
    assert status == module.ERROR
    assert detail == "bad"


def test_an_unreachable_host_warns_rather_than_erroring(isolated, monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    import harness_lab_app.observability.client as client

    monkeypatch.setattr(
        client,
        "check_connection",
        lambda _config: client.SendResult(False, 0, "Could not reach host."),
    )
    status, detail, _ = module._check_langfuse()
    assert status == module.WARN
    assert detail == "Could not reach host."


def test_a_reachable_langfuse_says_whether_export_is_automatic(isolated, monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_AUTO_EXPORT", "false")
    import harness_lab_app.observability.client as client

    monkeypatch.setattr(
        client, "check_connection", lambda _config: client.SendResult(True, 200, "up.")
    )
    status, detail, _ = module._check_langfuse()
    assert status == module.OK
    assert "manual export only" in detail


# -- heal --------------------------------------------------------------------


def test_an_unknown_heal_action_is_refused_before_any_work(isolated):
    with _session() as session, pytest.raises(module.HealthError, match="Unknown heal"):
        module.heal("rm -rf /", session)


def test_reconcile_runs_is_delegated_to_the_worker(isolated, monkeypatch):
    """The API must never hold the docker socket (ADR-0002), so the only thing
    this action may do is publish a task."""
    published: list[str] = []
    monkeypatch.setattr(
        "harness_lab_app.celery_app.enqueue_reconcile_runs",
        lambda: published.append("reconcile"),
    )
    with _session() as session:
        result = module.heal("reconcile_runs", session)

    assert published == ["reconcile"]
    assert result["ok"] is True


def test_building_the_runner_image_is_delegated_to_the_worker(isolated, monkeypatch):
    """Same rule as reconcile_runs: only the worker holds the socket, and only
    the worker can build (ADR-0002)."""
    published: list[str] = []
    monkeypatch.setattr(
        "harness_lab_app.celery_app.enqueue_build_runner_image",
        lambda: published.append("build"),
    )
    monkeypatch.setattr(module, "worker_facts", lambda **_kwargs: {})
    with _session() as session:
        result = module.heal("build_runner_image", session)

    assert published == ["build"]
    assert result["ok"] is True
    assert "minutes" in result["detail"]


def test_the_docker_row_carries_its_own_heal_button(isolated, monkeypatch):
    monkeypatch.setattr(module, "worker_facts", lambda **_kwargs: {})
    with _session() as session:
        report = module.collect_health(session)

    docker_row = next(row for row in report["checks"] if row["id"] == "docker")
    assert docker_row["heal"] == "build_runner_image"


def test_worker_facts_are_cached_between_callers(monkeypatch):
    """A 3 s broadcast per run preflight would make batch creation crawl."""
    calls: list[int] = []
    monkeypatch.setattr(module, "_facts_cache", None, raising=False)
    monkeypatch.setattr(
        module, "_worker_facts", lambda: (calls.append(1), {"backend": "docker"})[1]
    )

    first = module.worker_facts(refresh=True)
    second = module.worker_facts()

    assert first == second
    assert len(calls) == 1


def test_materialize_rebuilds_the_staging_tree(isolated, monkeypatch):
    calls: list[str] = []

    class _Service:
        def __init__(self, _session) -> None:
            pass

        def reconcile_all(self) -> dict:
            calls.append("reconcile_all")
            return {"rebuilt": ["probe"], "orphans": []}

    monkeypatch.setattr(
        "harness_lab_app.staged_skill_service.StagedSkillService", _Service
    )
    with _session() as session:
        result = module.heal("materialize_staged_skills", session)

    assert calls == ["reconcile_all"]
    assert result["rebuilt"] == ["probe"]


def test_the_allowlist_is_the_documented_set():
    assert set(module.HEAL_ACTIONS) == {
        "skills_sync",
        "materialize_staged_skills",
        "refresh_models",
        "reconcile_runs",
        "build_runner_image",
    }


# -- HTTP surface ------------------------------------------------------------


def test_health_detail_is_not_public():
    """`/api/health` stays public for the compose healthcheck; the detail
    endpoint reports gateway config and repo state, so it must not be."""
    from harness_lab_app.session_middleware import PUBLIC_PATHS

    assert "/api/health" in PUBLIC_PATHS
    assert "/api/health/detail" not in PUBLIC_PATHS


def test_heal_endpoint_maps_an_unknown_action_to_422(isolated):
    app = create_app()
    endpoint = _route_endpoint(app, "/api/health/heal")
    with _session() as session, pytest.raises(HTTPException) as exc:
        endpoint(HealRequest(action="shutdown"), session)
    assert exc.value.status_code == 422


def test_heal_endpoint_maps_a_failed_repair_to_502(isolated, monkeypatch):
    def _explode(_action, _session):
        raise RuntimeError("git is missing")

    monkeypatch.setattr(module, "heal", _explode)
    app = create_app()
    endpoint = _route_endpoint(app, "/api/health/heal")
    with _session() as session, pytest.raises(HTTPException) as exc:
        endpoint(HealRequest(action="skills_sync"), session)
    assert exc.value.status_code == 502
    assert "git is missing" in exc.value.detail


def _image_with_cli(version):
    return {
        "backend": "docker",
        "docker_ok": True,
        "runner_image_present": True,
        "runner_image": "harness-lab-runner:latest",
        "docker_version": "29.0",
        "claude_cli_version": version,
    }


def test_a_runner_cli_too_old_for_current_claude_models_is_a_warning():
    """K-006/007: the image was 'present', so Health read green while every
    claude-opus-5-5 run was refused for an old CLI."""
    status, detail, _ = module._check_docker(_image_with_cli("2.1.197"))
    assert status == module.WARN
    assert "2.1.197" in detail and "2.1.280" in detail
    assert "Build runner image" in detail


def test_the_runner_cli_version_is_reported_when_it_is_current():
    status, detail, _ = module._check_docker(_image_with_cli("2.1.309"))
    assert status == module.OK
    assert "Claude Code 2.1.309" in detail
