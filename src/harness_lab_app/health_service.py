"""System health and drift for the web backoffice (ADR-0018).

One row per check, each wrapped so a single failing probe never takes the panel
down — an operator looking at a broken lab needs *more* information, not a 500.

Two rules shape this module:

- **The API never touches the docker socket.** Docker facts come from the
  worker through a Celery control command (ADR-0002), so the api container can
  answer "is the runtime usable?" without ever being able to launch anything.
- **Healing is an allowlist, not a command runner.** ``heal()`` dispatches a
  fixed set of named actions; anything else is refused before any work happens.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from harness_lab_app.celery_app import celery_app
from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.models import Run, RunState

if TYPE_CHECKING:
    from datetime import datetime

# Statuses, worst-last: the panel sorts by this to put problems on top.
OK = "ok"
WARN = "warn"
ERROR = "error"
UNKNOWN = "unknown"

_SEVERITY = {OK: 0, UNKNOWN: 1, WARN: 2, ERROR: 3}

# Every heal action the API will dispatch. Adding a key here is a deliberate
# decision to let the browser trigger that work; there is no generic escape.
HEAL_ACTIONS = (
    "skills_sync",
    "materialize_staged_skills",
    "refresh_models",
    "reconcile_runs",
    "build_runner_image",
)

# A run that has been `running` longer than this without ending is almost
# certainly orphaned (worker killed, container gone). Not fatal — a very long
# benchmark is legitimate — so it is a warning with a heal button.
_ORPHAN_RUN_SECONDS = 6 * 60 * 60

_WORKER_REPLY_TIMEOUT = 3.0

# Worker facts cost a 3 s broadcast, and both the health panel and every run
# preflight want them. Docker state does not change on a shorter horizon than
# this, so serve repeat callers from the cache.
_FACTS_TTL_SECONDS = 30.0
_facts_cache: tuple[float, dict[str, Any]] | None = None


class HealthError(ValueError):
    """Raised when a heal action is not in the allowlist."""


@dataclass
class Check:
    id: str
    label: str
    status: str
    detail: str = ""
    latency_ms: int | None = None
    heal: str | None = None
    items: list[str] = field(default_factory=list)


def _timed(check_id: str, label: str, probe, heal: str | None = None) -> Check:
    """Run one probe, converting any failure into a reportable row.

    A probe returns ``(status, detail, items)``; raising is treated as
    ``unknown`` rather than propagating, because the panel's whole job is to
    keep reporting when parts of the lab are down.
    """
    started = time.monotonic()
    try:
        status, detail, items = probe()
    except Exception as exc:  # a failed probe is a datapoint, not an outage
        status, detail, items = UNKNOWN, f"{type(exc).__name__}: {exc}", []
    latency = int((time.monotonic() - started) * 1000)
    return Check(
        id=check_id,
        label=label,
        status=status,
        detail=detail,
        latency_ms=latency,
        heal=heal if status != OK else None,
        items=items,
    )


# -- individual probes -------------------------------------------------------


def _check_gateway() -> tuple[str, str, list[str]]:
    from harness_lab_app.executors import rewrite_localhost
    from harness_lab_app.gateway_models import list_gateway_models

    base = read_env_file_value("GATEWAY_BASE_URL")
    token = read_env_file_value("GATEWAY_TOKEN")
    if not base or not token:
        return WARN, "GATEWAY_BASE_URL / GATEWAY_TOKEN are not configured.", []
    if Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    models = list_gateway_models(base_url=base, token=token)
    if not models:
        return ERROR, "The gateway returned no models (unreachable or bad token).", []
    return OK, f"{len(models)} models served.", []


def _check_redis() -> tuple[str, str, list[str]]:
    connection = celery_app.connection()
    try:
        connection.ensure_connection(max_retries=0, timeout=2)
    finally:
        with contextlib.suppress(Exception):
            connection.release()
    return OK, "Broker reachable.", []


def _worker_facts() -> dict[str, Any]:
    """Ask the worker what it can see. Empty dict when no worker replies."""
    replies = celery_app.control.broadcast(
        "harness_facts", reply=True, timeout=_WORKER_REPLY_TIMEOUT
    )
    for reply in replies or []:
        for facts in reply.values():
            if isinstance(facts, dict):
                return facts
    return {}


def worker_facts(*, refresh: bool = False) -> dict[str, Any]:
    """Cached :func:`_worker_facts`, for callers outside the health panel.

    ``refresh=True`` bypasses the cache — use it right after a heal action that
    is supposed to have changed what the worker sees.
    """
    global _facts_cache
    now = time.monotonic()
    if not refresh and _facts_cache is not None:
        cached_at, facts = _facts_cache
        if now - cached_at < _FACTS_TTL_SECONDS:
            return facts
    try:
        facts = _worker_facts()
    except Exception:
        facts = {}
    _facts_cache = (now, facts)
    return facts


def _check_worker(facts: dict[str, Any]) -> tuple[str, str, list[str]]:
    if not facts:
        return ERROR, "No worker replied — runs will stay pending.", []
    return OK, f"Worker {facts.get('hostname', '?')} responding.", []


def _check_docker(facts: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Docker health as *the worker* sees it; the API has no socket (ADR-0002)."""
    if not facts:
        return UNKNOWN, "No worker replied, so docker could not be inspected.", []
    if facts.get("backend") != "docker":
        return OK, f"Execution backend is {facts.get('backend')!r}; docker unused.", []
    if not facts.get("docker_ok"):
        return ERROR, facts.get("docker_error") or "Docker socket unusable.", []
    if not facts.get("runner_image_present"):
        return (
            WARN,
            f"Runner image {facts.get('runner_image')!r} is missing — "
            "runs cannot start. Use Build runner image to build it here.",
            [],
        )
    return OK, f"Docker {facts.get('docker_version', '?')}, runner image present.", []


