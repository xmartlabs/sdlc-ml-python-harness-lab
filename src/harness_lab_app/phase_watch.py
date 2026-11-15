"""Phase-aware supervision of a live run: expected time, a person's decision, live grades.

A run used to have two clocks: a total budget, and a silence budget. Both are
blind to what the run is doing. Phase 4 of racing3d-v1-K-001 legitimately took
52 minutes while phases 1-3 took 5-14, and phase 5 then sat 43 minutes inside a
silent test. One fixed number either cuts the first or waits out the second.

This module replaces the guess with a question asked at the right moment:

- every phase has an **expected time** — the case's ``phase_budget_minutes``, else
  three times the median of that phase in the case's earlier runs, never below
  a floor;
- past it, the run is flagged **slow** (``slow_phase.json`` plus an event) and a
  person can **keep waiting** (more time for this phase) or **stop it and grade
  what it built**;
- with no answer within a grace period, the case's ``on_slow`` policy decides —
  ``stop`` by default, because experiment runs have nobody watching.

``LivePhaseGrader`` grades each phase as soon as its snapshot exists, so the
same screen shows whether the phase that is taking long is getting anywhere.

The clock is injected: tests drive time, and the worker uses the wall clock so
a sleeping laptop does not freeze it (see ``tasks._wall_clock``).
"""

from __future__ import annotations

import json
import statistics
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_lab_app.run_progress import LIVE_STEPS_FILE, SLOW_STATE_FILE

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Below this many earlier finished phases, history says nothing and the case's
# budget or the floor applies.
_MIN_HISTORY = 2
_HISTORY_FACTOR = 3.0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1))
    tmp.replace(path)


