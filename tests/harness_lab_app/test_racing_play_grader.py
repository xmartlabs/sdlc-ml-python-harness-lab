"""racing-play-v1: the hidden grader replays the agent's log with the lab's engine."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess  # nosec B404 - fixed local Node program under test
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE = REPO_ROOT / "benchmarks" / "racing-play-v1"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _load_grader():
    spec = importlib.util.spec_from_file_location(
        "racing_play_grade", CASE / "grader" / "grade.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


grade = _load_grader()

# Drives the scripted calibration controller through the shared condition, so a
# test has a real finishing lap without a model.
BASELINE = """
import * as engine from %(engine)s;
import {CONDITION} from %(condition)s;
const state = engine.createRace(CONDITION.seed);
engine.stepRace(state, {throttle:0,brake:0,steer:0,reset:0}, CONDITION.countdown_ticks);
const out = [];
while (state.phase !== 'finished' && state.tick < CONDITION.horizon_ticks) {
  const c = engine.baselineControls(state);
  engine.stepRace(state, c, Math.min(CONDITION.ticks_per_action, CONDITION.horizon_ticks - state.tick));
  // What the race tool would have printed after this step.
  out.push({controls: c, seen: {tick: state.tick, x: state.x, z: state.z, speed: state.speed, heading: state.heading}});
}
process.stdout.write(JSON.stringify(out));
"""


def baseline_actions(tmp_path: Path) -> list[dict]:
    script = tmp_path / "baseline.mjs"
    script.write_text(
        BASELINE
        % {
            "engine": json.dumps(str(REPO_ROOT / "frontend/src/gameplay/racing.mjs")),
            "condition": json.dumps(str(CASE / "starter" / "condition.mjs")),
        }
    )
    result = subprocess.run(  # nosec B603 - fixed argv
        [str(NODE), str(script)], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def workspace_with(tmp_path: Path, actions: list[dict], *, calls: bool = True) -> Path:
    ws = tmp_path / "ws"
    shutil.copytree(CASE / "starter", ws)
    race = ws / ".race"
    race.mkdir()
    (race / "actions.jsonl").write_text(
        "".join(
            json.dumps({"i": i, **(a if "controls" in a else {"controls": a})}) + "\n"
            for i, a in enumerate(actions)
        )
    )
    if calls:
        (race / "calls.jsonl").write_text(
            "".join(
                json.dumps({"i": i, "t": i * 4000}) + "\n" for i in range(len(actions))
            )
        )
    return ws


def check(name: str, ws: Path) -> int:
    return grade.main(["check", name, str(ws), "run-test"])


def test_check_names_are_the_ladder() -> None:
    assert grade.CHECKS == (
        "episode_valid",
        "progress_25",
        "progress_50",
        "progress_75",
        "all_gates",
        "finished",
        "clean",
        "fast",
    )


def test_missing_log_fails_every_rung(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    shutil.copytree(CASE / "starter", ws)
    assert all(check(name, ws) == 1 for name in grade.CHECKS)


def test_scripted_baseline_lap_passes_through_finished(tmp_path: Path) -> None:
    ws = workspace_with(tmp_path, baseline_actions(tmp_path))
    for name in (
        "episode_valid",
        "progress_25",
        "progress_50",
        "progress_75",
        "all_gates",
        "finished",
    ):
        assert check(name, ws) == 0, name
    report = json.loads((ws / ".lab-grade" / "racing.json").read_text())
    assert report["ended_by"] == "finished"
    assert report["episode_complete"] is True
    assert report["metrics"]["finished"] == 1
    assert len(report["calls"]) == report["actions"]
    assert report["condition"]["ticks_per_action"] == 30
    # The naive controller (22.02 s) must not reach the top rung.
    assert check("clean", ws) == 0
    assert check("fast", ws) == 1


def test_cut_episode_is_graded_on_progress(tmp_path: Path) -> None:
    ws = workspace_with(tmp_path, baseline_actions(tmp_path)[:10])
    assert check("episode_valid", ws) == 0
    assert check("finished", ws) == 1
    report = json.loads((ws / ".lab-grade" / "racing.json").read_text())
    assert report["episode_complete"] is False
    assert report["ended_by"] == "cut"
    assert 0 < report["metrics"]["progress"] < 1


def test_out_of_bounds_action_invalidates_episode(tmp_path: Path) -> None:
    actions = baseline_actions(tmp_path)[:20]
    actions[5] = {
        **actions[5],
        "controls": {"throttle": 1, "brake": 0, "steer": 3, "reset": 0},
    }
    ws = workspace_with(tmp_path, actions)
    assert check("episode_valid", ws) == 1
    assert check("progress_25", ws) == 1
    report = json.loads((ws / ".lab-grade" / "racing.json").read_text())
    assert report["valid"] is False
    assert "steer" in report["invalid_reason"]


def test_action_after_terminal_invalidates_episode(tmp_path: Path) -> None:
    brake = {"throttle": 0, "brake": 1, "steer": 0, "reset": 0}
    ws = workspace_with(tmp_path, [brake] * 121)
    assert check("episode_valid", ws) == 1


def test_unparsable_log_invalidates_episode(tmp_path: Path) -> None:
    ws = workspace_with(tmp_path, [])
    (ws / ".race" / "actions.jsonl").write_text("{not json\n")
    assert check("episode_valid", ws) == 1


def test_grader_uses_lab_engine_not_workspace_copy(tmp_path: Path) -> None:
    ws = workspace_with(tmp_path, baseline_actions(tmp_path))
    (ws / "racing.mjs").write_text("throw new Error('tampered');\n")
    (ws / "condition.mjs").write_text("throw new Error('tampered');\n")
    assert check("finished", ws) == 0


def test_hand_written_log_without_calls_still_grades(tmp_path: Path) -> None:
    ws = workspace_with(tmp_path, baseline_actions(tmp_path), calls=False)
    assert check("finished", ws) == 0
    report = json.loads((ws / ".lab-grade" / "racing.json").read_text())
    assert report["calls"] == []


def test_no_node_is_inconclusive(tmp_path: Path, monkeypatch, capsys) -> None:
    ws = workspace_with(tmp_path, baseline_actions(tmp_path)[:3])
    monkeypatch.setattr(grade.shutil, "which", lambda _name: None)
    assert check("episode_valid", ws) == 127
    assert "command not found" in capsys.readouterr().out


def test_cli_entry_point(tmp_path: Path) -> None:
    ws = workspace_with(tmp_path, baseline_actions(tmp_path)[:4])
    result = subprocess.run(  # nosec B603 - fixed argv
        [
            sys.executable,
            str(CASE / "grader" / "grade.py"),
            "check",
            "episode_valid",
            str(ws),
            "r",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- Final review fixes -------------------------------------------------------


def test_edited_log_no_longer_matches_recorded_observations(tmp_path: Path) -> None:
    """Review #1: an in-bounds edit used to replay as a valid episode."""
    ws = tmp_path / "ws"
    shutil.copytree(CASE / "starter", ws)
    for _ in range(4):
        subprocess.run(  # nosec B603 - fixed argv
            [
                str(NODE),
                "race.mjs",
                "step",
                json.dumps({"throttle": 1, "brake": 0, "steer": 0, "reset": 0}),
            ],
            cwd=ws,
            check=True,
            capture_output=True,
        )
    assert check("episode_valid", ws) == 0
    log = ws / ".race" / "actions.jsonl"
    log.write_text(log.read_text().replace('"steer":0,', '"steer":0.9,'))
    assert check("episode_valid", ws) == 1
    report = json.loads((ws / ".lab-grade" / "racing.json").read_text())
    assert "does not match" in report["invalid_reason"]


