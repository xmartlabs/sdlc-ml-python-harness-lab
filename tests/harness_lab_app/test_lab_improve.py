from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lab_improve  # noqa: E402

GOOD_OUTCOME = {
    "score": 9,
    "interventions": 0,
    "composite": 9.0,
    "objective_passed": True,
    "steps": 5,
}


def _findings(*verdicts: str) -> list[dict]:
    return [
        {"name": f"item-{i}", "kind": "skill", "verdict": verdict, "note": "n"}
        for i, verdict in enumerate(verdicts)
    ]


def _run(findings: list[dict]) -> dict:
    return {
        "run_id": "case-K-001",
        "case": "case",
        "harness": "claude",
        "executor_model": "m",
        "prompt_variant": "sdlc",
        "audit_md": "# Audit\nAll good.",
        "findings": findings,
    }


def test_route_repo_skills_and_governance_go_to_template():
    assert lab_improve.route_repo("skill", "test-driven-development", "") == (
        lab_improve.TEMPLATE_REPO
    )
    assert (
        lab_improve.route_repo("subagent", "reviewer", "") == lab_improve.TEMPLATE_REPO
    )
    assert (
        lab_improve.route_repo("doc", "standards.md", "") == lab_improve.TEMPLATE_REPO
    )


def test_route_repo_process_and_harness_hint_go_to_harness_repo():
    assert (
        lab_improve.route_repo("process", "sdlc-loop", "") == lab_improve.HARNESS_REPO
    )
    assert lab_improve.route_repo("doc", "anything", "harness") == (
        lab_improve.HARNESS_REPO
    )
    # A non-governance doc whose name references the harness routes to the guide.
    assert lab_improve.route_repo("doc", "harness-telemetry", "") == (
        lab_improve.HARNESS_REPO
    )


def test_parse_proposals_strips_fences_and_prose():
    text = 'here:\n```json\n[{"title":"Fix X","kind":"doc","body":"do Y"}]\n```\n'
    assert lab_improve._parse_proposals(text) == [
        {"title": "Fix X", "kind": "doc", "body": "do Y"}
    ]


def test_parse_proposals_drops_incomplete_and_non_list():
    # Missing title/body entries are dropped; non-array returns [].
    assert lab_improve._parse_proposals('[{"title":"only title"}]') == []
    assert lab_improve._parse_proposals("not json at all") == []
    assert lab_improve._parse_proposals('{"title":"x","body":"y"}') == []


def test_load_run_requires_an_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_improve, "DATA_DIR", tmp_path)
    (tmp_path / "run-1").mkdir()
    (tmp_path / "run-1" / "metadata.json").write_text("{}")
    with pytest.raises(SystemExit, match="No audit"):
        lab_improve.load_run("run-1")


# -- gate_reason --------------------------------------------------------------


def test_gate_passes_for_clean_scored_run():
    reason = lab_improve.gate_reason(GOOD_OUTCOME, improve_count=0, min_score=8)
    assert reason is not None
    assert "met expectations" in reason


def test_gate_uses_composite_when_manual_score_missing():
    outcome = dict(GOOD_OUTCOME, score=None, composite=8.5)
    assert lab_improve.gate_reason(outcome, 0, min_score=8) is not None


@pytest.mark.parametrize(
    "override",
    [
        {"score": 7.9, "composite": None},  # below threshold
        {"objective_passed": False},
        {"objective_passed": None},  # unscored/unknown never gates
        {"interventions": 1},
        {"score": None, "composite": None},
    ],
)
def test_gate_refuses_unconfirmed_or_below_threshold(override):
    outcome = dict(GOOD_OUTCOME, **override)
    assert lab_improve.gate_reason(outcome, 0, min_score=8) is None


def test_gate_refuses_when_improve_findings_exist():
    assert lab_improve.gate_reason(GOOD_OUTCOME, improve_count=1, min_score=8) is None


def test_gate_refuses_without_outcome():
    assert lab_improve.gate_reason(None, 0, min_score=8) is None


# -- build_user_message -------------------------------------------------------


