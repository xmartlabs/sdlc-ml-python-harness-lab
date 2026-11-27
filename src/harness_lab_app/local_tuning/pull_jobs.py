"""In-process background downloads for *Get more models* (ADR-0101).

Same shape as :mod:`harness_lab_app.local_tuning.test_sessions`: a module
registry backs a polling endpoint, and a daemon thread (or, in tests, a
synchronous injected executor) drives the server's own download. One download
runs per host at a time: two multi-gigabyte pulls on one small box only slow
each other down and fill its disk twice as fast.

The registry lives in the API process, so a restart forgets the job; the
server keeps downloading, and the model shows up in the next listing.
"""

from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING

from harness_lab_app.direct_hosts import DirectHostUnreachable
from harness_lab_app.local_tuning.contracts import (
    LoadCheck,
    PullJob,
    PullProgress,
    ServerError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_lab_app.local_tuning.contracts import LocalServer

_MAX_FINISHED_JOBS = 50

_lock = threading.Lock()
_jobs: dict[str, PullJob] = {}
_running_hosts: dict[str, str] = {}


def _normalized_host(base_url: str) -> str:
    from harness_lab_app.local_tuning.host_guard import _normalize_host

    return _normalize_host(base_url) or base_url


class PullBusy(RuntimeError):
    """A download is already running on this host."""

    def __init__(self, existing: PullJob) -> None:
        super().__init__(
            f"{existing.model} is still downloading on this host; wait for it to "
            "finish before starting another."
        )
        self.existing = existing


def _apply(job_id: str, progress: PullProgress) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = progress.status
        if progress.total_bytes is not None:
            job.total_bytes = progress.total_bytes
        if progress.completed_bytes is not None:
            job.completed_bytes = progress.completed_bytes
        job.detail = progress.detail
        job.error = progress.error


def _execute(job_id: str, host: str, server: LocalServer) -> None:
    job = get_job(job_id)
    if job is None:  # pragma: no cover - registered just before the thread starts
        return
    try:
        final = server.pull(
            job.model,
            job.quantization,
            on_progress=lambda progress: _apply(job_id, progress),
        )
        if final.status == "completed":
            final = _check_load(job_id, server, job.model, final)
        if final.status == "downloading":
            # pull() returned without an outcome: never leave the host locked
            # and the page polling a job nothing will update again.
            final = final.model_copy(
                update={
                    "status": "failed",
                    "error": "The server stopped reporting before the download ended.",
                }
            )
        _apply(job_id, final)
    except DirectHostUnreachable as exc:
        _apply(
            job_id,
            PullProgress(status="failed", error=f"The server stopped answering: {exc}"),
        )
    except ServerError as exc:
        _apply(job_id, PullProgress(status="failed", error=str(exc)))
    except Exception as exc:  # a thread records its failure instead of dying
        _apply(job_id, PullProgress(status="failed", error=f"Download failed: {exc}"))
    finally:
        with _lock:
            if _running_hosts.get(host) == job_id:
                del _running_hosts[host]
            _prune_finished()


def _check_load(
    job_id: str, server: LocalServer, model: str, final: PullProgress
) -> PullProgress:
    """Load the new model once, so a file the server cannot run fails here.

    The job stays "downloading" (with a detail line) while the check runs,
    so the page keeps polling. A failed check does not fail the download:
    the file is on the server, it just will not load there.
    """
    _apply(
        job_id,
        final.model_copy(
            update={"status": "downloading", "detail": "checking that it loads"}
        ),
    )
    try:
        check = server.check_load(model)
    except DirectHostUnreachable as exc:
        check = LoadCheck(status="failed", error=f"The server stopped answering: {exc}")
    except Exception as exc:  # the download itself succeeded; keep it
        check = LoadCheck(status="failed", error=str(exc))
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.load_check = check.status
            job.load_error = check.error
    return final


def _prune_finished() -> None:
    finished = [jid for jid, job in _jobs.items() if job.status != "downloading"]
    for jid in finished[: max(0, len(finished) - _MAX_FINISHED_JOBS)]:
        _jobs.pop(jid, None)


def start_pull(
    server: LocalServer,
    model: str,
    quantization: str = "",
    *,
    executor: Callable[..., None] | None = None,
) -> PullJob:
    """Start (or refuse with :class:`PullBusy`) a download of ``model``."""
    host = _normalized_host(server.base_url)
    with _lock:
        existing_id = _running_hosts.get(host)
        existing = _jobs.get(existing_id) if existing_id else None
        if existing is not None and existing.status == "downloading":
            raise PullBusy(existing.model_copy())
        job = PullJob(
            id=uuid.uuid4().hex,
            server_kind=server.kind,
            server_url=server.base_url,
            model=model,
            quantization=quantization,
        )
        _jobs[job.id] = job
        _running_hosts[host] = job.id
        snapshot = job.model_copy()

    runner = executor or (
        lambda fn, *args: threading.Thread(target=fn, args=args, daemon=True).start()
    )
    try:
        runner(_execute, snapshot.id, host, server)
    except RuntimeError as exc:  # e.g. "can't start new thread"
        _apply(snapshot.id, PullProgress(status="failed", error=str(exc)))
        with _lock:
            if _running_hosts.get(host) == snapshot.id:
                del _running_hosts[host]
    return get_job(snapshot.id) or snapshot


def get_job(job_id: str) -> PullJob | None:
    with _lock:
        job = _jobs.get(job_id)
        return job.model_copy() if job is not None else None