def test_prewritten_racing_json_is_removed_when_grading_cannot_run(
    tmp_path: Path, monkeypatch
) -> None:
    """Review #3: a forged report must not survive a missing Node."""
    ws = workspace_with(tmp_path, baseline_actions(tmp_path)[:3])
    (ws / ".lab-grade").mkdir()
    (ws / ".lab-grade" / "racing.json").write_text(
        '{"valid": true, "metrics": {"finished": 1}}'
    )
    monkeypatch.setattr(grade.shutil, "which", lambda _name: None)
    assert check("episode_valid", ws) == 127
    assert not (ws / ".lab-grade" / "racing.json").exists()


def test_final_workspace_report_is_also_written_to_the_run_dir(tmp_path: Path) -> None:
    """Review #3: the authoritative copy lives in data/runs/<id>/, beside the workspace."""
    run_dir = tmp_path / "data" / "runs" / "run-x"
    ws = workspace_with(run_dir, baseline_actions(tmp_path)[:5])
    (run_dir / "ws").rename(run_dir / "workspace")
    ws = run_dir / "workspace"
    assert grade.main(["check", "episode_valid", str(ws), "run-x"]) == 0
    assert json.loads((run_dir / "racing.json").read_text())["actions"] == 5


def test_snapshot_grading_does_not_overwrite_the_run_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "data" / "runs" / "run-y"
    (run_dir).mkdir(parents=True)
    (run_dir / "racing.json").write_text('{"final": true}')
    snap = workspace_with(
        run_dir / "steps" / "step-1.snapshot", baseline_actions(tmp_path)[:2]
    )
    assert grade.main(["check", "episode_valid", str(snap), "run-y"]) == 0
    assert json.loads((run_dir / "racing.json").read_text()) == {"final": True}


def test_replay_crash_is_inconclusive(tmp_path: Path, monkeypatch, capsys) -> None:
    """Review #4: a grader crash is infrastructure, not the agent's failure."""
    ws = workspace_with(tmp_path, baseline_actions(tmp_path)[:3])

    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="node", timeout=1)

    monkeypatch.setattr(grade.subprocess, "run", boom)
    assert check("episode_valid", ws) == 124
    assert "timed out" in capsys.readouterr().out

    monkeypatch.setattr(
        grade.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "SyntaxError"),
    )
    assert check("episode_valid", ws) == 127
