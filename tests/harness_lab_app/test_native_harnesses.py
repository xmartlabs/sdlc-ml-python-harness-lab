"""codex and gemini as subscription-backed harnesses (ADR-0094).

What these tests hold the integration to:

* the exact argv of each CLI, verified against codex-cli 0.171.0 and
  @google/gemini-cli 0.70.0 (``--help``, a real runner container), per lane;
* a run spends a plan's quota, never a per-token key that sits in ``.env``;
* a run never sees the operator's personal ``~/.codex`` or ``~/.gemini``;
* each harness sees exactly one lab skill catalog in Docker — its own;
* a Docker run mounts only its own provider's session;
* a subscription run's cost is unknown, never 0;
* the judges' transport (ADR-0043) does not move.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402

from harness_lab_app import executors, native_auth  # noqa: E402
from harness_lab_app.executors import (  # noqa: E402
    GOVERNANCE_MASK_ALL,
    DockerRunExecutor,
    build_run_environment,
    runner_masks,
    runner_skill_masks,
)

# --- command builders -------------------------------------------------------


def test_codex_on_the_host_keeps_its_own_workspace_write_sandbox() -> None:
    cmd = bench_run.build_codex_cmd("gpt-6.2-sol", in_container=False)

    assert cmd == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-m",
        "gpt-6.2-sol",
        "-",
    ]
    # The flags codex-cli 0.171.0 refuses must never come back.
    assert "--full-auto" not in cmd and "--quiet" not in cmd


def test_codex_in_the_runner_relies_on_the_container() -> None:
    # bubblewrap cannot create a namespace inside the runner container, so
    # workspace-write would kill every command there (verified on the image).
    cmd = bench_run.build_codex_cmd(None, in_container=True)

    assert cmd == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    ]
    assert "--sandbox" not in cmd


def test_gemini_is_sandboxed_on_the_host_and_contained_in_docker() -> None:
    host = bench_run.build_gemini_cmd("gemini-2.5-pro", in_container=False)
    docker = bench_run.build_gemini_cmd(None, in_container=True)

    assert host == [
        "gemini",
        "--output-format",
        "stream-json",
        "--approval-mode",
        "yolo",
        "--sandbox",
        "-m",
        "gemini-2.5-pro",
        "-p",
        "",
    ]
    assert docker == [
        "gemini",
        "--output-format",
        "stream-json",
        "--approval-mode",
        "yolo",
        "-p",
        "",
    ]


def test_the_prompt_goes_to_stdin_never_to_argv() -> None:
    prompt = "x" * 300_000  # far beyond what argv carries comfortably
    for harness in ("codex", "gemini"):
        cmd = bench_run.build_harness_cmd(harness, None, None, prompt)
        assert prompt not in cmd
        assert harness in bench_run.STDIN_PROMPT_HARNESSES


def test_a_gateway_model_never_reaches_a_native_cli() -> None:
    for harness in ("codex", "gemini"):
        cmd = bench_run.build_harness_cmd(harness, "gateway/gpt-5.5", None, "x")
        assert "-m" not in cmd


def test_claude_and_opencode_commands_are_unchanged() -> None:
    assert bench_run.build_harness_cmd("opencode", "gateway/m", None, "go") == [
        "opencode",
        "run",
        "--dangerously-skip-permissions",
        "--print-logs",
        "-m",
        "gateway/m",
        "go",
    ]
    assert bench_run.build_harness_cmd("claude", "claude-opus-5-5", None, "go") == [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-opus-5-5",
        "go",
    ]


def test_sandbox_is_recorded_per_lane() -> None:
    assert bench_run.harness_sandbox("codex", in_container=False) == "workspace-write"
    assert bench_run.harness_sandbox("codex", in_container=True) == "container"
    assert bench_run.harness_sandbox("gemini", in_container=False) == "seatbelt"
    assert bench_run.harness_sandbox("opencode", in_container=False) == ""


# --- running a step ---------------------------------------------------------


def _fake_cli(tmp_path: Path, lines: list[dict], exit_code: int = 0) -> list[str]:
    """A stand-in CLI that echoes its stdin size and prints fixed JSONL."""
    script = tmp_path / "fake_cli.py"
    payload = "\n".join(json.dumps(line) for line in lines)
    script.write_text(
        "import sys\n"
        "data = sys.stdin.read()\n"
        f"open({str(tmp_path / 'stdin.txt')!r}, 'w').write(data)\n"
        f"print({payload!r})\n"
        f"sys.exit({exit_code})\n"
    )
    return [sys.executable, str(script)]


def test_a_codex_step_archives_raw_events_and_writes_the_normalized_view(
    tmp_path: Path,
) -> None:
    events = [
        {"type": "thread.started", "thread_id": "th-1"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "i1", "type": "agent_message", "text": "done"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "output_tokens": 3,
                "reasoning_output_tokens": 1,
            },
        },
    ]
    steps = tmp_path / "steps"
    steps.mkdir()
    log = steps / "step-1.log"
    events_path = steps / "step-1.events.jsonl"

    rc = bench_run.run_opencode(
        _fake_cli(tmp_path, events),
        log,
        label="codex",
        cwd=tmp_path,
        events_path=events_path,
        stdin_text="THE PROMPT",
    )

    assert rc == 0
    assert (tmp_path / "stdin.txt").read_text() == "THE PROMPT"
    raw = (steps / "step-1.raw.jsonl").read_text().splitlines()
    assert json.loads(raw[0])["type"] == "thread.started"
    assert all('"_received_at"' in line for line in raw)
    normalized = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert normalized[0]["subtype"] == "init"
    assert normalized[-1]["type"] == "result"
    assert "# codex session init" in log.read_text()

    usage = bench_run.collect_claude_usage(tmp_path)
    assert usage["input_tokens"] == 6
    assert usage["cache_read_tokens"] == 4
    assert usage["output_tokens"] == 3  # reasoning is inside it, not added
    assert usage["billing_mode"] == "subscription"
    assert usage["cost_source"] == "subscription_quota"
    assert "cost_usd" not in usage


def test_a_gemini_exit_41_names_the_missing_login(tmp_path: Path) -> None:
    log = tmp_path / "step-1.log"
    log.write_text("# gemini run started\nPlease set an Auth method\n")

    cause = bench_run.harness_error_cause(log, harness="gemini", exit_code=41)

    assert cause is not None and "make auth-gemini" in cause


def test_a_codex_401_is_the_recorded_cause(tmp_path: Path) -> None:
    log = tmp_path / "step-1.log"
    log.write_text(
        "# turn failed: API Error: unexpected status 401 Unauthorized: Missing bearer\n"
    )

    cause = bench_run.harness_error_cause(log, harness="codex", exit_code=1)

    assert cause is not None and "401 Unauthorized" in cause


def test_a_subscription_run_is_never_priced_at_zero(tmp_path: Path) -> None:
    log = tmp_path / "opencode.log"
    log.write_text("")

    usage = bench_run.assemble_usage(
        tmp_path,
        log,
        run_id="r-1",
        session_id="s-1",
        harness="gemini",
        models=["gemini-2.5-pro"],
        harness_seconds=60.0,
        completed_steps=1,
        stop_reason=None,
        prompt_kinds=[],
    )

    assert usage["cost_usd"] is None
    assert usage["billing_mode"] == "subscription"
    assert usage["cost_source"] == "subscription_quota"


# --- sessions and environment -----------------------------------------------


def test_the_session_store_never_points_at_the_operators_own_cli_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))

    codex = native_auth.ensure_session_dir("codex")
    gemini = native_auth.ensure_session_dir("gemini")

    assert codex == tmp_path / "auth" / "codex"
    assert gemini == tmp_path / "auth" / "gemini"
    assert Path("~/.codex").expanduser() not in (codex, codex.parent)
    config = (codex / "config.toml").read_text()
    assert "memories = false" in config
    settings = json.loads((gemini / ".gemini" / "settings.json").read_text())
    assert settings["security"]["auth"]["selectedType"] == "oauth-personal"
    assert settings["general"]["enableAutoUpdate"] is False
    assert oct(codex.stat().st_mode & 0o777) == "0o700"


def test_session_status_reads_the_shape_never_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path))
    assert native_auth.session_status("codex")["state"] == "missing"

    codex = native_auth.ensure_session_dir("codex")
    (codex / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {"refresh_token": "secret-value", "access_token": "a"},
            }
        )
    )
    status = native_auth.session_status("codex")
    assert status["state"] == "connected"
    assert "secret-value" not in json.dumps(status)

    (codex / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-x"}))
    assert native_auth.session_status("codex")["state"] == "api_key"

    gemini = native_auth.ensure_session_dir("gemini")
    (gemini / ".gemini" / "oauth_creds.json").write_text("{not json")
    assert native_auth.session_status("gemini")["state"] == "unreadable"
    (gemini / ".gemini" / "oauth_creds.json").write_text(
        json.dumps({"refresh_token": "r", "expiry_date": 1})
    )
    assert native_auth.session_status("gemini")["state"] == "connected"


def test_api_keys_cannot_switch_a_native_run_to_per_token_billing() -> None:
    env = {
        "OPENAI_API_KEY": "sk-1",
        "CODEX_API_KEY": "sk-2",
        "GEMINI_API_KEY": "g-1",
        "GOOGLE_API_KEY": "g-2",
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "ANTHROPIC_API_KEY": "a-1",
        "CLAUDE_CODE_OAUTH_TOKEN": "t-1",
        "GATEWAY_TOKEN": "keep-me",
        "PATH": "/usr/bin",
    }

    codex = native_auth.harness_env(
        "codex",
        env,
        session_root="/s/codex",
        home="/tmp/h",  # nosec B108 - a container path string, no file is created
    )
    gemini = native_auth.harness_env("gemini", env, session_root="/s/gemini")

    for scrubbed in (codex, gemini):
        assert not {
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_GENAI_USE_VERTEXAI",
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
        } & set(scrubbed)
        assert scrubbed["PATH"] == "/usr/bin"
    assert codex["CODEX_HOME"] == "/s/codex"
    assert codex["HOME"] == "/tmp/h"  # nosec B108 - a container path string, no file is created
    assert gemini["GEMINI_CLI_HOME"] == "/s/gemini"
    assert gemini["GOOGLE_GENAI_USE_GCA"] == "true"
    assert gemini["GEMINI_CLI_TRUST_WORKSPACE"] == "true"
    # Other harnesses are left exactly as they were.
    assert native_auth.harness_env("claude", env, session_root="/x") == env


def test_billing_mode_follows_the_credential() -> None:
    assert native_auth.billing_mode("codex") == "subscription"
    assert native_auth.billing_mode("gemini") == "subscription"
    assert (
        native_auth.billing_mode("claude", {"CLAUDE_CODE_OAUTH_TOKEN": "t"})
        == "subscription"
    )
    assert native_auth.billing_mode("claude", {"ANTHROPIC_API_KEY": "k"}) == "api"
    assert (
        native_auth.billing_mode(
            "claude",
            {"CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "http://gw"},
        )
        == "gateway"
    )


# --- docker lane ------------------------------------------------------------


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
    def logs(self, **_kwargs: Any):
        return iter([b"ok\n"])

    def wait(self) -> dict:
        return {"StatusCode": 0}

    def remove(self, **_kwargs: Any) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] = {}
        client = self

        class _Containers:
            def run(self, **kwargs: Any) -> _Container:
                client.run_kwargs = kwargs
                return _Container()

            def get(self, _name: str):
                raise LookupError

        class _Images:
            def get(self, _name: str) -> object:
                return object()

        self.containers = _Containers()
        self.images = _Images()


def test_each_harness_keeps_exactly_its_own_skill_catalog_in_docker() -> None:
    expected_visible = {
        "claude": ".claude/skills",
        "opencode": ".claude/skills",
        "codex": ".codex/skills",
        "gemini": ".gemini/skills",
    }
    every_mirror = {
        "/app/.claude/skills",
        "/app/.agents/skills",
        "/app/.opencode/skills",
        "/app/.codex/skills",
        "/app/.gemini/skills",
    }
    for harness, visible in expected_visible.items():
        masks = set(runner_skill_masks("/app", harness))
        assert masks == every_mirror - {f"/app/{visible}"}, harness
        # `.agents/skills` is read by both codex and gemini; it is always masked
        # so the catalog never loads twice.
        assert "/app/.agents/skills" in masks


def test_bare_masks_hide_every_native_harness_surface() -> None:
    for harness in ("codex", "gemini"):
        tmpfs, files = runner_masks(GOVERNANCE_MASK_ALL, harness=harness, hidden=[])
        targets = {target for target, _kind in files}
        for directory in (".codex", ".agents", ".gemini", ".claude", ".github"):
            assert f"/app/{directory}" in tmpfs, (harness, directory)
        assert {"/app/AGENTS.md", "/app/GEMINI.md"} <= targets
        # The grading masks apply whatever the harness (ADR-0079).
        assert "/app/data/reference" in tmpfs


def test_a_docker_codex_run_mounts_a_private_copy_of_its_own_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: _Settings(tmp_path))
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    _connect("codex")
    _connect("gemini")
    store = native_auth.session_dir("codex")
    (store / "AGENTS.md").write_text("left by an earlier run")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-should-not-leak\n")
    client = _Client()

    DockerRunExecutor(client_factory=lambda: client).start(
        "tetris-v1-K-900", ["python", "scripts/bench_run.py"], harness="codex"
    )

    mounts = client.run_kwargs.get("mounts") or []
    sessions = [m for m in mounts if m["Target"].startswith("/run/harness-auth/")]
    assert len(sessions) == 1
    assert sessions[0]["Target"] == "/run/harness-auth/codex"
    # The store is never mounted: a per-run copy under the run directory, as
    # the host daemon sees it.
    assert sessions[0]["Source"] == (
        "/host/repo/data/runs/tetris-v1-K-900/session/codex"
    )
    assert sessions[0]["ReadOnly"] is False  # token refresh writes back
    staged = tmp_path / "data/runs/tetris-v1-K-900/session/codex"
    assert sorted(p.name for p in staged.iterdir()) == ["auth.json", "config.toml"]
    env = client.run_kwargs["environment"]
    assert env["CODEX_HOME"] == "/run/harness-auth/codex"
    assert env["HOME"] == "/tmp/agent-home"  # nosec B108 - a container path string, no file is created
    assert env["HARNESS_LAB_IN_RUNNER"] == "1"
    assert "OPENAI_API_KEY" not in env
    assert "/app/.codex/skills" not in client.run_kwargs["tmpfs"]
    assert "/app/.claude/skills" in client.run_kwargs["tmpfs"]


def test_a_claude_or_hardened_container_gets_no_native_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: _Settings(tmp_path))
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    for kwargs in ({"harness": "claude"}, {"harness": "codex", "hardened": True}):
        client = _Client()
        DockerRunExecutor(client_factory=lambda c=client: c).start(
            "case-K-1", ["true"], **kwargs
        )
        mounts = client.run_kwargs.get("mounts") or []
        assert not any(m["Target"].startswith("/run/harness-auth") for m in mounts)


def test_the_run_environment_is_unchanged_for_harnesses_without_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executors, "get_settings", lambda: _Settings(tmp_path))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-1\n")

    env = build_run_environment("opencode")

    assert env["OPENAI_API_KEY"] == "sk-1"
    assert "CODEX_HOME" not in env


# --- preflight ----------------------------------------------------------------


def _service(monkeypatch: pytest.MonkeyPatch, backend: str, facts: dict):
    import harness_lab_app.health_service as hs
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunService

    monkeypatch.setattr(rs.get_settings(), "execution_backend", backend, raising=False)
    monkeypatch.setattr(hs, "worker_facts", lambda **_k: facts)
    service = RunService.__new__(RunService)
    monkeypatch.setattr(service, "_check_runner_image", lambda _config=None: None)
    return service


def _connect(harness: str) -> None:
    root = native_auth.ensure_session_dir(harness)
    if harness == "codex":
        (root / "auth.json").write_text(
            json.dumps({"auth_mode": "chatgpt", "tokens": {"refresh_token": "r"}})
        )
    else:
        (root / ".gemini" / "oauth_creds.json").write_text(
            json.dumps({"refresh_token": "r"})
        )


@pytest.mark.parametrize("harness", ["codex", "gemini"])
def test_preflight_accepts_a_signed_in_runner(
    harness: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.schemas import RunConfigCreate

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path))
    _connect(harness)
    facts = {
        "runner_image_present": True,
        "cli_versions": {"codex": "0.171.0", "gemini": "0.70.0"},
    }
    service = _service(monkeypatch, "docker", facts)

    service.preflight(
        RunConfigCreate(case_id="tetris-v1", prompt_variant="naive", harness=harness)
    )


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        ({"runner_image_present": True, "cli_versions": {}}, "no codex CLI"),
        (
            {"runner_image_present": True, "cli_versions": {"codex": "0.120.0"}},
            "verified on 0.171.0",
        ),
    ],
)
def test_preflight_refuses_a_runner_without_a_verified_codex(
    facts: dict, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.run_service import RunPreflightError
    from harness_lab_app.schemas import RunConfigCreate

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path))
    _connect("codex")
    service = _service(monkeypatch, "docker", facts)

    with pytest.raises(RunPreflightError, match=message):
        service.preflight(
            RunConfigCreate(
                case_id="tetris-v1", prompt_variant="naive", harness="codex"
            )
        )


def test_preflight_refuses_an_api_key_session_and_a_gateway_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harness_lab_app.run_service import RunPreflightError
    from harness_lab_app.schemas import RunConfigCreate

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path))
    root = native_auth.ensure_session_dir("codex")
    (root / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-1"}))
    facts = {"runner_image_present": True, "cli_versions": {"codex": "0.171.0"}}
    service = _service(monkeypatch, "docker", facts)

    with pytest.raises(RunPreflightError, match="per token"):
        service.preflight(
            RunConfigCreate(
                case_id="tetris-v1", prompt_variant="naive", harness="codex"
            )
        )
    with pytest.raises(RunPreflightError, match="gateway"):
        service.preflight(
            RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="naive",
                harness="gemini",
                executor_model="gateway/gpt-5.5",
            )
        )


def test_preflight_refuses_effort_on_a_native_cli(monkeypatch) -> None:
    from harness_lab_app.run_service import RunPreflightError
    from harness_lab_app.schemas import RunConfigCreate

    service = _service(monkeypatch, "docker", {})
    with pytest.raises(RunPreflightError, match="effort"):
        service.preflight(
            RunConfigCreate(
                case_id="tetris-v1",
                prompt_variant="naive",
                harness="codex",
                executor_effort="high",
            )
        )


def test_preflight_on_the_host_needs_the_cli_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from harness_lab_app.run_service import RunPreflightError
    from harness_lab_app.schemas import RunConfigCreate

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path))
    _connect("gemini")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    service = _service(monkeypatch, "subprocess", {})

    with pytest.raises(RunPreflightError, match="not on the worker's PATH"):
        service.preflight(
            RunConfigCreate(
                case_id="tetris-v1", prompt_variant="naive", harness="gemini"
            )
        )


# --- catalog and judges --------------------------------------------------------


def test_the_native_catalog_offers_only_verified_ids_and_no_gateway_claim() -> None:
    from harness_lab_app.native_models import NATIVE_MODELS, native_catalog

    catalog = native_catalog({"gpt-6.2-sol": {}, "gemini-2.5-pro": {}})
    assert "codex-mini-latest" not in {m["id"] for m in NATIVE_MODELS["codex"]}
    for harness in ("codex", "gemini"):
        assert catalog[harness], harness
        for model in catalog[harness]:
            assert model["on_gateway"] is None  # a gateway card says nothing here
            assert model["source"].startswith(
                ("codex-cli 0.171.0", "gemini-cli 0.70.0")
            )
            assert model["effort_levels"] == []


def test_the_runner_pins_the_verified_native_cli_versions() -> None:
    from harness_lab_app.native_models import MIN_NATIVE_CLI_VERSION

    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile.runner").read_text()
    assert f"ARG CODEX_VERSION={MIN_NATIVE_CLI_VERSION['codex']}" in dockerfile
    assert f"ARG GEMINI_CLI_VERSION={MIN_NATIVE_CLI_VERSION['gemini']}" in dockerfile
    assert 'codex.version="${CODEX_VERSION}"' in dockerfile
    assert 'gemini-cli.version="${GEMINI_CLI_VERSION}"' in dockerfile


def test_native_sessions_never_change_the_judge_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ADR-0043: the gateway judges when configured, then the Claude CLI. A
    # codex/gemini login is an executor credential and must not move that.
    from harness_lab_app import llm_transport

    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path))
    monkeypatch.setattr(llm_transport, "gateway_config", lambda: ("http://gw", "t"))
    before = llm_transport.available_transport()
    _connect("codex")
    _connect("gemini")
    assert llm_transport.available_transport() == before == "gateway"

    monkeypatch.setattr(llm_transport, "gateway_config", lambda: None)
    monkeypatch.setattr(llm_transport, "claude_cli_ready", lambda: False)
    assert llm_transport.available_transport() == "none"


def test_the_session_store_works_mounted_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The worker and API mount the store read-only in compose; starting a run
    # must not try to write to it (review H1).
    monkeypatch.setattr(executors, "get_settings", lambda: _Settings(tmp_path))
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    _connect("gemini")
    store = tmp_path / "auth"
    for path in [store, *store.rglob("*")]:
        path.chmod(0o500 if path.is_dir() else 0o400)
    try:
        client = _Client()
        DockerRunExecutor(client_factory=lambda: client).start(
            "case-K-2", ["true"], harness="gemini"
        )
        staged = tmp_path / "data/runs/case-K-2/session/gemini/.gemini"
        assert (staged / "oauth_creds.json").is_file()
    finally:
        for path in [store, *store.rglob("*")]:
            path.chmod(0o700)


def test_only_a_refreshed_credential_goes_back_to_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    _connect("gemini")
    staged = native_auth.stage_run_session("gemini", tmp_path / "run" / "gemini")
    # The run refreshes its token, and the agent writes a memory and a skill.
    (staged / ".gemini" / "oauth_creds.json").write_text(
        json.dumps({"refresh_token": "r2", "access_token": "a2"})
    )
    (staged / ".gemini" / "GEMINI.md").write_text("remember: always cheat")
    (staged / ".gemini" / "skills").mkdir()

    assert native_auth.return_run_session("gemini", staged) is True

    store = native_auth.session_dir("gemini") / ".gemini"
    assert json.loads((store / "oauth_creds.json").read_text())["refresh_token"] == "r2"
    assert not (store / "GEMINI.md").exists()
    assert not (store / "skills").exists()
    assert not staged.exists()


def test_a_run_cannot_plant_an_api_key_in_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    _connect("codex")
    staged = native_auth.stage_run_session("codex", tmp_path / "run" / "codex")
    (staged / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-x"}))

    assert native_auth.return_run_session("codex", staged) is False
    assert native_auth.session_status("codex")["state"] == "connected"


def test_the_host_lane_keeps_only_the_allow_listed_session_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    _connect("codex")
    store = native_auth.session_dir("codex")
    (store / "AGENTS.md").write_text("x")
    (store / "skills" / "mine").mkdir(parents=True)
    (store / "memories").mkdir()

    removed = native_auth.prune_session_dir("codex")

    assert sorted(p.name for p in store.iterdir()) == ["auth.json", "config.toml"]
    assert "AGENTS.md" in removed


def test_the_host_lane_gives_every_step_a_fresh_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_LAB_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.delenv("HARNESS_LAB_IN_RUNNER", raising=False)
    first = bench_run.harness_run_env("gemini")
    second = bench_run.harness_run_env("gemini")

    assert first is not None and second is not None
    assert first["HOME"] != second["HOME"]
    assert first["GEMINI_CLI_HOME"] == str(native_auth.session_dir("gemini"))
    assert bench_run.harness_run_env("claude") is None


def test_codex_and_gemini_runs_never_share_an_arm_with_claude() -> None:
    from harness_lab_app.provenance import compute_condition_hash

    base = {
        "harness_hash": "h",
        "model_condition": {"executor_model": ""},
        "prompt_hash": "p",
        "case_hash": "c",
    }
    claude = compute_condition_hash(**base, harness="claude")
    opencode = compute_condition_hash(**base, harness="opencode")
    codex = compute_condition_hash(**base, harness="codex")
    gemini = compute_condition_hash(**base, harness="gemini")

    # History keeps its hash: claude and opencode are not added to the identity.
    assert claude == opencode == compute_condition_hash(**base)
    assert len({claude, codex, gemini}) == 3


def test_a_bare_status_code_marker_matches_only_a_whole_number(tmp_path: Path) -> None:
    log = tmp_path / "step-1.log"
    log.write_text("# turn failed: API Error: request 14290 failed\n")
    # No marker matched: the cause is the provider line itself, not "429".
    assert not (bench_run.harness_error_cause(log) or "").startswith("Cause: 429")

    log.write_text("# turn failed: API Error: unexpected status 429 Too Many\n")
    assert (bench_run.harness_error_cause(log) or "").startswith("Cause: 429")


def test_a_killed_step_still_leaves_its_translated_events(tmp_path: Path) -> None:
    # The sidecar is refreshed while the stream runs, so a step cut short by
    # the watchdog keeps its attribution (review M2).
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "i1",
                "type": "command_execution",
                "command": "ls",
                "aggregated_output": "",
                "exit_code": 0,
                "status": "completed",
            },
        },
    ]
    steps = tmp_path / "steps"
    steps.mkdir()
    raw = steps / "step-1.raw.jsonl"
    raw.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    events_path = steps / "step-1.events.jsonl"

    bench_run.write_normalized_events("codex", raw, events_path)

    kinds = [json.loads(line)["type"] for line in events_path.read_text().splitlines()]
    assert kinds[:2] == ["system", "assistant"]
