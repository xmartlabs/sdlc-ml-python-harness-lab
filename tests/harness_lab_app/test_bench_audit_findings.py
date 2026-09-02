"""Tests for the structured findings pass of scripts/bench_audit.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_audit  # noqa: E402


def test_harness_from_metadata():
    assert bench_audit.harness_from_metadata('{"harness": "claude"}') == "claude"
    assert bench_audit.harness_from_metadata('{"harness": ""}') == ""
    assert bench_audit.harness_from_metadata(None) == ""
    assert bench_audit.harness_from_metadata("not json") == ""


def test_detect_harness_falls_back_to_log_header():
    # Metadata missing the harness (older runs) → read the run-log header.
    assert bench_audit.harness_from_log("# claude run started 2026\nx") == "claude"
    assert bench_audit.detect_harness("{}", "# codex run started\n") == "codex"
    # Metadata wins when present.
    assert bench_audit.detect_harness('{"harness":"opencode"}', "# claude run") == (
        "opencode"
    )
    assert bench_audit.detect_harness(None, None) == ""


def test_harness_preamble_is_harness_aware():
    claude = bench_audit.harness_preamble("claude")
    # Names the right governance source and forgives summary-only logs.
    assert "CLAUDE.md" in claude
    assert "NO OPENCODE.md" in claude
    assert "LIMITACIÓN" in claude  # missing tool traces are not agent failure
    opencode = bench_audit.harness_preamble("opencode")
    assert "OPENCODE.md" in opencode
    assert "POR-TOOL" in opencode
    # Unknown harness still gets a safe, non-opencode-specific preamble.
    other = bench_audit.harness_preamble("weird")
    assert "weird" in other


def _item(**overrides) -> dict:
    base = {
        "name": "verify_changes",
        "kind": "skill",
        "used_evidence": "make check en el log",
        "verdict": "useful",
        "note": "Cerró con el gate verde",
    }
    return {**base, **overrides}


def test_parse_findings_clean_array():
    parsed = bench_audit.parse_findings(json.dumps([_item()]))
    assert parsed["items"][0]["name"] == "verify_changes"
    assert parsed["items"][0]["verdict"] == "useful"
    assert "parse_error" not in parsed


def test_parse_findings_fenced_json():
    text = "```json\n" + json.dumps([_item(verdict="redundant")]) + "\n```"
    parsed = bench_audit.parse_findings(text)
    assert parsed["items"][0]["verdict"] == "redundant"


def test_parse_findings_items_wrapper_and_prose():
    wrapped = "Acá va el veredicto:\n[" + json.dumps(_item()) + "]\nfin."
    parsed = bench_audit.parse_findings(wrapped)
    assert len(parsed["items"]) == 1
    wrapper = json.dumps({"items": [_item(kind="doc")]})
    parsed2 = bench_audit.parse_findings(wrapper)
    assert parsed2["items"][0]["kind"] == "doc"


def test_parse_findings_garbage_never_raises():
    parsed = bench_audit.parse_findings("total nonsense {{{")
    assert parsed["items"] == []
    assert "parse_error" in parsed
    parsed2 = bench_audit.parse_findings(json.dumps({"foo": "bar"}))
    assert parsed2["items"] == []
    assert "parse_error" in parsed2


def test_parse_findings_drops_invalid_and_coerces():
    raw = [
        _item(),
        {"kind": "skill"},  # no name → dropped
        "not a dict",  # dropped
        _item(name="mystery", kind="alien", verdict="amazing"),  # coerced
    ]
    parsed = bench_audit.parse_findings(json.dumps(raw))
    assert len(parsed["items"]) == 2
    coerced = parsed["items"][1]
    assert coerced["kind"] == "skill"
    assert coerced["verdict"] == "unknown"


def test_harness_catalog_from_metadata():
    metadata = json.dumps(
        {
            "skills_bundle": ["brainstorming", "verify_changes"],
            "subagents": ["planner"],
            "harness_fingerprint": {
                "governance_docs": {".github/standards.md": "abc123"},
                "hooks": {".claude/settings.json": "def456"},
            },
        }
    )
    catalog = bench_audit.harness_catalog_from_metadata(metadata)
    kinds = {(item["name"], item["kind"]) for item in catalog}
    assert ("brainstorming", "skill") in kinds
    assert ("planner", "subagent") in kinds
    assert (".github/standards.md", "doc") in kinds
    assert (".claude/settings.json", "hook") in kinds


def test_harness_catalog_tolerates_missing_or_bad_metadata():
    assert bench_audit.harness_catalog_from_metadata(None) == []
    assert bench_audit.harness_catalog_from_metadata("not json") == []
    assert bench_audit.harness_catalog_from_metadata(json.dumps([1, 2])) == []


def test_build_findings_user_message_lists_catalog():
    message = bench_audit.build_findings_user_message(
        "run-1",
        [{"name": "verify_changes", "kind": "skill"}],
        "## Uso de skills\nEl agente corrió make check.",
    )
    assert "verify_changes (skill)" in message
    assert "make check" in message


def _run_dir(tmp_path, *, metadata, workspace=None):
    out_dir = tmp_path / "runs" / "r1"
    out_dir.mkdir(parents=True)
    (out_dir / "metadata.json").write_text(json.dumps(metadata))
    (out_dir / "prompt.md").write_text("do the thing\n")
    for relative, body in (workspace or {}).items():
        path = out_dir / "workspace" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return out_dir


def test_build_inputs_judges_a_native_run_by_the_targets_rulebook(tmp_path):
    """Never evaluate the native control against rules it did not receive."""
    out_dir = _run_dir(
        tmp_path,
        metadata={"harness": "claude", "harness_source": "native"},
        workspace={"CLAUDE.md": "the target team's own rules\n"},
    )

    inputs = bench_audit.build_inputs({}, out_dir)

    assert inputs["rules"] == "the target team's own rules\n"
    assert inputs["harness_source"] == "native"
    assert inputs["governance"]["source"] == "target_workspace"
    assert inputs["governance"]["files"] == ["CLAUDE.md"]
    assert inputs["governance"]["fingerprint"]
    assert inputs["rules_warning"] == ""


def test_build_inputs_names_a_target_with_no_governance(tmp_path):
    """No rulebook is a finding about the target, not a reason to lend the lab's."""
    out_dir = _run_dir(
        tmp_path,
        metadata={"harness": "claude", "harness_source": "native"},
        workspace={"README.md": "just a readme\n"},
    )

    inputs = bench_audit.build_inputs({}, out_dir)

    assert "ships no CLAUDE.md" in inputs["rules"]
    assert inputs["governance"]["file_count"] == 0


def test_build_inputs_falls_back_to_the_lab_when_the_workspace_is_gone(tmp_path):
    out_dir = _run_dir(
        tmp_path, metadata={"harness": "claude", "harness_source": "native"}
    )

    inputs = bench_audit.build_inputs({}, out_dir)

    assert inputs["governance"]["source"] == "lab_root"
    # The verdict is degraded and the record says so.
    assert "archived" in inputs["rules_warning"]


def test_user_message_states_the_surface_it_judges_against(tmp_path):
    out_dir = _run_dir(
        tmp_path,
        metadata={"harness": "claude", "harness_source": "native"},
        workspace={"CLAUDE.md": "target rules\n"},
    )
    inputs = bench_audit.build_inputs({}, out_dir)

    message = bench_audit.build_user_message("r1", inputs)

    assert "target_workspace" in message
    assert "native" in message
    assert "CLAUDE.md" in message
