"""A case exposes exactly two variants, and they differ only by the preamble.

ADR-0047. These tests are the enforcement behind that decision: without them
`prompt_harness.md` is just a second file that happens to look similar today,
and the full-harness comparison degrades into a prompt comparison the moment
someone edits one side.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PREAMBLE = REPO_ROOT / "benchmarks" / "_harness_preamble.md"
TASK_YAMLS = sorted(REPO_ROOT.glob("benchmarks/*/task.yaml"))

_SCRIPTS = REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_prompt_variants  # noqa: E402


def _pairs() -> list[Path]:
    return sorted(REPO_ROOT.glob("benchmarks/*/prompt_default.md"))


def test_at_least_one_case_uses_the_two_variant_shape():
    assert _pairs(), "expected benchmarks/<case>/prompt_default.md to exist"


@pytest.mark.parametrize("default", _pairs(), ids=lambda p: p.parent.name)
def test_harness_prompt_is_the_preamble_plus_the_identical_task(default: Path):
    """The task body must be byte-identical on both arms.

    This is the whole point of the pair: anything that differs beyond the
    preamble is a second factor, and a delta measured across two factors
    cannot be attributed to either one.
    """
    harness = default.with_name("prompt_harness.md")
    assert harness.is_file(), f"missing {harness.relative_to(REPO_ROOT)}"
    # Asserted against the generator's own definition, not a second copy of the
    # concatenation: two copies drift, and the failure then reads as a stale
    # prompt when it is really a disagreement between the gate and the tool it
    # tells you to run.
    assert harness.read_text() == build_prompt_variants.expected(default), (
        f"{harness.relative_to(REPO_ROOT)} drifted from its default sibling — "
        "run `make prompts`"
    )


@pytest.mark.parametrize("default", _pairs(), ids=lambda p: p.parent.name)
def test_default_prompt_says_nothing_about_the_harness(default: Path):
    """The control arm must not smuggle in harness instructions.

    A `default` that names a skill or points at `.github/` is a treatment
    prompt wearing the control's label, which silently shrinks the very gap
    the experiment is built to measure.
    """
    body = default.read_text().lower()
    for leaked in (".github/", "memory/context.md", "skill", "governance"):
        assert leaked not in body, (
            f"{default.relative_to(REPO_ROOT)} mentions {leaked!r}; harness "
            "instructions belong in benchmarks/_harness_preamble.md"
        )


@pytest.mark.parametrize("task_path", TASK_YAMLS, ids=lambda p: p.parent.name)
def test_task_yaml_declares_only_declared_prompt_files(task_path: Path):
    """Every declared variant resolves to a file that exists.

    `resolve_prompt_variant_text` returns None for a missing path and the
    runner falls back to the raw path string, so a typo here ships a run whose
    prompt is a filename — which reads downstream as the model failing.
    """
    raw = yaml.safe_load(task_path.read_text())
    for variant, relpath in (raw.get("prompts") or {}).items():
        resolved = REPO_ROOT / str(relpath)
        assert resolved.is_file(), (
            f"{task_path.relative_to(REPO_ROOT)} variant {variant!r} points at "
            f"{relpath}, which does not exist"
        )


@pytest.mark.parametrize("task_path", TASK_YAMLS, ids=lambda p: p.parent.name)
def test_every_case_declares_exactly_the_two_variant_pair(task_path: Path):
    raw = yaml.safe_load(task_path.read_text())
    assert sorted(raw["prompts"]) == ["default", "harness"]


# What `validation_commands` actually grades, per case. The prompt has to say
# it, or the run is scored on a requirement it was never issued.
GRADED_CONTRACT = {
    "tetris-v2": ("BENCH_SMOKE", "test_game.py"),
    "cli-textstats": ("words", "lines", "unique"),
    "todo-web-v1": ("test_todo.py", "TODO_WORKSPACE", "index.html"),
    "frontend-bugfix-v1": ("test_schedule.py", "SCHEDULE_WORKSPACE", "index.html"),
    "clinic-slots-t1": ("test_slots.py", "CLINIC_WORKSPACE", "clinic.py"),
    "eda-iris-pipeline-v2": (
        "test_iris_pipeline.py",
        "IRIS_WORKSPACE",
        "ml_pipeline.py",
    ),
    "clinic-booking-t2": ("test_booking.py", "CLINIC_WORKSPACE", "clinic.py"),
    "clinic-rules-t3": ("test_rules.py", "CLINIC_WORKSPACE", "clinic.py"),
    # Check names the grader runs and the prompt's contract states (ADR-0078).
    "chess3d-v1": ("perft", "castling", "status_label"),
    "racing3d-v1": ("checkpoint_order", "lap_counts", "rivals_finish_heldout"),
    "opencity3d-v1": ("building_count", "minimap", "attribution"),
}


@pytest.mark.parametrize("task_path", TASK_YAMLS, ids=lambda p: p.parent.name)
def test_default_states_the_contract_it_is_graded_against(task_path: Path):
    """The graded contract lives in the task, not in one variant.

    Experiment `...-on--2` scored 1/10 on all four Tetris runs because the
    prompt it used never mentioned BENCH_SMOKE while `validation_commands`
    graded every run on it. A requirement the agent is never issued is not a
    capability measurement — it is a measurement of the prompt.
    """
    case = task_path.parent.name
    tokens = GRADED_CONTRACT.get(case)
    assert tokens, f"add {case!r} to GRADED_CONTRACT when you add the case"
    graded = " ".join(yaml.safe_load(task_path.read_text())["validation_commands"])
    body = (task_path.parent / "prompt_default.md").read_text()
    for token in tokens:
        assert token in graded, f"{case}: {token!r} is not actually graded"
        assert token in body, f"{case}: prompt_default.md never states {token!r}"


def test_the_preamble_needs_no_trailing_blank_line_to_survive_a_commit():
    """The separator must not live at the end of the preamble file.

    `end-of-file-fixer` runs as a pre-commit hook here and trims a file to
    exactly one closing newline, so a preamble ending in a blank line is
    reverted on every commit — silently, leaving the drift gate red with no
    diff to look at. The join belongs in the generator, and this test fails if
    anybody moves it back.
    """
    assert PREAMBLE.read_text().endswith("---\n")
    assert not PREAMBLE.read_text().endswith("\n\n")
    assert build_prompt_variants.SEPARATOR == "\n"
