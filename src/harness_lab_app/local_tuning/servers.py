"""Server adapters for *Tune your local model* (LM Studio, Ollama).

One class per serving backend, both satisfying the ``LocalServer`` protocol in
:mod:`harness_lab_app.local_tuning.contracts`. Reuses
:mod:`harness_lab_app.direct_hosts` for the HTTP plumbing and its error
vocabulary, the same way :mod:`harness_lab_app.lmstudio_direct` and
:mod:`harness_lab_app.ollama_direct` do for the run picker.

Facts specific to each backend (see the design spec for the sourcing):

- LM Studio reports a downloaded model's static facts on ``GET
  /api/v1/models`` without loading it, and the applied configuration of each
  loaded instance. ``POST /api/v1/models/load`` with ``echo_load_config: true``
  returns what it actually applied; ``POST /api/v1/models/unload`` takes an
  ``instance_id``. Completions stay on ``/api/v0/chat/completions``, the only
  endpoint that reports ``stats`` (time-to-first-token, tokens/second).
  LM Studio's GPU offload is a load-panel setting, not an API field, so
  :class:`LMStudioServer` never sends one.
- Ollama has no per-model "load" verb: ``/api/generate`` with an empty prompt
  warms a model into memory with the given ``options``, and ``keep_alive: 0``
  unloads it. Flash attention and the KV-cache type are server environment
  settings (``OLLAMA_FLASH_ATTENTION``, ``OLLAMA_KV_CACHE_TYPE``), never per
  request, which is what :func:`unsupported_settings` surfaces.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

from harness_lab_app.direct_hosts import (
    DirectHostUnreachable,
    as_int,
    get_json,
    host_root,
)
from harness_lab_app.local_tuning.catalog import KV_CACHE_TYPES
from harness_lab_app.local_tuning.contracts import (
    ArchShape,
    Completion,
    ConfigDiff,
    LoadConfig,
    LocalServer,
    ModelInfo,
    ServedConfig,
    ServerError,
    ServerKind,
)

# Model types LM Studio lists that can actually serve a completion. Embedding
# models are listed too, and would fail every test this page runs.
_LMSTUDIO_RUNNABLE_TYPES = ("llm", "vlm")

# LM Studio's own load timeout can run into minutes for a large GGUF; Ollama's
# warm-load request waits on the same disk read, hence the shared constant.
#
# Note: in prod, ``deploy/nginx.conf`` sets ``proxy_read_timeout 300s`` on the
# path this request travels, which is shorter than this 600 s client-side
# timeout. A load slower than 300 s is reported to the browser as a dropped
# connection well before this timeout would fire, even though LM Studio or
# Ollama keeps loading in the background. Raise the nginx setting to match
# this constant if large-model loads through the proxy need to survive past
# five minutes; this module does not change nginx's own configuration.
_LOAD_TIMEOUT_SECONDS = 600.0


class _HttpServerError(ServerError):
    """A :class:`ServerError` carrying the HTTP status, for internal routing.

    Only :func:`_http` and :class:`LMStudioServer.list_models` know this
    subclass exists; everything else only ever sees ``ServerError``.
    """

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


_MAX_REFLECTED_ERROR_CHARS = 300


def _truncate(message: str) -> str:
    """Cap server error text reflected back to the client, per the spec's SSRF hardening."""
    if len(message) <= _MAX_REFLECTED_ERROR_CHARS:
        return message
    return message[:_MAX_REFLECTED_ERROR_CHARS] + "..."


def _http(url: str, timeout: float, payload: dict | None = None) -> dict:
    """GET/POST ``url`` as JSON, translating transport failures.

    The single seam every adapter method calls through, so tests can
    monkeypatch one function instead of mocking ``urllib`` call by call.
    ``ValueError`` covers both a malformed JSON response (``json.JSONDecodeError``
    is a ``ValueError`` subclass) and ``urllib`` rejecting a bad URL ("unknown
    url type") before it ever opens a connection.
    """
    try:
        return get_json(url, timeout, payload)
    except urllib.error.HTTPError as exc:
        raise _HttpServerError(_truncate(_http_error_message(exc)), exc.code) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise DirectHostUnreachable(f"{url} did not answer: {exc}") from exc
    except ValueError as exc:
        raise ServerError(
            _truncate(f"{url} answered with an invalid response: {exc}")
        ) from exc


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """The server's own error text, parsed out of a JSON ``{"error": ...}`` body."""
    try:
        body = exc.read()
    except Exception:  # pragma: no cover - defensive, body already consumed
        body = b""
    if body:
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "")
                if message:
                    return message
            elif isinstance(error, str) and error:
                return error
        if isinstance(body, bytes):
            try:
                return body.decode()
            except UnicodeDecodeError:
                return str(exc)
    return str(exc)


