from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ is not a package; make bench_run importable the same way it runs.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402


def test_opencode_cmd_unchanged():
    cmd = bench_run.build_opencode_cmd("gateway/gpt-5.5", None, "do it")
    assert cmd == [
        "opencode",
        "run",
        "--dangerously-skip-permissions",
        "--print-logs",
        "-m",
        "gateway/gpt-5.5",
        "do it",
    ]


def test_codex_cmd_headless_ignores_gateway_model():
    # Gateway-namespaced models don't map to native Codex ids → CLI default.
    cmd = bench_run.build_codex_cmd("gateway/gpt-5.5", "build tetris")
    assert cmd == ["codex", "--quiet", "--full-auto", "build tetris"]


def test_codex_cmd_passes_native_model():
    cmd = bench_run.build_codex_cmd("o4-mini", "build tetris")
    assert cmd == ["codex", "--quiet", "--full-auto", "-m", "o4-mini", "build tetris"]


# stream-json + --verbose: observability flags every claude run now carries
# (raw events → *.events.jsonl for attribution/usage; log gets the rendering).
_CLAUDE_BASE = [
    "claude",
    "-p",
    "--dangerously-skip-permissions",
    "--output-format",
    "stream-json",
    "--verbose",
]


def test_claude_cmd_headless_ignores_gateway_model():
    cmd = bench_run.build_claude_cmd("gateway/claude-sonnet-4.6", "build tetris")
    assert cmd == [*_CLAUDE_BASE, "build tetris"]


def test_claude_cmd_passes_native_model():
    cmd = bench_run.build_claude_cmd("claude-opus-4-8", "build tetris")
    assert cmd == [*_CLAUDE_BASE, "--model", "claude-opus-4-8", "build tetris"]


def test_native_model_helper():
    assert bench_run._native_model("gateway/gpt-5.5") is None
    assert bench_run._native_model("") is None
    assert bench_run._native_model(None) is None
    assert bench_run._native_model("o4-mini") == "o4-mini"


def test_harness_dispatch_selects_builder():
    msg = "x"
    assert bench_run.build_harness_cmd("codex", None, None, msg)[0] == "codex"
    assert bench_run.build_harness_cmd("claude", None, None, msg)[0] == "claude"
    assert bench_run.build_harness_cmd("opencode", None, None, msg)[0] == "opencode"
    # Unknown harness falls back to opencode (safe default).
    assert bench_run.build_harness_cmd("other", None, None, msg)[0] == "opencode"


def test_runner_adapter_passes_harness():
    from harness_lab_app.runner_adapter import BenchRunAdapter
    from harness_lab_app.schemas import RunConfigCreate

    cmd = BenchRunAdapter().build_command(
        "tetris-v1-K-001",
        RunConfigCreate(case_id="tetris-v1", prompt_variant="sdlc", harness="claude"),
    )
    assert "--harness" in cmd
    assert cmd[cmd.index("--harness") + 1] == "claude"


def test_preflight_blocks_host_harness_without_subprocess(monkeypatch):
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunPreflightError, RunService
    from harness_lab_app.schemas import RunConfigCreate

    settings = rs.get_settings()
    monkeypatch.setattr(settings, "execution_backend", "docker", raising=False)
    service = RunService.__new__(RunService)
    # codex is host-only in Docker mode.
    cfg = RunConfigCreate(case_id="tetris-v1", prompt_variant="naive", harness="codex")
    with pytest.raises(RunPreflightError, match="host mode"):
        service.preflight(cfg)


def test_preflight_allows_host_harness_with_subprocess(monkeypatch):
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunService
    from harness_lab_app.schemas import RunConfigCreate

    settings = rs.get_settings()
    monkeypatch.setattr(settings, "execution_backend", "subprocess", raising=False)
    service = RunService.__new__(RunService)
    cfg = RunConfigCreate(case_id="tetris-v1", prompt_variant="naive", harness="claude")
    # No executor model / gateway token required for native-auth host harnesses.
    service.preflight(cfg)


