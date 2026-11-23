"""Antigravity CLI (`agy`) on a Gemini API key: the `antigravity_cli` harness.

What these tests hold the integration to:

* the exact argv of agy 1.5.14 — explicit allow list instead of
  `--dangerously-skip-permissions`, the prompt on stdin, an explicit model;
* the key lives only in the lab's store (0600), is never echoed by the API,
  never logged by Celery, and is the **only** secret an agy container gets;
* a run's stream, log, archived files and failure reason never carry it;
* the event translation of real agy 1.5.14 captures (``fixtures/agy``):
  tools, text deltas, usage with cache and thinking, every failure shape;
* cost is the run's own tokens at Google's dated list price, with its basis,
  and never a subscription's quota; unknown is null, never 0;
* preflight refuses what cannot run (host lane, no key, no or unverified
  model, missing CLI) and what should not (third-party code) unless allowed;
* codex, gemini, claude and opencode are unchanged, and historical condition
  hashes do not move.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402

from harness_lab_app import api_key_auth, executors, native_connect  # noqa: E402
from harness_lab_app.executors import (  # noqa: E402
    DockerRunExecutor,
    build_run_environment,
    runner_masks,
)
from harness_lab_app.harness_events import (  # noqa: E402
    antigravity_to_claude,
    render_antigravity_event,
    translate,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agy"
# Shaped like an AI Studio key, fake.
KEY = "AIzaTESTtestTESTtestTESTtestTESTte1234"


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def _events(name: str, model: str = "") -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in antigravity_to_claude(_lines(name), model=model)
    ]


# --- command ------------------------------------------------------------------


def test_the_command_grants_tools_explicitly_and_never_skips_permissions() -> None:
    cmd = bench_run.build_agy_cmd("gemini-3.8-flash-medium")

    assert cmd[0] == "agy"
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    assert settings["modelProvider"] == "gemini"
    assert settings["permissions"]["allow"] == [
        "command(*)",
        "read_file(*)",
        "write_file(*)",
        "read_url(*)",
    ]
    # MCP and browser execution are not granted to any arm.
    assert not any(
        rule.startswith(("mcp", "execute_url"))
        for rule in settings["permissions"]["allow"]
    )
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--mode") + 1] == "accept-edits"
    assert "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-medium"
    assert cmd[-4:] == [
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
    ]
    # The watchdog owns time budgets for every arm alike.
    assert "--print-timeout" not in cmd
    # No prompt in argv (it goes on stdin) and no credential anywhere.
    assert "-p" not in cmd
    assert not any("AIza" in part for part in cmd)


def test_the_prompt_is_one_stream_json_message_on_stdin() -> None:
    prompt = "Fix the bug.\nThen say `/done`." + "x" * 50_000

    message = json.loads(bench_run.agy_stdin_message(prompt))

    assert message == {"event": "user", "message": {"content": prompt}}
    assert "antigravity_cli" in bench_run.STDIN_PROMPT_HARNESSES


def test_a_gateway_model_never_reaches_agy() -> None:
    assert "--model" not in bench_run.build_agy_cmd("gateway/gpt-5.5")


def test_existing_harness_commands_are_unchanged() -> None:
    assert bench_run.build_harness_cmd("codex", "gpt-6.2-sol", None, "x")[:4] == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
    ]
    assert bench_run.build_harness_cmd("gemini", "auto", None, "x")[0] == "gemini"
    assert (
        bench_run.build_harness_cmd("opencode", "gateway/m", None, "x")[0] == "opencode"
    )
    assert (
        bench_run.harness_sandbox("antigravity_cli", in_container=True) == "container"
    )
    # Subscription accounting stays with codex/gemini only.
    assert {"codex", "gemini"} == bench_run.SUBSCRIPTION_PROMPT_HARNESSES


def test_the_injected_arm_projects_the_antigravity_registry_tool() -> None:
    assert bench_run.registry_tool_id("antigravity_cli") == "antigravity"
    for harness in ("claude", "codex", "gemini", "opencode"):
        assert bench_run.registry_tool_id(harness) == harness


# --- the key store ---------------------------------------------------------------


def test_the_key_is_stored_0600_outside_the_repo_and_shown_only_by_its_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))

    status = api_key_auth.save_key(f"  {KEY}\n")

    path = api_key_auth.key_path()
    assert path == tmp_path / "auth" / "antigravity_cli" / "gemini_api_key"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert api_key_auth.read_key() == KEY
    assert status == {
        "configured": True,
        "hint": "…1234",
        "saved_at": status["saved_at"],
    }
    assert KEY not in json.dumps(api_key_auth.key_status())
    # Metadata holds the hint, never the key.
    assert KEY not in (path.parent / "meta.json").read_text()

    assert api_key_auth.remove_key() == {
        "configured": False,
        "hint": None,
        "saved_at": None,
    }
    assert api_key_auth.read_key() == ""


@pytest.mark.parametrize(
    "bad", ["", "   ", "short", "has space inside the key value", 'q"uote' * 5]
)
def test_a_malformed_key_is_refused_without_echoing_it(bad: str) -> None:
    with pytest.raises(ValueError) as caught:
        api_key_auth.validate_key(bad)
    if bad.strip():
        assert bad.strip() not in str(caught.value)


def test_the_agy_environment_carries_its_key_and_no_one_elses() -> None:
    env = {
        "PATH": "/usr/bin",
        "GATEWAY_BASE_URL": "http://host.docker.internal:4000/v1",
        "GATEWAY_TOKEN": "sk-gw",
        "LITELLM_MASTER_KEY": "sk-master",
        "OPENAI_API_KEY": "sk-openai",
        "ANTHROPIC_API_KEY": "sk-ant",
        "ANTHROPIC_CUSTOM_HEADERS": "x-litellm-api-key: Bearer sk-x",
        "CLAUDE_CODE_OAUTH_TOKEN": "oat",
        "GOOGLE_API_KEY": "AIza-other",
        "GOOGLE_GEMINI_BASE_URL": "https://evil.example",
        "GEMINI_API_KEY": "AIza-from-dotenv",
        "LANGFUSE_SECRET_KEY": "lf",
        "NVIDIA_API_KEY": "nv",
        "SOME_NEW_SERVICE_TOKEN": "t",
        "IS_SANDBOX": "1",
    }

    out = api_key_auth.agy_env(env, key=KEY, home="/tmp/agent-home")  # nosec B108

    assert out["GEMINI_API_KEY"] == KEY  # the store's, not .env's
    assert out["HOME"] == "/tmp/agent-home"  # nosec B108
    assert out["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert out["PATH"] == "/usr/bin" and out["IS_SANDBOX"] == "1"
    assert out["GATEWAY_BASE_URL"].startswith("http://host.docker.internal")
    secrets = {k for k in env if k not in {"PATH", "GATEWAY_BASE_URL", "IS_SANDBOX"}}
    leaked = {k for k in secrets if k in out and k != "GEMINI_API_KEY"}
    assert leaked == set()


def test_redaction_replaces_the_exact_key_and_nothing_else(tmp_path: Path) -> None:
    text, hits = api_key_auth.redact_secret(f"key={KEY} again {KEY} AIzaOther", KEY)
    assert hits == 2 and KEY not in text and "AIzaOther" in text

    run = tmp_path / "run"
    (run / "steps").mkdir(parents=True)
    (run / "steps" / "step-1.raw.jsonl").write_text(f'{{"output":"{KEY}"}}\n')
    (run / "notes.md").write_text("nothing here\n")
    assert api_key_auth.redact_tree(run, KEY) == 1
    assert KEY not in (run / "steps" / "step-1.raw.jsonl").read_text()
    assert api_key_auth.redact_tree(run, "") == 0


# --- the runner container ---------------------------------------------------------


class _Settings:
    def __init__(self, root: Path):
        self.repo_root = root
        self.docker_host_alias = "host.docker.internal"
        self.host_repo_root = "/host/repo"
        self.runner_image = "harness-lab-runner:latest"
        self.execution_backend = "docker"
        self.data_runs_dir = root / "data" / "runs"
        self.validation_network = "none"


class _Container:
    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def logs(self, **_kwargs: Any):
        return iter(self._lines)

    def wait(self) -> dict:
        return {"StatusCode": 0}

    def remove(self, **_kwargs: Any) -> None:
        return None

    def stop(self, **_kwargs: Any) -> None:
        return None


class _Client:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.run_kwargs: dict[str, Any] = {}
        client = self

        class _Containers:
            def run(self, **kwargs: Any) -> _Container:
                client.run_kwargs = kwargs
                return _Container(lines or [b"ok\n"])

            def get(self, _name: str):
                raise LookupError

        class _Images:
            def get(self, _name: str) -> object:
                return object()

        self.containers = _Containers()
        self.images = _Images()


def _lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: _Settings(tmp_path))
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / ".env").write_text(
        "GATEWAY_BASE_URL=http://localhost:4000/v1\nGATEWAY_TOKEN=sk-gw\n"
        "CLAUDE_CODE_OAUTH_TOKEN=oat\nOPENAI_API_KEY=sk-openai\n"
        "GEMINI_API_KEY=AIza-dotenv-must-not-win\n"
    )
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"ANTHROPIC_CUSTOM_HEADERS": "x-key: sk-x"}})
    )
    api_key_auth.save_key(KEY)


def test_an_agy_container_gets_only_its_key_and_cannot_read_the_labs_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    client = _Client()

    DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-901", ["python", "scripts/bench_run.py"], harness="antigravity_cli"
    )

    env = client.run_kwargs["environment"]
    assert env["GEMINI_API_KEY"] == KEY
    assert env["HOME"] == "/tmp/agent-home"  # nosec B108 - container path string
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    for name in (
        "GATEWAY_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_CUSTOM_HEADERS",
    ):
        assert name not in env
    # .env and the Claude settings with a gateway header are masked inside
    # /app, so the agent cannot `cat` them either.
    targets = {m["Target"]: m for m in client.run_kwargs.get("mounts") or []}
    assert targets["/app/.env"]["ReadOnly"] is True
    assert targets["/app/.claude/settings.local.json"]["ReadOnly"] is True
    # No subscription session of any provider is mounted.
    assert not any(t.startswith("/run/harness-auth") for t in targets)
    # agy reads `.agents/skills` only: that mirror stays, the others are masked.
    tmpfs = client.run_kwargs["tmpfs"]
    assert "/app/.agents/skills" not in tmpfs
    assert {"/app/.claude/skills", "/app/.codex/skills", "/app/.gemini/skills"} <= set(
        tmpfs
    )


def test_the_key_never_leaves_the_container_through_its_log_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    client = _Client([f"agent printed {KEY}\n".encode(), b"done\n"])

    handle = DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-902", ["true"], harness="antigravity_cli"
    )

    lines = [line for _level, line in handle.iter_lines()]
    assert lines == ["agent printed ***REDACTED***", "done"]


def test_other_harness_containers_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)

    for harness in ("opencode", "claude"):
        env = build_run_environment(harness)
        assert env["GATEWAY_TOKEN"] == "sk-gw"
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oat"
        assert env.get("GEMINI_API_KEY") == "AIza-dotenv-must-not-win"
        _tmpfs, files = runner_masks("none", harness=harness)
        assert "/app/.env" not in {target for target, _kind in files}
    # The stored agy key never reaches another harness.
    for harness in ("opencode", "claude", "codex", "gemini"):
        assert build_run_environment(harness).get("GEMINI_API_KEY") != KEY
    client = _Client()
    DockerRunExecutor(client_factory=lambda: client).start(
        "c-K-1", ["true"], harness="claude"
    )
    assert not any(
        m["Target"] == "/app/.env" for m in client.run_kwargs.get("mounts") or []
    )


# --- events (real agy 1.5.14 captures) ---------------------------------------------


def test_tool_steps_become_claude_tool_calls_with_their_outcome() -> None:
    events = _events("success_tools.stream.jsonl")

    uses = [
        block
        for event in events
        if event["type"] == "assistant"
        for block in event["message"]["content"]
        if block["type"] == "tool_use"
    ]
    assert [u["name"] for u in uses] == ["Write", "Read", "Bash", "Edit"]
    assert uses[0]["input"]["file_path"] == "/work/hello.txt"
    assert uses[1]["input"]["file_path"].endswith(".agents/skills/demo-skill/SKILL.md")
    assert uses[2]["input"]["command"] == "cat /work/hello.txt && false"
    results = {
        event["message"]["content"][0]["tool_use_id"]: event["message"]["content"][0]
        for event in events
        if event["type"] == "user"
    }
    # `Command exited with code 1` inside a DONE step is a failed command.
    assert results[uses[2]["id"]].get("is_error") is True
    assert not results[uses[0]["id"]].get("is_error")
    texts = [
        block["text"]
        for event in events
        if event["type"] == "assistant"
        for block in event["message"]["content"]
        if block["type"] == "text"
    ]
    assert texts == ["I'll write the file.", "Done."]


def test_usage_keeps_cache_and_thinking_in_claudes_meaning() -> None:
    result = _events("success_tools.stream.jsonl")[-1]

    assert result["type"] == "result" and result["subtype"] == "success"
    # Captured: result.usage = {input 1200, output 60, thinking 5, cache_read 4800}.
    assert result["usage"] == {
        "input_tokens": 1200,
        "cache_read_input_tokens": 4800,
        "output_tokens": 60,
        "reasoning_output_tokens": 5,
    }
    assert result["total_cost_usd"] is None
    assert result["billing_mode"] == "api"
    assert result["harness"] == "antigravity_cli"
    assert result["duration_ms"] == 53


def test_streamed_text_is_joined_from_its_active_deltas() -> None:
    events = _events("success_streamed_text.stream.jsonl")

    text = next(e for e in events if e["type"] == "assistant")
    assert text["message"]["content"][0]["text"] == "P1. P2. P3. P4. P5. END."
    assert events[-1]["result"] == "P1. P2. P3. P4. P5. END."


def test_the_model_comes_from_init_or_the_configured_slug() -> None:
    with_init = _events("subagent_and_tool_error.stream.jsonl")
    assert with_init[0]["model"] == "gemini-3.8-flash-low"
    assert with_init[-1]["modelUsage"]["gemini-3.8-flash-low"]["outputTokens"] == 80

    # No `--model` → no init.model: the configured slug names the run.
    fallback = _events("success_text.stream.jsonl", model="gemini-4-neon-high")
    assert fallback[0]["model"] == "gemini-4-neon-high"
    assert "model" not in _events("success_text.stream.jsonl")[0]


def test_failing_tools_and_subagents_are_counted_as_errors() -> None:
    events = _events("subagent_and_tool_error.stream.jsonl")
    names = [
        block["name"]
        for event in events
        if event["type"] == "assistant"
        for block in event["message"]["content"]
        if block["type"] == "tool_use"
    ]
    assert names[-2:] == ["WebSearch", "Agent"]
    errors = [
        event["message"]["content"][0].get("is_error", False)
        for event in events
        if event["type"] == "user"
    ]
    assert errors[-2:] == [True, True]


def test_a_soft_denied_tool_is_an_error_and_the_turn_ends_with_no_answer() -> None:
    events = _events("soft_denied.stream.jsonl")
    denied = next(
        event["message"]["content"][0]
        for event in events
        if event["type"] == "user"
        and "permission check failed" in event["message"]["content"][0]["content"]
    )
    assert denied["is_error"] is True
    assert events[-1]["subtype"] == "success" and events[-1]["result"] == ""


@pytest.mark.parametrize(
    ("fixture", "words"),
    [
        ("error_429.stream.jsonl", "RESOURCE_EXHAUSTED"),
        ("error_403.stream.jsonl", "reported as leaked"),
        ("error_404.stream.jsonl", "is not found for API version"),
        ("error_500.stream.jsonl", "INTERNAL"),
        ("error_network.stream.jsonl", "network issue"),
        ("error_timeout.stream.jsonl", "print timeout"),
    ],
)
def test_provider_failures_are_error_results_in_the_providers_words(
    fixture: str, words: str
) -> None:
    result = _events(fixture)[-1]

    assert result["type"] == "result" and result["is_error"] is True
    assert words in result["result"]
    rendered = render_antigravity_event(_lines(fixture)[-1])
    assert rendered is not None and "API Error:" in rendered


def test_the_invalid_key_capture_renders_a_cause_and_translates() -> None:
    lines = (FIXTURES / "error_invalid_key.combined.txt").read_text().splitlines()

    result = json.loads(translate("antigravity_cli", lines)[-1])
    assert result["is_error"] and "API_KEY_INVALID" in result["result"]
    # Non-JSON stderr lines (`error:`, `AGY_ERROR:`) pass through the log.
    assert render_antigravity_event(lines[3]) == lines[3] + "\n"


def test_the_step_log_uses_the_shared_waiting_vocabulary() -> None:
    from harness_lab_app.run_failures import awaits_model_response, signals_for

    rendered = [
        render_antigravity_event(line) or ""
        for line in _lines("success_tools.stream.jsonl")
    ]
    assert rendered[0].startswith("# antigravity_cli session init")
    assert any(line.startswith("\n→ Write(/work/hello.txt)") for line in rendered)
    assert signals_for("antigravity_cli").wait != ()
    assert awaits_model_response("# awaiting model", "antigravity_cli")


def test_a_skill_read_counts_as_a_skill_consulted() -> None:
    from harness_lab_app.attribution import parse_claude_events

    parsed = parse_claude_events(
        antigravity_to_claude(_lines("success_tools.stream.jsonl"))
    )
    assert parsed["skills_consulted"] == ["demo-skill"]
    assert "work/hello.txt" in parsed["files_edited"]
    assert parsed["bash_commands"] == {"cat": 1}


# --- usage, cost and failure classification ----------------------------------------


def _run_dir(tmp_path: Path, fixture: str, model: str = "") -> Path:
    out = tmp_path / "run"
    steps = out / "steps"
    steps.mkdir(parents=True)
    raw = steps / "step-1.raw.jsonl"
    raw.write_text((FIXTURES / fixture).read_text())
    bench_run.write_normalized_events(
        "antigravity_cli", raw, steps / "step-1.events.jsonl", model=model
    )
    return out


def test_a_run_is_priced_at_the_gemini_list_price_with_its_basis(
    tmp_path: Path,
) -> None:
    out = _run_dir(tmp_path, "success_tools.stream.jsonl")

    usage = bench_run.assemble_usage(
        out,
        out / "opencode.log",
        run_id="r",
        session_id="s",
        harness="antigravity_cli",
        models=["gemini-3.8-flash-medium"],
        harness_seconds=60.0,
        completed_steps=1,
        stop_reason=None,
        prompt_kinds=["case"],
    )

    assert usage["input_tokens"] == 1200 and usage["cache_read_tokens"] == 4800
    assert usage["output_tokens"] == 60 and usage["reasoning_tokens"] == 5
    assert usage["billing_mode"] == "api"
    assert usage["api_model"] == "gemini-3.8-flash"
    assert usage["thinking_level"] == "medium"
    # 1200 x $0.75 + 4800 x $0.075 + 60 x $3.75, per 1M tokens.
    assert usage["cost_usd"] == pytest.approx(0.001485)
    assert usage["cost_source"] == "api_list_price_estimate"
    assert usage["cost_basis"]["as_of"] == "2026-11-22"
    assert usage["cost_source"] != "subscription_quota"


def test_an_unpriced_model_is_unknown_not_zero(tmp_path: Path) -> None:
    out = _run_dir(tmp_path, "success_tools.stream.jsonl")
    kwargs = {
        "run_id": "r",
        "session_id": "s",
        "harness": "antigravity_cli",
        "harness_seconds": 1.0,
        "completed_steps": 1,
        "stop_reason": None,
        "prompt_kinds": ["case"],
    }

    unpriced = bench_run.assemble_usage(
        out, out / "x.log", models=["gemini-3.7-flash-low"], **kwargs
    )
    assert unpriced["cost_usd"] is None
    assert unpriced["cost_source"] == "api_price_unknown"
    assert "gemini-3.7-flash" in unpriced["cost_unknown_reason"]

    unknown = bench_run.assemble_usage(
        out, out / "x.log", models=["gemini-9-new"], **kwargs
    )
    assert unknown["cost_usd"] is None and unknown["api_model"] is None


def test_a_run_that_reported_no_tokens_has_an_unknown_cost(
    tmp_path: Path,
) -> None:
    out = _run_dir(tmp_path, "error_403.stream.jsonl")
    usage = bench_run.agy_cost(
        {"input_tokens": None, "output_tokens": None}, ["gemini-4-neon-high"]
    )
    assert usage["cost_usd"] is None
    assert usage["cost_unknown_reason"] == "no token usage reported"
    assert (out / "steps" / "step-1.events.jsonl").read_text()


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ((FIXTURES / "error_no_key.stderr.txt").read_text(), "no Gemini API key"),
        (
            "error: invalid model selection: model x is not recognized as a known model",
            "does not know this model",
        ),
    ],
)
def test_a_setup_failure_without_json_names_its_cause(
    tmp_path: Path, text: str, cause: str
) -> None:
    log = tmp_path / "step-1.log"
    log.write_text(text)

    assert cause in (
        bench_run.harness_error_cause(log, harness="antigravity_cli", exit_code=1) or ""
    )


def test_a_provider_error_is_the_recorded_cause(tmp_path: Path) -> None:
    log = tmp_path / "step-1.log"
    log.write_text(
        "".join(
            render_antigravity_event(line) or ""
            for line in _lines("error_429.stream.jsonl")
        )
    )

    cause = bench_run.harness_error_cause(log, harness="antigravity_cli", exit_code=3)

    assert cause is not None and "RESOURCE_EXHAUSTED" in cause


_AGY = "agent executor error: generating and executing: "


@pytest.mark.parametrize(
    ("reason", "label"),
    [
        (_AGY + "Status: INVALID_ARGUMENT reason:API_KEY_INVALID", "API key rejected"),
        (_AGY + "Error 429 Status: RESOURCE_EXHAUSTED", "quota exhausted"),
        (
            "Cause: antigravity_cli does not know this model slug — "
            "model foo is not recognized as a known model",
            "model not served",
        ),
        (
            "Cause: antigravity_cli has no Gemini API key — configure it",
            "API key missing",
        ),
    ],
)
def test_a_credential_quota_or_model_failure_is_the_labs_not_the_agents(
    reason: str, label: str
) -> None:
    from harness_lab_app.run_failures import classify_failure, failure_label

    assert classify_failure(reason) == "lab"
    assert failure_label(reason) == label


@pytest.mark.parametrize(
    "reason",
    [
        "# result: error API Error: 429 RESOURCE_EXHAUSTED quota",
        "Error: API key not valid. Please pass a valid API key.",
    ],
)
def test_other_harnesses_failures_keep_their_classification(reason: str) -> None:
    """A gemini/opencode tail with the same words is not reclassified."""
    from harness_lab_app.run_failures import classify_failure, failure_label

    assert classify_failure(reason) == "agent"
    assert failure_label(reason) == "agent error"


def test_the_run_streams_redact_the_key_before_any_file(tmp_path: Path) -> None:
    log = tmp_path / "step-1.log"
    events = tmp_path / "step-1.events.jsonl"
    leak = (
        '{"event":"step_update","step_update":{"conversation_id":"c","step_index":1,'
        '"state":"DONE","step_type":"tool","tool_name":"run_command","tool_info":'
        f'{{"name":"run_command","parameters":{{"CommandLine":"env"}},"output":"GEMINI_API_KEY={KEY}"}}}}}}'
    )
    script = tmp_path / "fake_agy.py"
    script.write_text(f"print({leak!r})\nprint('plain {KEY}')\n")

    rc = bench_run.run_opencode(
        [sys.executable, str(script)],
        log,
        label="antigravity_cli",
        cwd=tmp_path,
        events_path=events,
        stdin_text=bench_run.agy_stdin_message("x"),
        env={"PATH": "/usr/bin:/bin", "GEMINI_API_KEY": KEY},
    )

    assert rc == 0
    for path in (log, events, bench_run.raw_events_path(events)):
        assert KEY not in path.read_text()
    assert "***REDACTED***" in bench_run.raw_events_path(events).read_text()


# --- catalog ---------------------------------------------------------------------------


def test_the_catalog_is_agys_own_slugs_not_the_old_gemini_list() -> None:
    from harness_lab_app.native_models import (
        AGY_SLUG_MAP,
        NATIVE_MODELS,
        agy_api_model,
        parse_agy_models,
    )

    listed = parse_agy_models((FIXTURES / "models.stdout.txt").read_text())
    slugs = [row["id"] for row in listed]
    assert len(slugs) == 25
    # Every slug agy 1.5.14 offers is in the verified map, and vice versa.
    assert set(slugs) == set(AGY_SLUG_MAP)
    assert {m["id"] for m in NATIVE_MODELS["antigravity_cli"]} == set(slugs)
    assert not {m["id"] for m in NATIVE_MODELS["gemini"]} & set(slugs)
    # Read off the request path agy sent for each slug.
    assert agy_api_model("gemini-3.1-pro-high") == ("gemini-3.1-pro-preview", "high")
    assert agy_api_model("gemini-4-neon-xhigh") == ("gemini-4-neon", "xhigh")
    assert agy_api_model("gemini-9-future") == (None, None)
    assert all(m["effort_levels"] == [] for m in NATIVE_MODELS["antigravity_cli"])


def test_the_runner_pins_the_verified_agy_release() -> None:
    from harness_lab_app.native_models import CLI_VERSION_LABELS, MIN_NATIVE_CLI_VERSION

    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile.runner").read_text()
    assert f"ARG AGY_VERSION={MIN_NATIVE_CLI_VERSION['antigravity_cli']}" in dockerfile
    assert "sha512sum -c" in dockerfile
    assert "AGY_CLI_DISABLE_AUTO_UPDATE=true" in dockerfile
    assert f"LABEL {CLI_VERSION_LABELS['antigravity_cli']}=" in dockerfile
    # Pinned build, never "latest" from the installer.
    assert "install.sh" not in dockerfile.split("ARG AGY_VERSION")[1].split("LABEL")[
        0
    ].replace("https://antigravity.google/cli/install.sh", "")


# --- connection test summary ----------------------------------------------------------


def test_a_connection_test_is_ok_only_when_the_model_answered() -> None:
    output = (FIXTURES / "success_text.stream.jsonl").read_text()

    summary = native_connect.summarize_agy_test(output, 0, "gemini-3.8-flash-low")

    assert summary["state"] == "ok" and summary["answer"] == "ok"
    assert summary["api_model"] == "gemini-3.8-flash"
    assert summary["thinking_level"] == "low"
    assert summary["usage"]["cache_read_tokens"] == 600


@pytest.mark.parametrize(
    ("fixture", "exit_code", "reason"),
    [
        ("error_invalid_key.combined.txt", 3, "auth"),
        ("error_403.stream.jsonl", 3, "auth"),
        ("error_429.stream.jsonl", 3, "quota"),
        ("error_404.stream.jsonl", 3, "model"),
        ("error_network.stream.jsonl", 3, "network"),
        ("error_timeout.stream.jsonl", 3, "timeout"),
        ("error_500.stream.jsonl", 3, "server"),
        ("error_no_key.stderr.txt", 1, "no_key"),
    ],
)
def test_a_failed_connection_test_says_why(
    fixture: str, exit_code: int, reason: str
) -> None:
    summary = native_connect.summarize_agy_test(
        (FIXTURES / fixture).read_text(), exit_code, "gemini-3.8-flash-low"
    )

    assert summary["state"] == "failed"
    assert summary["reason"] == reason
    assert summary["error"]


def test_the_test_command_is_a_real_request_with_an_explicit_model() -> None:
    cmd = native_connect.test_command("antigravity_cli")

    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-low"
    assert cmd[cmd.index("-p") + 1] == native_connect.TEST_PROMPT
    assert "--dangerously-skip-permissions" not in cmd
    assert (
        native_connect.test_command("antigravity_cli", "gemini-4-neon-high")[
            cmd.index("--model") + 1
        ]
        == "gemini-4-neon-high"
    )


# --- preflight --------------------------------------------------------------------------


class _Exec:
    def __init__(self, row: Any = None):
        self._row = row

    def first(self) -> Any:
        return self._row


class _Session:
    """Answers a Case lookup with ``case`` and an AdhocTask lookup with ``adhoc``."""

    def __init__(self, case: Any = None, adhoc: Any = None):
        self._case = case
        self._adhoc = adhoc

    def exec(self, statement: Any) -> _Exec:
        table = str(statement).split("FROM", 1)[-1].strip().split()[0].lower()
        return _Exec(self._adhoc if "adhoc" in table else self._case)


def _service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    backend: str = "docker",
    facts: dict | None = None,
    case: Any = None,
    adhoc: Any = None,
    key: bool = True,
):
    import harness_lab_app.health_service as hs
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunService

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.delenv(api_key_auth.ALLOW_UNTRUSTED_ENV, raising=False)
    if key:
        api_key_auth.save_key(KEY)
    monkeypatch.setattr(rs.get_settings(), "execution_backend", backend, raising=False)
    monkeypatch.setattr(
        hs,
        "worker_facts",
        lambda **_k: facts
        if facts is not None
        else {
            "runner_image_present": True,
            "cli_versions": {"antigravity_cli": "1.5.14"},
        },
    )
    service = RunService.__new__(RunService)
    service.session = _Session(case, adhoc)  # type: ignore[assignment]
    monkeypatch.setattr(service, "_check_runner_image", lambda _config=None: None)
    monkeypatch.setattr(service, "_check_target", lambda _config=None: None)
    return service


def _config(**overrides: Any):
    from harness_lab_app.schemas import RunConfigCreate

    fields = {
        "case_id": "tetris-v1",
        "prompt_variant": "naive",
        "harness": "antigravity_cli",
        "executor_model": "gemini-3.8-flash-medium",
    }
    fields.update(overrides)
    return RunConfigCreate(**fields)


def test_preflight_accepts_a_configured_key_and_a_verified_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _service(monkeypatch, tmp_path).preflight(_config())


@pytest.mark.parametrize(
    ("setup", "overrides", "message"),
    [
        ({"backend": "subprocess"}, {}, "only in the Docker runner"),
        ({}, {"executor_model": ""}, "Pick a model"),
        (
            {},
            {"executor_model": "gemini-9-future"},
            "not a model slug the lab verified",
        ),
        ({}, {"executor_model": "gateway/gemini-2.5-pro"}, "gateway"),
        ({"key": False}, {}, "No Gemini API key"),
        (
            {"facts": {"runner_image_present": True, "cli_versions": {}}},
            {},
            "no Antigravity CLI",
        ),
        (
            {
                "facts": {
                    "runner_image_present": True,
                    "cli_versions": {"antigravity_cli": "1.4.0"},
                }
            },
            {},
            "verified on 1.5.14",
        ),
        ({}, {"executor_effort": "high"}, "effort"),
    ],
)
def test_preflight_refuses_what_cannot_run(
    setup: dict,
    overrides: dict,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from harness_lab_app.run_service import RunPreflightError

    service = _service(monkeypatch, tmp_path, **setup)
    with pytest.raises(RunPreflightError, match=message):
        service.preflight(_config(**overrides))


def test_third_party_code_is_refused_unless_the_operator_allows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.models import CaseType
    from harness_lab_app.run_service import RunPreflightError

    class _Case:
        case_id = "django__django-1"
        type = CaseType.swe_bench_instance

    service = _service(monkeypatch, tmp_path, case=_Case())
    with pytest.raises(RunPreflightError, match="HARNESS_LAB_AGY_ALLOW_UNTRUSTED=1"):
        service.preflight(_config(case_id="django__django-1"))
    with pytest.raises(RunPreflightError, match="target repository"):
        _service(monkeypatch, tmp_path).preflight(_config(target_repo_id="acme-api"))

    # The operator's own folder is exempt only when it is a real ad hoc task:
    # a target merely *named* "adhoc-…" is someone else's code.
    assert (
        _service(monkeypatch, tmp_path, adhoc=object())._untrusted_code_reason(
            _config(target_repo_id="adhoc-my-repo-001")
        )
        == ""
    )
    assert "adhoc-x" in _service(monkeypatch, tmp_path)._untrusted_code_reason(
        _config(target_repo_id="adhoc-x")
    )

    monkeypatch.setenv(api_key_auth.ALLOW_UNTRUSTED_ENV, "1")
    service.preflight(_config(case_id="django__django-1"))


def test_a_batch_checks_every_case_of_an_agy_arm() -> None:
    """The preflight cache must not let a SWE arm ride on a lab case's answer."""
    from harness_lab_app.run_service import _preflight_key

    lab, swe = _config(case_id="tetris-v1"), _config(case_id="django__django-1")
    assert _preflight_key(lab) != _preflight_key(swe)
    # Other harnesses keep one check per model: their cache is not fragmented.
    assert _preflight_key(
        _config(harness="codex", executor_model="")
    ) == _preflight_key(
        _config(harness="codex", executor_model="", case_id="django__django-1")
    )


