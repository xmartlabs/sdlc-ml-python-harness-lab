"""Reasoning effort is a selectable part of the arm for Claude runs (ADR-0081).

Four promises. The catalog says which models accept which levels. The level
reaches the claude CLI as ``--effort`` and nothing else changes when it is
unset. Preflight refuses an effort the run could not honour. And effort is
part of the arm: it flips the condition hash only when set, survives every
path that reproduces a run, and is an axis a batch or experiment may vary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app import db as db_module
from harness_lab_app.experiment_service import CONDITION_FIELDS
from harness_lab_app.native_models import (
    EFFORT_LEVELS,
    NATIVE_MODELS,
    effort_levels,
    native_catalog,
)
from harness_lab_app.provenance import compute_condition_hash
from harness_lab_app.run_service import (
    RunPreflightError,
    RunService,
    _expand_batch,
    _preflight_key,
    _resume_config,
)
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunBatchCreate, RunConfigCreate, RunCreate

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _claude(**overrides) -> RunConfigCreate:
    base = {
        "case_id": "tetris-v1",
        "prompt_variant": "sdlc",
        "harness": "claude",
        "executor_model": "claude-opus-5-5",
        "dry_run": True,
    }
    return RunConfigCreate(**{**base, **overrides})


# --------------------------------------------------------------------------
# The catalog says which models accept which levels
# --------------------------------------------------------------------------


def test_every_native_model_declares_its_effort_levels():
    for harness, models in NATIVE_MODELS.items():
        for model in models:
            assert "effort_levels" in model, (harness, model["id"])
            assert set(model["effort_levels"]) <= set(EFFORT_LEVELS)


def test_current_claude_models_accept_every_level_and_haiku_4_5_none():
    assert effort_levels("claude", "claude-opus-5-5") == list(EFFORT_LEVELS)
    assert effort_levels("claude", "claude-haiku-5-5") == list(EFFORT_LEVELS)
    assert effort_levels("claude", "claude-haiku-4-5-20251001") == []
    # Spellings a gateway or a hand-typed id use resolve to the same row.
    assert effort_levels("claude", "claude-haiku-4.5") == []


def test_an_unknown_model_is_not_refused_by_the_catalog():
    assert effort_levels("claude", "claude-opus-9-9") is None


def test_the_served_catalog_carries_the_levels_to_the_ui():
    catalog = native_catalog(None)
    rows = {row["id"]: row for row in catalog["claude"]}
    assert rows["claude-sonnet-5-5"]["effort_levels"] == list(EFFORT_LEVELS)
    assert rows["claude-haiku-4-5-20251001"]["effort_levels"] == []


# --------------------------------------------------------------------------
# The level reaches the CLI, and an unset one changes nothing
# --------------------------------------------------------------------------


def test_claude_cmd_passes_effort_after_the_model():
    cmd = bench_run.build_claude_cmd("claude-opus-5-5", "go", effort="high")
    assert cmd[-5:] == ["--model", "claude-opus-5-5", "--effort", "high", "go"]


def test_claude_cmd_without_effort_is_unchanged():
    assert bench_run.build_claude_cmd(
        "claude-opus-5-5", "go"
    ) == bench_run.build_claude_cmd("claude-opus-5-5", "go", effort="")
    assert "--effort" not in bench_run.build_claude_cmd("claude-opus-5-5", "go")


def test_effort_binds_before_the_variadic_add_dir_and_the_prompt():
    cmd = bench_run.build_claude_cmd(
        "claude-opus-5-5", "go", add_dirs=[Path("/x")], effort="max"
    )
    assert cmd.index("--effort") < cmd.index("--add-dir=/x") < cmd.index("go")


def test_dispatch_forwards_effort_only_to_claude():
    claude = bench_run.build_harness_cmd(
        "claude", "claude-opus-5-5", None, "go", effort="low"
    )
    assert "--effort" in claude
    opencode = bench_run.build_harness_cmd(
        "opencode", "gateway/x", None, "go", effort="low"
    )
    assert "--effort" not in opencode


def test_bench_run_accepts_the_flag_and_defaults_to_empty():
    base = ["--case", "c", "--run", "r"]
    assert bench_run.parse_args(base).executor_effort == ""
    assert (
        bench_run.parse_args([*base, "--executor-effort", "xhigh"]).executor_effort
        == "xhigh"
    )
    with pytest.raises(SystemExit):
        bench_run.parse_args([*base, "--executor-effort", "turbo"])


def _write_metadata(out_dir: Path, **fields) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(json.dumps({"run_id": "r", **fields}))
    return out_dir


def test_a_cli_resume_continues_at_the_recorded_effort(tmp_path):
    """`RESUME=1` without the flag must not rewrite the run as the default."""
    out_dir = _write_metadata(tmp_path / "r", executor_effort="max")
    assert bench_run.resumed_effort(out_dir, "", resume=True) == "max"
    assert bench_run.resumed_effort(out_dir, "max", resume=True) == "max"


def test_a_resume_refuses_an_effort_other_than_the_one_that_ran(tmp_path):
    """Part of the run already ran at one level; filing it under another lies."""
    out_dir = _write_metadata(tmp_path / "r", executor_effort="low")
    with pytest.raises(SystemExit, match="ran at effort 'low'"):
        bench_run.resumed_effort(out_dir, "high", resume=True)
    # A run from before effort existed ran at the CLI default.
    legacy = _write_metadata(tmp_path / "legacy")
    with pytest.raises(SystemExit, match="CLI default"):
        bench_run.resumed_effort(legacy, "high", resume=True)
    assert bench_run.resumed_effort(legacy, "", resume=True) == ""


def test_effort_restoration_only_applies_to_a_resume_with_a_record(tmp_path):
    out_dir = _write_metadata(tmp_path / "r", executor_effort="max")
    assert bench_run.resumed_effort(out_dir, "low", resume=False) == "low"
    assert bench_run.resumed_effort(tmp_path / "missing", "low", resume=True) == "low"
    (out_dir / "metadata.json").write_text("not json")
    assert bench_run.resumed_effort(out_dir, "", resume=True) == ""


def test_adapter_emits_effort_only_when_set():
    adapter = BenchRunAdapter()
    with_effort = adapter.build_command("r-1", _claude(executor_effort="high"))
    assert with_effort[with_effort.index("--executor-effort") + 1] == "high"
    assert "--executor-effort" not in adapter.build_command("r-1", _claude())


# --------------------------------------------------------------------------
# Validation and preflight refuse what the run could not honour
# --------------------------------------------------------------------------


def test_schema_normalizes_case_and_refuses_unknown_levels():
    assert _claude(executor_effort=" HIGH ").executor_effort == "high"
    with pytest.raises(ValidationError, match="executor_effort"):
        _claude(executor_effort="turbo")


def test_preflight_accepts_a_supported_level():
    RunService.__new__(RunService)._check_effort(_claude(executor_effort="xhigh"))


def test_preflight_ignores_an_unset_effort_on_any_harness():
    service = RunService.__new__(RunService)
    service._check_effort(_claude(harness="opencode", executor_model="gateway/x"))


def test_preflight_refuses_effort_on_a_harness_that_cannot_pass_it():
    service = RunService.__new__(RunService)
    with pytest.raises(RunPreflightError, match="Claude Code setting"):
        service._check_effort(
            _claude(
                harness="opencode", executor_model="gateway/x", executor_effort="high"
            )
        )


def test_preflight_refuses_effort_without_a_native_claude_model():
    service = RunService.__new__(RunService)
    for model in (None, "", "gateway/claude-opus-5-5"):
        with pytest.raises(RunPreflightError, match="native Claude model"):
            service._check_effort(_claude(executor_model=model, executor_effort="high"))


def test_preflight_refuses_effort_on_a_model_without_effort_control():
    service = RunService.__new__(RunService)
    with pytest.raises(RunPreflightError, match="no effort control"):
        service._check_effort(
            _claude(executor_model="claude-haiku-4-5-20251001", executor_effort="low")
        )


def test_create_run_refuses_a_bad_effort_even_on_a_dry_run():
    """A dry run still records and hashes the arm, so it must be a real one."""
    with _session() as session, pytest.raises(RunPreflightError, match="no effort"):
        RunService(session).create_run(
            RunCreate(
                run_id="eff-1",
                config=_claude(
                    executor_model="claude-haiku-4-5-20251001", executor_effort="max"
                ),
            )
        )


def test_create_run_stores_the_effort():
    with _session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(run_id="eff-2", config=_claude(executor_effort="medium"))
        )
        stored = service.get_run_config(run.run_config_id)
    assert stored.executor_effort == "medium"
    assert "--executor-effort" in run.command


# --------------------------------------------------------------------------
# Effort is part of the arm
# --------------------------------------------------------------------------


def test_condition_hash_is_unchanged_without_effort_and_flips_with_it():
    model = {"executor_model": "claude-opus-5-5", "planner_model": None}
    base = compute_condition_hash("h", model, "p", "c")
    assert compute_condition_hash("h", model, "p", "c", executor_effort="") == base
    high = compute_condition_hash("h", model, "p", "c", executor_effort="high")
    low = compute_condition_hash("h", model, "p", "c", executor_effort="low")
    assert len({base, high, low}) == 3


def test_retry_carries_the_effort():
    with _session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(run_id="eff-3", config=_claude(executor_effort="max"))
        )
        stored = service.get_run_config(run.run_config_id)
    assert _resume_config(stored).executor_effort == "max"


def test_clone_carries_the_effort(monkeypatch):
    with _session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(run_id="eff-4", config=_claude(executor_effort="low"))
        )
        clone = service.clone_run(run.run_id)
        stored = service.get_run_config(clone.run_config_id)
    assert stored.executor_effort == "low"


def test_preflight_cache_distinguishes_effort_levels():
    assert _preflight_key(_claude(executor_effort="low")) != _preflight_key(
        _claude(executor_effort="high")
    )


def test_a_batch_crosses_the_effort_axis():
    data = RunBatchCreate(
        case_ids=["tetris-v1"],
        prompt_variants=["sdlc"],
        harnesses=["claude"],
        executor_models=["claude-opus-5-5"],
        executor_efforts=["low", "HIGH"],
        repetitions=2,
    )
    configs = _expand_batch(data, {"tetris-v1": ["sdlc"]})
    assert [c.executor_effort for c in configs] == ["low", "low", "high", "high"]


def test_a_batch_without_the_axis_keeps_the_cli_default():
    data = RunBatchCreate(case_ids=["tetris-v1"], prompt_variants=["sdlc"])
    assert data.executor_efforts == [""]
    with pytest.raises(ValidationError):
        RunBatchCreate(
            case_ids=["tetris-v1"], prompt_variants=["sdlc"], executor_efforts=[]
        )


def test_an_experiment_may_vary_effort_between_arms():
    assert "executor_effort" in CONDITION_FIELDS


# --------------------------------------------------------------------------
# Historical rows
# --------------------------------------------------------------------------


def test_migration_backfills_the_cli_default(tmp_path, monkeypatch):
    from tests.harness_lab_app.test_db_migrations import _legacy_runconfig_engine

    engine = _legacy_runconfig_engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(
        db_module,
        "get_settings",
        lambda: SimpleNamespace(database_url=f"sqlite:///{tmp_path}/targets.db"),
    )

    db_module._apply_sqlite_compat_migrations()
    db_module._apply_sqlite_compat_migrations()  # idempotent

    with engine.begin() as connection:
        effort = connection.execute(
            text("SELECT executor_effort FROM runconfig")
        ).scalar()
    # The legacy row ran with the CLI's own default; "" says exactly that.
    assert effort == ""
