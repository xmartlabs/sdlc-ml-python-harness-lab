"""Direct LM Studio access, bypassing the LiteLLM gateway entirely.

Why a second path at all: a ``gateway/<alias>`` executor depends on the LiteLLM
alias mapping being right, and when it is not the failure is silent and
expensive. Run tetris-v1-K-031 hit exactly that — the alias forwarded the model
name ``qwen3.6:35b`` (an Ollama-style id) and LM Studio answered ``Invalid model
identifier``, while the run-form preflight, which treats an unrecognized error
as ambiguous, showed green. A whole day of runs went to a name mismatch three
layers away from the picker.

This module removes the layers. It talks to the LM Studio server named by
``LMSTUDIO_BASE_URL`` with its own REST API (``/api/v0/models``), which — unlike
an OpenAI-compatible ``/v1/models`` list — reports, per model, whether it is
**loaded** and with **what context window**. That is the ground truth the
context preflight was trying to infer by sending a 32k-token prompt and timing
the answer: here it is one cheap GET.

Naming: opencode addresses these as ``lmstudio/<model id>`` (the provider
written by :mod:`harness_lab_app.opencode_overlay`); LM Studio itself wants the
bare id, which may itself contain a slash (``prism-ml/bonsai-27b``). Only the
first segment is the provider, so :func:`bare_model_id` strips one prefix.

Stdlib only: ``scripts/bench_run.py`` imports it from inside the runner
container, which has no web dependencies installed.
"""

from __future__ import annotations

import os
import urllib.error
from dataclasses import dataclass

from harness_lab_app.constants import EXAMPLE_LMSTUDIO_URL
from harness_lab_app.direct_hosts import (
    DirectHostUnreachable,
    TtlCache,
    as_int,
    get_json,
    host_root,
    strip_provider_prefix,
)

# The opencode provider id for models served straight off the LM Studio host.
LMSTUDIO_PREFIX = "lmstudio/"

# Heading the run picker and the Settings table draw these under. Deliberately
# spells out the bypass: the whole point of the group is that nothing between
# the lab and the model can rename it.
LMSTUDIO_GROUP_LABEL = "LM Studio (direct, no gateway)"
LMSTUDIO_BACKEND = "lmstudio-direct"

# Picker tiers for these models: the "direct" source, LM Studio as the vendor.
# The source axis is what the run form's first tier switches on, and it is the
# one distinction the old flat menu could not express — a ``gateway/lmstudio-*``
# alias and an ``lmstudio/…`` id both read as "LM Studio" while reaching the
# server by completely different routes.
LMSTUDIO_SOURCE = "direct"
LMSTUDIO_PROVIDER = "lmstudio"
LMSTUDIO_PROVIDER_LABEL = "LM Studio"

# Model kinds that can serve a run. LM Studio also lists embedding models, which
# would fail as executors and only make the menu longer.
_RUNNABLE_TYPES = ("llm", "vlm")


def lmstudio_root(base_url: str) -> str:
    """Server root for ``base_url``, with any OpenAI-compat suffix removed.

    ``LMSTUDIO_BASE_URL`` is written for OpenAI clients (``http://host:1234/v1``)
    but the native REST API lives beside it at ``/api/v0``. Accept either
    spelling rather than making the operator keep two variables in sync.
    """
    return host_root(base_url)


def bare_model_id(model: str) -> str:
    """Strip the opencode-only ``lmstudio/`` prefix, keeping the model's own slash.

    ``lmstudio/prism-ml/bonsai-27b`` → ``prism-ml/bonsai-27b``. Only one prefix
    is removed: the remainder is the publisher-qualified id LM Studio registers.
    """
    return strip_provider_prefix(model, LMSTUDIO_PREFIX)


def lmstudio_model_id(bare: str) -> str:
    """Add the ``lmstudio/`` provider prefix to a bare LM Studio model id."""
    if bare.startswith(LMSTUDIO_PREFIX):
        return bare
    return f"{LMSTUDIO_PREFIX}{bare}"


