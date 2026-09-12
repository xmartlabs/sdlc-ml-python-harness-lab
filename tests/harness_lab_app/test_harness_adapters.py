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