def _parse_function_call(function: dict) -> dict:
    """One ``{"name", "arguments"}`` pair from a server's native tool-call shape.

    LM Studio's ``function.arguments`` is a JSON-encoded string; Ollama's is
    already a dict. Invalid JSON is flagged rather than dropped, so the agent
    probe can tell "malformed call" from "no call".
    """
    name = str(function.get("name") or "")
    raw_arguments = function.get("arguments")
    if isinstance(raw_arguments, dict):
        return {"name": name, "arguments": raw_arguments}
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return {"name": name, "arguments": parsed}
        return {
            "name": name,
            "arguments": {"_raw": raw_arguments, "_invalid_json": True},
        }
    return {"name": name, "arguments": {}}


def _parse_tool_calls(raw_calls: object) -> list[dict]:
    calls: list[dict] = []
    for call in raw_calls if isinstance(raw_calls, list) else []:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if isinstance(function, dict):
            calls.append(_parse_function_call(function))
    return calls


def _served_from_lmstudio_config(
    config: dict, *, instance_id: str | None = None
) -> ServedConfig:
    return ServedConfig(
        context_length=as_int(config.get("context_length")),
        flash_attention=config.get("flash_attention"),
        offload_kv_cache_to_gpu=config.get("offload_kv_cache_to_gpu"),
        eval_batch_size=as_int(config.get("eval_batch_size")),
        num_experts=as_int(config.get("num_experts")),
        parallel=as_int(config.get("parallel")),
        instance_id=instance_id,
    )


