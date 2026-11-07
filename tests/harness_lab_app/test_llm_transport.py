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


# -- what actually served the call -------------------------------------------


def test_the_served_model_is_read_off_the_cli_usage_report():
    """`modelUsage` names the model that answered, not the one asked for."""
    reply = {
        "result": "ok",
        "modelUsage": {
            # A small model the CLI used for its own bookkeeping...
            "claude-haiku-5-5": {"outputTokens": 12},
            # ...and the one that wrote the answer.
            "claude-sonnet-5-5": {"outputTokens": 840},
        },
    }
    assert module.served_model(reply) == "claude-sonnet-5-5"


@pytest.mark.parametrize("usage", [None, {}, "not a map", {"m": "not a dict"}])
def test_a_reply_without_usage_names_no_model_rather_than_a_guess(usage):
    reply = {"result": "ok"} if usage is None else {"result": "ok", "modelUsage": usage}
    expected = "m" if usage == {"m": "not a dict"} else ""
    assert module.served_model(reply) == expected


def test_the_cli_reply_records_the_model_it_fell_back_to(monkeypatch):
    """A gateway name the CLI cannot serve is answered by the CLI's default.

    Before, the record kept the requested `gpt-5.4-mini` and the requested
    temperature, so an audit served by Sonnet at the CLI's own temperature was
    filed as a gpt-5.4-mini audit at 0.2.
    """
    monkeypatch.setattr(module, "available_transport", lambda: "claude_cli")
    _cli_ready(monkeypatch)
    _run_returns(
        monkeypatch,
        stdout=json.dumps(
            {
                "is_error": False,
                "result": "PONG",
                "modelUsage": {"claude-sonnet-5-5": {"outputTokens": 3}},
            }
        ),
    )
    reply = module.chat_reply("gpt-5.4-mini", "", "ping", temperature=0.2)

    assert reply.text == "PONG"
    assert reply.transport == "claude_cli"
    assert reply.requested_model == "gpt-5.4-mini"
    assert reply.model == "claude-sonnet-5-5"
    assert reply.requested_temperature == 0.2
    # The CLI takes no temperature: unknown, never the requested value.
    assert reply.temperature is None
    assert reply.provenance()["effective_model"] == "claude-sonnet-5-5"
    assert reply.provenance()["temperature"] is None


def test_a_cli_reply_without_usage_falls_back_to_the_model_it_was_sent(monkeypatch):
    monkeypatch.setattr(module, "available_transport", lambda: "claude_cli")
    _cli_ready(monkeypatch)
    _run_returns(monkeypatch, stdout=json.dumps({"is_error": False, "result": "hi"}))

    sent = module.chat_reply("claude-opus-5-5", "", "ping")
    assert sent.model == "claude-opus-5-5"
    # Nothing was forwarded and nothing was reported: unknown, not invented.
    unsent = module.chat_reply("gpt-5.4-mini", "", "ping")
    assert unsent.model == ""


def test_the_gateway_reply_is_served_as_requested(monkeypatch):
    """The gateway forwards model and temperature, so the request is the record."""
    monkeypatch.setattr(module, "available_transport", lambda: "gateway")
    monkeypatch.setattr(module, "gateway_config", lambda: ("http://gw.invalid", "sk"))
    monkeypatch.setattr(module, "gateway_chat", lambda *_a, **_k: "an answer")

    reply = module.chat_reply("gpt-5.4-mini", "s", "u", temperature=0.0)
    assert (reply.model, reply.temperature) == ("gpt-5.4-mini", 0.0)
    assert module.chat("gpt-5.4-mini", "s", "u") == "an answer"


def test_the_model_the_cli_was_sent_wins_when_it_is_listed():
    """A terse answer can be shorter than the CLI's own bookkeeping."""
    reply = {
        "modelUsage": {
            "claude-haiku-5-5": {"outputTokens": 200},
            "claude-sonnet-5-5": {"outputTokens": 3},
        }
    }
    assert module.served_model(reply, "claude-sonnet-5-5") == "claude-sonnet-5-5"
    # Not listed: the most output still decides.
    assert module.served_model(reply, "claude-opus-5-5") == "claude-haiku-5-5"


def test_absurd_token_counts_do_not_raise():
    reply = {"modelUsage": {"a": {"outputTokens": 10**400}, "b": {"outputTokens": 1}}}
    assert module.served_model(reply) == "b"
