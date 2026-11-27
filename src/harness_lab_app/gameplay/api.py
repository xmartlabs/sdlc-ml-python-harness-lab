"""Fixed racing routes covered by the application's session middleware."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from harness_lab_app.db import get_session
from harness_lab_app.gameplay.racing_service import (
    RacingService,
    model_url,
)
from harness_lab_app.lmstudio_direct import list_lmstudio_models


class ModelRace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = Field(default=7, ge=0, le=2**32 - 1)
    model: str = Field(min_length=1, max_length=200)


class Controls(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    throttle: float = Field(ge=0, le=1)
    brake: float = Field(ge=0, le=1)
    steer: float = Field(ge=-1, le=1)
    reset: float = Field(ge=0, le=1)


class HumanRace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = Field(default=7, ge=0, le=2**32 - 1)
    actions: list[Controls] = Field(min_length=1, max_length=600)
    ghost_episode_id: str | None = Field(default=None, pattern=r"^race-[0-9a-f]{20}$")


SessionDep = Annotated[Session, Depends(get_session)]


def game_router(
    prefix: str, service_type: type[RacingService], human_schema: type[BaseModel]
) -> APIRouter:
    """Each game's HTTP routes use the same failures and persistence contract."""
    router = APIRouter(prefix=prefix)

    @router.get("/models")
    def models():
        try:
            return [m.as_dict() for m in list_lmstudio_models(model_url(), timeout=3)]
        except Exception as exc:
            raise HTTPException(
                503, "LM Studio is unavailable; configure its address in Settings."
            ) from exc

    @router.get("/episodes")
    def episodes(session: SessionDep):
        return service_type(session).list_episodes()

    @router.post("/episodes", status_code=201)
    def launch(data: ModelRace, session: SessionDep):
        try:
            return service_type(session).launch_model(data.seed, data.model)
        except Exception as exc:
            raise HTTPException(
                422, "Choose an installed, already loaded LM Studio model."
            ) from exc

    def human(data: HumanRace, session: SessionDep):
        try:
            return service_type(session).save_human(
                data.seed, [a.model_dump() for a in data.actions], data.ghost_episode_id
            )
        except (ValueError, LookupError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, "Game runtime is unavailable") from exc

    # Resolve one concrete body schema per route; retain the union for static typing.
    human.__annotations__["data"] = human_schema
    router.add_api_route("/human", human, methods=["POST"], status_code=201)

    @router.get("/episodes/{episode_id}/replay")
    def replay(episode_id: str, session: SessionDep):
        try:
            return service_type(session).replay(episode_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/episodes/{episode_id}/cancel")
    def cancel(episode_id: str, session: SessionDep):
        try:
            return service_type(session).cancel(episode_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/ghosts/{episode_id}")
    def ghost(episode_id: str, session: SessionDep, seed: int = 7):
        try:
            return service_type(session).ghost_reference(episode_id, seed)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                422, "Ghost incompatible or corrupt: " + str(exc)
            ) from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, "Game runtime is unavailable") from exc

    @router.post("/ghosts/calibration", status_code=201)
    def calibration(session: SessionDep):
        try:
            row = service_type(session).create_calibration()
        except (OSError, RuntimeError) as exc:
            raise HTTPException(503, "Game runtime is unavailable") from exc
        if row.state != "completed":
            raise HTTPException(503, row.failure_reason)
        return row

    return router


racing_router = game_router("/api/gameplay/racing", RacingService, HumanRace)