def _duration(seconds: float) -> str:
    """``45 min``, ``1 h 5 min`` or ``40 s``: readable at every scale."""
    total = max(0, round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    if minutes:
        return f"{minutes} min"
    return f"{secs} s"


def phase_history(
    runs_dir: Path, case_id: str, exclude_run: str
) -> dict[int, list[float]]:
    """Seconds each step took in the case's earlier runs, by step number.

    Read from ``data/runs/<case_id>-K-*/steps/step-N.meta.json``: the steps a
    run actually finished. A cut step never wrote its meta, so it never pulls
    the median up.
    """
    seen: dict[int, list[float]] = {}
    if not runs_dir.is_dir():
        return seen
    for run_dir in runs_dir.glob(f"{case_id}-K-*"):
        if run_dir.name == exclude_run:
            continue
        for meta_path in (run_dir / "steps").glob("step-*.meta.json"):
            meta = _read_json(meta_path)
            step, seconds = meta.get("step"), meta.get("seconds")
            if (
                isinstance(step, int)
                and isinstance(seconds, int | float)
                and seconds > 0
            ):
                seen.setdefault(step, []).append(float(seconds))
    return seen


def expected_seconds(
    step: int,
    *,
    case_budget_minutes: int | None,
    history: dict[int, list[float]],
    floor_seconds: int,
) -> int:
    """How long phase ``step`` may run before it is flagged slow."""
    if case_budget_minutes:
        return case_budget_minutes * 60
    samples = history.get(step) or []
    if len(samples) >= _MIN_HISTORY:
        return max(floor_seconds, int(_HISTORY_FACTOR * statistics.median(samples)))
    return floor_seconds


@dataclass
class PhaseWatch:
    """Decides, tick by tick, whether the current phase is slow and what to do.

    ``tick`` is called by the stream watchdog about once a second with the
    current step, the time it started and the extra time a person granted.
    It returns ``None`` (keep going) or ``"slow_phase"`` (stop: the grace
    period ran out under an ``on_slow: stop`` policy).
    """

    run_dir: Path
    budget_for: Callable[[int], int]
    grace_seconds: int
    on_slow: str = "stop"
    on_flag: Callable[[str], None] = lambda _message: None
    _flagged_step: int | None = field(default=None, init=False)
    _flagged_extra: int = field(default=-1, init=False)
    _phase_step: int | None = field(default=None, init=False)
    _extra_base: int = field(default=0, init=False)

    def tick(
        self, step: int | None, step_started: float | None, extra: int, now: float
    ) -> str | None:
        """``extra`` is the run's total granted time; only this phase's share counts.

        ``Run.extra_wait_seconds`` only grows. Time a person granted to phase 1
        is not time granted to phase 2, so the total seen when a phase starts is
        its baseline, and the phase is extended only by what was added since.
        """
        if step is None or step_started is None:
            return None
        if step != self._phase_step:
            self._phase_step, self._extra_base = step, max(0, extra)
        extra = max(0, extra - self._extra_base)
        budget = self.budget_for(step)
        if budget <= 0:
            return None
        deadline = step_started + budget + max(0, extra)
        if now < deadline:
            if self._flagged_step == step:
                # A person granted more time: the flag is lifted until the new
                # deadline passes.
                self._clear()
            return None
        if self._flagged_step != step or self._flagged_extra != extra:
            self._flagged_step, self._flagged_extra = step, extra
            decide_by = now + self.grace_seconds
            _write_json(
                self.run_dir / SLOW_STATE_FILE,
                {
                    "step": step,
                    "expected_seconds": budget,
                    "extra_seconds": extra,
                    "slow_since": deadline,
                    "decide_by": decide_by,
                    "on_slow": self.on_slow,
                },
            )
            action = (
                "it will be stopped and graded on what it built"
                if self.on_slow == "stop"
                else "it will keep running"
            )
            self.on_flag(
                f"Phase {step} is slower than expected: "
                f"{_duration(now - step_started)} against "
                f"{_duration(budget + max(0, extra))} expected. Keep waiting or "
                f"stop it from the run page; with no answer in "
                f"{_duration(self.grace_seconds)} {action}."
            )
            return None
        state = _read_json(self.run_dir / SLOW_STATE_FILE)
        if self.on_slow == "stop" and now >= float(state.get("decide_by") or now + 1):
            return "slow_phase"
        return None

    def _clear(self) -> None:
        self._flagged_step, self._flagged_extra = None, -1
        path = self.run_dir / SLOW_STATE_FILE
        if path.exists():
            path.unlink()

    def close(self) -> None:
        """Remove the slow flag: the run is over, nothing is waiting for a decision."""
        self._clear()


class LivePhaseGrader:
    """Grades each finished phase's snapshot on a background thread.

    ``grade(step, first)`` does the work (``ValidationService.grade_step_live``
    in a session of its own); ``first`` asks it to install the graders'
    dependencies, once. Results land in ``live_steps.json`` beside the steps,
    which ``run_progress.read_progress`` shows next to each phase.

    A grade takes a minute or two (a Godot harvest), so it never runs on the
    watchdog's thread; at most one runs at a time, and the newest finished step
    is graded next, so a slow grade never queues a backlog.
    """

    def __init__(
        self, run_dir: Path, grade: Callable[[int, bool], dict[str, Any] | None]
    ):
        self.run_dir = run_dir
        self._grade = grade
        self._lock = threading.Lock()
        self._busy = False
        self._done: set[int] = {int(k) for k in self._load()}
        self._installed = False
        self._thread: threading.Thread | None = None

    def _load(self) -> dict[str, Any]:
        data = _read_json(self.run_dir / LIVE_STEPS_FILE).get("steps")
        return data if isinstance(data, dict) else {}

    def _finished_steps(self) -> list[int]:
        steps = self.run_dir / "steps"
        if not steps.is_dir():
            return []
        found = []
        for meta in steps.glob("step-*.meta.json"):
            data = _read_json(meta)
            if (
                isinstance(data.get("step"), int)
                and (steps / f"step-{data['step']}.snapshot").is_dir()
            ):
                found.append(data["step"])
        return sorted(found)

    def poll(self) -> None:
        """Start grading the newest finished, ungraded step, if idle."""
        with self._lock:
            if self._busy:
                return
            pending = [s for s in self._finished_steps() if s not in self._done]
            if not pending:
                return
            step = pending[-1]
            self._busy = True
        self._thread = threading.Thread(target=self._run, args=(step,), daemon=True)
        self._thread.start()

    def _run(self, step: int) -> None:
        try:
            first = not self._installed
            result = self._grade(step, first)
            self._installed = True
        except Exception as exc:  # a live grade must never touch the run
            result = {"error": repr(exc)[:300]}
        with self._lock:
            self._done.add(step)
            data = self._load()
            data[str(step)] = result if result is not None else {"unmeasured": True}
            _write_json(self.run_dir / LIVE_STEPS_FILE, {"steps": data})
            self._busy = False

    def join(self, timeout: float) -> bool:
        """Wait for a grade in flight, so the final validation does not race it.

        Returns whether the grader is idle. The final validation reinstalls the
        graders' dependencies and regrades the same snapshots, so it must not
        start while a live grade is still using them.
        """
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True