# --- hashes and history ----------------------------------------------------------------


def test_an_agy_arm_never_shares_a_hash_and_history_keeps_its_own() -> None:
    from harness_lab_app.provenance import compute_condition_hash

    base = {
        "harness_hash": "h",
        "model_condition": {"executor_model": "gemini-3.8-flash-medium"},
        "prompt_hash": "p",
        "case_hash": "c",
    }
    agy = compute_condition_hash(**base, harness="antigravity_cli")
    gemini = compute_condition_hash(**base, harness="gemini")
    claude = compute_condition_hash(**base, harness="claude")

    assert len({agy, gemini, claude}) == 3
    # claude/opencode stay out of the identity: their recorded hashes hold.
    assert (
        claude
        == compute_condition_hash(**base)
        == compute_condition_hash(**base, harness="opencode")
    )


def test_the_manual_antigravity_flow_and_the_legacy_gemini_card_are_untouched() -> None:
    from harness_lab_app.governance import HARNESS_RULES_FILE

    assert HARNESS_RULES_FILE["antigravity"] == ".agents/rules/GEMINI.md"
    assert HARNESS_RULES_FILE["antigravity_cli"] == ".agents/rules/GEMINI.md"
    assert "gemini" in native_connect.UNAVAILABLE
    assert "antigravity_cli" not in native_connect.UNAVAILABLE
    assert "antigravity_cli" not in native_connect.CONNECTABLE


