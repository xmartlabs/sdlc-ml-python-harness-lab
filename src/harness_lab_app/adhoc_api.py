"""HTTP surface for ad hoc tasks and storage (ADR-0095).

A router of its own, like ``local_api``: the feature has its own request models
and would only inflate ``api.py``. Covered by the session middleware like every
other ``/api`` route.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import Session  # noqa: TC002 - FastAPI resolves Depends at runtime

from harness_lab_app.adhoc_service import (
    AdhocConflictError,
    AdhocError,
    AdhocNotFoundError,
    AdhocService,
)
from harness_lab_app.db import get_session
from harness_lab_app.local_sources_service import (
    LocalSourcesDisabled,
    LocalSourcesError,
)
from harness_lab_app.run_service import RunPreflightError
from harness_lab_app.storage_service import StorageError, StorageService

adhoc_router = APIRouter()


class AdhocTaskCreate(BaseModel):
    source: str
    prompt: str
    title: str = ""
    setup_commands: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)


class AdhocTaskUpdate(BaseModel):
    title: str | None = None
    prompt: str | None = None


class AdhocRunLaunch(BaseModel):
    harness: str = "claude"
    executor_model: str | None = None
    executor_effort: str = ""
    max_steps: int = Field(default=1, ge=1, le=5)
    # What the agent sees (ADR-0095): "repo" (the repository alone), or the
    # lab's harness at a profile, "minimal" or "full".
    harness_level: str = "repo"
    # One staged skill to try on top of the level, and whether to tell the
    # agent to use it.
    trial_skill: str = ""
    invoke_trial_skill: bool = False


class AdhocDecision(BaseModel):
    decision: str
    reason: str = ""
    notes: str = ""


class StoragePurge(BaseModel):
    scope: str
    older_than_days: int | None = Field(default=None, ge=0)


def _enqueue_run(run_id: str) -> None:
    from harness_lab_app.celery_app import enqueue_run

    enqueue_run(run_id)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, AdhocNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AdhocConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, LocalSourcesDisabled):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


_EXPECTED = (AdhocError, LocalSourcesError, RunPreflightError, StorageError)


@adhoc_router.get("/api/adhoc/source")
def adhoc_source(path: str = "") -> dict[str, Any]:
    from harness_lab_app.adhoc_service import inspect_source

    try:
        return inspect_source(path)
    except _EXPECTED as exc:
        raise _http(exc) from exc


@adhoc_router.post("/api/adhoc/tasks", status_code=201)
def create_adhoc_task(data: AdhocTaskCreate, session: Session = Depends(get_session)):
    try:
        task = AdhocService(session).create_task(
            data.source,
            data.prompt,
            title=data.title,
            setup_commands=data.setup_commands,
            validation_commands=data.validation_commands,
        )
    except _EXPECTED as exc:
        raise _http(exc) from exc
    return AdhocService._task_dict(task)


@adhoc_router.get("/api/adhoc/tasks")
def list_adhoc_tasks(session: Session = Depends(get_session)):
    return AdhocService(session).list_tasks()


@adhoc_router.get("/api/adhoc/tasks/{task_id}")
def get_adhoc_task(task_id: str, session: Session = Depends(get_session)):
    try:
        return AdhocService(session).task_detail(task_id)
    except _EXPECTED as exc:
        raise _http(exc) from exc


@adhoc_router.patch("/api/adhoc/tasks/{task_id}")
def update_adhoc_task(
    task_id: str, data: AdhocTaskUpdate, session: Session = Depends(get_session)
):
    try:
        task = AdhocService(session).update_task(task_id, data.title, data.prompt)
    except _EXPECTED as exc:
        raise _http(exc) from exc
    return AdhocService._task_dict(task)


@adhoc_router.delete("/api/adhoc/tasks/{task_id}", status_code=204)
def delete_adhoc_task(
    task_id: str, force: bool = False, session: Session = Depends(get_session)
):
    try:
        AdhocService(session).delete_task(task_id, force=force)
    except _EXPECTED as exc:
        raise _http(exc) from exc
    return Response(status_code=204)


@adhoc_router.post("/api/adhoc/tasks/{task_id}/runs", status_code=201)
def launch_adhoc_run(
    task_id: str, data: AdhocRunLaunch, session: Session = Depends(get_session)
):
    try:
        return AdhocService(session, enqueue=_enqueue_run).launch_run(
            task_id,
            harness=data.harness,
            executor_model=data.executor_model,
            executor_effort=data.executor_effort,
            max_steps=data.max_steps,
            harness_level=data.harness_level,
            trial_skill=data.trial_skill,
            invoke_trial_skill=data.invoke_trial_skill,
        )
    except _EXPECTED as exc:
        raise _http(exc) from exc


@adhoc_router.get("/api/adhoc/runs/{run_id}")
def get_adhoc_run(run_id: str, session: Session = Depends(get_session)):
    service = AdhocService(session)
    try:
        service.capture_metrics(run_id)
        return service.run_record(run_id)
    except _EXPECTED as exc:
        raise _http(exc) from exc


@adhoc_router.get("/api/adhoc/runs/{run_id}/patch")
def get_adhoc_patch(run_id: str, session: Session = Depends(get_session)):
    try:
        return AdhocService(session).patch(run_id)
    except _EXPECTED as exc:
        raise _http(exc) from exc


@adhoc_router.get("/api/adhoc/runs/{run_id}/apply-check")
def adhoc_apply_check(run_id: str, session: Session = Depends(get_session)):
    try:
        return AdhocService(session).apply_check(run_id)
    except _EXPECTED as exc:
        raise _http(exc) from exc


def _start_apply(run_id: str, undo: bool, session: Session) -> dict[str, Any]:
    from harness_lab_app.celery_app import enqueue_adhoc_apply

    service = AdhocService(session)
    try:
        service.begin_apply(run_id, undo=undo)
    except _EXPECTED as exc:
        raise _http(exc) from exc
    try:
        enqueue_adhoc_apply(run_id, undo)
    except Exception as exc:
        # Nothing was written: give the folder back and say why.
        service.finish_apply(
            run_id, undo=undo, ok=False, error=f"Queue broker unavailable: {exc!r}"
        )
    return service.run_record(run_id)


@adhoc_router.post("/api/adhoc/runs/{run_id}/apply", status_code=202)
def apply_adhoc_run(run_id: str, session: Session = Depends(get_session)):
    return _start_apply(run_id, undo=False, session=session)


@adhoc_router.post("/api/adhoc/runs/{run_id}/undo", status_code=202)
def undo_adhoc_run(run_id: str, session: Session = Depends(get_session)):
    return _start_apply(run_id, undo=True, session=session)


@adhoc_router.post("/api/adhoc/runs/{run_id}/judge", status_code=202)
def judge_adhoc_run(run_id: str, session: Session = Depends(get_session)):
    from harness_lab_app.celery_app import enqueue_adhoc_judge

    try:
        record = AdhocService(session).request_judge(run_id)
    except _EXPECTED as exc:
        raise _http(exc) from exc
    try:
        enqueue_adhoc_judge(run_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Queue broker unavailable: {exc!r}"
        ) from exc
    return record


@adhoc_router.post("/api/adhoc/runs/{run_id}/decision")
def decide_adhoc_run(
    run_id: str, data: AdhocDecision, session: Session = Depends(get_session)
):
    try:
        return AdhocService(session).decide(
            run_id, data.decision, reason=data.reason, notes=data.notes
        )
    except _EXPECTED as exc:
        raise _http(exc) from exc


@adhoc_router.get("/api/storage")
def storage_report(session: Session = Depends(get_session)):
    return StorageService(session).report()


@adhoc_router.post("/api/storage/purge")
def storage_purge(data: StoragePurge, session: Session = Depends(get_session)):
    try:
        return StorageService(session).purge(data.scope, data.older_than_days)
    except _EXPECTED as exc:
        raise _http(exc) from exc
