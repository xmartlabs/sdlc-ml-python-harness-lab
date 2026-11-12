"""The commit-msg guard: a commit is the developer's, never an AI agent's."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_commit_authorship as guard  # noqa: E402

HUMAN = "Marcos Soto <marcos.soto@xmartlabs.com>"


@pytest.mark.parametrize(
    "message",
    [
        "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
        "feat: x\n\nCo-authored-by: Claude Opus 5.5 <noreply@anthropic.com>\n",
        "fix: y\n\nco-authored-by: claude <claude@anthropic.com>",
        "feat: x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n",
        "feat: x\n\nCo-authored-by: Copilot <copilot@github.com>\n",
        "feat: x\n\nCo-authored-by: someone <bot@openai.com>\n",
    ],
)
def test_ai_attribution_in_the_message_is_refused(message: str) -> None:
    assert guard.message_problems(message)


@pytest.mark.parametrize(
    "message",
    [
        "feat: add the Claude-native transport (ADR-0043)\n",
        "docs: explain how claude stream-json events are parsed\n",
        "feat: x\n\nCo-authored-by: Ana Pérez <ana@xmartlabs.com>\n",
        "",
    ],
)
def test_a_human_message_that_mentions_claude_is_fine(message: str) -> None:
    # The product is named in this repo's code and docs; only attribution of
    # authorship is refused.
    assert guard.message_problems(message) == []


@pytest.mark.parametrize(
    "ident",
    [
        "Claude <claude@anthropic.com>",
        "claude <someone@example.com>",
        "Marcos <noreply@anthropic.com>",
        "Claude Code <x@y.z>",
    ],
)
def test_an_ai_identity_is_refused(ident: str) -> None:
    assert guard.identity_problems(ident, role="author")


def test_the_developers_identity_passes() -> None:
    assert guard.identity_problems(HUMAN, role="author") == []


def test_main_checks_the_message_file_and_both_identities(tmp_path, monkeypatch):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("feat: x\n")
    idents = {"GIT_AUTHOR_IDENT": HUMAN, "GIT_COMMITTER_IDENT": HUMAN}
    monkeypatch.setattr(guard, "git_ident", lambda var: idents[var])
    assert guard.main([str(message)]) == 0

    idents["GIT_COMMITTER_IDENT"] = "Claude <claude@anthropic.com>"
    assert guard.main([str(message)]) == 1


def test_main_ignores_comment_lines_git_strips(tmp_path, monkeypatch):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("feat: x\n# Co-authored-by: Claude <noreply@anthropic.com>\n")
    monkeypatch.setattr(guard, "git_ident", lambda _var: HUMAN)
    assert guard.main([str(message)]) == 0
