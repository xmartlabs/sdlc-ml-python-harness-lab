"""The harness ladder as one experiment family (ADR-0051)."""

from __future__ import annotations

import sys
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ladder  # noqa: E402


def _args(**over):
    base = {
        "cases": ["tetris-v2", "cli-textstats-v1"],
        "harness": "claude",
        "model": "claude-haiku-4-5-20251001",
        "reps": 5,
        "max_steps": 10,
        "family": "",
        "launch": False,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_the_ladder_is_four_single_factor_rungs_in_one_family(monkeypatch):
    monkeypatch.setattr(ladder, "known_skill_names", lambda: {"a", "b"})

    designs = ladder.build(_args())

    assert len(designs) == 4
    assert len({design.family_id for design in designs}) == 1
    # Every rung's control is the previous rung's treatment: one factor moves.
    for previous, current in pairwise(designs):
        assert current.conditions[0].label == previous.conditions[1].label
        assert current.conditions[0].overrides == previous.conditions[1].overrides
    # Rung 1 starts from nothing; rung 4 ends with the activation block.
    assert designs[0].conditions[0].overrides["harness_source"] == "bare"
    assert designs[-1].conditions[1].overrides["skills_invoke"] == ["a", "b"]
    # Every arm names its own variant; listing both at the top would file
    # each rung twice (`launch` expands variants x arms).
    assert all(design.prompt_variants == [] for design in designs)
    assert all(
        "prompt_variant" in condition.overrides
        for design in designs
        for condition in design.conditions
    )


def test_every_rung_passes_design_validation_without_objection(monkeypatch):
    from harness_lab_app.experiment_service import validate_design

    monkeypatch.setattr(ladder, "known_skill_names", lambda: {"a", "b"})

    for design in ladder.build(_args()):
        conditions = [
            {"label": c.label, "role": c.role, "overrides": c.overrides}
            for c in design.conditions
        ]
        assert validate_design(design.mode, conditions, in_family=True) == [], (
            design.experiment_id
        )


def test_a_family_is_one_harness_model_pair(monkeypatch):
    monkeypatch.setattr(ladder, "known_skill_names", lambda: {"a"})

    claude = ladder.build(_args())[0].family_id
    opencode = ladder.build(_args(harness="opencode"))[0].family_id

    assert claude != opencode
    assert claude.startswith("ladder-claude-")
