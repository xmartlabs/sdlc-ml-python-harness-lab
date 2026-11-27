"""racing-play-v1 grader: replay the agent's racing episode and judge one rung.

    python grade.py check <name> <workspace> <run_id>

Exit 0 when the rung holds, 1 when it does not. Infrastructure is never the
agent's failure: a missing Node or a replay that crashed exits 127 and a replay
that timed out exits 124, which the lab classifies as inconclusive.

Every call first deletes any earlier report, so a file the agent wrote can never
stand in for a grade, then writes ``<workspace>/.lab-grade/racing.json``: the
replayed metrics, how the episode ended, the replayed controls and the
timestamps of the agent's ``race step`` calls. When the workspace is the run's
final archive (``data/runs/<run_id>/workspace``) the same report is written to
``data/runs/<run_id>/racing.json``, the only copy the lab's evaluator reads.

The engine and the condition come from the lab tree next to this file, never
from the workspace: the agent can read and even rewrite its own copies, and
the grade must not move with them. Pure stdlib; no network.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - fixed Node program from the lab tree
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
LAB_ROOT = CASE.parents[1]
ENGINE = LAB_ROOT / "frontend" / "src" / "gameplay" / "racing.mjs"
CONDITION = CASE / "starter" / "condition.mjs"
REPLAY = HERE / "replay.mjs"

CHECKS = (
    "episode_valid",
    "progress_25",
    "progress_50",
    "progress_75",
    "all_gates",
    "finished",
    "clean",
    "fast",
)
# A lap at or under this many seconds of game time is "fast": it has to beat
# the naive scripted controller, which finishes in 22.02 s under this exact
# condition. Two model laps on the same condition took ~15.5 and ~16.1 s.
PAR_SECONDS = 20.0
REPLAY_TIMEOUT_SECONDS = 90


class Inconclusive(RuntimeError):
    """Infrastructure, not the agent: the check settles nothing."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def _read_calls(path: Path) -> list[int]:
    calls: list[int] = []
    if not path.exists():
        return calls
    for line in path.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
            calls.append(int(record["t"]))
        except (ValueError, TypeError, KeyError):
            continue
    return calls


def grade(workspace: Path, engine: Path = ENGINE) -> dict[str, Any]:
    """Replay the workspace's episode and summarise it."""
    node = shutil.which("node")
    if node is None:
        raise Inconclusive("command not found: node", 127)
    actions_path = workspace / ".race" / "actions.jsonl"
    try:
        result = subprocess.run(  # nosec B603 - fixed argv, paths from the lab tree
            [node, str(REPLAY), str(engine), str(CONDITION), str(actions_path)],
            capture_output=True,
            text=True,
            timeout=REPLAY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise Inconclusive(
            f"replay timed out after {REPLAY_TIMEOUT_SECONDS}s", 124
        ) from exc
    try:
        replayed = json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        replayed = None
    if not isinstance(replayed, dict):
        raise Inconclusive(
            f"replay crashed; command not found or broken: {result.stderr.strip()[:300]}",
            127,
        )
    valid = bool(replayed.get("valid"))
    metrics = replayed.get("metrics") or {}
    reason = replayed.get("reason")
    ended_by = reason if valid and reason in ("finished", "horizon") else "cut"
    return {
        "valid": valid,
        "invalid_reason": replayed.get("invalid_reason", ""),
        "actions": int(replayed.get("actions") or 0),
        "metrics": metrics,
        "checkpoints_total": replayed.get("checkpoints_total"),
        "episode_complete": valid and ended_by != "cut",
        "ended_by": ended_by if valid else "invalid",
        "ticks": replayed.get("ticks"),
        "par_seconds": PAR_SECONDS,
        "condition": replayed.get("condition") or {},
        "controls": replayed.get("controls") or [],
        "calls": _read_calls(workspace / ".race" / "calls.jsonl"),
    }


def judge(name: str, report: dict[str, Any]) -> tuple[bool, str]:
    """Whether rung ``name`` holds for ``report``, and why."""
    if not report["valid"]:
        return False, f"no valid episode: {report['invalid_reason']}"
    metrics = report["metrics"]
    progress = float(metrics.get("progress") or 0.0)
    finished = metrics.get("finished") == 1
    if name == "episode_valid":
        return (
            True,
            f"{report['actions']} actions replayed, ended by {report['ended_by']}",
        )
    if name.startswith("progress_"):
        threshold = int(name.removeprefix("progress_")) / 100
        return progress >= threshold, f"progress {progress:.1%} (needs {threshold:.0%})"
    if name == "all_gates":
        passed = metrics.get("checkpoints_passed")
        return passed == report[
            "checkpoints_total"
        ], f"{passed}/{report['checkpoints_total']} checkpoints"
    if name == "finished":
        return (
            finished,
            "lap finished" if finished else f"no lap; progress {progress:.1%}",
        )
    collisions, resets = metrics.get("collisions", 0), metrics.get("resets", 0)
    if name == "clean":
        clean = finished and collisions == 0 and resets == 0
        return clean, f"finished={finished} collisions={collisions} resets={resets}"
    if name == "fast":
        seconds = metrics.get("finish_time_seconds")
        fast = finished and seconds is not None and seconds <= PAR_SECONDS
        return fast, f"finish {seconds}s (par {PAR_SECONDS}s)"
    raise ValueError(f"unknown check {name!r}; known: {', '.join(CHECKS)}")


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[0] != "check" or argv[1] not in CHECKS:
        print(f"usage: grade.py check <{'|'.join(CHECKS)}> <workspace> <run_id>")
        return 2
    name, workspace, run_id = argv[1], Path(argv[2]), argv[3]
    targets = [workspace / ".lab-grade" / "racing.json"]
    # Only the run's final archive owns the run-level report; a step snapshot
    # (data/runs/<id>/steps/...) is graded too and must not overwrite it.
    if workspace.name == "workspace" and workspace.parent.name == run_id:
        targets.append(workspace.parent / "racing.json")
    for target in targets:
        target.unlink(missing_ok=True)
    try:
        report = grade(workspace)
    except Inconclusive as exc:
        print(str(exc))
        return exc.exit_code
    text = json.dumps(report, indent=2, sort_keys=True)
    for target in targets:
        target.parent.mkdir(exist_ok=True)
        target.write_text(text)
    passed, why = judge(name, report)
    print(f"{'PASS' if passed else 'FAIL'} {name}: {why}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
