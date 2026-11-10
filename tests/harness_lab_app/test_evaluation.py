"""The evaluation model: declared scores, and the three kinds of claim (ADR-0034).

The rule these tests hold is that nothing undeclared can be stored, and that a
fact, an observation and a judgement stay distinguishable all the way through.
"""

from __future__ import annotations

import pytest

from harness_lab_app.evaluation import (
    DeterministicEvaluator,
    EfficiencyEvaluator,
    EvaluationContext,
    HarnessBehaviorEvaluator,
    ScoreValue,
    changed_files,
    path_matches,
    run_profile,
)
from harness_lab_app.scores import (
    FACT,
    JUDGEMENT,
    OBSERVATION,
    REGISTRY,
    get_definition,
)

_DIFF = """diff --git a/src/api/customers.py b/src/api/customers.py
--- a/src/api/customers.py
+++ b/src/api/customers.py
@@ -1,3 +1,5 @@
 import x
+def status():
+    return "ok"
-def old():
diff --git a/migrations/0001_init.sql b/migrations/0001_init.sql
--- a/migrations/0001_init.sql
+++ b/migrations/0001_init.sql
@@ -1 +1,2 @@
+ALTER TABLE customer ADD COLUMN status TEXT;
"""


# --------------------------------------------------------------------------
# The registry is the gate
# --------------------------------------------------------------------------


def test_an_undeclared_score_cannot_be_stored():
    with pytest.raises(KeyError, match="Unknown score"):
        ScoreValue("vibes", 0.9).as_row()


def test_every_definition_declares_what_a_reader_needs():
    for definition in REGISTRY.values():
        assert definition.version, definition.name
        assert definition.provenance in {FACT, OBSERVATION, JUDGEMENT}, definition.name
        assert definition.direction, definition.name
        assert definition.description.strip(), definition.name


def test_a_stored_score_carries_its_version_so_methods_cannot_pool():
    row = ScoreValue("test_pass_rate", 1.0).as_row()
    assert (row["name"], row["version"]) == ("test_pass_rate", "v1")
    assert get_definition("test_pass_rate").key == "test_pass_rate@v1"


def test_a_boolean_is_not_stored_as_a_number():
    """`bool` is a subclass of `int`; the naive branch order stores True as 1.0."""
    row = ScoreValue("task_validation_pass", True).as_row()
    assert row["value_bool"] is True
    assert row["value_num"] is None


# --------------------------------------------------------------------------
# Level 1 — outcome
# --------------------------------------------------------------------------


def test_diff_parsing_finds_the_paths_that_changed():
    assert changed_files(_DIFF) == [
        "migrations/0001_init.sql",
        "src/api/customers.py",
    ]


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("src/api/customers.py", "src/api/customers.py", True),
        ("app/src/api/customers.py", "src/api/customers.py", True),
        ("migrations/0001_init.sql", "migrations/", True),
        ("src/api/orders.py", "src/api/customers.py", False),
        ("src/api/customers.py", "migrations/", False),
    ],
)
def test_a_declared_surface_matches_files_and_directories(path, pattern, expected):
    assert path_matches(path, pattern) is expected


def test_a_forbidden_path_is_counted_even_when_the_tests_pass():
    scores = DeterministicEvaluator().evaluate(
        EvaluationContext(
            run_id="r1",
            validation={"objective": {"passed": True, "checks": [{"passed": True}]}},
            diff_text=_DIFF,
            files_expected=("src/api/customers.py",),
            files_forbidden=("migrations/",),
        )
    )
    by_name = {score.name: score for score in scores}

    assert by_name["task_validation_pass"].value is True
    assert by_name["expected_files_modified"].value == 1.0
    # The whole point: green tests and a violated boundary coexist.
    assert by_name["forbidden_files_modified"].value == 1.0
    assert "migrations/0001_init.sql" in by_name["forbidden_files_modified"].reason


def test_a_case_declaring_no_surface_produces_no_surface_scores():
    scores = DeterministicEvaluator().evaluate(
        EvaluationContext(
            run_id="r1",
            validation={"objective": {"passed": True, "checks": [{"passed": True}]}},
            diff_text=_DIFF,
        )
    )
    names = {score.name for score in scores}
    assert "expected_files_modified" not in names
    assert "forbidden_files_modified" not in names


def test_a_partial_test_result_is_a_rate_not_a_verdict():
    scores = DeterministicEvaluator().evaluate(
        EvaluationContext(
            run_id="r1",
            validation={
                "objective": {
                    "passed": False,
                    "checks": [{"passed": True}, {"passed": False}, {"passed": True}],
                }
            },
        )
    )
    by_name = {score.name: score.value for score in scores}
    assert by_name["task_validation_pass"] is False
    assert by_name["test_pass_rate"] == pytest.approx(2 / 3)


# --------------------------------------------------------------------------
# Level 2 — efficiency
# --------------------------------------------------------------------------


