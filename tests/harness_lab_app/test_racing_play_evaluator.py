"""racing-play-v1: the played/scripted label and the racing scores."""

from __future__ import annotations

import json

import pytest

from harness_lab_app import trajectory as trajectory_module
from harness_lab_app.evaluation import EVALUATORS, PROFILES, EvaluationContext
from harness_lab_app.racing_play import count_race_step_calls, play_mode


def _bash_events(commands: list[str]) -> list[str]:
    lines = []
    for index, command in enumerate(commands):
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": f"t{index}",
                                "name": "Bash",
                                "input": {"command": command},
                            }
                        ]
                    },
                }
            )
        )
    return lines


STEP = """./race step '{"throttle":1,"brake":0,"steer":0,"reset":0}'"""


def _steady(count: int, gap_ms: int) -> list[int]:
    return [i * gap_ms for i in range(count)]


@pytest.mark.parametrize(
    ("actions", "event_calls", "times", "expected"),
    [
        (120, 120, _steady(120, 5000), ("played", "events")),
        (120, 130, _steady(120, 5000), ("played", "events")),
        (120, 3, [], ("scripted", "events")),
        (120, None, _steady(120, 5000), ("played", "timing")),
        (120, None, _steady(120, 40), ("scripted", "timing")),
        (120, None, _steady(4, 10) + _steady(116, 5000), ("played", "timing")),
        (120, None, [], ("scripted", "timing")),
        (0, None, [], ("unknown", "none")),
        (0, 0, [], ("unknown", "none")),
    ],
)
def test_play_mode_matrix(actions, event_calls, times, expected) -> None:
    assert play_mode(actions, event_calls, times) == expected


def test_count_race_step_calls_counts_unclipped() -> None:
    lines = _bash_events([STEP] * 200 + ["./race state", "ls"])
    trajectory = trajectory_module.build(event_lines=lines)
    assert trajectory is not None
    assert trajectory["truncated"] is True
    assert count_race_step_calls(trajectory) == 200


@pytest.mark.parametrize(
    "command",
    [
        STEP,
        """node race.mjs step '{"throttle":1,"brake":0,"steer":0,"reset":0}'""",
        """cd /app/workspace/r && ./race step '{"throttle":1,"brake":0,"steer":0,"reset":0}'""",
    ],
)
def test_step_call_shapes_are_counted(command: str) -> None:
    trajectory = trajectory_module.build(event_lines=_bash_events([command]))
    assert count_race_step_calls(trajectory) == 1


def test_a_loop_counts_as_one_call() -> None:
    loop = "for i in $(seq 100); do ./race step '{...}'; done"
    trajectory = trajectory_module.build(event_lines=_bash_events([loop]))
    assert count_race_step_calls(trajectory) == 1


def test_opencode_trajectory_gives_none() -> None:
    assert count_race_step_calls({"source": "opencode_logs", "steps": []}) is None
    assert count_race_step_calls(None) is None


def _grade(**overrides) -> dict:
    grade = {
        "valid": True,
        "invalid_reason": "",
        "actions": 32,
        "metrics": {
            "finished": 1,
            "progress": 1,
            "checkpoints_passed": 12,
            "collisions": 0,
            "resets": 0,
            "off_track_seconds": 0,
            "finish_time_seconds": 15.5,
        },
        "episode_complete": True,
        "ended_by": "finished",
        "calls": [],
    }
    grade.update(overrides)
    return grade


def test_evaluator_is_in_every_profile_that_reads_outcomes() -> None:
    for profile in ("default", "no_judgement", "outcome_only"):
        assert "racing_play" in PROFILES[profile]


def test_evaluator_silent_without_racing_grade() -> None:
    assert EVALUATORS["racing_play"].evaluate(EvaluationContext(run_id="r")) == []


def test_evaluator_emits_scores() -> None:
    trajectory = trajectory_module.build(event_lines=_bash_events([STEP] * 32))
    grade = _grade(
        calls=_steady(32, 5000),
        controls=[{"throttle": 1, "brake": 0, "steer": 0, "reset": 0}] * 32,
    )
    context = EvaluationContext(run_id="r", trajectory=trajectory, racing_grade=grade)
    scores = {s.name: s for s in EVALUATORS["racing_play"].evaluate(context)}
    assert scores["racing_play_mode"].value == "played"
    assert scores["racing_play_mode"].reason == "played · events"
    assert scores["racing_finish_seconds"].value == 15.5
    assert scores["racing_progress"].value == 1.0
    assert scores["racing_episode_complete"].value is True
    for score in scores.values():
        score.as_row()  # declared in the registry


