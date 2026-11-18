"""What a case asks of the machine it runs on: its image and how to play it.

Most cases run in the one runner image every run shares. A case that needs a
toolchain nobody else does — a game engine, a 3D renderer — would otherwise force
that toolchain into every run's image, so it names its own instead (ADR-0078).
Three keys, authored in ``task.yaml`` and carried in ``Case.metadata_json``:

- ``runner_image`` — the image the agent *and* the grader run in. One value for
  both lanes on purpose: an agent that cannot launch the engine it is building
  for cannot test its own work, and a grader in another image would grade code
  against a toolchain the agent never saw.
- ``sandbox_image`` — the image a person opens the run's workspace in (terminal
  and desktop), so a game can be *played*, not only graded.
- ``play_command`` — what the desktop sandbox launches on start, so "Play" is
  one click and not a terminal the person has to know what to type into.
- ``agent_hidden`` — the case's own grading material (its grader, rubric and
  notes) masked out of the agent's container (ADR-0079). The validation lane
  still reads them.

Every value is checked here, because each one ends up in a Docker call: an image
name is restricted to the lab's own local images, a hidden path to the case's
own directory.

A hidden path stops *reading*, not a determined agent. A workspace without a
repository of its own is a directory of the lab's tree, and the runner needs the
lab's ``.git`` to snapshot each step, so anything committed is one ``git show``
away however its working-tree copy is masked. Held-out data is therefore
generated per run by the grader (seeded with the run id), never stored.
"""

from __future__ import annotations

import re
from typing import Any

# The lab's own images only: `harness-lab-<name>[:tag]`. Never a registry
# reference, so a case file cannot make the worker pull and run an arbitrary
# image, and a missing image fails with a build instruction rather than a pull.
_IMAGE_RE = re.compile(
    r"^harness-lab-[a-z0-9][a-z0-9-]{0,62}(?::[A-Za-z0-9_.-]{1,64})?$"
)

# A play command is a shell line run in the sandbox's own workspace copy. It is
# authored in the corpus (reviewed like the validation commands), but a newline
# would hide a second command from anyone reading it in the UI.
_MAX_PLAY_COMMAND = 400


class CaseProfileError(ValueError):
    """A case asked for something the lab will not hand to Docker."""


def image_is_allowed(image: str) -> bool:
    """Whether ``image`` names one of the lab's own local images."""
    return bool(_IMAGE_RE.match(image.strip()))


def _meta(case: Any) -> dict[str, Any]:
    meta = getattr(case, "metadata_json", None)
    if meta is None and isinstance(case, dict):
        meta = case
    return meta if isinstance(meta, dict) else {}


def _image(meta: dict[str, Any], key: str) -> str:
    value = str(meta.get(key) or "").strip()
    if not value:
        return ""
    if not image_is_allowed(value):
        raise CaseProfileError(
            f"{key} '{value}' is not a lab image (expected harness-lab-<name>[:tag])."
        )
    return value


def runner_image_for(case: Any) -> str:
    """The case's own runner image, or ``""`` for the shared default."""
    return _image(_meta(case), "runner_image")


def sandbox_image_for(case: Any) -> str:
    """The case's own sandbox image, or ``""`` for the shared default."""
    return _image(_meta(case), "sandbox_image")


def play_command_for(case: Any) -> str:
    """The shell line the desktop sandbox launches, or ``""`` for none."""
    value = str(_meta(case).get("play_command") or "").strip()
    if not value:
        return ""
    if "\n" in value or "\r" in value or len(value) > _MAX_PLAY_COMMAND:
        raise CaseProfileError(
            f"play_command must be one line of at most {_MAX_PLAY_COMMAND} characters."
        )
    return value


def _hidden_path(raw: str, case_id: str) -> str:
    """A path under ``benchmarks/<case_id>/``, refused when it could reach outside."""
    text = str(raw).strip()
    parts = [part for part in text.split("/") if part]
    if (
        not parts
        or text.startswith("/")
        or ".." in parts
        or parts[:2] != ["benchmarks", case_id]
        or len(parts) < 3
    ):
        raise CaseProfileError(
            f"agent_hidden path '{text}' must be under benchmarks/{case_id}/."
        )
    # A trailing slash marks a directory, which the executor masks with tmpfs.
    return "/".join(parts) + ("/" if text.endswith("/") else "")


def agent_hidden_for(case: Any) -> list[str]:
    """The case's own paths the agent's container must not read, validated."""
    meta = _meta(case)
    raw = meta.get("agent_hidden")
    if not raw:
        return []
    if not isinstance(raw, list):
        raise CaseProfileError("agent_hidden must be a list of paths.")
    case_id = str(
        getattr(case, "case_id", None)
        or meta.get("task_id")
        or meta.get("case_id")
        or ""
    )
    if not case_id:
        raise CaseProfileError("agent_hidden needs the case id to check its paths.")
    return [_hidden_path(item, case_id) for item in raw]


# A play guide is a few short lines a person reads on the Play tab: the goal,
# how the game starts, the controls. Kept small and plain, because it is shown
# as text next to a running game.
_MAX_GUIDE_LINES = 12
_MAX_GUIDE_LINE = 200


