"""Detect the context window a self-hosted model is serving right now.

opencode decides when to compact a session from ``limit.context`` on the model
entry, and a model that declares no limit has ``limit.context == 0`` — which
disables compaction outright (``isOverflow`` returns false before it counts a
token). The tracked ``opencode.json`` declares no limit for any ``lmstudio-*``
card, so a session against a 41k window simply grew past it. LM Studio then
truncated the prompt from the middle on every turn, the cached prefix never
matched again, and each call paid the full prefill (~60s for 35k tokens on the
lab's box): the "every iteration waits for minutes" symptom.

The lab already knows the served window — the run form prints "loaded, 41k ctx"
straight off ``/api/v0/models``. This module turns that fact into the ``limit``
opencode needs, per model, at launch, so the window the operator set in LM
Studio (41k today, 120k tomorrow) is used in full and never exceeded. Nothing
here is declared by hand: change the window in LM Studio and the next run
follows it.

Fail-open, deliberately. A cloud model has no served window to read, a host
that does not answer is the preflight's problem, and a wrong "no limit" costs
exactly what every run cost before this module existed. A wrong limit, on the
other hand, would compact a session that had room.

Stdlib only: ``scripts/bench_run.py`` imports it inside the runner container.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_lab_app.direct_hosts import as_int, host_root

if TYPE_CHECKING:
    from collections.abc import Iterable

# Below this many tokens of headroom a single tool output can push the next
# request past the served window before opencode's own estimate (taken from
# the previous response's usage) notices. Ten percent of the window, floored
# here, is what a large file read costs.
MIN_HEADROOM_TOKENS = 4096

# Output cap for a model addressed directly (no gateway card to read
# ``max_tokens`` from). Matches the ``-build`` profile at the gateway: a
# targeted edit fits, a whole-file rewrite does not (lever 1 of
# docs/local-model-runtime-config.md). Override with
# ``HARNESS_LAB_DIRECT_OUTPUT_TOKENS``.
DEFAULT_OUTPUT_TOKENS = 4096
ENV_DIRECT_OUTPUT_TOKENS = "HARNESS_LAB_DIRECT_OUTPUT_TOKENS"

# Master switch: ``0`` / ``false`` / ``no`` leaves every model undeclared, as
# before this module. The gateway-probe kill switch turns it off too, so test
# suites and offline shells never reach for a host.
ENV_AUTODETECT = "HARNESS_LAB_CONTEXT_AUTODETECT"
ENV_DISABLE_PROBE = "HARNESS_LAB_DISABLE_GATEWAY_PROBE"

_OFF = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class ContextWindow:
    """What one model is serving, and the limit opencode should be told."""

    model: str  # the id as the run addresses it: ``gateway/…``, ``lmstudio/…``
    backend: str  # "lmstudio" | "ollama"
    host: str  # server root the window was read from
    bare_id: str  # the id that host registers
    served: int  # tokens the server is serving right now
    context: int  # ``limit.context`` for opencode: served minus headroom
    output: int  # ``limit.output`` for opencode

    @property
    def limit(self) -> dict[str, int]:
        return {"context": self.context, "output": self.output}

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "backend": self.backend,
            "host": self.host,
            "bare_id": self.bare_id,
            "served": self.served,
            "context": self.context,
            "output": self.output,
        }


def autodetect_enabled(environ: dict[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    if env.get(ENV_DISABLE_PROBE):
        return False
    return str(env.get(ENV_AUTODETECT, "1")).strip().lower() not in _OFF


def headroom_for(served: int) -> int:
    """Tokens kept free below the served window."""
    return max(MIN_HEADROOM_TOKENS, served // 10)


def limit_for(served: int, output: int) -> dict[str, int] | None:
    """The ``limit`` block for a window of ``served`` tokens, or ``None``.

    opencode compacts when the last response's token count reaches
    ``context - min(output, 32000)``. The headroom subtracted here is what keeps
    the *next* request — the one carrying a fresh tool output — inside the
    window the server enforces. A window too small to hold the output cap plus
    that headroom cannot be declared usefully and is left to the preflight.
    """
    context = served - headroom_for(served)
    if context <= output:
        return None
    return {"context": context, "output": output}


def default_output_tokens(environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    value = as_int(env.get(ENV_DIRECT_OUTPUT_TOKENS, ""))
    return value if value and value > 0 else DEFAULT_OUTPUT_TOKENS


# -- reading the served window off each backend ----------------------------------


def _lmstudio_served(host: str, bare_id: str, timeout: float) -> int | None:
    from harness_lab_app.lmstudio_direct import find_model, list_lmstudio_models

    model = find_model(list_lmstudio_models(host, timeout=timeout), bare_id)
    if model is None or not model.loaded:
        return None
    return model.loaded_context


def _ollama_served(host: str, bare_id: str, timeout: float) -> int | None:
    from harness_lab_app.ollama_direct import find_model, list_ollama_models

    model = find_model(list_ollama_models(host, timeout=timeout), bare_id)
    if model is None or not model.running:
        return None
    return model.context_length


_READERS = {"lmstudio": _lmstudio_served, "ollama": _ollama_served}


def _read_served(
    backend: str, hosts: Iterable[str], bare_id: str, timeout: float
) -> tuple[str, int] | None:
    """First host that reports a served window for ``bare_id``."""
    reader = _READERS[backend]
    seen: set[str] = set()
    for host in hosts:
        root = host_root(host or "")
        if not root or root in seen:
            continue
        seen.add(root)
        try:
            served = reader(root, bare_id, timeout)
        except Exception:
            continue
        if served:
            return root, served
    return None


def _resolve_gateway_alias(
    alias: str, routing: dict[str, dict]
) -> tuple[str, str, str | None, int | None] | None:
    """``(backend, bare_id, api_base, max_tokens)`` for a self-hosted alias."""
    from harness_lab_app.gateway_models import strip_backend_prefix

    entry = routing.get(alias)
    if not entry:
        return None
    target = str(entry.get("target") or "")
    if target.startswith("lm_studio/"):
        backend = "lmstudio"
    elif target.startswith(("ollama_chat/", "ollama/")):
        backend = "ollama"
    else:
        return None
    params = entry.get("params") or {}
    return (
        backend,
        strip_backend_prefix(target),
        entry.get("api_base") or None,
        as_int(params.get("max_tokens")),
    )


def detect_window(
    model: str,
    *,
    routing: dict[str, dict] | None = None,
    lmstudio_base: str | None = None,
    ollama_base: str | None = None,
    output_default: int | None = None,
    timeout: float = 8.0,
) -> ContextWindow | None:
    """The window ``model`` is served with right now, or ``None``.

    ``routing`` is LiteLLM's ``/model/info`` view (see
    ``gateway_models.fetch_gateway_routing``); without it a ``gateway/`` alias
    cannot be followed to its host. The direct lanes need only their base URL.
    A gateway alias is read from the host its card names first, then from the
    lab's own ``LMSTUDIO_BASE_URL`` / ``OLLAMA_BASE_URL`` — the card may spell
    the host as ``localhost`` from the gateway's point of view.
    """
    from harness_lab_app.gateway_models import GATEWAY_PREFIX
    from harness_lab_app.lmstudio_direct import LMSTUDIO_PREFIX
    from harness_lab_app.ollama_direct import OLLAMA_PREFIX

    env_host = {
        "lmstudio": (
            lmstudio_base
            if lmstudio_base is not None
            else os.environ.get("LMSTUDIO_BASE_URL", "")
        ),
        "ollama": (
            ollama_base
            if ollama_base is not None
            else os.environ.get("OLLAMA_BASE_URL", "")
        ),
    }
    output = output_default if output_default is not None else default_output_tokens()

    hosts: list[str] = []
    if model.startswith(LMSTUDIO_PREFIX):
        backend, bare = "lmstudio", model[len(LMSTUDIO_PREFIX) :]
    elif model.startswith(OLLAMA_PREFIX):
        backend, bare = "ollama", model[len(OLLAMA_PREFIX) :]
    elif model.startswith(GATEWAY_PREFIX):
        resolved = _resolve_gateway_alias(model[len(GATEWAY_PREFIX) :], routing or {})
        if resolved is None:
            return None
        backend, bare, api_base, max_tokens = resolved
        hosts = [api_base] if api_base else []
        if max_tokens:
            output = max_tokens
    else:
        return None

    found = _read_served(backend, [*hosts, env_host[backend]], bare, timeout)
    if found is None:
        return None
    host, served = found
    limit = limit_for(served, output)
    if limit is None:
        return None
    return ContextWindow(
        model=model,
        backend=backend,
        host=host,
        bare_id=bare,
        served=served,
        context=limit["context"],
        output=limit["output"],
    )


def detect_windows(
    models: Iterable[str],
    *,
    gateway_base: str | None = None,
    gateway_token: str | None = None,
    timeout: float = 8.0,
) -> list[ContextWindow]:
    """Windows for every model in ``models`` that a server could answer for.

    One ``/model/info`` round-trip is shared by every ``gateway/`` alias; the
    direct lanes read their host directly. Order follows ``models``; models
    with nothing to report are simply absent.
    """
    from harness_lab_app.gateway_models import GATEWAY_PREFIX, fetch_gateway_routing

    wanted = [m for m in dict.fromkeys(models) if m]
    routing: dict[str, dict] = {}
    if any(m.startswith(GATEWAY_PREFIX) for m in wanted):
        routing = fetch_gateway_routing(gateway_base, gateway_token)
    windows: list[ContextWindow] = []
    for model in wanted:
        window = detect_window(model, routing=routing, timeout=timeout)
        if window is not None:
            windows.append(window)
    return windows


def limits_by_model(windows: Iterable[ContextWindow]) -> dict[str, dict[str, int]]:
    """``{model id: limit}`` in the shape the opencode overlay consumes."""
    return {window.model: window.limit for window in windows}


def describe(window: ContextWindow) -> str:
    """One line for the launch log."""
    return (
        f"{window.model} serves {window.served:,} tokens at {window.host} → "
        f"opencode limit.context={window.context:,}, limit.output={window.output:,}"
    )
