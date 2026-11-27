"""Shared types for *Tune your local model* (spec 2026-11-08-local-model-tuning).

Every unit in this package speaks these types, so the domain estimate, the
server adapters, the probes and the API can be built and tested apart. Pure
data: no I/O, no framework imports beyond pydantic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, Field, computed_field

if TYPE_CHECKING:
    from collections.abc import Callable

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
    # Gemma 4 gives its sliding-window layers fewer, narrower KV heads than its
    # full-attention ones; None = the same as ``kv_heads`` / ``head_dim``.
    swa_kv_heads: int | None = None
    swa_head_dim: int | None = None


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


PullStatus = Literal["downloading", "completed", "failed"]
# After an Ollama download: did the model load? "" = not checked (LM Studio).
LoadCheckStatus = Literal["", "ok", "failed", "skipped"]


class PullProgress(BaseModel):
    """One reading of a download's progress, as the server reports it."""

    status: PullStatus
    total_bytes: int | None = None
    completed_bytes: int | None = None
    detail: str = ""  # the server's own phase line, e.g. "verifying sha256 digest"
    error: str = ""


class PullJob(BaseModel):
    """A download the lab asked a serving host to run (ADR-0101)."""

    id: str
    server_kind: ServerKind
    server_url: str
    model: str  # what was asked for: an Ollama tag or a Hugging Face repo
    quantization: str = ""
    status: PullStatus = "downloading"
    total_bytes: int | None = None
    completed_bytes: int | None = None
    detail: str = ""
    error: str = ""
    # A completed download is loaded once, briefly, when nothing else is
    # loaded: a file the server cannot run must fail here, not in step 4.
    load_check: LoadCheckStatus = ""
    load_error: str = ""


class LoadCheck(BaseModel):
    """Whether a freshly downloaded model loads on the server."""

    status: LoadCheckStatus
    error: str = ""


class HubQuant(BaseModel):
    """One downloadable quantization of a model, and what to ask each server."""

    name: str  # e.g. "Q4_K_M"
    size_bytes: int | None = None
    # What ``LocalServer.pull`` is given for this file on each backend.
    ollama_model: str = ""  # library tag, or "hf.co/{repo}:{quant}"
    lmstudio_model: str = ""  # the Hugging Face link; quantization sent beside it
    # Memory estimate at the lab's default load config on the chosen machine.
    verdict: Verdict | None = None
    total_gb: float | None = None
    # Largest context option that still fits (ok or tight); None when even
    # the smallest does not. Lets a small box see "fits up to 16k" instead
    # of a flat "too big" at the 32k default.
    fit_context: int | None = None
    # One of the quantizations most people pick (Q4_K_M, Q5_K_M, Q8_0...).
    # The page lists these first; 1-2 bit and full-precision files come after.
    common: bool = False
    # Oldest Ollama that loads what ``ollama_model`` pulls; "" = no known
    # floor. A library tag carries its own (gemma4 needs 0.30.9); a Hugging
    # Face file from a repo with a separate vision file needs 0.30.
    min_ollama_version: str = ""


class HubModel(BaseModel):
    """A model the lab can ask a server to download, not yet on that server."""

    source: Literal["curated", "huggingface"]
    repo: str  # Hugging Face repo, e.g. "Qwen/Qwen3-8B-GGUF"
    label: str = ""
    params: str = ""  # e.g. "8B", "30B-A3B"
    arch: str = ""
    supports_tools: bool | None = None
    downloads: int | None = None
    max_context: int | None = None
    quants: list[HubQuant] = Field(default_factory=list)
    note: str = ""
    # The repo ships its vision weights as a separate mmproj file. Ollama
    # pulls it too, and before 0.30 cannot load such a split model for recent
    # architectures (Gemma 4, Qwen 3.5): the download works, the load fails.
    separate_projector: bool = False


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

    def pull(
        self,
        model: str,
        quantization: str = "",
        *,
        on_progress: Callable[[PullProgress], None],
    ) -> PullProgress:
        """Download ``model`` onto the server, reporting progress until it ends.

        Blocks until the download completes or fails; returns the last reading.
        """
        ...

    def version(self) -> str:
        """The server's version ("0.24.0"), or "" when it does not say."""
        ...

    def check_load(self, model_id: str) -> LoadCheck:
        """Load ``model_id`` briefly to prove it runs, then unload it.

        Skipped (never evicts) when another model is loaded.
        """
        ...


ModelInfo.model_rebuild()