def play_guide_for(case: Any) -> dict[str, Any]:
    """``{goal, controls: [[action, keys], …], tips: [...]}`` a case declares."""
    raw = _meta(case).get("play_guide")
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise CaseProfileError(
            "play_guide must be a mapping with goal, controls and tips."
        )
    goal = str(raw.get("goal") or "").strip()
    controls_raw = raw.get("controls") or []
    tips_raw = raw.get("tips") or []
    if not isinstance(controls_raw, list) or not isinstance(tips_raw, list):
        raise CaseProfileError("play_guide controls and tips must be lists.")
    controls: list[list[str]] = []
    for item in controls_raw:
        if not isinstance(item, dict) or len(item) != 1:
            raise CaseProfileError("each play_guide control is one 'what: keys' pair.")
        ((what, keys),) = item.items()
        controls.append([str(what).strip(), str(keys).strip()])
    tips = [str(tip).strip() for tip in tips_raw]
    lines = [goal, *(f"{a} {b}" for a, b in controls), *tips]
    if len(controls) + len(tips) > _MAX_GUIDE_LINES or any(
        len(line) > _MAX_GUIDE_LINE or "\n" in line for line in lines
    ):
        raise CaseProfileError(
            f"play_guide must be at most {_MAX_GUIDE_LINES} short single lines."
        )
    return {"goal": goal, "controls": controls, "tips": tips}


def profile_from_task(raw: dict[str, Any]) -> dict[str, Any]:
    """The metadata keys a ``task.yaml`` declares, validated; absent keys omitted.

    Raises :class:`CaseProfileError` on a value the lab would refuse at run
    time, so a bad case fails when it is seeded rather than when it is run.
    """
    profile: dict[str, Any] = {}
    for key in ("runner_image", "sandbox_image"):
        value = _image(raw, key)
        if value:
            profile[key] = value
    play = play_command_for(raw)
    if play:
        profile["play_command"] = play
    guide = play_guide_for(raw)
    if guide:
        # Stored as the case wrote it: play_guide_for re-validates on read.
        profile["play_guide"] = raw["play_guide"]
    hidden = agent_hidden_for(raw)
    if hidden:
        profile["agent_hidden"] = hidden
    budget = raw.get("phase_budget_minutes")
    if budget is not None:
        try:
            minutes = int(budget)
        except (TypeError, ValueError) as exc:
            raise CaseProfileError("phase_budget_minutes must be an integer.") from exc
        if not 1 <= minutes <= 24 * 60:
            raise CaseProfileError("phase_budget_minutes must be between 1 and 1440.")
        profile["phase_budget_minutes"] = minutes
    total = raw.get("run_timeout_minutes")
    if total is not None:
        try:
            total_minutes = int(total)
        except (TypeError, ValueError) as exc:
            raise CaseProfileError("run_timeout_minutes must be an integer.") from exc
        if not 10 <= total_minutes <= 24 * 60:
            raise CaseProfileError("run_timeout_minutes must be between 10 and 1440.")
        profile["run_timeout_minutes"] = total_minutes
    on_slow = raw.get("on_slow")
    if on_slow is not None:
        if on_slow not in ("stop", "wait"):
            raise CaseProfileError("on_slow must be 'stop' or 'wait'.")
        profile["on_slow"] = on_slow
    entry = str(raw.get("entry_point") or "").strip()
    if entry:
        if entry.startswith("/") or ".." in entry.split("/") or "\n" in entry:
            raise CaseProfileError(
                f"entry_point '{entry}' must be a path inside the workspace."
            )
        profile["entry_point"] = entry
    deliverable = str(raw.get("deliverable") or "").strip().lower()
    if deliverable:
        # `report`: one step, measured on what the agent hands in (ADR-0087).
        if deliverable not in DELIVERABLES:
            raise CaseProfileError(
                f"deliverable must be one of {sorted(DELIVERABLES)}, got '{deliverable}'."
            )
        profile["deliverable"] = deliverable
    return profile


DELIVERABLES = frozenset({"report"})


PROFILE_KEYS = (
    "runner_image",
    "sandbox_image",
    "play_command",
    "agent_hidden",
    "entry_point",
    "phase_budget_minutes",
    "on_slow",
    "run_timeout_minutes",
    "play_guide",
    "deliverable",
)


def run_timeout_for(case: Any, default_seconds: int) -> int:
    """The run's total budget in seconds: the case's own, else the lab's default."""
    minutes = _meta(case).get("run_timeout_minutes")
    if isinstance(minutes, int) and minutes > 0:
        return minutes * 60
    return default_seconds


def slow_policy_for(case: Any) -> tuple[int | None, str]:
    """``(phase_budget_minutes or None, on_slow)`` a case declares; stop by default."""
    meta = _meta(case)
    budget = meta.get("phase_budget_minutes")
    on_slow = str(meta.get("on_slow") or "stop")
    if on_slow not in ("stop", "wait"):
        on_slow = "stop"
    return (int(budget) if isinstance(budget, int) and budget > 0 else None), on_slow
