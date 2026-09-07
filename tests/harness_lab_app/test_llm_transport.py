"""The lab's single outbound LLM transport (ADR-0043).

Two properties carry the weight here. First, **the gateway always wins**: a lab
that routes several models through LiteLLM must keep doing exactly that, so
adding a Claude credential can never change which transport judges a run.
Second, **a call that failed is never an answer** — every failure path raises,
because the callers turn a raise into their own documented skip and would turn a
returned string into a score.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from harness_lab_app import llm_transport as module


@pytest.fixture
def blank_env(monkeypatch):
    """No credentials anywhere: not in the shell, not in a `.env`."""
    for name in (
        "GATEWAY_BASE_URL",
        "GATEWAY_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, "setting", lambda _name: "")
    monkeypatch.setattr(module, "claude_cli_path", lambda: "")


def _env(values: dict[str, str]):
    return lambda name: values.get(name, "")


# -- which transport ---------------------------------------------------------


def test_a_gateway_lab_is_untouched_by_adding_a_claude_credential(monkeypatch):
    """The compatibility guarantee, asserted rather than assumed."""
    monkeypatch.setattr(
        module,
        "setting",
        _env(
            {
                "GATEWAY_BASE_URL": "http://gw.invalid/v1",
                "GATEWAY_TOKEN": "sk-gw",
                "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x",
            }
        ),
    )
    monkeypatch.setattr(module, "claude_cli_path", lambda: "/usr/bin/claude")
    assert module.available_transport() == "gateway"


def test_a_claude_credential_alone_is_a_working_transport(monkeypatch):
    monkeypatch.setattr(
        module, "setting", _env({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x"})
    )
    monkeypatch.setattr(module, "claude_cli_path", lambda: "/usr/bin/claude")
    assert module.available_transport() == "claude_cli"


def test_a_credential_without_the_cli_is_not_a_transport(monkeypatch):
    """A token the lab cannot spend is not a capability."""
    monkeypatch.setattr(
        module, "setting", _env({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x"})
    )
    monkeypatch.setattr(module, "claude_cli_path", lambda: "")
    assert module.available_transport() == "none"


def test_nothing_configured_says_so_instead_of_guessing(blank_env):
    assert module.available_transport() == "none"
    with pytest.raises(module.TransportNotConfigured):
        module.chat("any-model", "sys", "user")


# -- model defaults ----------------------------------------------------------


def test_each_transport_defaults_to_a_model_it_can_actually_serve():
    """`gpt-5.4-mini` means nothing to the CLI, and the reverse."""
    assert module.default_audit_model("claude_cli").startswith("claude-")
    assert module.default_summary_model("claude_cli").startswith("claude-")
    assert not module.default_audit_model("gateway").startswith("claude-")


def test_an_unconfigured_lab_still_names_a_model(blank_env):
    """An argparse help string and a --dry-run render need a name to print."""
    assert module.default_audit_model()
    assert module.default_summary_model()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        # The opencode-only `gateway/` prefix is stripped before the CLI sees it.
        ("gateway/claude-sonnet-5", "claude-sonnet-5"),
        ("claude-haiku-4-5", "claude-haiku-4-5"),
        # Not a Claude API id: let the CLI pick its own default rather than send
        # a name that is a guaranteed 404. That covers both other providers and
        # gateway-side aliases that only LiteLLM knows how to resolve.
        ("gpt-5.4-mini", ""),
        ("gateway/gpt-5.5", ""),
        ("gateway/anthropic-claude-sonnet-5", ""),
        ("", ""),
    ],
)
def test_only_a_claude_id_survives_translation_to_the_cli(configured, expected):
    assert module.native_claude_model(configured) == expected


# -- the gateway wire format -------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def test_an_empty_system_prompt_sends_no_system_message(monkeypatch):
    """The DeepEval judge sends prompt-only; its payload must not grow a role."""
    captured: dict = {}

    def _urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    text = module.gateway_chat(
        "http://gw.invalid/v1", "sk", "m", "", "judge this", timeout=5, temperature=0.0
    )
    assert text == "ok"
    assert [m["role"] for m in captured["payload"]["messages"]] == ["user"]


def test_a_reply_the_lab_cannot_read_is_an_error_not_an_empty_answer(monkeypatch):
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResponse({"error": "quota"}),
    )
    with pytest.raises(module.TransportError, match="Unexpected gateway response"):
        module.gateway_chat(
            "http://gw.invalid/v1", "sk", "m", "s", "u", timeout=5, temperature=0.0
        )


# -- the claude cli ----------------------------------------------------------


def _cli_ready(monkeypatch):
    monkeypatch.setattr(module, "claude_cli_path", lambda: "/usr/bin/claude")
    monkeypatch.setattr(
        module, "claude_cli_credential", lambda: ("CLAUDE_CODE_OAUTH_TOKEN", "sk-oat")
    )


def _run_returns(
    monkeypatch, *, returncode: int = 0, stdout: str = "", stderr: str = ""
):
    captured: dict = {}

    def _run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(module.subprocess, "run", _run)
    return captured


def test_the_cli_is_driven_as_a_completion_not_as_an_agent(monkeypatch):
    """No tools means no permission prompt can ever block a worker."""
    _cli_ready(monkeypatch)
    captured = _run_returns(
        monkeypatch, stdout=json.dumps({"is_error": False, "result": "PONG"})
    )
    text = module._claude_cli_chat("claude-sonnet-5", "be terse", "ping", timeout=30)

    assert text == "PONG"
    command = captured["command"]
    assert command[1:2] == ["-p"]
    assert "--tools" in command and command[command.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in command
    assert command[command.index("--model") + 1] == "claude-sonnet-5"
    assert command[command.index("--append-system-prompt") + 1] == "be terse"
    # The prompt travels on stdin: audit prompts carry clipped diffs and would
    # otherwise hit the argument-length limit.
    assert captured["kwargs"]["input"] == "ping"


def test_a_gateway_model_name_is_not_forwarded_to_the_cli(monkeypatch):
    _cli_ready(monkeypatch)
    captured = _run_returns(
        monkeypatch, stdout=json.dumps({"is_error": False, "result": "hi"})
    )
    module._claude_cli_chat("gpt-5.4-mini", "", "ping", timeout=30)
    assert "--model" not in captured["command"]


def test_the_credential_is_handed_to_the_subprocess_explicitly(monkeypatch):
    """The api and worker containers never export `.env` into their env."""
    _cli_ready(monkeypatch)
    captured = _run_returns(
        monkeypatch, stdout=json.dumps({"is_error": False, "result": "hi"})
    )
    module._claude_cli_chat("claude-sonnet-5", "", "ping", timeout=30)
    assert captured["kwargs"]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oat"


def test_an_in_band_failure_raises_instead_of_returning_the_error_text(monkeypatch):
    """The CLI puts its error where the answer goes; that is not an answer."""
    _cli_ready(monkeypatch)
    _run_returns(
        monkeypatch,
        returncode=1,
        stdout=json.dumps({"is_error": True, "result": "Invalid model name"}),
    )
    with pytest.raises(module.TransportError, match="Invalid model name"):
        module._claude_cli_chat("claude-sonnet-5", "", "ping", timeout=30)


def test_an_empty_reply_is_a_failure_not_an_empty_score(monkeypatch):
    _cli_ready(monkeypatch)
    _run_returns(monkeypatch, stdout=json.dumps({"is_error": False, "result": ""}))
    with pytest.raises(module.TransportError, match="empty reply"):
        module._claude_cli_chat("claude-sonnet-5", "", "ping", timeout=30)


def test_a_timeout_raises_rather_than_hanging_the_caller(monkeypatch):
    _cli_ready(monkeypatch)

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=30)

    monkeypatch.setattr(module.subprocess, "run", _boom)
    with pytest.raises(module.TransportError, match="timed out"):
        module._claude_cli_chat("claude-sonnet-5", "", "ping", timeout=30)