def test_preflight_claude_in_docker_requires_credentials(monkeypatch):
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunPreflightError, RunService
    from harness_lab_app.schemas import RunConfigCreate

    settings = rs.get_settings()
    monkeypatch.setattr(settings, "execution_backend", "docker", raising=False)
    service = RunService.__new__(RunService)
    cfg = RunConfigCreate(case_id="tetris-v1", prompt_variant="naive", harness="claude")

    # No credentials in .env → blocked with an actionable message.
    monkeypatch.setattr(rs, "read_env_file_value", lambda *_a, **_k: "")
    with pytest.raises(RunPreflightError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        service.preflight(cfg)

    # A credential present → claude is allowed in Docker.
    monkeypatch.setattr(
        rs,
        "read_env_file_value",
        lambda name, *_a, **_k: "tok" if name == "CLAUDE_CODE_OAUTH_TOKEN" else "",
    )
    service.preflight(cfg)


def test_claude_cmd_registers_extra_skill_directories_before_the_prompt():
    cmd = bench_run.build_claude_cmd(None, "do it", add_dirs=(Path("/app"),))
    assert cmd[-3:] == ["--add-dir", "/app", "do it"]
    # Nothing added, nothing emitted: the existing command shape is unchanged.
    assert "--add-dir" not in bench_run.build_claude_cmd(None, "do it")


def test_a_lab_root_claude_run_started_inside_a_clone_needs_a_staged_catalog(tmp_path):
    """Claude Code walks `.claude/skills` up to the nearest repository root.

    A workspace that is itself a clone is that root, so a run started there
    never reaches the lab's catalog unless the CLI is handed one (ADR-0061).
    """
    clone = tmp_path / "workspace" / "r1"
    assert bench_run.needs_staged_catalog("claude", clone, True) is True
    # From the lab root itself the catalog loads natively.
    assert bench_run.needs_staged_catalog("claude", None, True) is False
    assert bench_run.needs_staged_catalog("claude", bench_run.REPO_ROOT, True) is False
    # A masked condition has nothing to stage: its views are empty stubs (ADR-0049).
    assert bench_run.needs_staged_catalog("claude", clone, False) is False
    # opencode reads the lab's instructions list, not --add-dir.
    assert bench_run.needs_staged_catalog("opencode", clone, True) is False


def test_the_staged_catalog_holds_the_skills_and_nothing_else(tmp_path):
    """What `--add-dir` registers is `<dir>/.claude/skills`, so the directory
    named must contain only that. Naming the repo root hung every run: the CLI
    indexes and watches an added directory, and that one is gigabytes wide and
    contains the workspace the agent runs in.
    """
    from ml_python_base.skills_sync.models import KIND_INTERNAL, Skill

    source = tmp_path / "skills"
    source.mkdir()
    (source / "caveman.md").write_text("# caveman\n")
    skill = Skill(name="caveman", kind=KIND_INTERNAL, source_path=source / "caveman.md")

    out_dir = tmp_path / "run"
    out_dir.mkdir()
    staged = bench_run.stage_skill_catalog(out_dir, [skill], "claude")

    assert staged == out_dir / "harness_skills"
    assert (staged / ".claude/skills/caveman/SKILL.md").read_text() == "# caveman\n"
    # Copied, not symlinked: an archived run still explains itself.
    assert not (staged / ".claude/skills/caveman/SKILL.md").is_symlink()
    # Only the catalog. Anything else here is another 4.7 GB waiting to happen.
    assert [entry.name for entry in staged.iterdir()] == [".claude"]


def test_the_staged_catalog_is_rebuilt_so_a_retry_stays_ablated(tmp_path):
    from ml_python_base.skills_sync.models import KIND_INTERNAL, Skill

    source = tmp_path / "skills"
    source.mkdir()
    for name in ("caveman", "tdd"):
        (source / f"{name}.md").write_text(f"# {name}\n")
    made = lambda name: Skill(  # noqa: E731
        name=name, kind=KIND_INTERNAL, source_path=source / f"{name}.md"
    )

    out_dir = tmp_path / "run"
    out_dir.mkdir()
    bench_run.stage_skill_catalog(out_dir, [made("caveman"), made("tdd")], "claude")
    staged = bench_run.stage_skill_catalog(out_dir, [made("caveman")], "claude")

    # The survivor of the first attempt would silently undo the ablation.
    assert [d.name for d in (staged / ".claude/skills").iterdir()] == ["caveman"]


def test_a_run_with_no_effective_skills_stages_nothing(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    assert bench_run.stage_skill_catalog(out_dir, [], "claude") is None
    assert not (out_dir / "harness_skills").exists()


def _docker_claude(monkeypatch, routing):
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunService

    monkeypatch.setattr(rs.get_settings(), "execution_backend", "docker", raising=False)
    monkeypatch.setattr(RunService, "_check_runner_image", lambda _self: None)
    monkeypatch.setattr(
        rs,
        "read_env_file_value",
        lambda name, *_a, **_k: "tok" if name == "CLAUDE_CODE_OAUTH_TOKEN" else "",
    )
    monkeypatch.setattr(RunService, "_gateway_routing", lambda _self: routing)
    # These tests describe an install whose claude CLI goes through the gateway.
    monkeypatch.setattr(
        RunService, "_claude_routes_through_gateway", lambda _self: True
    )
    return RunService.__new__(RunService)


def test_preflight_refuses_a_claude_model_the_gateway_does_not_serve(monkeypatch):
    """con-o-sin-harness (2026-10-23): eight runs of claude-haiku-5-5 died in
    under a second on 'Invalid model name' because the gateway had no card for
    it. The gateway answers that question before anything is queued."""
    from harness_lab_app.run_service import RunPreflightError
    from harness_lab_app.schemas import RunConfigCreate

    service = _docker_claude(
        monkeypatch, {"claude-opus-5-5": {}, "claude-haiku-4-5-20251001": {}}
    )
    cfg = RunConfigCreate(
        case_id="todo-web-v1",
        prompt_variant="default",
        harness="claude",
        executor_model="claude-haiku-5-5",
    )
    with pytest.raises(RunPreflightError) as refused:
        service.preflight(cfg)
    message = str(refused.value)
    assert "claude-haiku-5-5" in message
    assert "claude-opus-5-5" in message  # names what it does serve


def test_preflight_accepts_a_claude_model_the_gateway_serves(monkeypatch):
    from harness_lab_app.schemas import RunConfigCreate

    service = _docker_claude(monkeypatch, {"claude-opus-5-5-oauth": {}})
    cfg = RunConfigCreate(
        case_id="todo-web-v1",
        prompt_variant="default",
        harness="claude",
        executor_model="claude-opus-5-5",
    )
    service.preflight(cfg)


def test_preflight_does_not_block_claude_when_the_gateway_cannot_be_asked(monkeypatch):
    """No gateway, or one that did not answer: unknown is not 'missing'."""
    from harness_lab_app.schemas import RunConfigCreate

    service = _docker_claude(monkeypatch, None)
    cfg = RunConfigCreate(
        case_id="todo-web-v1",
        prompt_variant="default",
        harness="claude",
        executor_model="claude-haiku-5-5",
    )
    service.preflight(cfg)


def _docker_claude_cli(monkeypatch, cli_version):
    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunService

    service = _docker_claude(monkeypatch, None)
    monkeypatch.setattr(
        "harness_lab_app.health_service.worker_facts",
        lambda **_k: {
            "backend": "docker",
            "docker_ok": True,
            "runner_image_present": True,
            "runner_image": "harness-lab-runner:latest",
            "claude_cli_version": cli_version,
        },
    )
    monkeypatch.setattr(
        RunService, "_check_runner_image", rs.RunService._check_runner_image
    )
    return service


def test_preflight_refuses_a_claude_model_the_runner_cli_is_too_old_for(monkeypatch):
    """walletsystem-concurrent-deposits-K-006/007 (2026-10-23): claude-opus-5-5
    is served by the gateway, but the runner's Claude Code 2.1.197 was refused by
    the API — 'version 2.1.280 or newer is required'."""
    from harness_lab_app.run_service import RunPreflightError
    from harness_lab_app.schemas import RunConfigCreate

    service = _docker_claude_cli(monkeypatch, "2.1.197")
    cfg = RunConfigCreate(
        case_id="walletsystem-concurrent-deposits",
        prompt_variant="default",
        harness="claude",
        executor_model="claude-opus-5-5",
    )
    with pytest.raises(RunPreflightError) as refused:
        service.preflight(cfg)
    message = str(refused.value)
    assert "2.1.197" in message and "2.1.280" in message
    assert "runner image" in message.lower()


def test_preflight_accepts_a_runner_cli_new_enough_for_the_model(monkeypatch):
    from harness_lab_app.schemas import RunConfigCreate

    for model, version in (
        ("claude-opus-5-5", "2.1.309"),
        ("claude-haiku-4-5-20251001", "2.1.197"),
    ):
        service = _docker_claude_cli(monkeypatch, version)
        service.preflight(
            RunConfigCreate(
                case_id="walletsystem-concurrent-deposits",
                prompt_variant="default",
                harness="claude",
                executor_model=model,
            )
        )


def test_an_image_without_a_cli_version_label_is_not_blocked(monkeypatch):
    """Images built before the label existed say nothing: unknown, not too old."""
    from harness_lab_app.schemas import RunConfigCreate

    service = _docker_claude_cli(monkeypatch, "")
    service.preflight(
        RunConfigCreate(
            case_id="walletsystem-concurrent-deposits",
            prompt_variant="default",
            harness="claude",
            executor_model="claude-opus-5-5",
        )
    )


def test_the_runner_pins_a_claude_cli_new_enough_for_every_current_model():
    """The image's pinned CLI must accept every current-generation model the forms
    offer; an unpinned `npm install` stayed on a cached 2.1.197 for months."""
    import re

    from harness_lab_app.native_models import NATIVE_MODELS, min_claude_cli_version

    dockerfile = (bench_run.REPO_ROOT / "Dockerfile.runner").read_text()
    pinned = re.search(r"^ARG CLAUDE_CODE_VERSION=(\S+)$", dockerfile, re.MULTILINE)
    assert pinned, "Dockerfile.runner must pin CLAUDE_CODE_VERSION"
    assert '"@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"' in dockerfile

    def as_tuple(version):
        return tuple(int(part) for part in version.split("."))

    for model in NATIVE_MODELS["claude"]:
        required = min_claude_cli_version(model["id"])
        if required:
            assert as_tuple(pinned.group(1)) >= as_tuple(required), model["id"]


def test_a_claude_cli_talking_to_anthropic_directly_is_not_held_to_the_gateway(
    monkeypatch,
):
    """A developer's claude reaches Anthropic directly unless their own
    .claude/settings.local.json sets ANTHROPIC_BASE_URL. A gateway configured for
    opencode must not then refuse a model Anthropic serves."""
    from harness_lab_app.run_service import RunService
    from harness_lab_app.schemas import RunConfigCreate

    service = _docker_claude(monkeypatch, {"claude-opus-5-5": {}})
    monkeypatch.setattr(
        RunService, "_claude_routes_through_gateway", lambda _self: False
    )
    service.preflight(
        RunConfigCreate(
            case_id="todo-web-v1",
            prompt_variant="default",
            harness="claude",
            executor_model="claude-haiku-5-5",
        )
    )


def test_claude_routes_through_the_gateway_only_when_its_settings_say_so(
    tmp_path, monkeypatch
):
    import json

    import harness_lab_app.run_service as rs
    from harness_lab_app.run_service import RunService

    monkeypatch.setattr(rs.get_settings(), "repo_root", tmp_path, raising=False)
    service = RunService.__new__(RunService)
    assert service._claude_routes_through_gateway() is False
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gateway.example"}})
    )
    assert service._claude_routes_through_gateway() is True
