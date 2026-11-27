"""Fixed-game execution shares the serial worker lane, without a coding harness."""

from sqlmodel import Session

from harness_lab_app.celery_app import celery_app
from harness_lab_app.db import get_engine
from harness_lab_app.gameplay.racing_service import RacingService
from harness_lab_app.models import GameplayEpisode


@celery_app.task(name="harness_lab_app.gameplay.tasks.execute_race", acks_late=False)
def execute_race(episode_id: str) -> None:
    with Session(get_engine()) as session:
        row = session.get(GameplayEpisode, episode_id)
        if row is None:
            return
        services = {"tidal-ring": RacingService}
        service = services.get(row.environment_id)
        if service is None:
            raise ValueError("Unknown gameplay environment")
        service(session).execute(episode_id)
