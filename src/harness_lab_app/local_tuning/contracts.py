"""Shared types for *Tune your local model* (spec 2026-11-08-local-model-tuning).

Every unit in this package speaks these types, so the domain estimate, the
server adapters, the probes and the API can be built and tested apart. Pure
data: no I/O, no framework imports beyond pydantic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field, computed_field

ServerKind = Literal["lmstudio", "ollama"]
MemoryKind = Literal["unified", "discrete"]


class Verdict(StrEnum):
    """Traffic light shared by the memory bar and every test."""

    ok = "ok"  # green
    tight = "tight"  # amber
    fail = "fail"  # red
    inconclusive = "inconclusive"  # grey: timed out or could not be measured
    not_applicable = "not_applicable"  # e.g. agent test on a model without tool use


class Confidence(StrEnum):
    exact = "exact"  # architecture numbers reported by the server (Ollama /api/show)
    good = "good"  # architecture found in the catalog table
    rough = "rough"  # derived from the parameter count; shown as a range


class MachineSpec(BaseModel):
    """What the user told us about the machine that serves the model."""

    memory_kind: MemoryKind
    gpu_gb: float = Field(ge=0)  # unified pool on Apple silicon; VRAM on discrete
    ram_gb: float = Field(ge=0)  # system RAM (equal to gpu_gb on unified)
    reserve_gb: float = Field(default=4.0, ge=0)  # left free for the OS and apps
    # Apple silicon caps GPU-wired memory at a fraction of RAM unless raised
    # with `iogpu.wired_limit_mb`. Ignored for discrete GPUs.
    gpu_wired_fraction: float = Field(default=0.75, gt=0, le=1)
    # Memory bandwidth in GB/s: the ceiling on token generation. None = unknown.
    bandwidth_gbs: float | None = Field(default=None, ge=0)


class ArchShape(BaseModel):
    """The numbers the KV-cache size depends on."""

    layers: int  # total transformer blocks
    kv_layers: int  # blocks that keep a full KV cache (hybrid/SWA keep fewer)
    kv_heads: int
    head_dim: int
    # Sliding-window layers keep at most this many tokens; None = full context.
    sliding_window: int | None = None
    swa_layers: int = 0


class ModelInfo(BaseModel):
    """A downloaded model as the server lists it, without loading it."""

    id: str  # bare server id, e.g. "prism-ml/bonsai-27b" or "qwen3:8b"
    display_name: str = ""
    arch: str = ""  # e.g. "qwen35", "llama", "gemma3"
    params: str = ""  # e.g. "27B", "35B-A3B"
    quantization: str = ""
    bits_per_weight: float | None = None
    size_bytes: int | None = None
    max_context: int | None = None
    is_moe: bool = False
    supports_tools: bool | None = None  # None = server did not say
    loaded: bool = False
    instance_ids: list[str] = Field(default_factory=list)
    served: ServedConfig | None = None  # the loaded instance's applied config
    shape: ArchShape | None = None  # only when the server reports it (Ollama)


class LoadConfig(BaseModel):
    """What the user asks the server to load with. None = server default."""

    context_length: int = Field(ge=512)
    gpu_offload: float = Field(default=1.0, ge=0, le=1)  # fraction of layers on GPU
    flash_attention: bool | None = None
    offload_kv_cache_to_gpu: bool | None = None
    eval_batch_size: int | None = Field(default=None, ge=1)
    num_experts: int | None = Field(default=None, ge=1)
    parallel: int = Field(default=1, ge=1)
    kv_cache_bytes: float = 2.0  # f16 = 2; q8_0 ≈ 1.0625; q4_0 ≈ 0.5625


class ServedConfig(BaseModel):
    """What the server says it actually applied. Fields it does not report stay None."""

    context_length: int | None = None
    gpu_offload: float | None = None
    flash_attention: bool | None = None
    offload_kv_cache_to_gpu: bool | None = None
    eval_batch_size: int | None = None
    num_experts: int | None = None
    parallel: int | None = None
    vram_bytes: int | None = None  # Ollama /api/ps size_vram
    # The instance the server actually loaded: LM Studio's load response
    # ``instance_id`` field, or the model key when it echoed none; Ollama's is
    # always the model id. Lets /unload target only the instance this page
    # loaded, never a sibling instance it never touched.
    instance_id: str | None = None


class ConfigDiff(BaseModel):
    field: str
    requested: object | None
    served: object | None


class MemoryBreakdown(BaseModel):
    weights_gb: float
    kv_cache_gb: float
    overhead_gb: float
    total_gb: float
    gpu_gb: float  # part that lands on GPU / unified pool
    ram_gb: float  # part that spills to system RAM (discrete, partial offload)
    available_gb: float  # usable GPU/unified memory after reserve and wired cap
    low_gb: float | None = None  # range, only for Confidence.rough
    high_gb: float | None = None


class MemoryEstimate(BaseModel):
    breakdown: MemoryBreakdown
    verdict: Verdict  # ok / tight (<10% margin) / fail
    confidence: Confidence
    lever: str = ""  # the biggest change that would help, plain English
    notes: list[str] = Field(default_factory=list)


class Completion(BaseModel):
    """One completion with the timing the server reported."""

    text: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    time_to_first_token_s: float | None = None
    tokens_per_second: float | None = None  # generation (written)
    prompt_tokens_per_second: float | None = None  # prefill (read)
    tool_calls: list[dict] = Field(default_factory=list)  # [{"name", "arguments"}]
    error: str = ""


class SpeedPoint(BaseModel):
    prompt_tokens: int
    time_to_first_token_s: float | None
    prompt_tokens_per_second: float | None
    tokens_per_second: float | None


class TestResult(BaseModel):
    __test__ = False  # a result type, not a pytest test class
    name: Literal["fit", "speed", "window", "agent"]
    verdict: Verdict
    reading: str  # plain-language sentence for the UI
    detail: dict = Field(default_factory=dict)


class TestSession(BaseModel):
    __test__ = False  # a session record, not a pytest test class

    id: str
    server_url: str
    model_id: str
    status: Literal["running", "done", "failed"]
    current: str = ""  # name of the test in progress
    results: list[TestResult] = Field(default_factory=list)
    error: str = ""
    # The history row this session was saved as, once it finished.
    profile_id: int | None = None


class ActiveRunRef(BaseModel):
    run_id: str
    state: str
    model: str
    experiment_id: str | None = None


class GuardReport(BaseModel):
    """Lab runs that use the host an action is about to touch."""

    host: str
    running: list[ActiveRunRef] = Field(default_factory=list)
    queued: list[ActiveRunRef] = Field(default_factory=list)
    # True when the gateway routing table could not be fetched, so a
    # gateway/... run may be using this host without this report knowing it
    # (spec "Host guard"): the UI should say gateway runs could not be checked.
    routing_unknown: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def clear(self) -> bool:
        return not self.running and not self.queued


class ServerError(RuntimeError):
    """The server answered with an error; ``message`` is its own words."""


class LocalServer(Protocol):
    """One adapter per serving backend (LM Studio, Ollama)."""

    kind: ServerKind
    base_url: str

    def list_models(self) -> list[ModelInfo]:
        """Downloaded models, with loaded state and served config. Never loads."""
        ...

    def load(self, model_id: str, config: LoadConfig) -> ServedConfig:
        """Load (LM Studio) or warm (Ollama) the model; return what was applied."""
        ...

    def unload(self, model_id: str, instance_id: str | None = None) -> None:
        """Unload ``model_id``. With ``instance_id``, unload only that instance."""
        ...

    def complete(
        self,
        model_id: str,
        prompt: str,
        *,
        max_tokens: int = 128,
        tools: list[dict] | None = None,
        config: LoadConfig | None = None,
        timeout: float = 180.0,
    ) -> Completion:
        """One chat completion. Ollama sends ``config`` as request options."""
        ...


ModelInfo.model_rebuild()
