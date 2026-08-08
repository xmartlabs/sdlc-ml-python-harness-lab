"""Gitignored opencode configuration overlay for gateway model routing.

A model enabled in LiteLLM shows up in ``GET /api/models`` immediately, but
opencode resolves ``gateway/<id>`` against ``provider.gateway.models`` in the
git-tracked ``opencode.json`` — a map written only by ``make
sync-gateway-models``. The model therefore appears in the run form and then
fails at run time (ADR-0012 made that sync deliberately manual).

This module closes the gap without ever writing to the tracked tree. opencode
merges configuration from several sources, later ones winning::

    Remote → Global → OPENCODE_CONFIG → project opencode.json
           → .opencode/ → OPENCODE_CONFIG_CONTENT → Managed

Both env vars are set together rather than picking one. ``Dockerfile.runner``
pins ``OPENCODE_VERSION=latest``, so the runtime version is not knowable here;
the two degrade differently on an older build and setting both costs one small
gitignored file write instead of version probing.

Stdlib only: ``scripts/bench_run.py`` imports it from inside the runner
container, which has no web dependencies installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness_lab_app.provenance import hash_text

# Written by gateway_catalog_service from the DB; read here. Both live under
# data/app/, which is gitignored.
CATALOG_FILE = Path("data") / "app" / "gateway-models.json"
EFFECTIVE_CONFIG_FILE = Path("data") / "app" / "opencode-effective.json"

ENV_CONFIG_PATH = "OPENCODE_CONFIG"
ENV_CONFIG_CONTENT = "OPENCODE_CONFIG_CONTENT"


def read_tracked_gateway(repo_root: Path) -> dict[str, Any]:
    """The whole ``provider.gateway`` block from the tracked ``opencode.json``."""
    path = repo_root / "opencode.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    provider = data.get("provider")
    gateway = provider.get("gateway") if isinstance(provider, dict) else None
    return gateway if isinstance(gateway, dict) else {}


def read_catalog_models(repo_root: Path) -> dict[str, dict]:
    """Enabled models from the web catalog, keyed by bare LiteLLM id.

    Missing or unreadable file means "no overlay", never an error: a run must
    not fail because an optional convenience file is absent.
    """
    path = repo_root / CATALOG_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in models.items()
        if isinstance(value, dict)
    }


def compute_delta(
    tracked_models: dict[str, dict], catalog_models: dict[str, dict]
) -> dict[str, dict]:
    """Catalog entries the tracked map does not already express identically."""
    delta: dict[str, dict] = {}
    for model_id, entry in catalog_models.items():
        if tracked_models.get(model_id) != entry:
            delta[model_id] = entry
    return dict(sorted(delta.items()))


def overlay_hash(delta: dict[str, dict]) -> str | None:
    """Content hash of the delta, or ``None`` when there is nothing to overlay.

    ``None`` is what keeps historical arms intact: ``compute_condition_hash``
    only admits the key when it is truthy.
    """
    if not delta:
        return None
    return hash_text(json.dumps(delta, sort_keys=True, separators=(",", ":")))


def build_config(tracked_gateway: dict[str, Any], delta: dict[str, dict]) -> dict:
    """The full opencode config document the overlay contributes.

    The whole ``provider.gateway`` block is reproduced — ``npm``, ``name`` and
    ``options`` included — rather than only the changed models. opencode merges
    deeply, but reproducing the block means the overlay is still correct if a
    given version replaces at the provider level instead. The ``{env:...}``
    placeholders are copied verbatim; opencode resolves them itself.
    """
    gateway = {key: value for key, value in tracked_gateway.items() if key != "models"}
    merged = dict(tracked_gateway.get("models") or {})
    merged.update(delta)
    gateway["models"] = merged
    return {"provider": {"gateway": gateway}}


def build_overlay(repo_root: Path) -> tuple[dict[str, str], str | None]:
    """Materialize the overlay and return ``(env_vars, overlay_hash)``.

    Returns ``({}, None)`` when the catalog adds nothing to the tracked map, so
    a lab that never touches the Models page runs byte-identically to before.
    """
    tracked_gateway = read_tracked_gateway(repo_root)
    tracked_models = tracked_gateway.get("models") or {}
    delta = compute_delta(tracked_models, read_catalog_models(repo_root))
    if not delta:
        return {}, None

    config = build_config(tracked_gateway, delta)
    content = json.dumps(config, sort_keys=True, separators=(",", ":"))

    path = repo_root / EFFECTIVE_CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    return (
        {ENV_CONFIG_PATH: str(path), ENV_CONFIG_CONTENT: content},
        overlay_hash(delta),
    )


def routable_model_ids(repo_root: Path) -> set[str]:
    """Bare ids opencode can resolve: the tracked map plus the enabled overlay.

    The preflight check uses this to reject a ``gateway/<id>`` executor that
    would fail once the run is already burning steps.
    """
    tracked = read_tracked_gateway(repo_root).get("models") or {}
    return set(tracked) | set(read_catalog_models(repo_root))
