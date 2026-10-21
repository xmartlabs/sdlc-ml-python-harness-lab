#!/usr/bin/env python3
"""Readiness checklist before showing the lab: `make demo-check`.

Read-only. It inspects Docker, the credential path in `.env` (names only — a
value is never printed), and, when the stack is up, the API and the loaded
cases. It writes nothing: the database belongs to the running stack, and a
host-side write to SQLite on a bind mount is how the lab once split its DB.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_lab_app.run_service import _PLACEHOLDER_TOKENS

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API = "http://localhost:8009"
_CLAUDE_CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
_ONBOARDING = "see docs/onboarding.md — credentials"

Row = dict[str, Any]


def _row(name: str, ok: bool, detail: str, fix: str = "", required: bool = True) -> Row:
    return {
        "name": name,
        "ok": ok,
        "detail": detail,
        "fix": "" if ok else fix,
        "required": required,
    }


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs from a dotenv file; comments and junk are skipped."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        if not sep or not key.strip() or " " in key.strip():
            continue
        values[key.strip()] = value.strip().strip("\"'")
    return values


def credential_check(env: dict[str, str]) -> Row:
    """Which credential path the lab will use. Names only, never values."""
    gateway = (
        env.get("GATEWAY_BASE_URL")
        and env.get("GATEWAY_TOKEN", "") not in _PLACEHOLDER_TOKENS
    )
    claude = [name for name in _CLAUDE_CREDENTIALS if env.get(name)]
    if gateway:
        extra = f" (Claude credential also set: {', '.join(claude)})" if claude else ""
        return _row(
            "Credential",
            True,
            f"AI gateway via GATEWAY_BASE_URL + GATEWAY_TOKEN{extra}",
        )
    if claude:
        return _row("Credential", True, f"Claude credential via {', '.join(claude)}")
    return _row(
        "Credential",
        False,
        "No usable credential in .env (gateway token missing or still the placeholder, "
        "no Claude credential).",
        f"Set GATEWAY_BASE_URL + GATEWAY_TOKEN, or CLAUDE_CODE_OAUTH_TOKEN — {_ONBOARDING}.",
    )


def docker_check(run: Callable[..., Any] = subprocess.run) -> Row:
    if shutil.which("docker") is None:
        return _row(
            "Docker", False, "docker not found on PATH.", "Install Docker Desktop."
        )
    probe = run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return _row(
            "Docker",
            False,
            "The Docker daemon is not reachable.",
            "Start Docker Desktop.",
        )
    return _row("Docker", True, f"daemon {probe.stdout.strip()}")


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=4) as response:  # nosec B310 — local URL
        return json.loads(response.read())


def api_checks(base: str, fetch: Callable[[str], Any] = _fetch_json) -> list[Row]:
    """The stack's health and its cases; one row explaining how to start it if down."""
    try:
        health = fetch(f"{base}/api/health")
    except Exception as error:  # any failure means "not up"
        return [
            _row(
                "Lab API",
                False,
                f"{base} is not answering ({type(error).__name__}).",
                "Run `make app-up`, then open http://localhost:5173.",
            )
        ]
    rows = [
        _row(
            "Lab API",
            health.get("status") == "ok",
            f"{base} is up.",
            "Check `make app-up` logs.",
        )
    ]
    try:
        cases = fetch(f"{base}/api/cases")
    except Exception:  # the API may require a login
        rows.append(
            _row(
                "Cases loaded",
                False,
                "Could not list cases (login required or API error).",
                "Open the UI and check Cases.",
                required=False,
            )
        )
        return rows
    count = len(cases) if isinstance(cases, list) else 0
    rows.append(
        _row(
            "Cases loaded",
            count > 0,
            f"{count} case(s) available.",
            "Restart the API: benchmark cases are seeded at startup.",
        )
    )
    return rows


def exit_code(rows: list[Row]) -> int:
    return 1 if any(row["required"] and not row["ok"] for row in rows) else 0


def render(rows: list[Row]) -> str:
    lines = ["Demo readiness", ""]
    for row in rows:
        mark = "✓" if row["ok"] else ("✗" if row["required"] else "!")
        lines.append(f"{mark} {row['name']}: {row['detail']}")
        if row["fix"]:
            lines.append(f"    → {row['fix']}")
    lines += [
        "",
        "Ready." if exit_code(rows) == 0 else "Not ready yet — fix the ✗ items above.",
        "Script: docs/demo-guide.md  ·  quick check on any repo: make harness-check REPO=<path>",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    api = (argv or [DEFAULT_API])[0] if argv else DEFAULT_API
    rows = [
        docker_check(),
        credential_check(read_env_file(ROOT / ".env")),
        *api_checks(api),
    ]
    print(render(rows))
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
