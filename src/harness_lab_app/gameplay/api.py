"""Fixed racing routes covered by the application's session middleware."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from harness_lab_app.db import get_session
from harness_lab_app.gameplay.racing_service import RacingService, model_url
from harness_lab_app.lmstudio_direct import list_lmstudio_models

racing_router = APIRouter(prefix="/api/gameplay/racing")


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


SessionDep = Annotated[Session, Depends(get_session)]


@racing_router.get("/models")
def models():
    try:
        return [m.as_dict() for m in list_lmstudio_models(model_url(), timeout=3)]
    except Exception as exc:
        raise HTTPException(
            503, "LM Studio is unavailable; configure its address in Settings."
        ) from exc


@racing_router.get("/episodes")
def episodes(session: SessionDep):
    return RacingService(session).list_episodes()


@racing_router.post("/episodes", status_code=201)
def launch(data: ModelRace, session: SessionDep):
    try:
        return RacingService(session).launch_model(data.seed, data.model)
    except Exception as exc:
        raise HTTPException(
            422, "Choose an installed, already loaded LM Studio model."
        ) from exc


@racing_router.post("/human", status_code=201)
def human(data: HumanRace, session: SessionDep):
    return RacingService(session).save_human(
        data.seed, [a.model_dump() for a in data.actions]
    )


@racing_router.get("/episodes/{episode_id}/replay")
def replay(episode_id: str, session: SessionDep):
    try:
        return RacingService(session).replay(episode_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@racing_router.post("/episodes/{episode_id}/cancel")
def cancel(episode_id: str, session: SessionDep):
    try:
        return RacingService(session).cancel(episode_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
