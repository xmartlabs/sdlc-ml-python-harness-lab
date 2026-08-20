#!/usr/bin/env python3
"""Reconcile opencode.json's gateway models from the live LiteLLM gateway.

The gateway (LiteLLM :4000) is meant to be the single place the user updates when
adding or removing models. opencode, however, only routes models declared in
``opencode.json``'s ``provider.gateway.models`` map. This script closes that gap:
it fetches the live model list from the gateway and rewrites *only* that block,
so a model added to the gateway becomes selectable and routable without editing
``opencode.json`` by hand.

Behavior:
  - Existing entries keep their human ``name`` label and original order.
  - New models are appended with ``name`` defaulting to the bare id.
  - Models no longer served by the gateway are dropped.
  - Everything else in opencode.json is left byte-for-byte untouched.

Run with:
  uv run python scripts/sync_gateway_models.py            # write changes
  uv run python scripts/sync_gateway_models.py --dry-run  # preview only

Requires (can live in .env at repo root):
  GATEWAY_BASE_URL    e.g. http://localhost:4000/v1
  GATEWAY_TOKEN       bearer token for the OpenAI-compatible gateway
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from create_run import REPO_ROOT, _load_env

from harness_lab_app.gateway_models import list_gateway_models

OPENCODE_JSON = REPO_ROOT / "opencode.json"


def _find_models_span(text: str) -> tuple[int, int]:
    """Return the [start, end) char span of provider.gateway.models' object value.

    Anchors on the ``"gateway"`` provider then brace-matches the ``"models"``
    object (string-aware, so braces inside labels don't confuse the scan).
    """
    gateway = text.index('"gateway"')
    models = text.index('"models"', gateway)
    start = text.index("{", models)
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise ValueError("Unbalanced braces in opencode.json provider.gateway.models")


def _render_entry(cfg: dict) -> str:
    """Serialize one model config as a single-line ``{ "name": "..." }``."""
    inner = ", ".join(
        f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
        for k, v in cfg.items()
    )
    return f"{{ {inner} }}" if inner else "{}"


def _render_models_block(models: dict[str, dict], base_indent: str = "      ") -> str:
    """Render the ``models`` object matching opencode.json's compact style."""
    entry_indent = base_indent + "  "
    items = list(models.items())
    lines = ["{"]
    for idx, (mid, cfg) in enumerate(items):
        comma = "," if idx < len(items) - 1 else ""
        key = json.dumps(mid, ensure_ascii=False)
        lines.append(f"{entry_indent}{key}: {_render_entry(cfg)}{comma}")
    lines.append(f"{base_indent}}}")
    return "\n".join(lines)


def _merge_models(current: dict[str, dict], live_ids: list[str]) -> dict[str, dict]:
    """Keep existing entries (order + labels) for served ids; append new ones."""
    live = set(live_ids)
    merged: dict[str, dict] = {mid: cfg for mid, cfg in current.items() if mid in live}
    for mid in live_ids:
        if mid not in merged:
            merged[mid] = {"name": mid}
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing opencode.json.",
    )
    args = parser.parse_args()

    _load_env()
    live_ids = list_gateway_models()
    if not live_ids:
        print(
            "No models returned from the gateway. Check GATEWAY_BASE_URL / "
            "GATEWAY_TOKEN and that LiteLLM is running. Nothing written.",
            file=sys.stderr,
        )
        return 1

    text = OPENCODE_JSON.read_text()
    start, end = _find_models_span(text)
    current = json.loads(text[start:end])

    merged = _merge_models(current, live_ids)
    added = [mid for mid in merged if mid not in current]
    removed = [mid for mid in current if mid not in merged]

    new_text = text[:start] + _render_models_block(merged) + text[end:]
    # Validate the rewrite is still valid JSON before touching disk.
    json.loads(new_text)

    print(f"gateway models: {len(merged)} total  (+{len(added)} / -{len(removed)})")
    for mid in added:
        print(f"  + {mid}")
    for mid in removed:
        print(f"  - {mid}")

    if args.dry_run:
        print("\n--dry-run: opencode.json left unchanged.")
        return 0
    if new_text == text:
        print("opencode.json already in sync.")
        return 0

    OPENCODE_JSON.write_text(new_text)
    print(f"\nWrote {OPENCODE_JSON.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