# --- API --------------------------------------------------------------------------------


def _endpoint(app: Any, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", ()
        ):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


def test_the_api_queues_the_key_masked_and_never_echoes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    import harness_lab_app.celery_app as celery_module
    from harness_lab_app.api import create_app
    from harness_lab_app.schemas import CliApiKeyUpdate

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(
        native_connect, "state_dir", lambda _root=None: tmp_path / "state"
    )
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        celery_module.celery_app,
        "send_task",
        lambda name, **kwargs: sent.append({"name": name, **kwargs}),
    )
    app = create_app()
    save = _endpoint(app, "/api/harness/clis/{harness}/api-key", "PUT")
    read = _endpoint(app, "/api/harness/clis/{harness}/api-key", "GET")

    body = CliApiKeyUpdate(api_key=KEY)
    response = save("antigravity_cli", body)

    assert response["state"] == "queued"
    assert KEY not in json.dumps(response)
    # The request model never prints the key, not even in its repr.
    assert KEY not in repr(body) and KEY not in str(body.model_dump())
    task = sent[-1]
    assert task["name"] == "harness_lab_app.tasks.save_cli_api_key"
    # The broker never carries the key: only the name of a 0600 drop file,
    # which is all an "unregistered task" dump could ever print.
    assert KEY not in json.dumps(task, default=str)
    drop = task["kwargs"]["drop"]
    drop_file = tmp_path / "state" / drop
    assert stat.S_IMODE(drop_file.stat().st_mode) == 0o600
    assert drop_file.read_text() == KEY
    assert task["ignore_result"] is True

    with pytest.raises(HTTPException) as bad:
        save("antigravity_cli", CliApiKeyUpdate(api_key="x y"))
    assert bad.value.status_code == 400 and "x y" not in str(bad.value.detail)
    with pytest.raises(HTTPException) as other:
        save("codex", body)
    assert other.value.status_code == 404
    # No device login for an API-key harness.
    connect = _endpoint(app, "/api/harness/clis/{harness}/connect", "POST")
    with pytest.raises(HTTPException) as login:
        connect("antigravity_cli")
    assert login.value.status_code == 404
    assert read("antigravity_cli")["api_key"]["configured"] is False