def test_user_message_says_zero_improve_instead_of_dumping_all_findings():
    # The old fallback dumped every redundant/unknown item as "needing
    # improvement" — the direct cause of proposals from clean runs.
    message = lab_improve.build_user_message(
        _run(_findings("redundant", "unknown", "useful")), GOOD_OUTCOME
    )
    assert "0 of the audited items were judged 'improve'" in message
    assert "item-0" not in message
    assert "item-1" not in message


def test_user_message_lists_only_improve_findings():
    message = lab_improve.build_user_message(
        _run(_findings("redundant", "improve")), None
    )
    assert "item-1" in message
    assert "item-0" not in message
    assert "(not scored/validated yet)" in message


def test_user_message_includes_measured_outcome():
    message = lab_improve.build_user_message(_run([]), GOOD_OUTCOME)
    assert "objective passed: True" in message
    assert "manual score (0-10): 9" in message
    assert "human interventions: 0" in message


# -- strictness ---------------------------------------------------------------


def test_system_prompt_reflects_strictness_levels():
    for level, config in lab_improve.STRICTNESS_LEVELS.items():
        prompt = lab_improve.build_system_prompt(level)
        assert f"at most {config['max_proposals']}" in prompt
        assert "never as gaps" in prompt  # unknown-neutral rule
        assert "observable" in prompt  # anti-observability-bias rule


def test_synthesize_caps_proposals_at_strictness_max(monkeypatch):
    many = [
        {"title": f"P{i}", "body": "b", "kind": "skill", "target": "t"}
        for i in range(6)
    ]
    monkeypatch.setattr(lab_improve, "call_gateway", lambda *_args: json.dumps(many))
    proposals = lab_improve.synthesize(_run([]), "model", strictness="strict")
    assert len(proposals) == 2


# -- generate: gate wiring ----------------------------------------------------


def _write_run_dir(tmp_path: Path, run_id: str, verdicts: list[str]) -> None:
    d = tmp_path / run_id
    d.mkdir(parents=True)
    (d / "audit.md").write_text("# Audit\nFine.")
    (d / "audit_findings.json").write_text(json.dumps({"items": _findings(*verdicts)}))
    (d / "metadata.json").write_text(
        json.dumps({"run_id": run_id, "case": "case", "harness": "claude"})
    )


def test_generate_skips_synthesis_when_gated(tmp_path, monkeypatch):
    _write_run_dir(tmp_path, "case-K-001", ["redundant", "useful"])
    monkeypatch.setattr(lab_improve, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lab_improve, "load_outcome", lambda _run_id: GOOD_OUTCOME)

    def _no_llm(*args):
        raise AssertionError("gateway must not be called when the gate passes")

    monkeypatch.setattr(lab_improve, "call_gateway", _no_llm)
    path = lab_improve.generate("case-K-001", "model")
    doc = json.loads(path.read_text())
    assert doc["proposals"] == []
    assert doc["gate"]["skipped"] is True
    assert "met expectations" in doc["gate"]["reason"]


def test_generate_force_bypasses_gate(tmp_path, monkeypatch):
    _write_run_dir(tmp_path, "case-K-001", ["redundant"])
    monkeypatch.setattr(lab_improve, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lab_improve, "load_outcome", lambda _run_id: GOOD_OUTCOME)
    monkeypatch.setattr(lab_improve, "call_gateway", lambda *_args: "[]")
    path = lab_improve.generate("case-K-001", "model", force=True)
    doc = json.loads(path.read_text())
    assert doc["gate"]["skipped"] is False
    assert doc["strictness"] == "balanced"


def test_generate_synthesizes_when_unscored(tmp_path, monkeypatch):
    # No outcome (awaiting manual score) → never gate; synthesis proceeds.
    _write_run_dir(tmp_path, "case-K-001", ["improve"])
    monkeypatch.setattr(lab_improve, "DATA_DIR", tmp_path)
    monkeypatch.setattr(lab_improve, "load_outcome", lambda _run_id: None)
    monkeypatch.setattr(
        lab_improve,
        "call_gateway",
        lambda *_args: json.dumps([{"title": "T", "body": "b", "kind": "skill"}]),
    )
    doc = json.loads(lab_improve.generate("case-K-001", "model").read_text())
    assert len(doc["proposals"]) == 1
    assert doc["gate"]["skipped"] is False
