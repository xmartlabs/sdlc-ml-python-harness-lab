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

Every value is checked here, because each one ends up in a Docker call: an image
name is restricted to the lab's own local images.

There is deliberately no "hidden path" key. A workspace without a repository of
its own is a directory of the lab's tree, and the runner needs the lab's ``.git``
to snapshot each step, so anything committed is one ``git show`` away from the
agent however its working-tree copy is masked. Held-out data is generated per
run by the grader instead (seeded with the run id), which nothing can leak.
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
    return profile


PROFILE_KEYS = ("runner_image", "sandbox_image", "play_command")