def test_the_worker_writes_the_key_and_a_new_key_clears_the_old_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app import tasks

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(
        native_connect, "state_dir", lambda _root=None: tmp_path / "state"
    )
    native_connect.write_state("antigravity_cli", "test", {"state": "ok"})
    state = tmp_path / "state"
    drop = api_key_auth.write_drop(KEY, state)

    record = tasks.save_cli_api_key("antigravity_cli", drop=drop)

    assert record["state"] == "saved" and record["key_hint"] == "…1234"
    assert KEY not in json.dumps(record)
    assert api_key_auth.read_key() == KEY
    assert not (state / drop).exists()  # collected once, then gone
    assert native_connect.read_state("antigravity_cli", "test")["state"] == "idle"
    # A replayed, forged or path-like name never reads anything.
    for name in (drop, "../../etc/passwd", "key-drop-zz"):
        assert tasks.save_cli_api_key("antigravity_cli", drop=name)["state"] == "failed"

    tasks.remove_cli_api_key("antigravity_cli")
    assert api_key_auth.read_key() == ""


def test_the_card_row_reports_the_key_by_its_tail_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app import health_service

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(
        native_connect, "state_dir", lambda _root=None: tmp_path / "state"
    )
    api_key_auth.save_key(KEY)

    row = health_service._agy_row(
        "docker", {"cli_versions": {"antigravity_cli": "1.5.14"}}
    )

    assert row["session"] == "connected" and row["billing_mode"] == "api"
    assert row["auth"] == "api_key" and row["provider"] == "gemini_api"
    assert row["api_key"]["hint"] == "…1234"
    assert row["version"] == "1.5.14" and row["lane_supported"] is True
    assert row["unavailable"] is None and row["connectable"] is False
    assert KEY not in json.dumps(row)
    assert health_service._agy_row("host", None)["lane_supported"] is False


