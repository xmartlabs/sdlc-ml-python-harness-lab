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

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def _canonical(model_id: str) -> str:
    """claude-haiku-4.5-oauth / claude-haiku-4-5-20251001 → claude-haiku-4-5."""
    bare = model_id.rsplit("/", 1)[-1].lower()
    bare = bare.removesuffix("-oauth").removesuffix("[1m]")
    bare = _DATE_SUFFIX.sub("", bare)
    return bare.replace(".", "-")


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