def is_direct_model(model: str | None) -> bool:
    """True when ``model`` addresses LM Studio directly rather than the gateway."""
    return bool(model) and str(model).startswith(LMSTUDIO_PREFIX)


@dataclass(frozen=True)
class LMStudioModel:
    """One model as the LM Studio server reports it right now."""

    id: str
    type: str
    state: str  # "loaded" | "not-loaded" | "loading"
    max_context: int | None
    loaded_context: int | None
    arch: str = ""
    quantization: str = ""

    @property
    def loaded(self) -> bool:
        return self.state == "loaded"

    @property
    def usable_context(self) -> int | None:
        """The window this model is *serving*, not the one it could support.

        A model that is not loaded has no answer: LM Studio will JIT-load it on
        first use with whatever default the model config carries, which is the
        very trap (an 8k auto-load) this whole module exists to make visible.
        """
        return self.loaded_context if self.loaded else None

    def as_dict(self) -> dict:
        return {
            "id": lmstudio_model_id(self.id),
            "bare_id": self.id,
            "type": self.type,
            "state": self.state,
            "loaded": self.loaded,
            "max_context": self.max_context,
            "loaded_context": self.loaded_context,
            "arch": self.arch,
            "quantization": self.quantization,
        }


class LMStudioUnreachable(DirectHostUnreachable):
    """The LM Studio server did not answer — distinct from 'model not found'."""


def _get_json(url: str, timeout: float) -> dict:
    return get_json(url, timeout)


def _parse_v0(payload: dict) -> list[LMStudioModel]:
    models: list[LMStudioModel] = []
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        kind = str(entry.get("type") or "llm")
        if kind not in _RUNNABLE_TYPES:
            continue
        models.append(
            LMStudioModel(
                id=str(entry["id"]),
                type=kind,
                state=str(entry.get("state") or "not-loaded"),
                max_context=_as_int(entry.get("max_context_length")),
                loaded_context=_as_int(entry.get("loaded_context_length")),
                arch=str(entry.get("arch") or ""),
                quantization=str(entry.get("quantization") or ""),
            )
        )
    return models


def _parse_v1(payload: dict) -> list[LMStudioModel]:
    """Fallback for a server too old for ``/api/v0``: ids only, no state.

    Everything the native API adds is unknown here, so each model is reported
    ``not-loaded`` with no window. Callers must treat "unknown" as unknown
    rather than as a failure — an older LM Studio is not a broken one.
    """
    return [
        LMStudioModel(
            id=str(entry["id"]),
            type="llm",
            state="unknown",
            max_context=None,
            loaded_context=None,
        )
        for entry in payload.get("data") or []
        if isinstance(entry, dict) and entry.get("id")
    ]


_as_int = as_int


def list_lmstudio_models(
    base_url: str | None = None, timeout: float = 8.0
) -> list[LMStudioModel]:
    """Every model the LM Studio host knows, loaded ones first.

    Raises :class:`LMStudioUnreachable` when the host does not answer. That is
    on purpose and the opposite of the gateway probes' fail-open contract: a
    direct model whose server is off cannot run, there is no ambiguity to
    protect, and pretending otherwise is what wasted the runs this module
    replaces.
    """
    base = base_url if base_url is not None else os.environ.get("LMSTUDIO_BASE_URL", "")
    root = lmstudio_root(base or "")
    if not root:
        raise LMStudioUnreachable(
            "LMSTUDIO_BASE_URL is not set — point it at your LM Studio server "
            f"(for example {EXAMPLE_LMSTUDIO_URL}) on the Settings page."
        )
    try:
        payload = _get_json(f"{root}/api/v0/models", timeout)
        models = _parse_v0(payload)
    except urllib.error.HTTPError:
        # Older builds have no native API; the OpenAI-compatible list still
        # tells us which ids exist.
        try:
            models = _parse_v1(_get_json(f"{root}/v1/models", timeout))
        except Exception as exc:  # reported verbatim to the user
            raise LMStudioUnreachable(f"{root} did not answer: {exc}") from exc
    except Exception as exc:  # reported verbatim to the user
        raise LMStudioUnreachable(f"{root} did not answer: {exc}") from exc
    # Loaded first (those run now), then by id so the order is stable.
    return sorted(models, key=lambda m: (not m.loaded, m.id))


