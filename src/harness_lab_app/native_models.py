"""The models each host CLI addresses natively, in one place (ADR-0069).

The run forms used to carry their own copy of this list, and it went stale twice:
for months it offered no current-generation Claude model, which is why most of the
archive ran on Haiku 4.5. One list here, served by ``GET /api/models/native``, so
every form shows the same thing and a test can hold it to the gateway.

``size_rank`` orders models smallest first within a generation — the order the
harness x model mode reads its interaction in (ADR-0066). ``on_gateway`` says
whether the configured LiteLLM gateway has a card that serves the model: a run
routed through the gateway to a model it does not serve fails at launch, so the
form says so first.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

# Current generation as of 2026-10 (Claude 5.5 family and Fable 5.1); previous
# models stay so new runs can be compared with the archive.
NATIVE_MODELS: dict[str, list[dict[str, Any]]] = {
    "claude": [
        {
            "id": "claude-haiku-5-5",
            "label": "Claude Haiku 5.5",
            "generation": "current",
            "size_rank": 1,
        },
        {
            "id": "claude-sonnet-5-5",
            "label": "Claude Sonnet 5.5",
            "generation": "current",
            "size_rank": 2,
        },
        {
            "id": "claude-opus-5-5",
            "label": "Claude Opus 5.5",
            "generation": "current",
            "size_rank": 3,
        },
        {
            "id": "claude-fable-5-1",
            "label": "Claude Fable 5.1",
            "generation": "current",
            "size_rank": 4,
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "label": "Claude Haiku 4.5",
            "generation": "previous",
            "size_rank": 1,
        },
        {
            "id": "claude-sonnet-5",
            "label": "Claude Sonnet 5",
            "generation": "previous",
            "size_rank": 2,
        },
        {
            "id": "claude-opus-4-8",
            "label": "Claude Opus 4.8",
            "generation": "previous",
            "size_rank": 3,
        },
    ],
    # Only the confirmed CLI default is listed — add your codex model ids here.
    "codex": [
        {
            "id": "codex-mini-latest",
            "label": "Codex Mini (latest)",
            "generation": "current",
            "size_rank": 1,
        },
    ],
}

# The oldest Claude Code CLI the API accepts for a model, where one is known.
# 2.1.280 is what the API itself answered for claude-opus-5-5 on 2026-10-23
# ("claude_code_version_too_old", runs walletsystem-concurrent-deposits-K-006/007);
# the 5.5 family and Fable 5.1 shipped together, so they share it as a floor.
MIN_CLAUDE_CLI_VERSION: dict[str, str] = {
    "claude-haiku-5-5": "2.1.280",
    "claude-sonnet-5-5": "2.1.280",
    "claude-opus-5-5": "2.1.280",
    "claude-fable-5-1": "2.1.280",
}

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def _canonical(model_id: str) -> str:
    """claude-haiku-4.5-oauth / claude-haiku-4-5-20251001 → claude-haiku-4-5."""
    bare = model_id.rsplit("/", 1)[-1].lower()
    bare = bare.removesuffix("-oauth").removesuffix("[1m]")
    bare = _DATE_SUFFIX.sub("", bare)
    return bare.replace(".", "-")


def min_claude_cli_version(model_id: str) -> str | None:
    """The oldest Claude Code CLI that can address ``model_id``, if known."""
    return MIN_CLAUDE_CLI_VERSION.get(_canonical(model_id))


def _version_tuple(version: str) -> tuple[int, ...] | None:
    parts = re.findall(r"\d+", version.split(" ", 1)[0])
    return tuple(int(part) for part in parts) if parts else None


def claude_cli_too_old(model_id: str, cli_version: str) -> str | None:
    """The required version when ``cli_version`` cannot run ``model_id``.

    ``None`` when the model has no known floor or the CLI version is unknown —
    an image built before it recorded its version is not assumed too old.
    """
    required = min_claude_cli_version(model_id)
    have = _version_tuple(cli_version or "")
    need = _version_tuple(required or "")
    if not required or have is None or need is None:
        return None
    return required if have < need else None


def gateway_coverage(
    harness: str, routing: Mapping[str, Any] | None
) -> dict[str, bool | None]:
    """Whether each native model has a gateway card that serves it.

    ``None`` for every model when there is no gateway to ask: unknown is not
    "missing".
    """
    models = NATIVE_MODELS.get(harness, [])
    if routing is None:
        return {model["id"]: None for model in models}
    served = {_canonical(name) for name in routing}
    return {model["id"]: _canonical(model["id"]) in served for model in models}


def served_claude_models(routing: Mapping[str, Any]) -> list[str]:
    """The native Claude ids the gateway serves, in catalog order."""
    coverage = gateway_coverage("claude", routing)
    return [model_id for model_id, served in coverage.items() if served]


def native_catalog(
    routing: Mapping[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Every harness's native models, each with its gateway coverage."""
    catalog: dict[str, list[dict[str, Any]]] = {}
    for harness, models in NATIVE_MODELS.items():
        coverage = gateway_coverage(harness, routing)
        catalog[harness] = [
            {**model, "on_gateway": coverage[model["id"]]} for model in models
        ]
    return catalog
