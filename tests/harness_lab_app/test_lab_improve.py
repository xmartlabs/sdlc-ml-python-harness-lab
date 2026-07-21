from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lab_improve  # noqa: E402


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
