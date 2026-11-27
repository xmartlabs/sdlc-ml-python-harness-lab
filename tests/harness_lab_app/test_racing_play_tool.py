"""racing-play-v1: the `race` tool the agent plays through.

Each test copies the case's starter into a fresh directory, as the runner seeds
a workspace, and drives the tool with Node exactly as an agent would.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - fixed local Node program under test
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTER = REPO_ROOT / "benchmarks" / "racing-play-v1" / "starter"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

BRAKE = {"throttle": 0, "brake": 1, "steer": 0, "reset": 0}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "ws"
    shutil.copytree(STARTER, target)
    return target


def race(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - fixed argv, no shell
        [str(NODE), str(workspace / "race.mjs"), *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def test_state_before_any_step_is_racing_at_tick_180(workspace: Path) -> None:
    result = race(workspace, "state")
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["phase"] == "racing"
    assert state["tick"] == 180
    assert state["actions_taken"] == 0
    assert state["terminal"] is False


def test_step_advances_30_ticks_and_logs_once(workspace: Path) -> None:
    result = race(workspace, "step", json.dumps(BRAKE))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["tick"] == 210
    assert len(lines(workspace / ".race" / "actions.jsonl")) == 1
    assert len(lines(workspace / ".race" / "calls.jsonl")) == 1


@pytest.mark.parametrize(
    "payload",
    [
        "nope",
        '{"throttle":1}',
        '{"throttle":1,"brake":0,"steer":5,"reset":0}',
        '{"throttle":NaN,"brake":0,"steer":0,"reset":0}',
        '{"throttle":"1","brake":0,"steer":0,"reset":0}',
    ],
)
def test_invalid_controls_are_refused_without_logging(
    workspace: Path, payload: str
) -> None:
    result = race(workspace, "step", payload)
    assert result.returncode == 2
    assert lines(workspace / ".race" / "actions.jsonl") == []
    assert lines(workspace / ".race" / "calls.jsonl") == []


def test_horizon_stops_at_tick_3780(workspace: Path) -> None:
    # Write 119 braking actions directly, then take the last one through the tool.
    race_dir = workspace / ".race"
    race_dir.mkdir()
    (race_dir / "actions.jsonl").write_text(
        "".join(json.dumps({"i": i, "controls": BRAKE}) + "\n" for i in range(119))
    )
    last = race(workspace, "step", json.dumps(BRAKE))
    assert last.returncode == 0, last.stderr
    state = json.loads(race(workspace, "state").stdout)
    assert state["tick"] == 3780
    assert state["terminal"] is True
    assert state["reason"] == "horizon"
    refused = race(workspace, "step", json.dumps(BRAKE))
    assert refused.returncode == 2
    assert "episode is over" in refused.stderr
    assert len(lines(race_dir / "actions.jsonl")) == 120


def test_replay_is_deterministic(tmp_path: Path) -> None:
    finals = []
    for name in ("a", "b"):
        ws = tmp_path / name
        shutil.copytree(STARTER, ws)
        for k in range(20):
            controls = {"throttle": 1, "brake": 0, "steer": (k % 5 - 2) / 4, "reset": 0}
            assert race(ws, "step", json.dumps(controls)).returncode == 0
        finals.append(race(ws, "state").stdout)
    assert finals[0] == finals[1]


def test_rules_are_printed(workspace: Path) -> None:
    result = race(workspace, "rules")
    assert result.returncode == 0
    assert "checkpoint" in result.stdout.lower()


def test_starter_engine_matches_frontend_engine() -> None:
    shipped = (STARTER / "racing.mjs").read_bytes()
    source = (REPO_ROOT / "frontend" / "src" / "gameplay" / "racing.mjs").read_bytes()
    assert shipped == source, (
        "re-copy frontend/src/gameplay/racing.mjs into the starter"
    )
