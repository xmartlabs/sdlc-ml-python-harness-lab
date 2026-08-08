"""Safe, comment-preserving reader/writer for the repository ``.env``.

The Settings page edits real credentials, so this module is deliberately narrow
and dependency-free:

* every raw line survives a round-trip, so hand-written comments and ordering
  are never lost;
* writes are atomic (tempfile + ``os.replace``) and mode ``0600``;
* a rolling backup is kept, because a bad write here breaks every run.

Quoting is intentionally *not* supported. Three different parsers read this
file — ``config.read_env_file_value``, ``executors.build_run_environment`` and
the Makefile's ``set -a; . ./.env`` — and none of them strip quotes, so a
quoted value would reach the gateway with the quotes embedded. Values needing
quotes are rejected at validation time instead.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MANAGED_HEADER = "# --- managed by the web Settings page ---"
BACKUP_DIRNAME = "env-backups"
MAX_BACKUPS = 20

KIND_ASSIGNMENT = "assignment"
KIND_COMMENT = "comment"
KIND_BLANK = "blank"

# Mirrors what the naive parsers can round-trip losslessly.
_UNSAFE_VALUE_RE = re.compile(r"[\s\"'#\\]")
_VALID_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class EnvFileError(ValueError):
    """Raised when a key or value cannot be represented in the .env format."""


@dataclass(frozen=True)
class EnvLine:
    kind: str
    raw: str
    key: str = ""
    value: str = ""


def parse_env(text: str) -> list[EnvLine]:
    """Split ``.env`` text into typed lines, preserving each raw line verbatim."""
    lines: list[EnvLine] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            lines.append(EnvLine(kind=KIND_BLANK, raw=raw))
        elif stripped.startswith("#") or "=" not in stripped:
            lines.append(EnvLine(kind=KIND_COMMENT, raw=raw))
        else:
            key, _, value = stripped.partition("=")
            lines.append(
                EnvLine(
                    kind=KIND_ASSIGNMENT,
                    raw=raw,
                    key=key.strip(),
                    value=value.strip(),
                )
            )
    return lines


def duplicate_keys(lines: list[EnvLine]) -> list[str]:
    """Keys assigned more than once.

    Worth surfacing: ``read_env_file_value`` returns the *first* occurrence
    while shell sourcing and this module's writer use the *last*, so a
    duplicated key means different components disagree about its value.
    """
    counts = Counter(ln.key for ln in lines if ln.kind == KIND_ASSIGNMENT)
    return sorted(key for key, n in counts.items() if n > 1)


def validate_assignment(key: str, value: str) -> None:
    if not _VALID_KEY_RE.match(key):
        raise EnvFileError(
            f"Invalid env key {key!r}: expected UPPER_SNAKE_CASE letters, "
            "digits and underscores."
        )
    if _UNSAFE_VALUE_RE.search(value):
        raise EnvFileError(
            f"Value for {key} contains whitespace, quotes, '#' or a backslash. "
            "The .env parsers in this repo do not support quoting, so such a "
            "value cannot be stored safely."
        )


def render_env(lines: list[EnvLine], updates: dict[str, str]) -> str:
    """Apply ``updates`` to parsed lines and render the file back to text.

    Existing keys are rewritten in place at their *last* occurrence, matching
    shell-sourcing semantics. Unknown keys are appended under a managed header.
    """
    for key, value in updates.items():
        validate_assignment(key, value)

    last_index: dict[str, int] = {}
    for index, line in enumerate(lines):
        if line.kind == KIND_ASSIGNMENT and line.key in updates:
            last_index[line.key] = index

    rendered = [line.raw for line in lines]
    for key, index in last_index.items():
        rendered[index] = f"{key}={updates[key]}"

    appended = [key for key in updates if key not in last_index]
    if appended:
        if rendered and rendered[-1].strip():
            rendered.append("")
        if MANAGED_HEADER not in rendered:
            rendered.append(MANAGED_HEADER)
        rendered.extend(f"{key}={updates[key]}" for key in appended)

    return "\n".join(rendered) + "\n"


def read_all(path: Path) -> list[EnvLine]:
    if not path.exists():
        return []
    return parse_env(path.read_text(encoding="utf-8"))


def values(path: Path) -> dict[str, str]:
    """Effective key/value map, last occurrence winning."""
    result: dict[str, str] = {}
    for line in read_all(path):
        if line.kind == KIND_ASSIGNMENT:
            result[line.key] = line.value
    return result


def _write_backup(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f".env.{stamp}"
    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(target, 0o600)

    existing = sorted(backup_dir.glob(".env.*"))
    for stale in existing[:-MAX_BACKUPS]:
        stale.unlink(missing_ok=True)
    return target


def write_env(path: Path, updates: dict[str, str], backup_dir: Path) -> Path | None:
    """Atomically apply ``updates`` to the .env at ``path``.

    Returns the backup path, or None when the file did not exist yet. The
    tempfile is created in the destination directory so ``os.replace`` stays on
    one filesystem and is therefore atomic.
    """
    if not updates:
        return None

    lines = read_all(path)
    text = render_env(lines, updates)
    backup = _write_backup(path, backup_dir)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".env.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)
    return backup