def test_the_validation_cost_names_its_basis_and_other_runs_are_unchanged(
    tmp_path: Path,
) -> None:
    from harness_lab_app.validation import collect_cost

    out = _run_dir(tmp_path, "success_tools.stream.jsonl")
    usage = bench_run.assemble_usage(
        out,
        out / "opencode.log",
        run_id="r",
        session_id="s",
        harness="antigravity_cli",
        models=["gemini-4-neon-high"],
        harness_seconds=60.0,
        completed_steps=1,
        stop_reason=None,
        prompt_kinds=["case"],
    )
    bench_run.write_usage_json(out, usage)

    cost = collect_cost(out)

    assert cost["cost_source"] == "api_list_price_estimate"
    assert cost["billing_mode"] == "api"
    assert cost["api_model"] == "gemini-4-neon"
    assert cost["cost_basis"]["source"].startswith("https://aistudio.google.com")
    # 1200 x $3.00 + 4800 x $0.15 + 60 x $15.00, per 1M tokens.
    assert cost["cost_usd"] == pytest.approx(0.00522)

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "usage.json").write_text(json.dumps({"input_tokens": 1, "cost_usd": 0.5}))
    assert "api_model" not in collect_cost(legacy)
    assert "cost_basis" not in collect_cost(legacy)


