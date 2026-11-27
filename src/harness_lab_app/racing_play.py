"""racing-play-v1: was the episode played decision by decision, or scripted?

The case lets an agent drive the car through ``race step`` one call at a time,
or write a program that calls it in a loop. Both are allowed; the run is
labelled so a comparison never mixes the two silently. The label is an
observation from the record, and it names which record it came from:

- ``events``: the harness's tool-call stream, normalised to claude's shape
  (``harness_events``). One shell call per action is playing.
- ``timing``: the tool's own call log, for a harness that leaves no parsable
  tool events (OpenCode). A burst of steps under a second apart is a program.
- ``none``: nothing to read; the run is ``unknown``, never guessed ``played``.
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

# `./race step`, `race step`, `node race.mjs step`, after `cd ... &&` or inside
# `bash -lc '...'` — but not as an argument to echo/printf/cat.
RACE_STEP_RE = re.compile(
    r"(?:^|[;&|(\"']|\bnode\s+|\s)(?:\S*/)?race(?:\.mjs)?\s+step\b"
)
_ECHO_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:echo|printf|cat)\b[^;&|]*race(?:\.mjs)?\s+step"
)
# The controls JSON of one `race step` call, single- or double-quoted.
_CONTROLS_RE = re.compile(r"race(?:\.mjs)?\s+step\s+(?:'(\{[^']*\})'|\"(\{.*?\})\")")
BURST_LENGTH = 5
BURST_GAP_MS = 1000


def is_race_step(command: str) -> bool:
    """Whether one shell command issues a ``race step`` (not merely mentions it)."""
    text = command or ""
    if _ECHO_RE.search(text):
        return False
    return bool(RACE_STEP_RE.search(text))


def step_controls(trajectory: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The controls of every ``race step`` call in the recorded trajectory, in order."""
    found: list[dict[str, Any]] = []
    for command in (trajectory or {}).get("race_step_commands") or []:
        match = _CONTROLS_RE.search(command.replace("\\'", "'"))
        if not match:
            continue
        try:
            value = json.loads(match.group(1) or match.group(2))
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def trajectory_matches_log(
    trajectory: dict[str, Any] | None, controls: list[dict[str, Any]]
) -> bool | None:
    """Whether the logged controls were issued, in order, by recorded tool calls.

    ``None`` when there is no tool-event record to compare against. A log the
    agent rewrote after playing — or replaced with a better lap computed offline
    — has controls no recorded call ever sent.
    """
    if count_race_step_calls(trajectory) is None:
        return None
    issued = step_controls(trajectory)
    position = 0
    for wanted in controls:
        while position < len(issued) and not _same(issued[position], wanted):
            position += 1
        if position == len(issued):
            return False
        position += 1
    return True


def _same(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = ("throttle", "brake", "steer", "reset")
    try:
        return all(float(left[k]) == float(right[k]) for k in keys)
    except (KeyError, TypeError, ValueError):
        return False


def count_race_step_calls(trajectory: dict[str, Any] | None) -> int | None:
    """Tool calls that issued a ``race step``; ``None`` without a tool-event record."""
    if not trajectory or trajectory.get("source") != "claude_events":
        return None
    count = trajectory.get("race_step_calls")
    return int(count) if count is not None else None


def _has_burst(call_times_ms: list[int]) -> bool:
    run = 1
    for before, after in pairwise(call_times_ms):
        run = run + 1 if 0 <= after - before < BURST_GAP_MS else 1
        if run >= BURST_LENGTH:
            return True
    return False


def play_mode(
    actions: int, event_calls: int | None, call_times_ms: list[int]
) -> tuple[str, str]:
    """``(label, source)``: ``played``/``scripted``/``unknown`` and what said so."""
    if actions <= 0:
        return "unknown", "none"
    if event_calls is not None:
        # One recorded tool call per decision, and the tool's own call log
        # complete and paced like decisions — not a log rewritten afterwards.
        played = (
            event_calls >= actions
            and len(call_times_ms) == actions
            and not _has_burst(call_times_ms)
        )
        return ("played" if played else "scripted"), "events"
    if not call_times_ms:
        # Actions with no recorded tool call were written without the tool.
        return "scripted", "timing"
    return ("scripted" if _has_burst(call_times_ms) else "played"), "timing"


CASE_ID = "racing-play-v1"


def load_racing_grade(run_dir: Path) -> dict[str, Any] | None:
    """The grader's run report, ``data/runs/<id>/racing.json``, or ``None``.

    Only that copy is read. The grader deletes it before every grade of the
    final workspace and rewrites it after, so a report the agent wrote cannot
    survive a grade; the copy under ``workspace/.lab-grade`` is the agent's to
    overwrite and is never trusted.
    """
    try:
        data = json.loads((run_dir / "racing.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def racing_replay(run_dir: Path) -> dict[str, Any] | None:
    """What the run page needs to replay the graded episode, or ``None``.

    Built from the grader's report only (see :func:`load_racing_grade`): the
    condition it replayed under and the controls it accepted, so the page
    replays exactly the episode that was scored. An invalid episode has no
    accepted controls to show and returns ``None``.
    """
    grade = load_racing_grade(run_dir)
    if not grade or not grade.get("valid"):
        return None
    condition = grade.get("condition")
    controls = grade.get("controls")
    if not isinstance(condition, dict) or not isinstance(controls, list):
        return None
    if not all(isinstance(item, dict) for item in controls):
        return None
    return {
        "condition": condition,
        "controls": controls,
        "metrics": grade.get("metrics") or {},
        "ended_by": grade.get("ended_by") or "",
        "par_seconds": grade.get("par_seconds"),
        "checkpoints_total": grade.get("checkpoints_total"),
    }
