"""The quick results page: speed tests and local case runs, ranked apart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from harness_lab_app.local_tuning.profile_service import ProfileService
from harness_lab_app.local_tuning.results_service import local_results
from harness_lab_app.models import Run, RunConfig, RunState, Validation


def _speed(step: float) -> list[dict]:
    return [
        {
            "name": "speed",
            "verdict": "ok",
            "reading": "",
            "detail": {
                "seconds_per_step": step,
                "read_tokens_per_second": 14000 / step,
                "write_tokens_per_second": 70.0,
            },
        }
    ]


def _run(session, run_id, model, case="tetris-v2", checks=(True, True), minutes=20):
    config = RunConfig(
        case_id=case, prompt_variant="default", harness="opencode", executor_model=model
    )
    session.add(config)
    session.commit()
    start = datetime(2026, 11, 7, 12, tzinfo=UTC)
    session.add(
        Run(
            run_id=run_id,
            run_config_id=config.id,
            state=RunState.completed,
            condition_label="bare",
            started_at=start,
            ended_at=start + timedelta(minutes=minutes),
        )
    )
    session.add(
        Validation(
            run_id=run_id,
            objective={
                "passed": all(checks),
                "checks": [{"passed": c, "conclusive": True} for c in checks],
            },
        )
    )
    session.commit()


def test_speed_tests_rank_fastest_first_and_name_their_machine(session):
    service = ProfileService(session)
    machine = service.create_machine(
        {"name": "Win-i9-3090", "memory_kind": "discrete", "gpu_gb": 24, "ram_gb": 64}
    )
    for label, step in (("slow", 14.0), ("fast", 7.0)):
        service.create_profile(
            {
                "machine_id": machine.id,
                "server_kind": "lmstudio",
                "server_url": "http://h:1234",
                "model_id": "prism-ml/bonsai-27b",
                "label": label,
                "results": _speed(step),
            }
        )
    speed = local_results(session)["speed"]
    assert [row["label"] for row in speed] == ["fast", "slow"]
    assert speed[0]["machine"] == "Win-i9-3090"
    assert speed[0]["read_tokens_per_second"] == 2000.0


def test_case_runs_group_by_model_and_rank_by_test_pass_rate(session):
    _run(session, "a1", "lmstudio/prism-ml/bonsai-27b", checks=(True, False))
    _run(session, "a2", "lmstudio/prism-ml/bonsai-27b", checks=(True, True))
    _run(session, "b1", "lmstudio/qwen/qwen3.6-27b", checks=(True, True), minutes=40)
    _run(session, "c1", "claude-opus-5-5", checks=(True, True))  # cloud: excluded
    cases = local_results(session)["cases"]
    assert [row["model"] for row in cases] == [
        "lmstudio/qwen/qwen3.6-27b",
        "lmstudio/prism-ml/bonsai-27b",
    ]
    bonsai = cases[1]
    assert bonsai["runs"] == 2 and bonsai["passed_runs"] == 1
    assert bonsai["test_pass_rate"] == 0.75
    assert bonsai["median_minutes"] == 20


def test_case_runs_keep_each_arm_apart(session):
    _run(session, "x1", "lmstudio/prism-ml/bonsai-27b", checks=(True, False))
    _run(session, "x2", "lmstudio/prism-ml/bonsai-27b", checks=(True, True))
    run = session.get(Run, 2)
    run.condition_label = "full-harness"
    session.add(run)
    session.commit()
    arms = {row["arm"] for row in local_results(session)["cases"]}
    assert arms == {"bare", "full-harness"}
