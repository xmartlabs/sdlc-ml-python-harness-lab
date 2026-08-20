"""Discovery and naming helpers for the LiteLLM / OpenAI-compatible gateway.

Shared by the audit script (``scripts/bench_audit.py``), the model-sync script
(``scripts/sync_gateway_models.py``) and the API model-list endpoint. Kept
dependency-free (stdlib only) so both the scripts and the FastAPI app can import
it without pulling in web-only dependencies.

Two naming conventions coexist:

- opencode addresses gateway models as ``gateway/<id>`` (provider prefix).
- LiteLLM's REST API (``/models``, ``/chat/completions``) wants the bare ``<id>``.

``list_gateway_models`` is the *discovery* source (what LiteLLM currently serves);
``opencode.json``'s ``provider.gateway.models`` map is the *routing* source (what
opencode can actually reach). ``scripts/sync_gateway_models.py`` reconciles the
second from the first so the gateway stays the single place to update.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_lab_app.direct_hosts import host_root

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# Cloudflare in front of the gateway bans the default urllib user-agent
# (HTTP 1010 browser_signature_banned). Override with a real browser UA.
GATEWAY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# The single provider prefix the opencode harness uses for gateway models.
GATEWAY_PREFIX = "gateway/"

# Preferred executor default for the run form (kept if the gateway serves it).
DEFAULT_EXECUTOR_MODEL = "gateway/lmstudio-qwen3.6-35b-build"

# How the run form groups and orders the model menu.
#
# The gateway serves ~40 aliases across five backends and the API used to hand
# them over in one flat, alphabetically-sorted list, which interleaved
# ``lmstudio-*``, ``ollama-*`` and the cloud ids into an undifferentiated wall.
# That is a correctness problem, not just a cosmetic one: LM Studio is the
# default local backend here, while the ``ollama-*`` routes point at a host that
# is usually down — picking one by mistake costs a whole run.
#
# Order is intentional and fixed: local first, cloud second, Ollama last.
# ``None`` is the catch-all bucket, so a backend nobody anticipated lands in
# Cloud rather than vanishing from the menu.
BACKEND_GROUPS: tuple[tuple[str | None, str, str], ...] = (
    ("lmstudio-", "lmstudio", "LM Studio (local)"),
    (None, "cloud", "Cloud"),
    ("ollama-", "ollama", "Ollama (rarely used)"),
)

# Finer classification for the run form's three-tier picker: source (gateway or
# direct) → provider → model.
#
# :data:`BACKEND_GROUPS` answers "which machine serves this", which is the right
# axis for ordering but a poor one for *finding* a model: every hosted vendor
# lands in one "Cloud" bucket, so a menu of ~40 aliases shows `gpt-*`,
# `claude-*`, `gemini-*` and `nim-*` interleaved under a single heading. These
# rules split that bucket by vendor. They are deliberately kept beside the
# backend table rather than replacing it: the Settings catalog and the sort
# order still key off backends, and one table changing shape should not move the
# other.
#
# Matching is by id prefix, the only signal LiteLLM's /models list carries.
# First match wins; an unmatched id falls into "other" so a vendor nobody
# anticipated is still reachable in the menu instead of disappearing from it.
PROVIDER_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("lmstudio-",), "lmstudio", "LM Studio"),
    (("ollama-",), "ollama", "Ollama"),
    (("gpt-", "codex-"), "openai", "OpenAI"),
    (("claude-", "bedrock-claude-"), "anthropic", "Claude"),
    (("gemini-",), "google", "Gemini"),
    (("nim-",), "nvidia", "NVIDIA"),
)
PROVIDER_FALLBACK = ("other", "Other")

# The value every gateway-routed option carries in its ``source`` field. Not to
# be confused with the catalog-level ``source`` ("gateway" | "opencode.json"),
# which records where the *list* came from rather than how a model is reached.
SOURCE_GATEWAY = "gateway"

# Smallest context window a model may have and still run this harness.
#
# Measured, not guessed: the system prompt alone (governance docs, 24 skill
# descriptions, 6 agent definitions and the tool schemas) is ~13.7k tokens
# before the first user turn. Run tetris-v1-K-020 proved what happens below
# that — LM Studio had auto-loaded the model with an 8k window and every one of
# the run's 4432 steps died instantly on
# ``n_keep: 13777 >= n_ctx: 8192``, burning 2.5 hours and a 12 MB log.
# 32k is the smallest ceiling that still leaves headroom for a multi-step run.
MIN_CONTEXT_TOKENS = 32768


def normalize_model_name(model: str) -> str:
    """Strip the opencode-only ``gateway/`` prefix so LiteLLM gets its real name.

    opencode addresses models as ``<provider>/<model>`` (e.g. ``gateway/gpt-5.5``),
    but LiteLLM's ``/chat/completions`` expects the registered name without that
    provider prefix (e.g. ``gpt-5.5``). Other prefixes (``openai/...``) are left
    untouched.
    """
    if model.startswith(GATEWAY_PREFIX):
        return model[len(GATEWAY_PREFIX) :]
    return model


def gateway_model_id(model: str) -> str:
    """Add the opencode ``gateway/`` prefix to a bare LiteLLM model id.

    Ids that already carry a provider prefix (``openai/...``, ``ollama/...``) are
    returned unchanged.
    """
    if "/" in model:
        return model
    return f"{GATEWAY_PREFIX}{model}"


def list_gateway_models(
    base_url: str | None = None, token: str | None = None
) -> list[str]:
    """Best-effort fetch of available model names (bare ids) from the gateway.

    When ``base_url``/``token`` are omitted they fall back to the ``GATEWAY_BASE_URL``
    / ``GATEWAY_TOKEN`` environment variables, so callers running inside the runner
    container (where the values are exported) work unchanged. Returns ``[]`` on any
    error or missing config; the caller decides the fallback.
    """
    base = (
        base_url if base_url is not None else os.environ.get("GATEWAY_BASE_URL", "")
    ).rstrip("/")
    tok = token if token is not None else os.environ.get("GATEWAY_TOKEN", "")
    if not base or not tok:
        return []
    req = urllib.request.Request(
        f"{base}/models",
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/json",
            "User-Agent": GATEWAY_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            data = json.loads(resp.read())
        return sorted(m["id"] for m in data.get("data", []) if m.get("id"))
    except Exception:
        return []


_CACHE_TTL_SECONDS = 30.0
_model_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


def list_gateway_models_cached(base_url: str, token: str) -> list[str]:
    """``list_gateway_models`` behind a short TTL, keyed by endpoint+token.

    The run form and the models page both hit ``/api/models``, and each call is
    a blocking HTTP round-trip to LiteLLM. Thirty seconds is short enough that
    "enable a model, press Refresh" still feels immediate. The pure function is
    left untouched so scripts and tests keep a cache-free path.
    """
    if not base_url or not token:
        return []
    key = (base_url, token)
    now = time.monotonic()
    cached = _model_cache.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    models = list_gateway_models(base_url=base_url, token=token)
    # Never cache an empty list: it usually means the gateway was briefly
    # unreachable, and caching that would make the Refresh button look broken.
    if models:
        _model_cache[key] = (now, models)
    return models


_routing_cache: dict[tuple[str, str], tuple[float, dict[str, dict]]] = {}


def fetch_gateway_routing_cached(base_url: str, token: str) -> dict[str, dict]:
    """``fetch_gateway_routing`` behind the same short TTL as the model list.

    ``/api/models`` is hit on every run-form render; the alias de-duplication it
    needs must not cost a second blocking round-trip each time.
    """
    if not base_url or not token:
        return {}
    key = (base_url, token)
    now = time.monotonic()
    cached = _routing_cache.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    routing = fetch_gateway_routing(base_url=base_url, token=token)
    if routing:
        _routing_cache[key] = (now, routing)
    return routing


def clear_model_cache() -> None:
    """Drop the TTL caches so the next read hits the gateway (Refresh button)."""
    _model_cache.clear()
    _routing_cache.clear()


# Substrings that mark a gateway 5xx as "the backend behind this model is down"
# (LiteLLM wraps backend connect failures as InternalServerError with these
# phrases). Deliberately conservative: anything else is treated as ambiguous.
_BACKEND_DOWN_MARKERS = (
    "connection error",
    "connection refused",
    "connectionerror",
    "econnrefused",
    "no route to host",
    "connect timeout",
)

# Substrings that mark the *name* as wrong rather than the backend as down: the
# alias resolved, reached the server, and the server does not have that model.
#
# Fail-open used to swallow these, and run tetris-v1-K-031 is what that cost: a
# LiteLLM alias forwarded ``qwen3.6:35b`` to LM Studio, the server answered
# ``Invalid model identifier``, the preflight read an unfamiliar 4xx as
# ambiguous and reported the configuration runnable. A named model that the
# backend rejects is not ambiguous — it is the one failure a preflight exists
# to catch, and no retry or warm-up will change the answer.
_MODEL_UNKNOWN_MARKERS = (
    "invalid model identifier",
    "model_not_found",
    "model not found",
    "invalid model name",
    "does not exist",
    "no such model",
    "unknown model",
)

# The completion budget opencode sends for every gateway model that declares no
# output limit — which today is all of them: the `gateway/*` entries in
# opencode.json carry only display names (docs/known-limitations.md). Observed
# in run instance_qutebrowser…-K-003. The probe must ask for what the run will
# ask for: probing with ``max_tokens: 1`` approved a request shape the run
# never sends, and a model whose completion cap is below opencode's default
# passed preflight green only to refuse every real request in two seconds.
OPENCODE_COMPLETION_BUDGET = 32000

# The provider validated the request against the model's completion cap and
# refused it — LiteLLM/OpenAI phrasing, from the same K-003 rejection. Not
# ambiguous: the number is fixed per model, so every request the run makes
# gets the same answer.
_COMPLETION_CAP_MARKERS = ("max_tokens is too large",)


def probe_gateway_model(
    model: str,
    *,
    base_url: str,
    token: str,
    timeout: float = 12.0,
    budget: int | None = None,
) -> str | None:
    """Cheap health probe for a gateway-routed model before a run is created.

    Sends one completion through the gateway **with the request shape the run
    will use** — same completion budget. ``budget`` is the model's declared
    ``limit.output`` when opencode.json states one, and ``OPENCODE_COMPLETION_BUDGET``
    (opencode's default for an undeclared model) when it does not.
    Returns a short error detail when the run is provably doomed: the backend
    serving the model is unreachable, or the request shape itself is refused
    (a run would burn every step on the same rejection). Returns ``None`` when
    healthy or ambiguous. Fail-open on ambiguity: a slow response may just be
    a JIT model load, and this probe must never block a runnable configuration.
    """
    base = base_url.rstrip("/")
    if not base or not token:
        return None
    max_tokens = budget or OPENCODE_COMPLETION_BUDGET
    body = json.dumps(
        {
            # LiteLLM registers bare ids; sending opencode's `gateway/` prefix
            # earns a 400 "Invalid model name", which this probe reads as
            # "not a backend outage" and lets through — i.e. it silently never
            # fired. Normalize, always.
            "model": normalize_model_name(model),
            "messages": [{"role": "user", "content": "ping"}],
            # The run's budget, not a token-count: generation still stops at
            # the model's short answer, but cap validation happens against the
            # number the run will actually send.
            "max_tokens": max_tokens,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": GATEWAY_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):  # nosec B310
            return None
    except urllib.error.HTTPError as exc:
        from harness_lab_app.lmstudio_direct import LMSTUDIO_GROUP_LABEL

        payload = ""
        with contextlib.suppress(Exception):
            payload = exc.read().decode(errors="replace")
        lowered = payload.lower()
        if exc.code >= 500 and any(m in lowered for m in _BACKEND_DOWN_MARKERS):
            return (
                f"the gateway reported a connection error reaching the backend "
                f"that serves this model (HTTP {exc.code}). This usually means "
                f"the self-hosted server behind it (LM Studio / Ollama / NIM) "
                f"is off — start it, or pick another model"
            )
        if any(m in lowered for m in _MODEL_UNKNOWN_MARKERS):
            snippet = " ".join(payload.split())[:200]
            return (
                f"the backend rejected the model name this alias forwards "
                f"(HTTP {exc.code}): {snippet}. The LiteLLM alias points at an "
                f"id the server does not serve — fix the mapping in the "
                f"gateway, or pick the model from the "
                f"'{LMSTUDIO_GROUP_LABEL}' group, which addresses the host "
                f"directly and cannot be renamed in transit"
            )
        if any(m in lowered for m in _COMPLETION_CAP_MARKERS):
            snippet = " ".join(payload.split())[:200]
            return (
                f"the model refuses the completion budget every request of "
                f"the run will carry (max_tokens={max_tokens}): {snippet}. "
                f"opencode sends that budget for any model that declares no "
                f"output limit — cap max_tokens for this model at the gateway, "
                f"or declare `limit.output` for it in opencode.json"
            )
        return None  # auth/validation/etc — other layers surface those
    except Exception:
        # Gateway unreachable or probe timed out (possibly a model still
        # loading) — ambiguous, let the run proceed.
        return None


# -- context preflight -------------------------------------------------------

# llama.cpp / LM Studio refuse an oversized prompt *before* evaluating it and
# name both numbers, which is what makes the loaded window observable at all:
#   "The number of tokens to keep from the initial prompt is greater than the
#    context length (n_keep: 13777 >= n_ctx: 8192)"
_N_CTX_RE = re.compile(r"n_ctx:\s*(\d+)")
# OpenAI-shaped backends phrase the same refusal with their own limit.
_MAX_CONTEXT_RE = re.compile(r"maximum context length is (\d+)")
_CONTEXT_MARKERS = (
    "n_ctx",
    "context length",
    "context_length_exceeded",
    "context window",
    "too many tokens",
)

# A backend that got as far as complaining about the *output* budget accepted
# the input, which is the only thing this probe asks about. Reasoning models
# refuse a tiny max_tokens outright, so this is a pass, not a failure.
_OUTPUT_LIMIT_MARKERS = (
    "max_tokens or model output limit",
    "output limit was reached",
)

# Enough output budget that a reasoning model does not reject the request
# before reading the prompt, still small enough to cost nothing.
_PROBE_MAX_TOKENS = 64

# A word list rather than one repeated token: repetitive text compresses to
# far fewer tokens than natural prose, which would make the probe undershoot
# the window it claims to have proved.
_FILLER = (
    "the quick brown fox jumps over a lazy dog while seventeen bright copper "
    "kettles hum beside the harbour and every navigator counts another mile "
    "before dawn breaks over the quiet valley below "
)


@dataclass(frozen=True)
class ContextProbeResult:
    """What a context preflight learned about one model's usable window."""

    model: str
    status: str  # "ok" | "insufficient" | "unknown"
    required_tokens: int
    context_tokens: int | None
    prompt_tokens: int | None
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "status": self.status,
            "ok": self.ok,
            "required_tokens": self.required_tokens,
            "context_tokens": self.context_tokens,
            "prompt_tokens": self.prompt_tokens,
            "detail": self.detail,
        }


