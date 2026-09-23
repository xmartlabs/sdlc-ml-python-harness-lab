"""Print the opencode overlay for an interactive session, window limits included.

`make opencode` evaluates this script's output before launching the TUI, so
the models named in `.env` (OPENCODE_MODEL, OPENCODE_MODEL_PLAN,
OPENCODE_SMALL_MODEL) carry the context window their serving host reports
right now — the same detection `bench_run.py` applies to a lab run. Set
HARNESS_LAB_CONTEXT_AUTODETECT=0 to launch opencode exactly as before.

Output is `export KEY='value'` lines, one per variable, safe to `eval`. Every
diagnostic goes to stderr so a failed probe never corrupts the shell.

Usage:
    eval "$(uv run python scripts/opencode_context.py)"
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from create_run import REPO_ROOT, _load_env

MODEL_VARS = ("OPENCODE_MODEL", "OPENCODE_MODEL_PLAN", "OPENCODE_SMALL_MODEL")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="model id to detect a window for (default: the OPENCODE_* variables)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from harness_lab_app.context_window import (
        autodetect_enabled,
        describe,
        detect_windows,
        limits_by_model,
    )
    from harness_lab_app.lmstudio_direct import is_direct_model as is_lmstudio
    from harness_lab_app.ollama_direct import is_direct_model as is_ollama
    from harness_lab_app.opencode_overlay import build_overlay

    args = parse_args(argv)
    _load_env()
    models = tuple(
        dict.fromkeys(
            m for m in (args.model or [os.environ.get(v, "") for v in MODEL_VARS]) if m
        )
    )
    direct = tuple(m for m in models if is_lmstudio(m) or is_ollama(m))
    windows: list = []
    if models and autodetect_enabled():
        try:
            windows = detect_windows(models)
        except Exception as exc:
            print(f"⚠️  context window detection skipped: {exc!r}", file=sys.stderr)
    for window in windows:
        print(f"✓ {describe(window)}", file=sys.stderr)
    if models and not windows:
        print(
            "-  no served context window detected; opencode keeps its defaults "
            f"(models: {', '.join(models)})",
            file=sys.stderr,
        )
    env, _ = build_overlay(REPO_ROOT, direct, limits_by_model(windows))
    for key, value in env.items():
        print(f"export {key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