def _check_skills_drift() -> tuple[str, str, list[str]]:
    from ml_python_base.skills_sync.cli import check_drift
    from ml_python_base.skills_sync.config import load_registry

    root = get_settings().repo_root
    stale = check_drift(root, load_registry(root / "adapters" / "registry.toml"))
    if stale:
        return WARN, f"{len(stale)} generated artifact(s) are stale.", stale
    return OK, "Adapters, native views and agents are in sync.", []


def _check_git_tree() -> tuple[str, str, list[str]]:
    """A dirty harness tree after a run means an ablation did not restore."""
    from harness_lab_app.provenance import _harness_git_dirty

    dirty = _harness_git_dirty(get_settings().repo_root)
    if dirty is None:
        return UNKNOWN, "Not a git checkout, or git is unavailable.", []
    if dirty:
        return (
            WARN,
            "Governed skill/adapter files are modified — a run may have died "
            "mid-ablation without restoring them.",
            [],
        )
    return OK, "Governed files are unmodified.", []


def _check_staging(session: Session) -> tuple[str, str, list[str]]:
    from harness_lab_app.staged_skill_service import StagedSkillService

    service = StagedSkillService(session)
    problems = service.staging_drift() + [
        f"data/skills_staging/{name} (orphan)" for name in service.staging_orphans()
    ]
    if problems:
        return (
            WARN,
            f"{len(problems)} staged-skill path(s) disagree with the database.",
            problems,
        )
    return OK, "Staging tree matches the database.", []


def _check_overlay(session: Session) -> tuple[str, str, list[str]]:
    """Is the materialized opencode overlay current with the model catalog?

    The file is what ``bench_run`` reads at launch; the DB is what the Settings
    page edits. When they disagree, a model you enabled in the browser is not
    actually routable yet.
    """
    from harness_lab_app.gateway_catalog_service import GatewayCatalogService
    from harness_lab_app.opencode_overlay import CATALOG_FILE, read_catalog_models

    root = get_settings().repo_root
    wanted = GatewayCatalogService(session).overlay_models()
    if not wanted:
        return OK, "No models enabled beyond the tracked opencode.json.", []
    if not (root / CATALOG_FILE).is_file():
        return WARN, f"{CATALOG_FILE} has not been written yet.", []
    if read_catalog_models(root) != wanted:
        return WARN, f"{CATALOG_FILE} is stale relative to the model catalog.", []
    return OK, f"{len(wanted)} model(s) routed through the overlay.", []


