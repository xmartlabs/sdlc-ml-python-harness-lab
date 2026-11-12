#!/usr/bin/env python3
"""commit-msg guard: every commit is authored by the developer, never an AI agent.

The developer owns every commit and pull request in this repository. AI agents
(Claude Code, Copilot, Codex, OpenCode) may write code, but they are never the
commit's author, committer, or co-author. This hook refuses a commit when:

* the resolved author or committer identity is an AI agent (``Claude``,
  ``*@anthropic.com``, ...) — typically a stray ``[user]`` block an agent wrote
  into ``.git/config``, which silently overrides the developer's own identity;
* the message carries an AI co-author trailer or a "Generated with" line.

Naming a product in a message ("add the Claude-native transport") is fine; only
attribution of authorship is refused.

Installed by ``make setup-hooks`` (pre-commit, ``commit-msg`` stage). Usage:
``check_commit_authorship.py <path-to-commit-message-file>``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Identities an AI agent commits under. Matched against "Name <email>".
_AI_NAME = re.compile(
    r"^\s*(claude|copilot|github copilot|codex|opencode|chatgpt|gemini)\b",
    re.IGNORECASE,
)
_AI_EMAIL = re.compile(
    r"<[^>]*@(anthropic\.com|openai\.com)>|<copilot@github\.com>",
    re.IGNORECASE,
)
# Authorship lines an agent appends to a message.
_CO_AUTHOR = re.compile(r"^\s*co-authored-by:\s*(?P<who>.+)$", re.IGNORECASE)
_GENERATED = re.compile(
    r"generated with .*(claude|copilot|codex|opencode)", re.IGNORECASE
)

_FIX = (
    "Commits in this repository are authored by the developer only.\n"
    "  - Identity: run `git config --local --unset-all user.name` and "
    "`git config --local --unset-all user.email` so your global identity applies.\n"
    "  - Message: remove the AI co-author / 'Generated with' line."
)


def identity_problems(ident: str, *, role: str) -> list[str]:
    """Problems with one git identity ("Name <email> <timestamp>")."""
    if _AI_NAME.search(ident) or _AI_EMAIL.search(ident):
        return [f"{role} is an AI agent identity: {ident.strip()}"]
    return []


def message_problems(message: str) -> list[str]:
    """AI attribution lines in a commit message, ignoring git comment lines."""
    problems: list[str] = []
    for line in message.splitlines():
        if line.startswith("#"):
            continue
        co_author = _CO_AUTHOR.match(line)
        if co_author and (
            _AI_NAME.search(co_author.group("who"))
            or _AI_EMAIL.search(co_author.group("who"))
        ):
            problems.append(f"AI co-author trailer: {line.strip()}")
        elif _GENERATED.search(line):
            problems.append(f"AI attribution line: {line.strip()}")
    return problems


def git_ident(variable: str) -> str:
    """The identity git will record, as ``git var`` resolves it."""
    result = subprocess.run(
        ["git", "var", variable], capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: check_commit_authorship.py <commit-message-file>")
        return 2
    message = Path(args[0]).read_text(encoding="utf-8", errors="replace")
    problems = [
        *identity_problems(git_ident("GIT_AUTHOR_IDENT"), role="author"),
        *identity_problems(git_ident("GIT_COMMITTER_IDENT"), role="committer"),
        *message_problems(message),
    ]
    if not problems:
        return 0
    print("❌ Commit refused:")
    for problem in problems:
        print(f"  - {problem}")
    print(_FIX)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
