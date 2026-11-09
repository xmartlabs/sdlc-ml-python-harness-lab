"""In-process background runner for *Tune your local model*'s four tests.

Not Celery: the lab's single ``runs`` queue may be busy for hours with an
unrelated experiment, and these tests must still run in a few minutes
(spec "Tests"). A module-level registry backs the status polling endpoint; a
daemon thread (or, in tests, a synchronous injected executor) runs the tests
sequentially so the page's progress line advances one test at a time.
"""

from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING

from harness_lab_app.local_tuning.contracts import (
    LoadConfig,
    MachineSpec,
    ModelInfo,
    ServedConfig,
    TestSession,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_lab_app.local_tuning.contracts import LocalServer

# Keep the registry from growing unbounded across a long-lived API process:
# only the most recent finished sessions stay around for polling.
_MAX_FINISHED_SESSIONS = 50

_lock = threading.Lock()
_sessions: dict[str, TestSession] = {}
# Which host currently has a running session, so a second start on the same
# host is refused rather than racing the first (spec "Tests": "only one test
# session runs per host at a time"). Keyed by host_guard's normalized host,
# not the raw base_url: "http://localhost:1234" and "http://127.0.0.1:1234"
# are the same server and must not be allowed to run two sessions at once.
_running_hosts: dict[str, str] = {}

_ALL_TESTS = ("fit", "speed", "window", "agent")


def _normalized_host(base_url: str) -> str:
    from harness_lab_app.local_tuning.host_guard import _normalize_host

    return _normalize_host(base_url) or base_url


class SessionBusy(RuntimeError):
    """A test session is already running on this host."""

    def __init__(self, existing: TestSession) -> None:
        super().__init__(
            f"A test session is already running on this host ({existing.server_url})."
        )
        self.existing = existing


def _run_one(
    name: str,
    server: LocalServer,
    model: ModelInfo,
    config: LoadConfig,
    machine: MachineSpec,
    served: ServedConfig | None,
):
    from harness_lab_app.local_tuning import probes

    if name == "fit":
        return probes.run_fit(model, config, machine, served)
    if name == "speed":
        return probes.run_speed(
            server,
            model.id,
            config,
            served,
            model=model,
            bandwidth_gbs=machine.bandwidth_gbs,
        )
    if name == "window":
        return probes.run_window(server, model.id, config, served)
    if name == "agent":
        return probes.run_agent(server, model.id, model, config, served)
    raise ValueError(f"unknown test name: {name}")


def _execute(
    session_id: str,
    server: LocalServer,
    model: ModelInfo,
    config: LoadConfig,
    machine: MachineSpec,
    served: ServedConfig | None,
    tests: tuple[str, ...],
    on_done: Callable[[TestSession], int | None] | None = None,
) -> None:
    try:
        for name in tests:
            with _lock:
                row = _sessions.get(session_id)
                if row is None:
                    return
                row.current = name
            result = _run_one(name, server, model, config, machine, served)
            with _lock:
                row = _sessions.get(session_id)
                if row is None:
                    return
                row.results.append(result)
        with _lock:
            row = _sessions.get(session_id)
            if row is not None:
                row.current = ""
                row.status = "done"
    except Exception as exc:
        with _lock:
            row = _sessions.get(session_id)
            if row is not None:
                row.status = "failed"
                row.error = str(exc)
    finally:
        with _lock:
            _running_hosts.pop(_normalized_host(server.base_url), None)
            row = _sessions.get(session_id)
            snapshot = row.model_copy(deep=True) if row is not None else None
        # Persist outside the lock: a slow DB write must not stall pollers.
        if on_done is not None and snapshot is not None and snapshot.results:
            try:
                profile_id = on_done(snapshot)
            except Exception:  # history is best-effort; the session still reads
                profile_id = None
            with _lock:
                row = _sessions.get(session_id)
                if row is not None:
                    row.profile_id = profile_id
        with _lock:
            _prune_finished()


def _prune_finished() -> None:
    finished = [
        sid for sid, row in _sessions.items() if row.status in ("done", "failed")
    ]
    if len(finished) <= _MAX_FINISHED_SESSIONS:
        return
    # Oldest-created finished sessions go first; ids are time-ordered uuid4
    # insertion order in the dict, which is good enough for a soft cap.
    excess = len(finished) - _MAX_FINISHED_SESSIONS
    for sid in finished[:excess]:
        _sessions.pop(sid, None)


def start_session(
    server: LocalServer,
    model: ModelInfo,
    config: LoadConfig,
    machine: MachineSpec,
    tests: tuple[str, ...] = _ALL_TESTS,
    *,
    served: ServedConfig | None = None,
    executor: Callable[..., None] | None = None,
    on_done: Callable[[TestSession], int | None] | None = None,
) -> TestSession:
    """Start (or refuse) a test session for ``model`` on ``server``.

    ``executor`` lets tests run the session synchronously instead of in a
    daemon thread; production code leaves it unset.
    """
    host = _normalized_host(server.base_url)
    with _lock:
        existing_id = _running_hosts.get(host)
        if existing_id is not None:
            existing = _sessions.get(existing_id)
            if existing is not None and existing.status == "running":
                raise SessionBusy(existing)

        session_id = uuid.uuid4().hex
        row = TestSession(
            id=session_id,
            server_url=server.base_url,
            model_id=model.id,
            status="running",
        )
        _sessions[session_id] = row
        _running_hosts[host] = session_id

    runner = executor or (
        lambda fn, *args: threading.Thread(target=fn, args=args, daemon=True).start()
    )
    runner(
        _execute,
        session_id,
        server,
        model,
        config,
        machine,
        served,
        tuple(tests),
        on_done,
    )
    return row


def get_session(session_id: str) -> TestSession | None:
    with _lock:
        return _sessions.get(session_id)