def test_evaluator_unfinished_has_no_finish_time() -> None:
    grade = _grade(
        metrics={"finished": 0, "progress": 0.07, "collisions": 1, "resets": 0},
        ended_by="horizon",
    )
    context = EvaluationContext(run_id="r", trajectory=None, racing_grade=grade)
    scores = {s.name: s for s in EVALUATORS["racing_play"].evaluate(context)}
    assert "racing_finish_seconds" not in scores
    assert scores["racing_progress"].value == pytest.approx(0.07)
    assert scores["racing_play_mode"].value == "scripted"  # no calls, no events


def test_evaluator_invalid_episode_reports_only_mode() -> None:
    grade = _grade(
        valid=False, invalid_reason="no actions recorded", actions=0, metrics={}
    )
    context = EvaluationContext(run_id="r", racing_grade=grade)
    scores = {s.name: s for s in EVALUATORS["racing_play"].evaluate(context)}
    assert set(scores) == {"racing_play_mode", "racing_episode_complete"}
    assert scores["racing_play_mode"].value == "unknown"


# --- Final review fixes -------------------------------------------------------


def _step_command(controls: dict) -> str:
    return f"./race step '{json.dumps(controls)}'"


C1 = {"throttle": 1, "brake": 0, "steer": 0, "reset": 0}
C2 = {"throttle": 1, "brake": 0, "steer": 0.3, "reset": 0}


def test_event_label_needs_complete_call_log_and_no_burst() -> None:
    """Review #2: events alone used to say played even with the call log gone."""
    steady = _steady(120, 5000)
    assert play_mode(120, 120, steady) == ("played", "events")
    assert play_mode(120, 120, []) == ("scripted", "events")
    assert play_mode(120, 120, steady[:60]) == ("scripted", "events")
    assert play_mode(120, 120, _steady(120, 40)) == ("scripted", "events")


def test_trajectory_controls_must_match_the_log() -> None:
    """Review #1: a log rewritten after real calls is not the played episode."""
    from harness_lab_app.racing_play import step_controls, trajectory_matches_log

    trajectory = trajectory_module.build(
        event_lines=_bash_events([_step_command(C1), _step_command(C2)])
    )
    assert step_controls(trajectory) == [C1, C2]
    assert trajectory_matches_log(trajectory, [C1, C2]) is True
    assert trajectory_matches_log(trajectory, [C2, C2]) is False
    assert trajectory_matches_log(None, [C1]) is None


def test_evaluator_labels_rewritten_log_scripted() -> None:
    trajectory = trajectory_module.build(
        event_lines=_bash_events([_step_command(C1), _step_command(C1)])
    )
    grade = _grade(
        actions=2,
        controls=[C2, C2],
        calls=[0, 5000],
        metrics={"finished": 0, "progress": 0.01},
        ended_by="cut",
    )
    context = EvaluationContext(run_id="r", trajectory=trajectory, racing_grade=grade)
    scores = {s.name: s for s in EVALUATORS["racing_play"].evaluate(context)}
    assert scores["racing_play_mode"].value == "scripted"
    assert "does not match" in scores["racing_play_mode"].reason


def test_echo_is_not_a_step_call() -> None:
    from harness_lab_app.racing_play import is_race_step

    assert is_race_step("echo race step") is False
    assert is_race_step("echo ./race step '{}'") is False
    assert is_race_step("bash -lc './race step \\'{}\\''") is True


def test_codex_shell_call_counts_as_a_step() -> None:
    from harness_lab_app.harness_events import translate

    command = "bash -lc \"./race step '" + json.dumps(C1) + "'\""
    raw = [
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "command_execution",
                    "command": command,
                    "aggregated_output": "{}",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
        )
    ]
    trajectory = trajectory_module.build(event_lines=translate("codex", raw))
    assert count_race_step_calls(trajectory) == 1


def test_invalid_episode_reason_is_shown() -> None:
    grade = _grade(
        valid=False,
        invalid_reason="no actions recorded",
        actions=0,
        metrics={},
        ended_by="invalid",
    )
    context = EvaluationContext(run_id="r", racing_grade=grade)
    scores = {s.name: s for s in EVALUATORS["racing_play"].evaluate(context)}
    assert scores["racing_episode_complete"].reason == "no actions recorded"


def test_load_racing_grade_reads_only_the_run_report(tmp_path) -> None:
    from harness_lab_app.racing_play import load_racing_grade

    forged = tmp_path / "workspace" / ".lab-grade"
    forged.mkdir(parents=True)
    (forged / "racing.json").write_text(json.dumps({"forged": True}))
    assert load_racing_grade(tmp_path) is None
    (tmp_path / "racing.json").write_text(json.dumps({"real": True}))
    assert load_racing_grade(tmp_path) == {"real": True}
