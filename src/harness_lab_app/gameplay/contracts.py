"""Versioned wire contracts and the two boundaries an episode needs."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    version: Literal["gameplay.v1"] = "gameplay.v1"


class EpisodeConfig(WireModel):
    episode_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    seed: int = Field(ge=0, le=2**32 - 1)
    tick_rate: int = Field(default=10, ge=1, le=240)
    ticks_per_action: int = Field(default=2, ge=1, le=240)
    max_ticks: int = Field(default=20, ge=1, le=100_000)
    decision_timeout_seconds: float = Field(default=30.0, gt=0, le=300)


class ControlBounds(WireModel):
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def ordered(self) -> ControlBounds:
        if self.minimum > self.maximum:
            raise ValueError("Control minimum exceeds maximum")
        return self


class Observation(WireModel):
    episode_id: str
    observation_id: str
    tick: int = Field(ge=0)
    ticks_to_advance: int = Field(ge=0)
    tick_rate: int = Field(ge=1)
    instructions: str = Field(min_length=1, max_length=4096)
    state: dict[str, JsonValue]
    controls: dict[str, ControlBounds]
    terminal: bool


class Action(WireModel):
    observation_id: str = Field(min_length=1)
    controls: dict[str, float]

    def validate_for(self, observation: Observation) -> None:
        if self.observation_id != observation.observation_id:
            raise ValueError("Stale or foreign observation")
        if set(self.controls) != set(observation.controls):
            raise ValueError("Supply exactly the advertised controls")
        for name, value in self.controls.items():
            bounds = observation.controls[name]
            if not bounds.minimum <= value <= bounds.maximum:
                raise ValueError("Control outside advertised bounds")


class PolicyReply(WireModel):
    action_json: str = Field(max_length=16_384)
    # Provider facts, not a claim that the requested model/settings were effective.
    provenance: dict[str, JsonValue] = Field(default_factory=dict)


DecisionOutcome = Literal["applied", "invalid_action", "timeout", "provider_error"]


class Frame(WireModel):
    action: Action | None
    observation: Observation
    ticks_advanced: int = Field(ge=0)
    events: list[str]
    latency_seconds: float = Field(ge=0)
    outcome: DecisionOutcome
    response_text: str | None = Field(default=None, max_length=16_384)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)


class EpisodeResult(WireModel):
    status: Literal[
        "completed", "budget_exhausted", "invalid_action", "timeout", "provider_error"
    ]
    simulation_ticks: int = Field(ge=0)
    simulation_seconds: float = Field(ge=0)
    inference_seconds: float = Field(ge=0)
    latency_p50_seconds: float = Field(ge=0)
    latency_p95_seconds: float = Field(ge=0)
    metrics: dict[str, float]


class ReplayHeader(WireModel):
    environment_id: str
    environment_version: str
    config: EpisodeConfig
    initial: Observation
    environment_provenance: dict[str, JsonValue] = Field(default_factory=dict)
    policy_provenance: dict[str, JsonValue] = Field(default_factory=dict)


class Replay(ReplayHeader):
    frames: list[Frame]
    result: EpisodeResult


class Environment(Protocol):
    """Authoritative simulation: step advances exactly ticks, never wall time.

    State contains only player-visible JSON. Controls are held for the full
    interval; a terminal environment must stay frozen for the remaining ticks.
    """

    environment_id: str
    environment_version: str
    instructions: str

    def reset(self, seed: int) -> None: ...
    def state(self) -> dict[str, JsonValue]: ...
    def controls(self) -> dict[str, ControlBounds]: ...
    def step(self, controls: dict[str, float], ticks: int) -> list[str]: ...
    def terminal(self) -> bool: ...
    def metrics(self) -> dict[str, float]: ...


class Policy(Protocol):
    """Return one JSON action; adapters must enforce the supplied I/O timeout."""

    def __call__(self, observation: Observation, timeout: float) -> PolicyReply: ...
