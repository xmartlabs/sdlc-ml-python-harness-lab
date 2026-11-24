"""Fixed-game execution shares the serial worker lane, without a coding harness."""

from sqlmodel import Session

from harness_lab_app.celery_app import celery_app
from harness_lab_app.db import get_engine
from harness_lab_app.gameplay.racing_service import RacingService


@celery_app.task(name="harness_lab_app.gameplay.tasks.execute_race", acks_late=False)
def execute_race(episode_id: str) -> None:
    with Session(get_engine()) as session:
        RacingService(session).execute(episode_id)