def test_an_uncollected_drop_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    drop = api_key_auth.write_drop(KEY, tmp_path)
    old = time.time() - api_key_auth.DROP_TTL_SECONDS - 5
    os.utime(tmp_path / drop, (old, old))

    assert api_key_auth.take_drop(drop, tmp_path) == ""
    assert not (tmp_path / drop).exists()


def test_two_saves_at_once_never_share_a_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    keys = [f"AIzaCONCURRENT{n:02d}xxxxxxxxxxxxxxxxxxx" for n in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(api_key_auth.save_key, keys))

    assert api_key_auth.read_key() in keys  # one whole key, never a mix
    assert not list(api_key_auth.store_dir().glob(".key-*.tmp"))


def test_a_run_is_priced_at_the_rate_of_its_own_date() -> None:
    from harness_lab_app.api_price_estimate import (
        GEMINI_PRICES_VALID_UNTIL,
        estimate_gemini_api_cost,
        gemini_price,
    )

    assert gemini_price("gemini-4-carbon", "2026-11-30") == (2.00, 0.10, 10.00)
    assert gemini_price("gemini-4-carbon", "2026-12-01") == (4.00, 0.20, 20.00)
    assert gemini_price("gemini-3.8-flash", "2027-01-01") == (1.50, 0.15, 7.50)
    # Past what the table was read for: unknown, never a stale price.
    assert gemini_price("gemini-4-neon", "2099-01-01") is None
    assert GEMINI_PRICES_VALID_UNTIL < "2099-01-01"
    assert gemini_price("gemini-3.7-flash", "2026-11-22") is None
    assert (
        estimate_gemini_api_cost("gemini-4-carbon", 1_000_000, 0, 0, on="2026-12-02")
        == 4.0
    )