# Starting guess only. Real tokenizers pack this filler at ~5.5 chars/token,
# and the ratio differs per model, so the probe measures it and corrects rather
# than trusting the constant.
_CHARS_PER_TOKEN = 4.5


def _filler_of(n_chars: int) -> str:
    """Filler text of exactly ``n_chars`` characters."""
    return (_FILLER * (max(1, n_chars // len(_FILLER)) + 1))[:n_chars]


def probe_model_context(
    model: str,
    *,
    base_url: str,
    token: str,
    required_tokens: int = MIN_CONTEXT_TOKENS,
    timeout: float = 120.0,
) -> ContextProbeResult:
    """Prove a model can actually hold ``required_tokens`` of prompt.

    LiteLLM's declared ``max_input_tokens`` is configuration, not truth: LM
    Studio serves whatever window the model was *loaded* with, and a JIT
    auto-load silently uses the model's small default. The only honest check is
    to send a prompt of the required size and see whether the backend takes it.

    A prompt that is refused for being too long is a definitive answer and the
    error usually names the real window, so ``insufficient`` carries the number
    to fix. Anything else — gateway down, timeout, a backend still loading — is
    ``unknown`` and must never block a run: same fail-open contract as
    :func:`probe_gateway_model`.
    """
    base = base_url.rstrip("/")
    if not base or not token:
        return ContextProbeResult(
            model=model,
            status="unknown",
            required_tokens=required_tokens,
            context_tokens=None,
            prompt_tokens=None,
            detail="Gateway base URL and token are both required.",
        )
    # Overshoot slightly so a pass proves *at least* the requirement, never a
    # window that merely rounds up to it.
    n_chars = int(required_tokens * _CHARS_PER_TOKEN * 1.02) + 128
    for attempt in (0, 1):
        accepted, payload, raw, code = _send_context_probe(
            model, base, token, n_chars, timeout
        )
        if not accepted:
            lowered = raw.lower()
            if any(m in lowered for m in _OUTPUT_LIMIT_MARKERS) and not any(
                m in lowered for m in _CONTEXT_MARKERS
            ):
                return ContextProbeResult(
                    model=model,
                    status="ok",
                    required_tokens=required_tokens,
                    context_tokens=None,
                    prompt_tokens=None,
                    detail=(
                        f"Accepted a prompt of about {required_tokens} tokens "
                        "(the backend only objected to the output budget)."
                    ),
                )
            return _classify_context_error(model, required_tokens, raw, code)

        prompt_tokens = None
        with contextlib.suppress(Exception):
            prompt_tokens = int((payload or {})["usage"]["prompt_tokens"])
        if prompt_tokens is None or prompt_tokens >= required_tokens:
            return ContextProbeResult(
                model=model,
                status="ok",
                required_tokens=required_tokens,
                context_tokens=prompt_tokens,
                prompt_tokens=prompt_tokens,
                detail=(
                    f"Accepted a {prompt_tokens}-token prompt "
                    f"(needs {required_tokens})."
                    if prompt_tokens
                    else f"Accepted a prompt of about {required_tokens} tokens."
                ),
            )
        if attempt == 0:
            # The filler tokenized denser than estimated, so the call proved
            # less than the requirement. Correct the ratio from what was
            # actually measured and go once more rather than reporting a
            # shortfall the user cannot act on.
            n_chars = int(n_chars * (required_tokens / prompt_tokens) * 1.05) + 128
            continue
        return ContextProbeResult(
            model=model,
            status="unknown",
            required_tokens=required_tokens,
            context_tokens=None,
            prompt_tokens=prompt_tokens,
            detail=(
                f"The probe only reached {prompt_tokens} tokens, short of the "
                f"{required_tokens} required — inconclusive, retry."
            ),
        )
    raise AssertionError("unreachable")  # pragma: no cover


def _send_context_probe(
    model: str, base: str, token: str, n_chars: int, timeout: float
) -> tuple[bool, dict | None, str, int | None]:
    """One probe request → ``(accepted, body, raw_error, http_code)``."""
    body = json.dumps(
        {
            "model": normalize_model_name(model),
            "messages": [{"role": "user", "content": _filler_of(n_chars)}],
            "max_tokens": _PROBE_MAX_TOKENS,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": GATEWAY_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return True, json.loads(resp.read()), "", None
    except urllib.error.HTTPError as exc:
        raw = ""
        with contextlib.suppress(Exception):
            raw = exc.read().decode(errors="replace")
        return False, None, raw, exc.code
    except Exception as exc:
        # LiteLLM surfaces backend refusals as a 5xx with the message in the
        # body, but a socket-level failure lands here with nothing to read.
        return False, None, str(exc), None


def _classify_context_error(
    model: str, required_tokens: int, raw: str, code: int | None
) -> ContextProbeResult:
    """Turn a failed probe into ``insufficient`` only when provably so."""
    lowered = raw.lower()
    if not any(marker in lowered for marker in _CONTEXT_MARKERS):
        return ContextProbeResult(
            model=model,
            status="unknown",
            required_tokens=required_tokens,
            context_tokens=None,
            prompt_tokens=None,
            detail=(
                f"Probe did not complete "
                f"({'HTTP ' + str(code) if code else 'no response'}): "
                f"{raw.strip()[:200] or 'no detail'}"
            ),
        )
    match = _N_CTX_RE.search(raw) or _MAX_CONTEXT_RE.search(raw)
    window = int(match.group(1)) if match else None
    if window is not None:
        detail = (
            f"Context window is {window} tokens, below the {required_tokens} "
            f"this harness needs. Reload the model with a larger context."
        )
    else:
        detail = (
            f"The backend refused a {required_tokens}-token prompt as too long: "
            f"{raw.strip()[:200]}"
        )
    return ContextProbeResult(
        model=model,
        status="insufficient",
        required_tokens=required_tokens,
        context_tokens=window,
        prompt_tokens=None,
        detail=detail,
    )


_CONTEXT_CACHE_TTL_SECONDS = 600.0
_context_cache: dict[tuple[str, str, int], tuple[float, ContextProbeResult]] = {}


def probe_model_context_cached(
    model: str,
    *,
    base_url: str,
    token: str,
    required_tokens: int = MIN_CONTEXT_TOKENS,
    timeout: float = 120.0,
) -> ContextProbeResult:
    """``probe_model_context`` behind a TTL so run creation stays responsive.

    A passing probe costs one full prompt evaluation (seconds on a local
    model), which is fine once but not on every run the user submits. Only
    conclusive results are cached: an ``unknown`` is usually a transient, and
    caching it would hide the answer for ten minutes.
    """
    key = (base_url, normalize_model_name(model), required_tokens)
    now = time.monotonic()
    cached = _context_cache.get(key)
    if cached is not None and now - cached[0] < _CONTEXT_CACHE_TTL_SECONDS:
        return cached[1]
    result = probe_model_context(
        model,
        base_url=base_url,
        token=token,
        required_tokens=required_tokens,
        timeout=timeout,
    )
    if result.status != "unknown":
        _context_cache[key] = (now, result)
    return result


def clear_context_cache() -> None:
    """Drop memoized probes so the next check re-measures (Recheck button)."""
    _context_cache.clear()


# -- backend liveness --------------------------------------------------------

# LiteLLM prefixes the upstream id with the provider it speaks. Only the
# self-hosted ones are listed: a card whose target carries one of these is
# served by a machine on this LAN, which is the only case the direct lane can
# answer for. A cloud target (``openai/gpt-4o``) never matches, so its api_base
# being empty stays what it is — normal — instead of reading as a broken card.
SELF_HOSTED_PREFIXES = ("lm_studio/", "ollama_chat/", "ollama/")


@dataclass(frozen=True)
class DirectHostProbe:
    """What the lab just learned by asking a serving host itself.

    The direct lane already queries LM Studio and Ollama on every
    ``/api/models`` render, so a gateway alias that forwards to one of those
    same hosts inherits the answer for free: same machine, same models, no
    second round-trip.

    ``model_ids`` are the ids that host registered, in its own spelling
    (``qwen/qwen3.6-35b-a3b``, ``qwen3.6:27b``) — the same spelling LiteLLM
    carries after its provider prefix, which is what makes the join possible.
    """

    root: str
    label: str
    reachable: bool
    model_ids: frozenset[str] = frozenset()
    error: str | None = None


def strip_backend_prefix(target: str) -> str:
    """``lm_studio/qwen/qwen3.6-35b-a3b`` -> ``qwen/qwen3.6-35b-a3b``.

    Exactly one prefix goes. LM Studio publishers are part of the id, so
    ``lm_studio/openai/gpt-oss-20b`` must keep its second ``openai/``: that is
    the publisher, not a provider.
    """
    for prefix in SELF_HOSTED_PREFIXES:
        if target.startswith(prefix):
            return target[len(prefix) :]
    return target


def backend_unavailable_reason(
    entry: dict, probes: Sequence[DirectHostProbe]
) -> str | None:
    """Why a gateway alias cannot answer right now, or ``None``.

    ``routable`` answers a different question — whether this harness knows how
    to *address* the id — and a card can be perfectly routable and still be
    pointed at a machine that is powered off. That gap is the whole reason this
    exists: the run form used to list ``lmstudio-qwen3.6-35b`` as launchable
    while every request to it died on a connection error, and the operator only
    found out after burning a run.

    Fail-open, deliberately. Anything this cannot decide — a cloud card, a host
    the lab does not probe, an alias LiteLLM would not describe — returns
    ``None`` and keeps its place in the menu. A wrong "unavailable" hides a
    model that works; a missing one costs a preflight message that
    :func:`probe_gateway_model` still emits at launch.
    """
    target = str(entry.get("target") or "")
    if not target.startswith(SELF_HOSTED_PREFIXES):
        return None
    api_base = str(entry.get("api_base") or "")
    if not api_base:
        return (
            "the gateway card declares no api_base, so LiteLLM would send the "
            "request to its own default host — set the card's api_base "
            "environment variable in the gateway"
        )
    root = host_root(api_base)
    probe = next((p for p in probes if p.root == root), None)
    if probe is None:
        return None
    if not probe.reachable:
        detail = f" ({probe.error})" if probe.error else ""
        return f"the {probe.label} host at {root} is not answering{detail}"
    bare = strip_backend_prefix(target)
    if probe.model_ids and bare not in probe.model_ids:
        return (
            f"{probe.label} at {root} does not serve `{bare}` — the gateway "
            f"alias points at an id that host does not have"
        )
    return None


# -- alias de-duplication ----------------------------------------------------


# Keys LiteLLM reports on every entry that say nothing about how the model
# answers. Everything else in ``litellm_params`` — temperature, penalties,
# max_tokens, timeouts — changes the served behaviour and therefore separates
# two aliases into two experimental arms.
_NON_BEHAVIOURAL_PARAMS = frozenset(
    {
        "merge_reasoning_content_in_choices",
        "use_in_pass_through",
        "use_litellm_proxy",
        "use_xai_oauth",
        "model",
        "api_base",
    }
)


def fetch_gateway_routing(
    base_url: str | None = None, token: str | None = None
) -> dict[str, dict]:
    """Per-alias routing facts from LiteLLM's ``/model/info``.

    Two aliases that resolve to the same upstream model, on the same backend,
    with the same sampling and serving parameters are the same served model
    wearing two names — indistinguishable at run time, so they are one
    experimental arm, not two. Returns ``{}`` on any error: the catalog must
    degrade to "show every alias", never to an exception.
    """
    base = (
        base_url if base_url is not None else os.environ.get("GATEWAY_BASE_URL", "")
    ).rstrip("/")
    tok = token if token is not None else os.environ.get("GATEWAY_TOKEN", "")
    if not base or not tok:
        return {}
    req = urllib.request.Request(
        f"{base}/model/info",
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/json",
            "User-Agent": GATEWAY_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            data = json.loads(resp.read())
    except Exception:
        return {}
    routing: dict[str, dict] = {}
    for entry in data.get("data", []):
        alias = entry.get("model_name")
        if not alias:
            continue
        params = entry.get("litellm_params") or {}
        info = entry.get("model_info") or {}
        routing[str(alias)] = {
            "target": params.get("model"),
            "api_base": params.get("api_base"),
            "max_input_tokens": info.get("max_input_tokens"),
            "params": {
                k: v
                for k, v in params.items()
                if k not in _NON_BEHAVIOURAL_PARAMS and v is not None
            },
        }
    return routing


def routing_key(entry: dict) -> str | None:
    """Identity of the *served* model behind an alias, or ``None`` if unknown.

    Target and backend are not enough. ``lmstudio-qwen3.6-35b-build`` and
    ``lmstudio-qwen3.6-35b`` point at the same LM Studio model but ship
    different sampling (temperature 0.2 with penalties and a 4096-token output
    cap, against a bare 0.7), so they are two arms and merging them would
    silently pool two conditions into one row.
    """
    target = entry.get("target")
    if not target:
        return None
    params = json.dumps(entry.get("params") or {}, sort_keys=True, default=str)
    return f"{target}@{entry.get('api_base') or 'default'}#{params}"


def group_aliases(
    bare_ids: list[str],
    routing: dict[str, dict],
    preferred: list[str] | None = None,
) -> list[tuple[str, list[str]]]:
    """Collapse aliases of one served model into ``(canonical, aliases)`` pairs.

    Order of ``bare_ids`` is preserved by first appearance. An alias the gateway
    could not describe keeps its own group, so an unreachable ``/model/info``
    degrades to today's flat list rather than merging unrelated models.

    ``preferred`` is a priority-ordered list of ids that should win the naming
    of their group.
    """
    preferred = preferred or []
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for bare in bare_ids:
        key = routing_key(routing.get(bare, {})) or f"__alias__{bare}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(bare)
    collapsed = []
    for key in order:
        members = groups[key]
        canonical = _choose_canonical(members, preferred)
        collapsed.append((canonical, [m for m in members if m != canonical]))
    return collapsed


def _choose_canonical(members: list[str], preferred: list[str]) -> str:
    """Pick the name a merged group is shown under.

    A preferred id (the configured default first, then anything the harness can
    already route) wins so merging never silently repoints an existing arm;
    otherwise the shortest name, which is the one without the hand-added
    suffix.
    """
    return min(
        (preferred.index(m) if m in preferred else len(preferred), len(m), m)
        for m in members
    )[2]


def read_opencode_gateway_models(repo_root: Path) -> dict[str, dict]:
    """Return the ``provider.gateway.models`` map from ``opencode.json`` (or ``{}``).

    Used both as the offline fallback for the API model list and as the label
    source for the sync script (so regenerating the block preserves human names).
    """
    path = repo_root / "opencode.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    provider = data.get("provider", {})
    gateway = provider.get("gateway", {}) if isinstance(provider, dict) else {}
    models = gateway.get("models", {}) if isinstance(gateway, dict) else {}
    return models if isinstance(models, dict) else {}


def _label_for(bare_id: str, opencode_models: dict[str, dict]) -> str:
    """Human label from opencode.json, falling back to the raw id."""
    entry = opencode_models.get(bare_id)
    if isinstance(entry, dict) and entry.get("name"):
        return str(entry["name"])
    return bare_id


def classify_backend(bare_id: str) -> tuple[int, str, str]:
    """Map a bare gateway id to its ``(rank, backend, group_label)``.

    ``rank`` is the position of the backend in :data:`BACKEND_GROUPS` and is what
    orders the run form's menu. Matching is by id prefix because that is the only
    signal the ``/models`` list carries — LiteLLM does not report which backend
    serves an alias.
    """
    fallback = next(
        (i, backend, label)
        for i, (prefix, backend, label) in enumerate(BACKEND_GROUPS)
        if prefix is None
    )
    for index, (prefix, backend, label) in enumerate(BACKEND_GROUPS):
        if prefix is not None and bare_id.startswith(prefix):
            return index, backend, label
    return fallback


def classify_provider(bare_id: str) -> tuple[str, str]:
    """Map a bare gateway id to its ``(provider, provider_label)``.

    The vendor tier of the run form's picker. See :data:`PROVIDER_RULES` for why
    this is separate from :func:`classify_backend`.
    """
    for prefixes, provider, label in PROVIDER_RULES:
        if bare_id.startswith(prefixes):
            return provider, label
    return PROVIDER_FALLBACK


def build_model_catalog(
    live_ids: list[str],
    opencode_models: dict[str, dict],
    default_executor: str = DEFAULT_EXECUTOR_MODEL,
    overlay: dict[str, dict] | None = None,
    routing: dict[str, dict] | None = None,
    probes: Sequence[DirectHostProbe] | None = None,
) -> dict:
    """Compose the ``/api/models`` payload for the run form.

    Prefers the live LiteLLM list (``live_ids``, bare ids); when the gateway is
    unreachable it falls back to the ``opencode.json`` routing map so the form
    always has options. Ids are returned ``gateway/``-prefixed (what the harness
    expects) with a human label when opencode.json provides one.

    ``overlay`` is the enabled slice of the web model catalog. It contributes
    labels and, more importantly, the ``routable`` flag: a model the gateway
    serves but neither source can route would appear in the dropdown and then
    fail at run time, which is exactly the trap this catalog exists to close.

    ``routing`` (from :func:`fetch_gateway_routing`) collapses aliases that
    resolve to the same served model into a single option, listing the
    swallowed names under ``aliases``. Without it the list degrades to one row
    per alias, which is correct but noisy.

    ``probes`` are the direct lane's readings of the LAN hosts (see
    :class:`DirectHostProbe`). They answer the second half of "can I run this":
    ``routable`` says the id can be addressed, ``reachable`` says the machine
    behind it is up and serving that model. A card can be routable and dead —
    the case this catalog exists to stop shipping to the run form.

    Every model carries the ``backend``/``group``/``group_rank`` triple from
    :func:`classify_backend`, and the list is returned pre-sorted in that order
    (see :data:`BACKEND_GROUPS`) so every consumer renders the same hierarchy
    without re-deriving it. Alongside it each option carries
    ``source``/``provider``/``provider_label`` for the run form's three-tier
    picker (see :data:`PROVIDER_RULES`).
    """
    overlay = overlay or {}
    probes = probes or []
    routing = routing or {}
    if live_ids:
        source = "gateway"
        bare_ids = live_ids
    else:
        source = "opencode.json"
        bare_ids = sorted(set(opencode_models) | set(overlay))
    labels = {**opencode_models, **overlay}
    default_bare = normalize_model_name(default_executor)
    preferred = [default_bare, *sorted(set(opencode_models) | set(overlay))]
    grouped = (
        group_aliases(bare_ids, routing, preferred)
        if routing
        else [(bid, []) for bid in bare_ids]
    )
    # Aliases in one group share a routing key, hence one api_base and one
    # upstream id, so the canonical member's verdict is the group's verdict.
    unavailable = {
        canonical: backend_unavailable_reason(routing.get(canonical, {}), probes)
        for canonical, _ in grouped
    }
    models = [
        {
            "id": gateway_model_id(canonical),
            "label": _label_for(canonical, labels),
            "routable": any(
                bid in opencode_models or bid in overlay
                for bid in (canonical, *aliases)
            ),
            # None where nothing could be decided (cloud card, unprobed host):
            # the menu shows it normally rather than guessing at it.
            "reachable": None if unavailable[canonical] is None else False,
            "unreachable_reason": unavailable[canonical],
            "aliases": [gateway_model_id(alias) for alias in aliases],
            "backend": backend,
            "group": group_label,
            "group_rank": rank,
            # Picker tiers: everything here is reached through the gateway.
            "source": SOURCE_GATEWAY,
            "provider": provider,
            "provider_label": provider_label,
        }
        for canonical, aliases in grouped
        for rank, backend, group_label in (classify_backend(canonical),)
        for provider, provider_label in (classify_provider(canonical),)
    ]
    # Backend, then launchable-before-not, then curated-before-raw, then label.
    #
    # The third key is what makes the menu readable. A model somebody named
    # ("Qwen3.6 35B build (local, capped)") is one this lab deliberately runs;
    # one still carrying its bare id ("lmstudio-bonsai-27b") was merely adopted
    # wholesale from the gateway. Sorting on the label alone interleaves them —
    # and because "l" sorts before "Q", it pushed every curated model, the
    # default executor included, to the bottom of its own group.
    #
    # ``routable`` and ``reachable`` stay ahead of it: a model that would fail
    # at launch belongs below one that works, however nicely it is named — an
    # unaddressable id first, then one whose serving machine is down. Labels
    # compare case-insensitively so capitalisation does not outrank a bare id.
    models.sort(
        key=lambda m: (
            m["group_rank"],
            not m["routable"],
            m["reachable"] is False,
            m["label"] == normalize_model_name(str(m["id"])),
            str(m["label"]).lower(),
        )
    )
    ordered = [str(m["id"]) for m in models]
    default = default_executor if default_executor in ordered else ""
    if not default and ordered:
        default = ordered[0]
    return {"source": source, "models": models, "default_executor": default}
