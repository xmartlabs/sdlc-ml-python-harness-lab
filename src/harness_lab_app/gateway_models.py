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

import json
import os
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def build_model_catalog(
    live_ids: list[str],
    opencode_models: dict[str, dict],
    default_executor: str = DEFAULT_EXECUTOR_MODEL,
) -> dict:
    """Compose the ``/api/models`` payload for the run form.

    Prefers the live LiteLLM list (``live_ids``, bare ids); when the gateway is
    unreachable it falls back to the ``opencode.json`` routing map so the form
    always has options. Ids are returned ``gateway/``-prefixed (what the harness
    expects) with a human label when opencode.json provides one.
    """
    if live_ids:
        source = "gateway"
        bare_ids = live_ids
    else:
        source = "opencode.json"
        bare_ids = sorted(opencode_models.keys())
    models = [
        {"id": gateway_model_id(bid), "label": _label_for(bid, opencode_models)}
        for bid in bare_ids
    ]
    ordered = [m["id"] for m in models]
    if default_executor in ordered:
        default = default_executor
    else:
        default = ordered[0] if ordered else ""
    return {"source": source, "models": models, "default_executor": default}