def _naive_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes while ``utc_now()`` is aware.

    Both are UTC, so dropping the tzinfo makes them comparable without
    pretending a stored value carries an offset it never had.
    """
    return value.replace(tzinfo=None)


def _check_orphan_runs(session: Session) -> tuple[str, str, list[str]]:
    from harness_lab_app.models import utc_now

    now = _naive_utc(utc_now())
    stuck: list[str] = []
    statement = select(Run).where(Run.state.in_([RunState.running, RunState.pending]))  # type: ignore[attr-defined]
    for run in session.exec(statement):
        reference = run.started_at or run.created_at
        if (
            reference
            and (now - _naive_utc(reference)).total_seconds() > _ORPHAN_RUN_SECONDS
        ):
            stuck.append(f"{run.run_id} ({run.state})")
    if stuck:
        return WARN, f"{len(stuck)} run(s) have been active for over 6h.", stuck
    return OK, "No stuck runs.", []


def _check_langfuse() -> tuple[str, str, list[str]]:
    """Tracing reachability. Off is a choice, not a problem: it reports `ok`.

    The only failure worth a warning is a lab that *asked* for tracing and is
    not getting it — bad keys, or a host that does not answer.
    """
    from harness_lab_app.observability.client import check_connection
    from harness_lab_app.observability.config import LangfuseConfig

    config = LangfuseConfig.from_env()
    if not config.enabled:
        if config.requested:
            return WARN, config.disabled_reason, []
        return OK, "Tracing is off; run traces stay in data/runs/.", []
    result = check_connection(config)
    if result.ok:
        mode = "auto-export on" if config.auto_export else "manual export only"
        return OK, f"{result.reason} ({mode}).", []
    return (ERROR if result.status in (401, 403) else WARN), result.reason, []


# -- panel -------------------------------------------------------------------


def collect_health(session: Session) -> dict:
    """Every check, worst first. Never raises."""
    facts = worker_facts(refresh=True)

    checks = [
        _timed("gateway", "AI gateway", _check_gateway),
        _timed("redis", "Redis broker", _check_redis),
        _timed("worker", "Celery worker", lambda: _check_worker(facts)),
        _timed(
            "docker",
            "Docker runtime",
            lambda: _check_docker(facts),
            "build_runner_image",
        ),
        _timed(
            "skills_drift", "Generated skill views", _check_skills_drift, "skills_sync"
        ),
        _timed("git_tree", "Governed git tree", _check_git_tree, "skills_sync"),
        _timed(
            "staging",
            "Staged-skill materialization",
            lambda: _check_staging(session),
            "materialize_staged_skills",
        ),
        _timed(
            "model_overlay",
            "Model routing overlay",
            lambda: _check_overlay(session),
            "refresh_models",
        ),
        _timed("langfuse", "Langfuse tracing", _check_langfuse),
        _timed(
            "orphan_runs",
            "Run queue",
            lambda: _check_orphan_runs(session),
            "reconcile_runs",
        ),
    ]
    checks.sort(key=lambda check: -_SEVERITY[check.status])
    worst = max((_SEVERITY[check.status] for check in checks), default=0)
    overall = next(name for name, value in _SEVERITY.items() if value == worst)
    return {"status": overall, "checks": [asdict(check) for check in checks]}


def heal(action: str, session: Session) -> dict:
    """Run one allowlisted repair. Anything else is refused, not attempted."""
    if action not in HEAL_ACTIONS:
        raise HealthError(f"Unknown heal action: {action!r}")

    if action == "skills_sync":
        from ml_python_base.skills_sync.cli import main as skills_main

        exit_code = skills_main(["sync", "--root", str(get_settings().repo_root)])
        return {"action": action, "ok": exit_code == 0, "detail": "skills_sync ran."}

    if action == "materialize_staged_skills":
        from harness_lab_app.staged_skill_service import StagedSkillService

        report = StagedSkillService(session).reconcile_all()
        return {"action": action, "ok": True, "detail": "Staging rebuilt.", **report}

    if action == "refresh_models":
        from harness_lab_app.gateway_catalog_service import GatewayCatalogService
        from harness_lab_app.gateway_models import list_gateway_models

        base = read_env_file_value("GATEWAY_BASE_URL")
        token = read_env_file_value("GATEWAY_TOKEN")
        if Path("/.dockerenv").exists():
            from harness_lab_app.executors import rewrite_localhost

            base = rewrite_localhost(base, get_settings().docker_host_alias)
        service = GatewayCatalogService(session)
        report = service.refresh(list_gateway_models(base_url=base, token=token))
        service.write_overlay_file()
        return {"action": action, "ok": True, "detail": "Catalog refreshed.", **report}

    if action == "build_runner_image":
        from harness_lab_app.celery_app import enqueue_build_runner_image

        enqueue_build_runner_image()
        # The build takes minutes; the panel re-checks the docker row rather
        # than holding the request open, so drop the stale "image missing".
        worker_facts(refresh=True)
        return {
            "action": action,
            "ok": True,
            "detail": (
                "Runner image build queued on the worker. It takes a few "
                "minutes; re-check this panel when it finishes."
            ),
        }

    # reconcile_runs: only the worker owns the docker socket, so the API asks
    # rather than acts (ADR-0002).
    from harness_lab_app.celery_app import enqueue_reconcile_runs

    enqueue_reconcile_runs()
    return {
        "action": action,
        "ok": True,
        "detail": "Reconciliation queued on the worker.",
    }