def test_a_killed_step_keeps_its_tokens(tmp_path: Path) -> None:
    """No `result` (the watchdog stopped agy): the per-step usage is summed."""
    lines = [
        line
        for line in _lines("success_tools.stream.jsonl")
        if '"event":"result"' not in line
    ]

    result = json.loads(antigravity_to_claude(lines)[-1])

    assert result["type"] == "result" and result["is_error"] is True
    assert result["usage_partial"] is True
    # The five DONE agent_response steps of the capture: 400+200+200+200+200.
    assert result["usage"]["input_tokens"] == 1200
    assert result["usage"]["cache_read_input_tokens"] == 4800
    assert result["usage"]["output_tokens"] == 60


def test_the_live_sidecar_refreshes_on_agy_steps(tmp_path: Path) -> None:
    import inspect

    source = inspect.getsource(bench_run.run_opencode)
    assert '\'"state":"DONE"\' in line' in source


def test_a_retry_rechecks_the_key_and_the_untrusted_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.models import CaseType
    from harness_lab_app.run_service import RunPreflightError

    class _Case:
        case_id = "django__django-1"
        type = CaseType.swe_bench_instance

    service = _service(monkeypatch, tmp_path, case=_Case())
    stored = _config(case_id="django__django-1")
    monkeypatch.setattr(service, "_config_create", lambda _config: stored)

    class _Run:
        run_config_id = 1
        state = "failed"

    monkeypatch.setattr(service, "get_run", lambda _run_id: _Run())
    monkeypatch.setattr(service, "get_run_config", lambda _id: stored)

    with pytest.raises(RunPreflightError, match="ALLOW_UNTRUSTED"):
        service.retry_run("django__django-1-K-001", "resume")


