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