_CACHE_TTL_SECONDS = 15.0

# TTL for a listing that came from the ``/v1`` fallback rather than the native
# API. Those answers are strictly worse — ids only, no state, no window — and on
# some builds ``/v1/models`` reports just the model currently loaded, so a
# transient hiccup can cache a one-entry menu for the full TTL and make the
# picker look like the server lost every other model. Two seconds is long enough
# to keep a genuinely old LM Studio from being hammered on every render, short
# enough that the list self-heals before anyone reaches for the Reload button.
_DEGRADED_TTL_SECONDS = 2.0

_cache = TtlCache()


def _is_degraded(models: list[LMStudioModel]) -> bool:
    """True when this listing came from the ``/v1`` fallback.

    Only :func:`_parse_v1` reports ``state == "unknown"``, so an all-unknown
    listing is exactly the degraded case and a non-empty one cannot be confused
    with a healthy answer.
    """
    return bool(models) and all(m.state == "unknown" for m in models)


def list_lmstudio_models_cached(
    base_url: str, timeout: float = 8.0
) -> list[LMStudioModel]:
    """``list_lmstudio_models`` behind a short TTL.

    Short on purpose: loading or ejecting a model in LM Studio must show up in
    the picker on the next render, not in half a minute. Failures are never
    cached — the server coming back has to be visible immediately — and a
    degraded ``/v1`` listing expires sooner still (see
    :data:`_DEGRADED_TTL_SECONDS`).
    """
    root = lmstudio_root(base_url or "")
    cached = _cache.get(root)
    if cached is not None:
        return cached  # type: ignore[return-value]
    models = list_lmstudio_models(root, timeout=timeout)
    ttl = _DEGRADED_TTL_SECONDS if _is_degraded(models) else _CACHE_TTL_SECONDS
    _cache.set(root, models, ttl)
    return models


def clear_lmstudio_cache() -> None:
    """Drop the TTL cache so the next read hits the server (Refresh button)."""
    _cache.clear()


def find_model(models: list[LMStudioModel], model_id: str) -> LMStudioModel | None:
    """Look up a model by id, tolerating the ``lmstudio/`` prefix on either side."""
    wanted = bare_model_id(model_id)
    for model in models:
        if model.id == wanted:
            return model
    return None


def label_for(model: LMStudioModel) -> str:
    """Picker label carrying the two facts that decide whether a run works.

    Whether it is loaded, and with how much context — the same two numbers the
    old preflight spent 90 seconds and several GPU-minutes failing to measure.
    """
    if model.loaded:
        window = (
            f"{round(model.loaded_context / 1000)}k ctx"
            if model.loaded_context
            else "context unknown"
        )
        return f"{model.id} — loaded, {window}"
    if model.state == "unknown":
        return f"{model.id} — state unknown (old LM Studio build)"
    return f"{model.id} — not loaded (JIT would use its default window)"


def build_direct_options(models: list[LMStudioModel]) -> list[dict]:
    """Run-picker options for the direct group, shaped like the gateway ones.

    ``routable`` is true for every entry: the overlay writes a provider block
    for whichever direct model a run selects, so there is no second map that
    can disagree with this list. That is the entire point of the bypass.
    """
    return [
        {
            "id": lmstudio_model_id(model.id),
            "label": label_for(model),
            "routable": True,
            "aliases": [],
            "backend": LMSTUDIO_BACKEND,
            "group": LMSTUDIO_GROUP_LABEL,
            # Ahead of every gateway group: these are the ones that are
            # provably loaded on the host right now.
            "group_rank": -1,
            "source": LMSTUDIO_SOURCE,
            "provider": LMSTUDIO_PROVIDER,
            "provider_label": LMSTUDIO_PROVIDER_LABEL,
            "loaded": model.loaded,
            "loaded_context": model.loaded_context,
            "max_context": model.max_context,
        }
        for model in models
    ]
