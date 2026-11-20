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

# Reasoning effort levels the Claude Code CLI accepts through ``--effort``
# (ADR-0081), lowest first. Effort trades thoroughness against token spend on
# one model; empty means "the CLI's own default", which is what every run made
# before the selector existed got. A model's ``effort_levels`` says which of
# these it accepts: an empty list means the model has no effort control and the
# selector is not offered for it.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# Current generation as of 2026-11 (Claude 5.5 family and Fable 5.2); previous
# models stay so new runs can be compared with the archive.
NATIVE_MODELS: dict[str, list[dict[str, Any]]] = {
    "claude": [
        {
            "id": "claude-haiku-5-5",
            "label": "Claude Haiku 5.5",
            "generation": "current",
            "size_rank": 1,
            "effort_levels": list(EFFORT_LEVELS),
        },
        {
            "id": "claude-sonnet-5-5",
            "label": "Claude Sonnet 5.5",
            "generation": "current",
            "size_rank": 2,
            "effort_levels": list(EFFORT_LEVELS),
        },
        {
            "id": "claude-opus-5-5",
            "label": "Claude Opus 5.5",
            "generation": "current",
            "size_rank": 3,
            "effort_levels": list(EFFORT_LEVELS),
        },
        {
            "id": "claude-fable-5-2",
            "label": "Claude Fable 5.2",
            "generation": "current",
            "size_rank": 4,
            "effort_levels": list(EFFORT_LEVELS),
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "label": "Claude Haiku 4.5",
            "generation": "previous",
            "size_rank": 1,
            "effort_levels": [],
        },
        {
            "id": "claude-sonnet-5",
            "label": "Claude Sonnet 5",
            "generation": "previous",
            "size_rank": 2,
            "effort_levels": list(EFFORT_LEVELS),
        },
        {
            "id": "claude-opus-4-8",
            "label": "Claude Opus 4.8",
            "generation": "previous",
            "size_rank": 3,
            "effort_levels": list(EFFORT_LEVELS),
        },
        {
            "id": "claude-fable-5-1",
            "label": "Claude Fable 5.1",
            "generation": "previous",
            "size_rank": 4,
            "effort_levels": list(EFFORT_LEVELS),
        },
    ],
    # Codex CLI (ChatGPT sign-in). The ids are the `visibility: list` entries of
    # the catalog the pinned binary ships, read with `codex debug models
    # --bundled` on codex-cli 0.171.0 (2026-11-20). That proves the CLI knows
    # the slug, not that a given ChatGPT plan grants it: the run form defaults
    # to the CLI's own choice, and an id the plan refuses fails with the CLI's
    # message. `codex-mini-latest`, the old placeholder, is not in that catalog.
    # Effort is not offered (ADR-0081 scopes it to claude).
    "codex": [
        {
            "id": "gpt-6.2-sol",
            "label": "GPT-6.2-Sol",
            "generation": "current",
            "size_rank": 3,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
        {
            "id": "gpt-6.2-astra",
            "label": "GPT-6.2-Astra",
            "generation": "current",
            "size_rank": 2,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
        {
            "id": "gpt-6.1-terra",
            "label": "GPT-6.1 Terra",
            "generation": "current",
            "size_rank": 1,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
        {
            "id": "gpt-6.1-sol",
            "label": "GPT-6.1-Sol",
            "generation": "previous",
            "size_rank": 3,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
        {
            "id": "gpt-6-sol",
            "label": "GPT-6-Sol",
            "generation": "previous",
            "size_rank": 3,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
        {
            "id": "gpt-6-astra",
            "label": "GPT-6-Astra",
            "generation": "previous",
            "size_rank": 2,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
        {
            "id": "gpt-6-luna",
            "label": "GPT-6-Luna",
            "generation": "previous",
            "size_rank": 1,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
        {
            "id": "gpt-5.5",
            "label": "GPT-5.5",
            "generation": "previous",
            "size_rank": 2,
            "effort_levels": [],
            "source": "codex-cli 0.171.0 bundled catalog",
        },
    ],
    # Gemini CLI (Sign in with Google). The ids are what `gemini --list-models`
    # prints offline on gemini-cli 0.70.0 (2026-11-20). `auto` lets the CLI
    # route between Pro and Flash per request, so the effective model is read
    # back from the run's own `result.stats.models` (ADR-0073).
    "gemini": [
        {
            "id": "auto",
            "label": "Auto (CLI routes Pro/Flash)",
            "generation": "current",
            "size_rank": 2,
            "effort_levels": [],
            "source": "gemini-cli 0.70.0 --list-models",
        },
        {
            "id": "gemini-2.5-pro",
            "label": "Gemini 2.5 Pro",
            "generation": "current",
            "size_rank": 3,
            "effort_levels": [],
            "source": "gemini-cli 0.70.0 --list-models",
        },
        {
            "id": "gemini-3.5-flash",
            "label": "Gemini 3.5 Flash",
            "generation": "current",
            "size_rank": 2,
            "effort_levels": [],
            "source": "gemini-cli 0.70.0 --list-models",
        },
        {
            "id": "gemini-3.1-flash-lite",
            "label": "Gemini 3.1 Flash-Lite",
            "generation": "current",
            "size_rank": 1,
            "effort_levels": [],
            "source": "gemini-cli 0.70.0 --list-models",
        },
    ],
}

# The CLI versions the codex/gemini integration was verified against (flags,
# event schema, skill discovery, auth storage). An older CLI is refused at
# preflight: `codex --full-auto` is how a schema change looks when it is not.
MIN_NATIVE_CLI_VERSION: dict[str, str] = {
    "codex": "0.171.0",
    "gemini": "0.70.0",
}

# The image label each runner CLI's pinned version is recorded under
# (Dockerfile.runner), read by the worker without starting a container.
CLI_VERSION_LABELS: dict[str, str] = {
    "claude": "claude-code.version",
    "codex": "codex.version",
    "gemini": "gemini-cli.version",
}


def native_cli_too_old(harness: str, cli_version: str) -> str | None:
    """The required version when a codex/gemini CLI is older than verified.

    ``None`` when the version is unknown (not assumed too old) or new enough.
    """
    required = MIN_NATIVE_CLI_VERSION.get(harness)
    have = _version_tuple(cli_version or "")
    need = _version_tuple(required or "")
    if not required or have is None or need is None:
        return None
    return required if have < need else None


# The oldest Claude Code CLI the API accepts for a model, where one is known.
# 2.1.280 is what the API itself answered for claude-opus-5-5 on 2026-10-23
# ("claude_code_version_too_old", runs walletsystem-concurrent-deposits-K-006/007);
# the 5.5 family and Fable 5.1 shipped together, so they share it as a floor.
# Fable 5.2 is refused by the CLI itself, before any request: 2.1.309 answers
# `unrecognized_model` (probed 2026-11-15). 2.1.321 is the release whose
# changelog adds it.
MIN_CLAUDE_CLI_VERSION: dict[str, str] = {
    "claude-haiku-5-5": "2.1.280",
    "claude-sonnet-5-5": "2.1.280",
    "claude-opus-5-5": "2.1.280",
    "claude-fable-5-1": "2.1.280",
    "claude-fable-5-2": "2.1.321",
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


def effort_levels(harness: str, model_id: str | None) -> list[str] | None:
    """The effort levels ``model_id`` accepts under ``harness``.

    ``None`` when the model is not in the catalog: an id the catalog does not
    know (a newer release typed by hand) is not refused, the CLI decides. An
    empty list means the model is known and has no effort control.
    """
    wanted = _canonical(model_id or "")
    for model in NATIVE_MODELS.get(harness, []):
        if _canonical(model["id"]) == wanted:
            return list(model.get("effort_levels") or [])
    return None


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
    *,
    claude_via_gateway: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Every harness's native models, each with its gateway coverage.

    ``claude_via_gateway`` is False when the claude CLI talks to Anthropic
    directly; its models then get ``None`` — the gateway is not on its path.
    """
    catalog: dict[str, list[dict[str, Any]]] = {}
    for harness, models in NATIVE_MODELS.items():
        # codex and gemini always talk to their own provider on the operator's
        # subscription; a gateway card says nothing about whether they can run.
        uses_gateway = harness != "claude" or claude_via_gateway
        if harness in MIN_NATIVE_CLI_VERSION:
            uses_gateway = False
        coverage = gateway_coverage(harness, routing if uses_gateway else None)
        catalog[harness] = [
            {**model, "on_gateway": coverage[model["id"]]} for model in models
        ]
    return catalog