def test_an_agy_container_cannot_read_other_runs_sessions_or_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lab(tmp_path, monkeypatch)
    (tmp_path / "data/runs/other-K-1/session/codex").mkdir(parents=True)
    (tmp_path / "data/runs/mine-K-1/session").mkdir(parents=True)
    (tmp_path / "data/app/env-backups").mkdir(parents=True)
    (tmp_path / "data/app/native-connect").mkdir(parents=True)
    (tmp_path / "data/app/harness_lab.db").write_text("db")
    (tmp_path / "backups").mkdir()
    client = _Client()

    DockerRunExecutor(client_factory=lambda: client).start(
        "mine-K-1", ["true"], harness="antigravity_cli"
    )

    tmpfs = client.run_kwargs["tmpfs"]
    for hidden in (
        "/app/data/runs/other-K-1/session",
        "/app/data/app/env-backups",
        "/app/data/app/native-connect",
        "/app/backups",
    ):
        assert hidden in tmpfs
    assert "/app/data/runs/mine-K-1/session" not in tmpfs
    targets = {m["Target"] for m in client.run_kwargs.get("mounts") or []}
    assert "/app/data/app/harness_lab.db" in targets
    # A claude run of the same lab is unchanged.
    other = _Client()
    DockerRunExecutor(client_factory=lambda: other).start(
        "c-K-2", ["true"], harness="claude"
    )
    assert "/app/backups" not in other.run_kwargs["tmpfs"]


def test_the_key_written_anywhere_in_the_lab_is_scrubbed_after_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from harness_lab_app import tasks

    class _S:
        repo_root = tmp_path
        data_runs_dir = tmp_path / "data" / "runs"
        workspace_dir = tmp_path / "workspace"

    monkeypatch.setattr(tasks, "get_settings", lambda: _S())
    started = time.time() - 1
    run = tmp_path / "data/runs/r-K-1"
    run.mkdir(parents=True)
    (run / "metadata.json").write_text("{}")
    (tmp_path / "notes.txt").write_text(f"leaked {KEY}\n")
    (tmp_path / "workspace" / "r-K-1").mkdir(parents=True)
    (tmp_path / "workspace" / "r-K-1" / "x.py").write_text(f"K='{KEY}'\n")

    hits = tasks._redact_api_key("r-K-1", KEY, since=started)

    assert hits == 2
    assert KEY not in (tmp_path / "notes.txt").read_text()
    assert KEY not in (tmp_path / "workspace/r-K-1/x.py").read_text()
    assert (
        json.loads((run / "metadata.json").read_text())["secret_exposed"]["hits"] == 2
    )
    # Idempotent: a second pass finds nothing.
    assert tasks._redact_api_key("r-K-1", KEY, since=started) == 0


def test_host_mode_health_is_not_degraded_by_agy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app import health_service

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(native_connect, "state_dir", lambda _root=None: tmp_path / "s")
    monkeypatch.setattr(
        health_service.get_settings(), "execution_backend", "subprocess", raising=False
    )

    status, detail, _ = health_service._check_agy({})

    assert status == health_service.OK and "Not applicable" in detail


def test_a_mixed_case_slug_is_sent_as_the_one_preflight_checked() -> None:
    cmd = bench_run.build_agy_cmd("Gemini-3.8-Flash-Low")
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-low"
