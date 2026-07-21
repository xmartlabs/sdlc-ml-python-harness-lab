from __future__ import annotations

from celery import Celery

from harness_lab_app.config import get_settings

# Redis is broker-only: the `run` table is the single source of truth, so no
# result backend is configured (task_ignore_result).
celery_app = Celery("harness_lab", broker=get_settings().redis_url)
celery_app.conf.update(
    task_ignore_result=True,
    # Ack after the task finishes so a worker crash redelivers the run; the
    # task itself is idempotent (it skips runs that are no longer pending).
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_routes={"harness_lab_app.tasks.*": {"queue": "runs"}},
    imports=("harness_lab_app.tasks",),
)


def enqueue_run(run_id: str) -> None:
    """Publish a run execution task; raises if the broker is unreachable."""
    celery_app.send_task(
        "harness_lab_app.tasks.execute_run",
        args=[run_id],
        queue="runs",
        retry=False,
    )


def enqueue_validation(run_id: str) -> None:
    """Publish an objective-validation task (runs in the worker, which owns the
    docker socket); raises if the broker is unreachable."""
    celery_app.send_task(
        "harness_lab_app.tasks.validate_run",
        args=[run_id],
        queue="runs",
        retry=False,
    )


def enqueue_audit(run_id: str, auditor_model: str | None = None) -> None:
    """Publish a qualitative-audit task (LLM call, runs in the worker); raises if
    the broker is unreachable."""
    celery_app.send_task(
        "harness_lab_app.tasks.audit_run",
        args=[run_id, auditor_model],
        queue="runs",
        retry=False,
    )


def enqueue_sandbox_start(run_id: str, kind: str) -> None:
    """Publish a task to launch a validation sandbox (runs in the worker)."""
    celery_app.send_task(
        "harness_lab_app.tasks.start_sandbox",
        args=[run_id, kind],
        queue="runs",
        retry=False,
    )


def enqueue_sandbox_stop(run_id: str, kind: str) -> None:
    """Publish a task to stop + clean up a validation sandbox."""
    celery_app.send_task(
        "harness_lab_app.tasks.stop_sandbox",
        args=[run_id, kind],
        queue="runs",
        retry=False,
    )


def enqueue_container_cleanup(run_id: str) -> None:
    """Publish a container-teardown task after a run is deleted (worker owns the
    docker socket); raises if the broker is unreachable."""
    celery_app.send_task(
        "harness_lab_app.tasks.cleanup_run_container",
        args=[run_id],
        queue="runs",
        retry=False,
    )