class LMStudioServer:
    """Talks to LM Studio's native ``/api/v1`` and ``/api/v0`` endpoints."""

    kind: ServerKind = "lmstudio"

    def __init__(self, base_url: str, *, timeout: float = 8.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def list_models(self) -> list[ModelInfo]:
        try:
            payload = _http(f"{self.base_url}/api/v1/models", self.timeout)
        except _HttpServerError as exc:
            if exc.status == 404:
                return self._list_models_v0()
            raise
        return self._parse_v1_models(payload)

    def _parse_v1_models(self, payload: dict) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        for entry in payload.get("models") or []:
            if not isinstance(entry, dict) or str(entry.get("type")) != "llm":
                continue
            params = str(entry.get("params_string") or "")
            quantization = entry.get("quantization") or {}
            instances = entry.get("loaded_instances") or []
            instance_ids = [
                str(instance.get("id"))
                for instance in instances
                if isinstance(instance, dict) and instance.get("id")
            ]
            served = None
            if instances and isinstance(instances[0], dict):
                config = instances[0].get("config")
                if isinstance(config, dict):
                    served = _served_from_lmstudio_config(
                        config, instance_id=instance_ids[0] if instance_ids else None
                    )
            capabilities = entry.get("capabilities") or {}
            models.append(
                ModelInfo(
                    id=str(entry.get("key") or ""),
                    display_name=str(entry.get("display_name") or ""),
                    arch=str(entry.get("architecture") or ""),
                    params=params,
                    quantization=str(quantization.get("name") or ""),
                    bits_per_weight=quantization.get("bits_per_weight"),
                    size_bytes=as_int(entry.get("size_bytes")),
                    max_context=as_int(entry.get("max_context_length")),
                    is_moe="A3B" in params,
                    supports_tools=capabilities.get("trained_for_tool_use"),
                    loaded=bool(instances),
                    instance_ids=instance_ids,
                    served=served,
                )
            )
        return models

    def _list_models_v0(self) -> list[ModelInfo]:
        """Fallback for a server too old for ``/api/v1`` (LM Studio < 0.4)."""
        payload = _http(f"{self.base_url}/api/v0/models", self.timeout)
        models: list[ModelInfo] = []
        for entry in payload.get("data") or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            kind = str(entry.get("type") or "llm")
            if kind not in _LMSTUDIO_RUNNABLE_TYPES:
                continue
            loaded = str(entry.get("state")) == "loaded"
            served = None
            if loaded:
                served = ServedConfig(
                    context_length=as_int(entry.get("loaded_context_length")),
                    instance_id=str(entry["id"]),
                )
            capabilities = entry.get("capabilities") or []
            models.append(
                ModelInfo(
                    id=str(entry["id"]),
                    arch=str(entry.get("arch") or ""),
                    quantization=str(entry.get("quantization") or ""),
                    max_context=as_int(entry.get("max_context_length")),
                    supports_tools="tool_use" in capabilities,
                    loaded=loaded,
                    instance_ids=[str(entry["id"])] if loaded else [],
                    served=served,
                )
            )
        return models

    def load(self, model_id: str, config: LoadConfig) -> ServedConfig:
        """Apply ``config`` through ``/api/v1/models/load``.

        ``gpu_offload`` is not sent: LM Studio has no API field for it, GPU
        offload is set in the app's own load panel (see
        :func:`unsupported_settings`).
        """
        body: dict = {
            "model": model_id,
            "context_length": config.context_length,
            "echo_load_config": True,
        }
        if config.eval_batch_size is not None:
            body["eval_batch_size"] = config.eval_batch_size
        if config.flash_attention is not None:
            body["flash_attention"] = config.flash_attention
        if config.offload_kv_cache_to_gpu is not None:
            body["offload_kv_cache_to_gpu"] = config.offload_kv_cache_to_gpu
        if config.num_experts is not None:
            body["num_experts"] = config.num_experts
        payload = _http(
            f"{self.base_url}/api/v1/models/load", _LOAD_TIMEOUT_SECONDS, body
        )
        instance_id = payload.get("instance_id")
        instance_id = str(instance_id) if instance_id else model_id
        load_config = payload.get("load_config")
        if isinstance(load_config, dict):
            nested = load_config.get("config")
            source = nested if isinstance(nested, dict) else load_config
            return _served_from_lmstudio_config(source, instance_id=instance_id)
        for model in self.list_models():
            if model.id == model_id and model.served is not None:
                served = model.served
                if served.instance_id is None:
                    served = served.model_copy(update={"instance_id": instance_id})
                return served
        return ServedConfig(instance_id=instance_id)

    def unload(self, model_id: str, instance_id: str | None = None) -> None:
        """Unload ``model_id``'s instance(s).

        With ``instance_id`` given, only that instance is unloaded. Without
        it, every loaded instance of ``model_id`` goes — callers that must
        not evict an instance the lab did not load should pass ``instance_id``
        (spec item 3).
        """
        if instance_id is not None:
            _http(
                f"{self.base_url}/api/v1/models/unload",
                self.timeout,
                {"instance_id": instance_id},
            )
            return
        model = next((m for m in self.list_models() if m.id == model_id), None)
        if model is None:
            return
        for iid in model.instance_ids:
            _http(
                f"{self.base_url}/api/v1/models/unload",
                self.timeout,
                {"instance_id": iid},
            )

    def complete(
        self,
        model_id: str,
        prompt: str,
        *,
        max_tokens: int = 128,
        tools: list[dict] | None = None,
        config: LoadConfig | None = None,  # noqa: ARG002 - part of the LocalServer protocol
        timeout: float = 180.0,
    ) -> Completion:
        body: dict = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        try:
            payload = _http(f"{self.base_url}/api/v0/chat/completions", timeout, body)
        except ServerError as exc:
            return Completion(error=str(exc))
        choices = payload.get("choices") or []
        message = choices[0].get("message") or {} if choices else {}
        usage = payload.get("usage") or {}
        stats = payload.get("stats") or {}
        prompt_tokens = as_int(usage.get("prompt_tokens"))
        time_to_first_token = stats.get("time_to_first_token")
        prompt_tps = None
        if (
            prompt_tokens
            and time_to_first_token
            and prompt_tokens > 0
            and time_to_first_token > 0
        ):
            prompt_tps = prompt_tokens / time_to_first_token
        return Completion(
            text=str(message.get("content") or ""),
            prompt_tokens=prompt_tokens,
            completion_tokens=as_int(usage.get("completion_tokens")),
            time_to_first_token_s=time_to_first_token,
            tokens_per_second=stats.get("tokens_per_second"),
            prompt_tokens_per_second=prompt_tps,
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
        )


def _ollama_shape(arch: str, model_info: dict) -> ArchShape | None:
    """``ArchShape`` from ``/api/show``'s ``model_info``, the "exact" source."""
    layers = as_int(model_info.get(f"{arch}.block_count"))
    kv_heads = as_int(model_info.get(f"{arch}.attention.head_count_kv"))
    head_dim = as_int(model_info.get(f"{arch}.attention.key_length"))
    if head_dim is None:
        embedding_length = as_int(model_info.get(f"{arch}.embedding_length"))
        head_count = as_int(model_info.get(f"{arch}.attention.head_count"))
        if embedding_length and head_count:
            head_dim = embedding_length // head_count
    if layers is None or kv_heads is None or head_dim is None:
        return None
    return ArchShape(
        layers=layers, kv_layers=layers, kv_heads=kv_heads, head_dim=head_dim
    )


class OllamaServer:
    """Talks to Ollama's native ``/api/tags``, ``/api/ps``, ``/api/show`` and
    ``/api/chat`` endpoints."""

    kind: ServerKind = "ollama"

    def __init__(self, base_url: str, *, timeout: float = 8.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def _running_by_name(self) -> dict[str, dict]:
        try:
            payload = _http(f"{self.base_url}/api/ps", self.timeout)
        except (DirectHostUnreachable, ServerError):
            return {}
        running: dict[str, dict] = {}
        for entry in payload.get("models") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("model") or entry.get("name")
            if name:
                running[str(name)] = entry
        return running

    def list_models(self) -> list[ModelInfo]:
        tags_payload = _http(f"{self.base_url}/api/tags", self.timeout)
        running = self._running_by_name()
        models: list[ModelInfo] = []
        for entry in tags_payload.get("models") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("model") or entry.get("name")
            if not name:
                continue
            name = str(name)
            details = entry.get("details") or {}
            running_entry = running.get(name)
            served = None
            if running_entry is not None:
                served = ServedConfig(
                    context_length=as_int(running_entry.get("context_length")),
                    vram_bytes=as_int(running_entry.get("size_vram")),
                )
            arch = str(details.get("family") or "")
            params = str(details.get("parameter_size") or "")
            quantization = str(details.get("quantization_level") or "")
            max_context = None
            shape = None
            try:
                show_payload = _http(
                    f"{self.base_url}/api/show", self.timeout, {"model": name}
                )
            except (DirectHostUnreachable, ServerError):
                show_payload = None
            if show_payload is not None:
                model_info = show_payload.get("model_info") or {}
                arch = str(model_info.get("general.architecture") or arch)
                max_context = as_int(model_info.get(f"{arch}.context_length"))
                shape = _ollama_shape(arch, model_info)
            models.append(
                ModelInfo(
                    id=name,
                    arch=arch,
                    params=params,
                    quantization=quantization,
                    max_context=max_context or as_int(details.get("context_length")),
                    size_bytes=as_int(entry.get("size")),
                    is_moe="A3B" in params,
                    # Ollama >= 0.6 lists capabilities ("tools", "thinking");
                    # older servers say nothing, which stays "unknown".
                    supports_tools=(
                        "tools" in entry["capabilities"]
                        if isinstance(entry.get("capabilities"), list)
                        else None
                    ),
                    loaded=running_entry is not None,
                    instance_ids=[name] if running_entry is not None else [],
                    served=served,
                    shape=shape,
                )
            )
        return models

    def load(self, model_id: str, config: LoadConfig) -> ServedConfig:
        """Warm-load ``model_id`` with a prompt-less ``/api/generate`` call.

        Flash attention and the KV-cache type cannot be set per request (see
        :func:`unsupported_settings`), so :class:`ServedConfig` leaves them
        ``None`` here; the served context length and VRAM come from
        ``/api/ps`` read back after the warm load.
        """
        options: dict = {"num_ctx": config.context_length}
        if config.eval_batch_size is not None:
            options["num_batch"] = config.eval_batch_size
        if config.gpu_offload < 1:
            shape = next(
                (model.shape for model in self.list_models() if model.id == model_id),
                None,
            )
            if shape is not None:
                options["num_gpu"] = round(config.gpu_offload * shape.layers)
        body = {
            "model": model_id,
            "prompt": "",
            "keep_alive": "30m",
            "options": options,
        }
        _http(f"{self.base_url}/api/generate", _LOAD_TIMEOUT_SECONDS, body)
        running = self._running_by_name()
        entry = running.get(model_id)
        if entry is None:
            return ServedConfig(instance_id=model_id)
        return ServedConfig(
            context_length=as_int(entry.get("context_length")),
            vram_bytes=as_int(entry.get("size_vram")),
            instance_id=model_id,
        )

    def unload(self, model_id: str, instance_id: str | None = None) -> None:  # noqa: ARG002
        """Unload ``model_id``.

        Ollama has one instance per model id, so ``instance_id`` (always
        ``model_id`` itself, per the spec) carries no extra information here;
        it only exists to satisfy the shared ``LocalServer`` surface.
        """
        _http(
            f"{self.base_url}/api/generate",
            self.timeout,
            {"model": model_id, "keep_alive": 0},
        )

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
        options: dict = {"num_predict": max_tokens}
        if config is not None:
            options["num_ctx"] = config.context_length
            if config.eval_batch_size is not None:
                options["num_batch"] = config.eval_batch_size
        body: dict = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": options,
        }
        if tools:
            body["tools"] = tools
        try:
            payload = _http(f"{self.base_url}/api/chat", timeout, body)
        except ServerError as exc:
            return Completion(error=str(exc))
        message = payload.get("message") or {}
        prompt_eval_count = as_int(payload.get("prompt_eval_count"))
        prompt_eval_duration = payload.get("prompt_eval_duration")
        eval_count = as_int(payload.get("eval_count"))
        eval_duration = payload.get("eval_duration")
        load_duration = payload.get("load_duration") or 0
        time_to_first_token = None
        if prompt_eval_duration is not None:
            time_to_first_token = (prompt_eval_duration + load_duration) / 1e9
        prompt_tps = None
        if prompt_eval_count and prompt_eval_duration:
            prompt_seconds = prompt_eval_duration / 1e9
            if prompt_seconds > 0:
                prompt_tps = prompt_eval_count / prompt_seconds
        tokens_per_second = None
        if eval_count and eval_duration:
            eval_seconds = eval_duration / 1e9
            if eval_seconds > 0:
                tokens_per_second = eval_count / eval_seconds
        return Completion(
            text=str(message.get("content") or ""),
            prompt_tokens=prompt_eval_count,
            completion_tokens=eval_count,
            time_to_first_token_s=time_to_first_token,
            tokens_per_second=tokens_per_second,
            prompt_tokens_per_second=prompt_tps,
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
        )


class InvalidServerUrl(ValueError):
    """``url`` is not an acceptable LM Studio/Ollama server address.

    Raised instead of letting a crafted URL reach ``urllib`` at all (spec
    item 5, SSRF hardening): every endpoint that takes a ``url`` must answer
    422 with a clear message rather than ever dialing it.
    """


_ALLOWED_PATHS = frozenset({"", "/", "/v1", "/api"})


def validate_server_url(url: str) -> str:
    """Reject anything but a plain ``http(s)://host[:port][/v1|/api]`` address.

    No query string, fragment or userinfo; the path, once any trailing slash
    is stripped, must be one of the handful of client-library suffixes
    operators paste in. Returns ``url`` unchanged (callers still normalise it
    with :func:`harness_lab_app.direct_hosts.host_root`).
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InvalidServerUrl(f"{url!r} is not a valid URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise InvalidServerUrl(
            f"{url!r} must use http or https, not {parts.scheme or '(none)'}."
        )
    if not parts.hostname:
        raise InvalidServerUrl(f"{url!r} has no host.")
    if parts.query:
        raise InvalidServerUrl(f"{url!r} must not carry a query string.")
    if parts.fragment:
        raise InvalidServerUrl(f"{url!r} must not carry a fragment.")
    if parts.username or parts.password:
        raise InvalidServerUrl(f"{url!r} must not carry userinfo.")
    path = parts.path.rstrip("/")
    if path not in _ALLOWED_PATHS:
        raise InvalidServerUrl(f"{url!r} must have an empty path, or one of /v1, /api.")
    try:
        _ = parts.port
    except ValueError as exc:
        raise InvalidServerUrl(f"{url!r} has a non-numeric port: {exc}") from exc
    return url


def make_server(
    kind: ServerKind, base_url: str, *, timeout: float = 8.0
) -> LocalServer:
    """Build the adapter for ``kind``, normalising and (in Docker) rewriting the URL."""
    validate_server_url(base_url)
    root = host_root(base_url)
    if Path("/.dockerenv").exists():
        from harness_lab_app.config import get_settings
        from harness_lab_app.executors import rewrite_localhost

        root = rewrite_localhost(root, get_settings().docker_host_alias)
    if kind == "lmstudio":
        return LMStudioServer(root, timeout=timeout)
    if kind == "ollama":
        return OllamaServer(root, timeout=timeout)
    raise ValueError(f"unknown server kind: {kind}")


def unsupported_settings(kind: ServerKind, config: LoadConfig) -> list[str]:
    """Human-readable hints for settings ``kind`` cannot apply per request."""
    hints: list[str] = []
    if kind == "ollama":
        if config.flash_attention is not None:
            hints.append(
                "Set OLLAMA_FLASH_ATTENTION=1 on the Ollama server and restart it."
            )
        if config.kv_cache_bytes != 2.0:
            hints.append(
                "Set OLLAMA_KV_CACHE_TYPE on the Ollama server and restart it."
            )
    elif kind == "lmstudio":
        if config.gpu_offload < 1:
            hints.append(
                "Set GPU offload in LM Studio's load panel; the API does not take it."
            )
        if config.kv_cache_bytes != 2.0:
            kv_type = _kv_cache_type_name(config.kv_cache_bytes)
            hints.append(
                f"Set K Cache Quantization and V Cache Quantization to {kv_type} in "
                "LM Studio's load panel (tick both boxes, then reload the model); "
                "the API does not take it, so the lab cannot apply or verify it."
            )
    return hints


def _kv_cache_type_name(kv_cache_bytes: float) -> str:
    """Catalog name (``q8_0``, ``q4_0``…) for a KV cache bytes-per-element value."""
    for name, value in KV_CACHE_TYPES.items():
        if value == kv_cache_bytes:
            return name
    return f"{kv_cache_bytes} bytes per element"


_DIFF_FIELDS = (
    "context_length",
    "flash_attention",
    "offload_kv_cache_to_gpu",
    "eval_batch_size",
    "num_experts",
    # ``parallel`` is diffed separately (see below): LM Studio's load response
    # always echoes a ``parallel`` the UI never requested, which would show a
    # spurious diff on every single load.
)

# LoadConfig.parallel's own default; a request that never touched this field
# still carries this value, indistinguishable from an explicit "1".
_DEFAULT_PARALLEL = 1


def diff_config(requested: LoadConfig, served: ServedConfig) -> list[ConfigDiff]:
    """Fields where the server reported a value that differs from the request.

    ``parallel`` is excluded unless the request explicitly asked for a
    non-default value: LM Studio's load response always echoes back a
    ``parallel`` count (even though the UI never sends one, and never offers a
    control for it), so comparing it against the request's untouched default
    would flag a diff on every load (spec item 10). The served value is still
    reported elsewhere (``ServedConfig.parallel``), only the diff is skipped.
    """
    diffs: list[ConfigDiff] = []
    for field in _DIFF_FIELDS:
        served_value = getattr(served, field)
        if served_value is None:
            continue
        requested_value = getattr(requested, field, None)
        if requested_value is None or requested_value == served_value:
            continue
        diffs.append(
            ConfigDiff(field=field, requested=requested_value, served=served_value)
        )
    if (
        served.parallel is not None
        and requested.parallel != _DEFAULT_PARALLEL
        and requested.parallel != served.parallel
    ):
        diffs.append(
            ConfigDiff(
                field="parallel", requested=requested.parallel, served=served.parallel
            )
        )
    return diffs
