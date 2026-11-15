"""Where a stopped run can pick up again, and how to wipe it for a clean retry.

A retry used to have one meaning: rerun from step 1 over whatever workspace was
left. For a phased case that is the expensive half of both choices — it pays
again for every phase already done, and it does not start clean either.

So a retry is now one of two things (ADR-0083):

- **from scratch**: the run's workspace and step artifacts are wiped and it
  runs again from step 1, as if it were new, under the same id;
- **from the last completed step**: the workspace is restored from that step's
  snapshot and the run resumes at the next step, keeping the steps it already
  finished — their time, cost and per-step grade.

A step is *completed* when the runner wrote its ``step-N.meta.json`` with a
restorable snapshot: a copy of the tree, taken after the agent's session for
that step ended. Anything the interrupted step left in the workspace afterwards
is discarded — no one can say whether that half-written state is sound.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Files the worker writes into the run directory before the runner starts. A
# retry from scratch keeps them: they are the run's configuration, not its
# results, and the worker rewrites them anyway.
_KEEP_ON_SCRATCH = frozenset({"case_spec.json", "masks", "harness_source"})


@dataclass(frozen=True)
class ResumePoint:
    """The step a run can continue after, or why it cannot."""

    last_step: int | None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.last_step is not None


def _step_meta(steps_dir: Path) -> dict[int, dict]:
    metas: dict[int, dict] = {}
    if not steps_dir.is_dir():
        return metas
    for path in steps_dir.glob("step-*.meta.json"):
        try:
            meta = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        step = meta.get("step")
        if isinstance(step, int) and step > 0:
            metas[step] = meta
    return metas


def resume_point(run_dir: Path, max_steps: int) -> ResumePoint:
    """The last step a run finished, if it can continue after it.

    Consecutive from step 1: a gap means the record is not one run's trajectory
    and nothing after it can be trusted. The last of them must have a snapshot
    copy — a ``diff`` against a cloned repository cannot be restored here — and
    there must be a step left to run.
    """
    metas = _step_meta(run_dir / "steps")
    if not metas:
        return ResumePoint(None, "No step finished, so there is nothing to keep.")
    last = 0
    while last + 1 in metas:
        last += 1
    if last == 0:
        return ResumePoint(None, "Step 1 never finished, so there is nothing to keep.")
    meta = metas[last]
    if meta.get("kind") != "snapshot":
        return ResumePoint(
            None,
            f"Step {last} was saved as {meta.get('kind') or 'nothing'}, not a copy "
            "of the workspace, so it cannot be restored. Retry from scratch.",
        )
    snapshot = run_dir / "steps" / str(meta.get("path") or f"step-{last}.snapshot")
    if not snapshot.is_dir():
        return ResumePoint(None, f"Step {last}'s snapshot is gone. Retry from scratch.")
    if last >= max_steps:
        return ResumePoint(
            None,
            f"All {max_steps} steps finished; the run stopped after its last one. "
            "There is no step left to continue with.",
        )
    return ResumePoint(last)


def restore_step(run_dir: Path, workspace: Path, step: int) -> None:
    """Put the workspace back as step ``step`` left it, and drop later steps.

    The steps after ``step`` are removed from the record: the interrupted one
    is rerun, and its partial events, log and prompt would otherwise be read as
    part of the run (usage sums every step's events).
    """
    steps_dir = run_dir / "steps"
    snapshot = steps_dir / f"step-{step}.snapshot"
    if not snapshot.is_dir():
        raise FileNotFoundError(f"No snapshot for step {step} at {snapshot}")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot, workspace, symlinks=True, ignore_dangling_symlinks=True)
    for path in steps_dir.iterdir():
        number = _step_number(path.name)
        if number is not None and number > step:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def _step_number(name: str) -> int | None:
    if not name.startswith("step-"):
        return None
    digits = ""
    for char in name[len("step-") :]:
        if not char.isdigit():
            break
        digits += char
    return int(digits) if digits else None


def wipe_for_scratch(run_dir: Path, workspace: Path) -> None:
    """Remove everything a previous attempt produced, keeping the run's config.

    The workspace goes, live and archived, with the steps, logs, usage and
    grades. What the worker staged before the runner started stays.
    """
    if workspace.exists():
        shutil.rmtree(workspace)
    if not run_dir.is_dir():
        return
    for path in run_dir.iterdir():
        if path.name in _KEEP_ON_SCRATCH:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
