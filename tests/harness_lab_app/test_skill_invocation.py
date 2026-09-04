"""A skill under test is invoked, not just available (ADR-0041).

Two halves. Asking is part of the arm: `skills_invoke` reaches the runner as a
flag, prefixes every step's prompt, must name a skill the run can see, and
flips the condition hash. Invocation is observed: the trajectory records Skill
calls and SKILL.md reads, an evaluator turns them into an observation, and the
verdict leads with the arm that never opened its skill.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.evaluation import (
    EvaluationContext,
    SkillUsageEvaluator,
    run_profile,
)
from harness_lab_app.provenance import compute_condition_hash
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunConfigCreate, RunCreate
from harness_lab_app.scores import OBSERVATION, get_definition
from harness_lab_app.statistics import (
    ConditionSummary,
    skill_usage_caveats,
    success_delta,
    summarize_condition,
    verdict,
)
from harness_lab_app.trajectory import build, from_claude_events, from_opencode_logs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _config(**overrides) -> RunConfigCreate:
    base = {
        "case_id": "tetris-v1",
        "prompt_variant": "sdlc",
        "executor_model": "stub-executor",
    }
    return RunConfigCreate(**{**base, **overrides})


# --------------------------------------------------------------------------
# Asking is part of the arm
# --------------------------------------------------------------------------


def test_adapter_emits_invoke_flags_sorted_after_the_skill_flags():
    command = BenchRunAdapter().build_command(
        "r-1", _config(skills_extra=["zeta"], skills_invoke=["zeta", "alpha"])
    )
    assert command[command.index("--extra-skill") + 1] == "zeta"
    invokes = [
        command[i + 1] for i, tok in enumerate(command) if tok == "--invoke-skill"
    ]
    assert invokes == ["alpha", "zeta"]


def test_preflight_refuses_asking_for_a_skill_the_run_cannot_see():
    """Asked-and-absent would be recorded as asked-and-not-invoked."""
    with (
        _session() as session,
        pytest.raises(RunPreflightError, match="cannot see: ghost"),
    ):
        RunService(session).create_run(
            RunCreate(run_id="inv-1", config=_config(skills_invoke=["ghost"]))
        )


def test_preflight_refuses_asking_for_a_skill_this_run_disables():
    from harness_lab_app.skills_catalog import known_skill_names

    governed = sorted(known_skill_names())
    if not governed:
        pytest.skip("no governed skills in this checkout")
    with (
        _session() as session,
        pytest.raises(RunPreflightError, match="cannot see"),
    ):
        RunService(session).create_run(
            RunCreate(
                run_id="inv-2",
                config=_config(
                    skills_disabled=[governed[0]], skills_invoke=[governed[0]]
                ),
            )
        )


def test_a_governed_skill_may_be_asked_for_without_staging():
    from harness_lab_app.skills_catalog import known_skill_names

    governed = sorted(known_skill_names())
    if not governed:
        pytest.skip("no governed skills in this checkout")
    with _session() as session:
        run = RunService(session).create_run(
            RunCreate(run_id="inv-3", config=_config(skills_invoke=[governed[0]]))
        )
        stored = RunService(session).get_run_config(run.run_config_id)
    assert stored.skills_invoke == [governed[0]]


def test_condition_hash_flips_when_a_skill_is_asked_for():
    model = {"executor_model": "m", "planner_model": None}
    base = compute_condition_hash("h", model, "p", "c")
    assert compute_condition_hash("h", model, "p", "c", skills_invoke=()) == base
    asked = compute_condition_hash("h", model, "p", "c", skills_invoke=("b", "a"))
    assert asked != base
    assert (
        compute_condition_hash("h", model, "p", "c", skills_invoke=("a", "b")) == asked
    )


def test_the_activation_block_names_the_mechanism_and_every_step():
    block = bench_run.skill_activation_block(["caveman"])
    assert "invoke the `caveman` skill" in block
    assert "SKILL.md" in block
    assert "every later one" in block
    assert bench_run.skill_activation_block([]) == ""
    assert bench_run.skill_activation_block(["", "b", "a"]).count("`") == 4


def test_every_prompt_of_a_run_carries_the_activation_block(tmp_path, monkeypatch):
    """Each step is a fresh session with no memory of having been asked."""
    monkeypatch.setattr(bench_run, "WORKSPACE_ROOT", tmp_path / "workspace")
    source = tmp_path / "body.md"
    source.write_text("Build it.\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    steps_dir = out_dir / "steps"
    steps_dir.mkdir()

    first = bench_run.generate_prompt(
        run_id="r-K-001",
        prompt_short="default",
        prompt_source=source,
        planner_model=None,
        executor_model="m",
        session_id="s",
        out_dir=out_dir,
        invoke_skills=["caveman"],
    ).read_text()
    case_step = bench_run.generate_case_step_prompt(
        "r-K-001", 2, steps_dir, "Do step two.", invoke_skills=["caveman"]
    ).read_text()
    generic = bench_run.generate_generic_continuation_prompt(
        "r-K-001", 3, steps_dir, invoke_skills=["caveman"]
    ).read_text()
    tetris = bench_run.generate_continuation_prompt(
        "r-K-001", 4, steps_dir, invoke_skills=["caveman"]
    ).read_text()
    swe = bench_run.generate_swe_continuation_prompt(
        "r-K-001",
        5,
        steps_dir,
        {"fail_to_pass": ["t.py"]},
        None,
        invoke_skills=["caveman"],
    ).read_text()

    for text in (first, case_step, generic, tetris, swe):
        assert "Skill activation (required)" in text
        assert "`caveman`" in text
    # The activation sits before the task body, and the body is intact.
    assert first.index("Skill activation") < first.index("Build it.")
    # Without the ask, nothing is added.
    plain = bench_run.generate_generic_continuation_prompt(
        "r-K-001", 6, steps_dir
    ).read_text()
    assert "Skill activation" not in plain


# --------------------------------------------------------------------------
# Invocation is observed
# --------------------------------------------------------------------------


def _event(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def _tool(name: str, **payload) -> dict:
    return {"type": "tool_use", "name": name, "input": payload}


def test_trajectory_counts_skill_calls_and_skill_file_reads():
    lines = [
        _event(_tool("Skill", skill="caveman")),
        _event(_tool("Read", file_path="/app/.claude/skills/tdd/SKILL.md")),
        _event(_tool("Bash", command="cat .agents/skills/caveman/SKILL.md")),
        _event(_tool("Read", file_path="/app/src/api.py")),
    ]
    parsed = from_claude_events(lines)
    assert parsed["skill_calls"] == ["caveman", "tdd", "caveman"]
    trajectory = build(event_lines=lines)
    assert trajectory is not None
    assert trajectory["skill_invocations"] == {"caveman": 2, "tdd": 1}


def test_opencode_permission_lines_also_reveal_a_skill_read():
    log = (
        "permission=read pattern=/app/.opencode/skills/caveman/SKILL.md\n"
        "permission=edit pattern=/app/src/x.py\n"
    )
    assert from_opencode_logs(log)["skill_calls"] == ["caveman"]


def _context(skills: tuple[str, ...], invocations: dict[str, int] | None):
    trajectory = (
        None
        if invocations is None
        else {
            "source": "claude_events",
            "steps": [{"index": 1, "tool": "Bash", "input": "ls"}],
            "skill_invocations": invocations,
        }
    )
    return EvaluationContext(
        run_id="r", trajectory=trajectory, skills_under_test=skills
    )


def test_skill_usage_is_an_observation_with_a_named_source():
    for name in ("skill_invoked", "skill_invocations"):
        assert get_definition(name).provenance == OBSERVATION
        assert get_definition(name).source == "skill_usage"


def test_an_opened_skill_scores_one_and_says_which():
    scores = SkillUsageEvaluator().evaluate(_context(("caveman",), {"caveman": 3}))
    by_name = {score.name: score for score in scores}
    assert by_name["skill_invoked"].value == 1.0
    assert by_name["skill_invocations"].value == 3.0
    assert "caveman" in by_name["skill_invoked"].reason


def test_an_available_but_untouched_skill_scores_zero_not_nothing():
    scores = SkillUsageEvaluator().evaluate(_context(("caveman",), {"other": 1}))
    by_name = {score.name: score for score in scores}
    assert by_name["skill_invoked"].value == 0.0
    assert by_name["skill_invoked"].reason.startswith("none of caveman")


def test_a_bundle_scores_the_share_that_was_opened():
    scores = SkillUsageEvaluator().evaluate(
        _context(("a", "b", "c", "d"), {"a": 1, "c": 2})
    )
    by_name = {score.name: score for score in scores}
    assert by_name["skill_invoked"].value == 0.5
    assert by_name["skill_invocations"].value == 3.0


def test_a_run_about_no_skill_emits_nothing():
    assert SkillUsageEvaluator().evaluate(_context((), {"caveman": 1})) == []


def test_not_observed_is_not_stored_as_not_used():
    """No trajectory → no score; a zero here would be a fabricated fact."""
    assert SkillUsageEvaluator().evaluate(_context(("caveman",), None)) == []


def test_skill_usage_rides_in_the_default_profile():
    names = {s.name for s in run_profile("default", _context(("caveman",), {}))}
    assert {"skill_invoked", "skill_invocations"} <= names


# --------------------------------------------------------------------------
# The verdict says it first
# --------------------------------------------------------------------------


def _arm(label: str, passes: list[bool]) -> ConditionSummary:
    return summarize_condition(
        label, [{"task_validation_pass": passed} for passed in passes]
    )


def test_an_arm_that_never_opened_its_skill_is_flagged_as_availability():
    caveats = skill_usage_caveats(
        {"with-skills": {"skills": ["caveman"], "runs": 5, "invoked": 0}}
    )
    assert len(caveats) == 1
    assert "never invoked in its 5 run(s)" in caveats[0]
    assert "availability, not its use" in caveats[0]
    assert "Enforce skill use" in caveats[0]


def test_an_arm_that_was_asked_and_still_did_not_open_it_says_so():
    caveats = skill_usage_caveats(
        {
            "with-skills": {
                "skills": ["caveman"],
                "runs": 5,
                "invoked": 0,
                "requested": True,
            }
        }
    )
    assert "although the prompt asked for it" in caveats[0]
    assert "Enforce skill use" not in caveats[0]


def test_a_mixed_arm_is_flagged_as_mixing_treatments():
    caveats = skill_usage_caveats(
        {"with-skills": {"skills": ["caveman"], "runs": 5, "invoked": 2}}
    )
    assert "invoked in 2 of 5 run(s)" in caveats[0]


def test_an_arm_with_no_observation_or_no_skill_says_nothing():
    assert (
        skill_usage_caveats({"baseline": {"skills": [], "runs": 5, "invoked": 0}}) == []
    )
    assert (
        skill_usage_caveats({"with-skills": {"skills": ["x"], "runs": 0, "invoked": 0}})
        == []
    )


def test_the_causal_verdict_leads_with_the_missing_treatment():
    control = _arm("baseline", [True] * 5)
    treatment = _arm("with-skills", [True] * 5)
    answer = verdict(
        mode="harness_evaluation",
        cases=1,
        repetitions=5,
        control=control,
        treatment=treatment,
        success=success_delta(control, treatment),
        skill_usage={
            "baseline": {"skills": [], "runs": 0, "invoked": 0},
            "with-skills": {"skills": ["caveman"], "runs": 5, "invoked": 0},
        },
    )
    assert answer["headline"] == "No difference observed"
    assert answer["caveats"][0].startswith("with-skills: 'caveman' was never invoked")


def test_subject_validation_warns_when_the_driver_was_never_opened():
    arm = _arm("claude/haiku", [True, True, True])
    answer = verdict(
        mode="subject_validation",
        cases=1,
        repetitions=3,
        subject="my tool",
        arms=[arm],
        skill_usage={"claude/haiku": {"skills": ["driver"], "runs": 3, "invoked": 0}},
    )
    assert answer["headline"] == "Reliability per arm"
    assert "never invoked" in answer["caveats"][0]


def test_staging_a_skill_and_asking_for_it_is_one_factor():
    """The most common design in this lab must not warn about itself.

    Adding a skill without asking is not a treatment for a mode skill
    (ADR-0041), so "absent" versus "present and asked for" is one intervention —
    even though it moves two fields.
    """
    from harness_lab_app.experiment_service import validate_design
    from harness_lab_app.models import ExperimentMode

    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            {"label": "native", "role": "control", "overrides": {}},
            {
                "label": "native + skills",
                "role": "treatment",
                "overrides": {
                    "skills_extra": ["caveman"],
                    "skills_invoke": ["caveman"],
                },
            },
        ],
    )

    assert not any("More than one harness factor" in text for text in warnings)


def test_crossing_skill_sets_really_are_two_factors():
    """One arm adding X unasked while another asks for a Y it already had."""
    from harness_lab_app.experiment_service import validate_design
    from harness_lab_app.models import ExperimentMode

    warnings = validate_design(
        ExperimentMode.harness_evaluation,
        [
            {
                "label": "a",
                "role": "control",
                "overrides": {"skills_extra": ["x"], "skills_invoke": []},
            },
            {
                "label": "b",
                "role": "treatment",
                "overrides": {"skills_extra": [], "skills_invoke": ["y"]},
            },
        ],
    )

    assert any("More than one harness factor" in text for text in warnings)


def test_a_native_arm_gets_its_staged_skills_placed_in_the_workspace(tmp_path):
    """Under `native` the agent's cwd is the clone, so a lab-side skill is invisible.

    Without this the "their repo plus this skill" arm equals its own control and
    measures nothing — the same failure as ADR-0041, one level down.
    """
    from ml_python_base.skills_sync.linker import add_skills
    from ml_python_base.skills_sync.models import KIND_INTERNAL, Skill, ToolSpec

    source = tmp_path / "skills"
    source.mkdir()
    (source / "caveman.md").write_text("# caveman\n")
    skill = Skill(name="caveman", kind=KIND_INTERNAL, source_path=source / "caveman.md")

    workspace = tmp_path / "workspace"
    theirs = workspace / ".claude" / "skills" / "their-own-skill"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("# theirs\n")

    tool = ToolSpec(
        id="claude",
        display_name="Claude Code",
        link_strategy="copy",
        native_skills_dir=".claude/skills",
    )
    add_skills(workspace, tool, [skill])

    assert (workspace / ".claude/skills/caveman/SKILL.md").read_text() == "# caveman\n"
    # Their own skills survive: an arm that added one skill by deleting the rest
    # would be measuring something else entirely.
    assert (theirs / "SKILL.md").exists()
