"""Shared plumbing for the direct-host lanes (LM Studio, Ollama).

"Direct" means the lab asks the machine that will serve the model which models
it has, instead of asking a gateway which aliases it exposes. The two backends
answer very different native APIs and have different rules about what "loaded"
means, so their semantics live in :mod:`harness_lab_app.lmstudio_direct` and
:mod:`harness_lab_app.ollama_direct`. What they genuinely share is here: an HTTP
GET, integer coercion, base-URL normalization and a TTL cache.

Stdlib only: ``scripts/bench_run.py`` imports the direct modules from inside the
runner container, which has no web dependencies installed.
"""

from __future__ import annotations

import json
import time
import urllib.request

# OpenAI-compatible and native API suffixes an operator may have pasted into a
# *_BASE_URL. Both are stripped to get back to the server root, so one variable
# serves endpoints that live side by side.
_KNOWN_SUFFIXES = ("/api/v0", "/api", "/v1")


class DirectHostUnreachable(RuntimeError):
    """The host did not answer — distinct from 'it answered, no such model'."""


def host_root(base_url: str) -> str:
    """Server root for ``base_url``, with any known API suffix removed.

    A ``*_BASE_URL`` is written for OpenAI clients (``http://host:1234/v1``) but
    the native APIs live beside that path, not under it. Accepting either
    spelling beats making the operator keep two variables in sync.
    """
    root = base_url.strip().rstrip("/")
    for suffix in _KNOWN_SUFFIXES:
        if root.endswith(suffix):
            return root[: -len(suffix)]
    return root


def strip_provider_prefix(model: str, prefix: str) -> str:
    """Remove one opencode provider prefix, keeping the model's own separators.

    ``lmstudio/prism-ml/bonsai-27b`` → ``prism-ml/bonsai-27b``. Exactly one
    prefix goes: opencode splits ``provider/model`` on the first slash, so
    everything after it is the id the server registered — including any further
    slash (LM Studio publishers) or colon (Ollama tags).
    """
    return model[len(prefix) :] if model.startswith(prefix) else model


def get_json(url: str, timeout: float, payload: dict | None = None) -> dict:
    """GET (or POST, with ``payload``) a JSON document."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read())


def as_int(value: object) -> int | None:
    """Coerce a JSON field to ``int``, or ``None`` when it is not a number."""
    if not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class TtlCache:
    """A tiny per-key, per-entry-TTL cache for host model listings.

    Per-entry rather than one global TTL because not all answers are equally
    trustworthy: a degraded listing (see the callers) has to expire in seconds
    while a full one can be held for the normal lifetime. Failures are never
    stored at all — a host coming back has to be visible on the next render, not
    a cache lifetime later.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, float, object]] = {}

    def get(self, key: str) -> object | None:
        entry = self._entries.get(key)
        if entry is None or time.monotonic() - entry[0] >= entry[1]:
            return None
        return entry[2]

    def set(self, key: str, value: object, ttl: float) -> None:
        self._entries[key] = (time.monotonic(), ttl, value)

    def clear(self) -> None:
        self._entries.clear()