def test_efficiency_reads_cost_and_diff_size():
    scores = EfficiencyEvaluator().evaluate(
        EvaluationContext(
            run_id="r1",
            validation={
                "cost": {"total_tokens": 12000, "cost_usd": 0.81, "time_minutes": 4.5},
                "effort": {"steps": 7},
            },
            diff_text=_DIFF,
        )
    )
    by_name = {score.name: score.value for score in scores}
    assert by_name["total_tokens"] == 12000.0
    assert by_name["cost_usd"] == 0.81
    assert by_name["agent_steps"] == 7.0
    assert by_name["files_changed"] == 2.0
    assert by_name["diff_lines_changed"] > 0


def test_a_run_with_no_usage_emits_nothing_rather_than_zero():
    """A missing cost is unknown, and zero is a claim."""
    scores = EfficiencyEvaluator().evaluate(EvaluationContext(run_id="r1"))
    assert scores == []


# --------------------------------------------------------------------------
# Level 4 — behaviour, labelled as judgement
# --------------------------------------------------------------------------


def test_audit_verdicts_become_scores_marked_as_judgements():
    scores = HarnessBehaviorEvaluator().evaluate(
        EvaluationContext(
            run_id="r1",
            audit_findings={
                "items": [
                    {"name": "a", "verdict": "útil"},
                    {"name": "b", "verdict": "useful"},
                    {"name": "c", "verdict": "redundante"},
                    {"name": "d", "verdict": "unclear"},
                ]
            },
        )
    )
    by_name = {score.name: score for score in scores}
    assert by_name["harness_items_useful"].value == 2.0
    assert by_name["harness_items_redundant"].value == 1.0
    assert by_name["skill_adherence"].value == pytest.approx(0.5)
    # Every one of these is an opinion, and the stored row must say so.
    for score in scores:
        if score.name != "governance_read_coverage":
            assert get_definition(score.name).provenance == JUDGEMENT


def test_a_run_with_no_audit_produces_no_behavioural_scores():
    assert HarnessBehaviorEvaluator().evaluate(EvaluationContext(run_id="r1")) == []


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------


def test_a_profile_can_exclude_judgement_entirely():
    context = EvaluationContext(
        run_id="r1",
        validation={"objective": {"passed": True, "checks": [{"passed": True}]}},
        audit_findings={"items": [{"name": "a", "verdict": "útil"}]},
    )
    names = {score.name for score in run_profile("no_judgement", context)}
    assert "task_validation_pass" in names
    assert "skill_adherence" not in names


def test_an_unknown_profile_is_refused():
    with pytest.raises(KeyError, match="Unknown evaluation profile"):
        run_profile("made-up", EvaluationContext(run_id="r1"))


def test_trajectory_metrics_ride_in_the_default_profile():
    """§12 completed: DeepEval is part of ordinary scoring, not a special mode.

    Safe because the evaluator skips gracefully — a lab without the library,
    a judge, or a recorded trajectory scores exactly as it did before.
    """
    from harness_lab_app.evaluation import PROFILES

    assert "deepeval" in PROFILES["default"]
    # And a bare context still evaluates without raising.
    run_profile("default", EvaluationContext(run_id="r1"))


def test_the_judges_level_becomes_a_score_row():
    """The judge's number never reached the experiment page (ADR-0034 gap).

    Recorded as JUDGEMENT provenance so it shows in the deltas — where a 10 vs
    2 split on identical evidence is finally visible — and never as success.
    """
    from harness_lab_app.evaluation import EvaluationContext, RubricJudgeEvaluator
    from harness_lab_app.scores import JUDGEMENT, get_definition

    context = EvaluationContext(
        run_id="r",
        validation={
            "objective": {},
            "cost": {},
            "effort": {},
            "subjective": {
                "functional_score": 9,
                "score_source": "llm_judge",
                "notes": "level 9",
            },
        },
    )

    [score] = RubricJudgeEvaluator().evaluate(context)

    assert score.name == "rubric_score"
    assert score.value == 9.0
    assert get_definition("rubric_score").provenance == JUDGEMENT


def test_a_human_score_is_not_filed_as_the_judges():
    from harness_lab_app.evaluation import EvaluationContext, RubricJudgeEvaluator

    context = EvaluationContext(
        run_id="r",
        validation={
            "subjective": {"functional_score": 7, "score_source": "manual"},
        },
    )

    assert RubricJudgeEvaluator().evaluate(context) == []


def test_efficiency_records_a_zero_cost_and_the_generated_tokens():
    """`if value is not None`, never truthiness: $0 is a measurement."""
    scores = EfficiencyEvaluator().evaluate(
        EvaluationContext(
            run_id="r1",
            validation={
                "cost": {
                    "total_tokens": 1300,
                    "output_tokens": 300,
                    "cost_usd": 0.0,
                    "time_minutes": 12.5,
                    "model_minutes": 9.0,
                },
            },
        )
    )
    by_name = {score.name: score.value for score in scores}
    assert by_name["cost_usd"] == 0.0
    assert by_name["output_tokens"] == 300.0
    assert by_name["wall_time_minutes"] == 12.5
    assert by_name["model_minutes"] == 9.0
