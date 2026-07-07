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
