"""The LLM reading of an ad-hoc comparison (ADR-0060).

Compare is a selection a person made in the browser, not a saved object, so
these tests pin the one thing that is genuinely new: the key that gives that
selection an identity. The rest mirrors the experiment reading — the evidence
document, the fixed sections, the "every run has stopped" gate, the failure
path — never the model's words.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from harness_lab_app.compare_audit_service import (
    COMPARE_AUDIT_SYSTEM_PROMPT,
    MAX_RUNS,
    MIN_RUNS,
    CompareAuditService,
    build_evidence,
    comparison_key,
    render_audit_prompt,
    runs_finished,
)
from harness_lab_app.models import (
    Attribution,
    HarnessSource,
    Run,
    RunConfig,
    RunState,
    Score,
    Validation,
)
from harness_lab_app.tasks import _audit_comparison_body


def _make_run(
    session,
    run_id: str,
    *,
    prompt_variant: str = "default",
    state: RunState = RunState.completed,
    harness_source: HarnessSource = HarnessSource.lab_root,
    skills_invoke: list[str] | None = None,
) -> Run:
    config = RunConfig(
        case_id="cli-textstats-v1",
        prompt_variant=prompt_variant,
        executor_model="stub-executor",
        planner_model="stub-planner",
        max_steps=10,
        harness_source=harness_source,
        skills_invoke=list(skills_invoke or []),
    )
    session.add(config)
    session.commit()
    session.refresh(config)
    run = Run(run_id=run_id, run_config_id=config.id or 0, state=state)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _measure(
    session,
    run_id: str,
    *,
    passed: bool = True,
    cost: float | None = 0.4,
    docs_read=(),
    skills=(),
) -> None:
    session.add(
        Score(run_id=run_id, name="task_validation_pass", version=1, value_bool=passed)
    )
    if cost is not None:
        session.add(Score(run_id=run_id, name="cost_usd", version=1, value_num=cost))
    session.add(
        Validation(
            run_id=run_id,
            objective={
                "passed": passed,
                "summary": "3/3 checks passed" if passed else "2/3 checks passed",
                "checks": [
                    {
                        "command": "pytest -q",
                        "passed": passed,
                        "exit_code": 0 if passed else 1,
                        "detail": "boom",
                    }
                ],
            },
            subjective={
                "functional_score": 10 if passed else 6,
                "score_source": "llm_judge",
                "notes": "reads well",
            },
            effort={"stop_reason": "completion_signal"},
        )
    )
    session.add(
        Attribution(
            run_id=run_id,
            harness_usage={
                "governance_docs_available": ["CLAUDE.md", ".github/standards.md"],
                "governance_docs_read": list(docs_read),
                "skills_available": ["a-skill", "other-skill"],
                "skills_consulted": list(skills),
                "governance_source": "lab_root",
            },
        )
    )
    session.commit()


def _pair(session):
    baseline = _make_run(session, "K-002", prompt_variant="default")
    treatment = _make_run(
        session, "K-004", prompt_variant="harness", skills_invoke=["a-skill"]
    )
    _measure(session, "K-002", passed=True, cost=0.4)
    _measure(
        session,
        "K-004",
        passed=True,
        cost=1.2,
        docs_read=["CLAUDE.md"],
        skills=["a-skill"],
    )
    return baseline, treatment


# --------------------------------------------------------------------------
# The key — the identity an ad-hoc selection does not otherwise have
# --------------------------------------------------------------------------


def test_the_key_ignores_the_order_the_columns_were_added_in():
    assert comparison_key(["b", "a"]) == comparison_key(["a", "b"])
    assert comparison_key(["a", "a", "b"]) == comparison_key(["a", "b"])
    assert comparison_key(["a", "b"]) != comparison_key(["a", "c"])


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_the_gate_waits_for_the_last_run(session):
    _make_run(session, "K-002")
    running = _make_run(session, "K-004", state=RunState.running)

    assert runs_finished(session, ["K-002", "K-004"]) == (False, 1, 2)
    running.state = RunState.awaiting_manual_score
    session.add(running)
    session.commit()
    # A run awaiting its manual score has stopped: it has a validation and a
    # cost record, and the table already renders it.
    assert runs_finished(session, ["K-002", "K-004"]) == (True, 2, 2)


def _endpoint(method: str):
    from harness_lab_app.api import create_app

    app = create_app()
    return next(
        route.endpoint
        for route in app.router.routes
        if getattr(route, "path", None) == "/api/compare/audit"
        and method in getattr(route, "methods", set())
    )


def test_the_endpoint_refuses_a_selection_it_cannot_read(session):
    _pair(session)
    post = _endpoint("POST")

    with pytest.raises(HTTPException) as single:
        post(["K-002"], None, session)
    assert single.value.status_code == 400
    assert f"{MIN_RUNS} to {MAX_RUNS}" in str(single.value.detail)

    with pytest.raises(HTTPException) as itself:
        post(["K-002", "K-002"], None, session)
    assert itself.value.status_code == 400

    with pytest.raises(HTTPException) as ghost:
        post(["K-002", "nope"], None, session)
    assert ghost.value.status_code == 404


def test_the_endpoint_refuses_a_run_that_has_not_stopped(session):
    _pair(session)
    _make_run(session, "K-009", state=RunState.running)

    with pytest.raises(HTTPException) as exc:
        _endpoint("POST")(["K-002", "K-009"], None, session)

    assert exc.value.status_code == 409
    assert "1 of 2" in str(exc.value.detail)


def test_an_unread_comparison_is_an_empty_box_not_an_error(session):
    _pair(session)

    with pytest.raises(HTTPException) as exc:
        _endpoint("GET")(["K-002", "K-004"], session)

    assert exc.value.status_code == 404


# --------------------------------------------------------------------------
# The evidence
# --------------------------------------------------------------------------


def test_the_evidence_names_what_was_held_and_what_varied(session):
    _pair(session)

    evidence = build_evidence(session, ["K-002", "K-004"])

    assert evidence["run_ids"] == ["K-002", "K-004"]
    assert evidence["baseline_run_id"] == "K-002"
    # The table shows both columns but never says which of them is the variable.
    assert evidence["held_constant"]["case_id"] == "cli-textstats-v1"
    assert evidence["held_constant"]["executor_model"] == "stub-executor"
    assert "prompt_variant" in evidence["differed"]
    assert "skills_invoke" in evidence["differed"]
    assert "case_id" not in evidence["differed"]

    rows = {row["run_id"]: row for row in evidence["runs"]}
    assert rows["K-004"]["usage"]["governance_docs_not_read"] == [
        ".github/standards.md"
    ]
    assert rows["K-004"]["scores"]["cost_usd"] == 1.2
    assert rows["K-002"]["judge"]["source"] == "llm_judge"
    assert rows["K-002"]["objective"] == "3/3 checks passed"


def test_an_inconclusive_check_is_not_listed_as_a_failure(session):
    """Failed and did-not-measure are two different claims (ADR-0062)."""
    from harness_lab_app.models import Validation

    _make_run(session, "K-002", prompt_variant="default")
    _make_run(session, "K-004", prompt_variant="harness")
    _measure(session, "K-002")
    session.add(
        Validation(
            run_id="K-004",
            objective={
                "passed": None,
                "summary": "0/1 checks passed; 1 inconclusive (no network)",
                "checks": [
                    {
                        "command": "uv run pytest -x -q",
                        "passed": False,
                        "exit_code": 2,
                        "conclusive": False,
                        "inconclusive_reason": "no_network",
                        "detail": "dns error",
                    }
                ],
            },
        )
    )
    session.commit()

    evidence = build_evidence(session, ["K-002", "K-004"])

    row = next(r for r in evidence["runs"] if r["run_id"] == "K-004")
    assert row["failed_checks"] == []
    assert row["inconclusive_checks"][0]["reason"] == "no_network"


def test_an_unmeasured_metric_is_flagged_not_read_as_a_zero(session):
    _make_run(session, "K-002", prompt_variant="default")
    _make_run(session, "K-004", prompt_variant="harness")
    _measure(session, "K-002", cost=0.4)
    _measure(session, "K-004", cost=None)  # the page renders this as an em dash

    evidence = build_evidence(session, ["K-002", "K-004"])

    cost = next(d for d in evidence["deltas"] if d["metric"] == "cost_usd")
    assert cost["measured"] is False
    assert cost["change"] is None
    assert cost["baseline"] == 0.4
    assert cost["value"] is None


def test_a_measured_delta_carries_its_direction(session):
    _pair(session)

    evidence = build_evidence(session, ["K-002", "K-004"])

    cost = next(d for d in evidence["deltas"] if d["metric"] == "cost_usd")
    assert cost["measured"] is True
    assert cost["change"] == pytest.approx(0.8)
    assert cost["direction"] == "lower_is_better"
    assert cost["baseline_run_id"] == "K-002"


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_the_prompt_demands_the_fixed_sections():
    for section in (
        "## Verdict",
        "## What was compared",
        "## What the numbers say",
        "## What differed between these runs",
        "## Why this is not a causal claim",
        "## What to do next",
    ):
        assert section in COMPARE_AUDIT_SYSTEM_PROMPT
    assert "significant" in COMPARE_AUDIT_SYSTEM_PROMPT  # named so it is banned
    assert "NOT MEASURED" in COMPARE_AUDIT_SYSTEM_PROMPT
    user = render_audit_prompt({"comparison_key": "abc", "runs": []})
    assert '"comparison_key": "abc"' in user


def test_the_reading_is_written_in_english(session):
    """This auditor renders inside the UI, and the lab's UI copy is English.

    The three older auditors write Spanish by a convention that predates the
    fork; this guard stops that convention leaking back in here by hand.
    """
    assert COMPARE_AUDIT_SYSTEM_PROMPT.isascii()


# --------------------------------------------------------------------------
# Storage and failure
# --------------------------------------------------------------------------


def test_the_reading_is_stored_with_its_evidence(session):
    _pair(session)
    seen: dict = {}

    def gateway(model: str, system: str, user: str) -> str:
        seen.update(model=model, system=system, user=user)
        return "## Verdict\nNo measured difference.\n"

    _audit_comparison_body(session, ["K-002", "K-004"], "judge-x", gateway=gateway)

    audit = CompareAuditService(session).get(comparison_key(["K-002", "K-004"]))
    assert audit is not None
    assert audit.status == "done"
    assert audit.auditor_model == "judge-x"
    assert audit.run_ids == ["K-002", "K-004"]
    assert audit.markdown.startswith("## Verdict")
    assert audit.evidence_json["run_ids"] == ["K-002", "K-004"]
    assert seen["system"] == COMPARE_AUDIT_SYSTEM_PROMPT


def test_asking_again_overwrites_the_same_row(session):
    _pair(session)
    answers = iter(["## Verdict\nFirst.\n", "## Verdict\nSecond.\n"])

    def gateway(*_args) -> str:
        return next(answers)

    _audit_comparison_body(session, ["K-002", "K-004"], "judge-x", gateway=gateway)
    first = CompareAuditService(session).get(comparison_key(["K-002", "K-004"]))
    assert first is not None
    created = first.created_at

    # The same selection, picked in the other order: one reading, not two.
    _audit_comparison_body(session, ["K-004", "K-002"], "judge-x", gateway=gateway)

    service = CompareAuditService(session)
    again = service.get(comparison_key(["K-002", "K-004"]))
    assert again is not None
    assert again.id == first.id
    assert again.created_at == created
    assert again.markdown.strip().endswith("Second.")


def test_a_failed_reading_is_recorded_not_raised(session):
    _pair(session)

    def gateway(*_args) -> str:
        raise RuntimeError("gateway down")

    _audit_comparison_body(session, ["K-002", "K-004"], "judge-x", gateway=gateway)

    audit = CompareAuditService(session).get(comparison_key(["K-002", "K-004"]))
    assert audit is not None
    assert audit.status == "failed"
    assert "gateway down" in (audit.error or "")


def test_an_empty_answer_is_a_failure_not_a_blank_reading(session):
    _pair(session)

    _audit_comparison_body(
        session, ["K-002", "K-004"], "judge-x", gateway=lambda *_a: "   "
    )

    audit = CompareAuditService(session).get(comparison_key(["K-002", "K-004"]))
    assert audit is not None
    assert audit.status == "failed"
    assert "empty reading" in (audit.error or "")


def test_a_transport_error_is_retried_and_the_reading_still_lands(session):
    from harness_lab_app.llm_transport import TransportError

    _pair(session)
    attempts: list[int] = []

    def gateway(*_args) -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise TransportError("connection reset")
        return "## Verdict\nNo measured difference.\n"

    _audit_comparison_body(
        session,
        ["K-002", "K-004"],
        "judge-x",
        gateway=gateway,
        sleep=lambda _s: None,
    )

    audit = CompareAuditService(session).get(comparison_key(["K-002", "K-004"]))
    assert audit is not None
    assert audit.status == "done"
    assert len(attempts) == 3


def test_a_bad_request_is_not_asked_three_times(session):
    from harness_lab_app.llm_transport import GatewayHTTPError

    _pair(session)
    attempts: list[int] = []

    def gateway(*_args) -> str:
        attempts.append(1)
        raise GatewayHTTPError(400, "unknown model")

    _audit_comparison_body(
        session,
        ["K-002", "K-004"],
        "judge-x",
        gateway=gateway,
        sleep=lambda _s: None,
    )

    audit = CompareAuditService(session).get(comparison_key(["K-002", "K-004"]))
    assert audit is not None
    assert audit.status == "failed"
    assert len(attempts) == 1
