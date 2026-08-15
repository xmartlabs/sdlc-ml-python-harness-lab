"""Direct Ollama access, bypassing the LiteLLM gateway.

The sibling of :mod:`harness_lab_app.lmstudio_direct`, for the second machine on
the LAN. Same reason for existing: an ``ollama-*`` gateway alias is a name that
can be mapped wrong three layers away from the picker, while ``ollama/<id>``
addresses the host that will actually serve the model.

Two native endpoints answer everything the picker needs, in two requests:

* ``GET /api/tags`` — every model **installed** on the host, with its family,
  parameter size and quantization. This is the menu.
* ``GET /api/ps`` — the models **running** right now. Recent Ollama builds also
  report ``context_length`` here, which is the window the server is actually
  serving; that is the number the harness cares about.

Where this differs from LM Studio, and why the preflight is looser: LM Studio's
JIT auto-load takes the *model's own* default window, which is unknowable in
advance and is what cost run tetris-v1-K-020 two and a half hours at 8k. Ollama
instead applies a *server-wide* setting (``OLLAMA_CONTEXT_LENGTH``, default
4096) to whatever it loads. An unloaded Ollama model therefore has a knowable —
just not remotely readable — window, so a cold model is a loud warning here
rather than a hard block, while a *running* model whose reported window is too
small is refused exactly like an LM Studio one.

That 4096 default matters: it is below the ~13.7k-token system prompt, so a
default-configured Ollama host cannot run this harness at all until
``OLLAMA_CONTEXT_LENGTH`` is raised. The picker labels say so.

Stdlib only: ``scripts/bench_run.py`` imports it from inside the runner
container, which has no web dependencies installed.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

from harness_lab_app.direct_hosts import (
    DirectHostUnreachable,
    TtlCache,
    as_int,
    get_json,
    host_root,
    strip_provider_prefix,
)

# The opencode provider id for models served straight off the Ollama host. It
# matches the tracked ``provider.ollama`` block in opencode.json, so a direct id
# and a curated one route through the same provider entry.
OLLAMA_PREFIX = "ollama/"

OLLAMA_GROUP_LABEL = "Ollama (direct, no gateway)"
OLLAMA_BACKEND = "ollama-direct"
OLLAMA_SOURCE = "direct"
OLLAMA_PROVIDER = "ollama"
OLLAMA_PROVIDER_LABEL = "Ollama"

# What Ollama serves when nobody sets OLLAMA_CONTEXT_LENGTH. Far below this
# harness's floor, hence the warning in every cold model's label.
OLLAMA_DEFAULT_CONTEXT = 4096

_CACHE_TTL_SECONDS = 15.0
_cache = TtlCache()


def ollama_root(base_url: str) -> str:
    """Server root for ``base_url`` (``/v1`` and ``/api`` suffixes removed)."""
    return host_root(base_url)


def bare_model_id(model: str) -> str:
    """Strip the opencode-only ``ollama/`` prefix, keeping the model's tag."""
    return strip_provider_prefix(model, OLLAMA_PREFIX)


def ollama_model_id(bare: str) -> str:
    """Add the ``ollama/`` provider prefix to a bare Ollama model id."""
    return bare if bare.startswith(OLLAMA_PREFIX) else f"{OLLAMA_PREFIX}{bare}"


def is_direct_model(model: str | None) -> bool:
    """True when ``model`` addresses an Ollama host directly."""
    return bool(model) and str(model).startswith(OLLAMA_PREFIX)


@dataclass(frozen=True)
class OllamaModel:
    """One model as the Ollama server reports it right now."""

    id: str
    running: bool
    # The window the server is serving for this model. Only knowable while it
    # runs, and only on builds whose /api/ps reports it.
    context_length: int | None = None
    parameter_size: str = ""
    quantization: str = ""
    family: str = ""

    @property
    def state(self) -> str:
        return "running" if self.running else "not-loaded"

    def as_dict(self) -> dict:
        return {
            "id": ollama_model_id(self.id),
            "bare_id": self.id,
            "state": self.state,
            "running": self.running,
            "context_length": self.context_length,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
            "family": self.family,
        }


def _parse_tags(payload: dict) -> list[OllamaModel]:
    models: list[OllamaModel] = []
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("model") or entry.get("name")
        if not name:
            continue
        raw_details = entry.get("details")
        details: dict = raw_details if isinstance(raw_details, dict) else {}
        models.append(
            OllamaModel(
                id=str(name),
                running=False,
                parameter_size=str(details.get("parameter_size") or ""),
                quantization=str(details.get("quantization_level") or ""),
                family=str(details.get("family") or ""),
            )
        )
    return models


def _parse_running(payload: dict) -> dict[str, int | None]:
    """``{model id: served context length or None}`` for what is loaded now."""
    running: dict[str, int | None] = {}
    for entry in payload.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("model") or entry.get("name")
        if name:
            running[str(name)] = as_int(entry.get("context_length"))
    return running


def list_ollama_models(
    base_url: str | None = None, timeout: float = 8.0
) -> list[OllamaModel]:
    """Every model the Ollama host has, running ones first.

    Raises :class:`DirectHostUnreachable` when the host does not answer — the
    same deliberate break from the gateway probes' fail-open contract that
    :mod:`harness_lab_app.lmstudio_direct` makes: a direct model whose server is
    off cannot run, and there is no ambiguity worth protecting.

    A failing ``/api/ps`` is not fatal: the installed list is still the right
    menu, everything in it is simply reported as not running.
    """
    base = base_url if base_url is not None else os.environ.get("OLLAMA_BASE_URL", "")
    root = ollama_root(base or "")
    if not root:
        raise DirectHostUnreachable(
            "OLLAMA_BASE_URL is not set — point it at your Ollama server "
            "(for example http://192.168.0.155:11434/v1) on the Settings page."
        )
    try:
        models = _parse_tags(get_json(f"{root}/api/tags", timeout))
    except Exception as exc:  # reported verbatim to the user
        raise DirectHostUnreachable(f"{root} did not answer: {exc}") from exc

    # An old or busy host still has a valid menu, so a failing /api/ps costs
    # only the loaded-state column, not the list.
    running: dict[str, int | None] = {}
    with contextlib.suppress(Exception):
        running = _parse_running(get_json(f"{root}/api/ps", timeout))

    resolved = [
        OllamaModel(
            id=model.id,
            running=model.id in running,
            context_length=running.get(model.id),
            parameter_size=model.parameter_size,
            quantization=model.quantization,
            family=model.family,
        )
        for model in models
    ]
    # Running first (those answer immediately), then by id so order is stable.
    return sorted(resolved, key=lambda m: (not m.running, m.id))


def list_ollama_models_cached(base_url: str, timeout: float = 8.0) -> list[OllamaModel]:
    """``list_ollama_models`` behind a short TTL. Failures are never cached."""
    root = ollama_root(base_url or "")
    cached = _cache.get(root)
    if cached is not None:
        return cached  # type: ignore[return-value]
    models = list_ollama_models(root, timeout=timeout)
    _cache.set(root, models, _CACHE_TTL_SECONDS)
    return models


def clear_ollama_cache() -> None:
    """Drop the TTL cache so the next read hits the server (Reload button)."""
    _cache.clear()


def find_model(models: list[OllamaModel], model_id: str) -> OllamaModel | None:
    """Look up a model by id, tolerating the ``ollama/`` prefix on either side."""
    wanted = bare_model_id(model_id)
    return next((model for model in models if model.id == wanted), None)


def label_for(model: OllamaModel) -> str:
    """Picker label: whether it is running, and with how much context."""
    size = f" {model.parameter_size}" if model.parameter_size else ""
    if model.running:
        window = (
            f"{round(model.context_length / 1000)}k ctx"
            if model.context_length
            else "context unreported"
        )
        return f"{model.id}{size} — running, {window}"
    return f"{model.id}{size} — not loaded (server default, often 4k)"


def build_direct_options(models: list[OllamaModel]) -> list[dict]:
    """Run-picker options for the direct Ollama group."""
    return [
        {
            "id": ollama_model_id(model.id),
            "label": label_for(model),
            "routable": True,
            "aliases": [],
            "backend": OLLAMA_BACKEND,
            "group": OLLAMA_GROUP_LABEL,
            # Beside the direct LM Studio group, ahead of every gateway one.
            "group_rank": -1,
            "source": OLLAMA_SOURCE,
            "provider": OLLAMA_PROVIDER,
            "provider_label": OLLAMA_PROVIDER_LABEL,
            "loaded": model.running,
            "loaded_context": model.context_length,
            "max_context": None,
        }
        for model in models
    ]
