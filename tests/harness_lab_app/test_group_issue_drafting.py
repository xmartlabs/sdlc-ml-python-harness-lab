"""Multi-proposal group synthesis (drafting), with a fake gateway."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness_lab_app.harness_lifecycle import drafting, metrics, sanitizer

FIXED = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)

_DRAFT = {
    "title": "Add a retry budget to systematic debugging",
    "observation": {
        "failure_mode": "Agents re-read the same file and churned hypotheses.",
        "expected_behavior": "Reset the hypothesis after a retry budget.",
        "evidence_summary": "Seen across the debugging steps of several runs.",
    },
    "affected_artifacts": [".github/skills/systematic_debugging.md"],
    "suggested_change": "Add an explicit retry budget and hypothesis reset rule.",
    "suggested_cases": ["systematic-debugging-v1"],
    "success_criteria": [
        {"metric": "repeated_command_max", "operator": "lte", "value": 2}
    ],
    "confidence": "high",
}


def _run_dir(root: Path, run_id: str, condition_hash: str) -> Path:
    d = root / "data" / "runs" / run_id
    d.mkdir(parents=True)
    # Private evidence lives in the audit; it must never reach the public issue.
    (d / "audit.md").write_text(
        f"# Audit for {run_id}\nRe-read /Users/marcos/x every step.\n",
        encoding="utf-8",
    )
    (d / "metadata.json").write_text(
        json.dumps({"run_id": run_id, "condition_hash": condition_hash}),
        encoding="utf-8",
    )
    return d


def _items() -> list[dict]:
    # Unsanitized snapshots straight from improvements.json: run ids and local
    # paths in the bodies are the normal case, not the exception.
    return [
        {
            "run_id": "dbg-K-001",
            "title": "Add retry budget",
            "body": "In run dbg-K-001 the agent re-read /Users/marcos/x repeatedly.",
            "kind": "skill",
            "target": "systematic_debugging",
            "case": "debugging-v1",
            "harness": "claude",
            "executor_model": "stub-executor",
        },
        {
            "run_id": "dbg-K-002",
            "title": "Reset hypotheses",
            "body": "Run dbg-K-002 showed hypothesis churn.",
            "kind": "process",
            "target": "debug loop",
            "case": "debugging-v2",
            "harness": "opencode",
            "executor_model": "stub-executor",
        },
    ]


def test_group_system_prompt_constrains_metrics_to_the_catalog() -> None:
    for name in metrics.MEASURABLE_METRICS:
        assert name in drafting.GROUP_SYSTEM_PROMPT


def test_harness_target_surface_single_harness_hits_its_adapter() -> None:
    # Every proposal ran under claude → the fix belongs in claude's adapter.
    scope = drafting.harness_target_surface(
        [{"harness": "claude", "executor_model": "claude-opus-4-8"}]
    )
    assert scope["harnesses"] == ["claude"]
    assert "CLAUDE.md" in scope["surface"]
    assert scope["self_hosted"] is False
    assert scope["note"] == ""


def test_harness_target_surface_mixed_harnesses_go_to_shared_layer() -> None:
    # A gap seen under two harnesses generalizes → the governed shared layer.
    scope = drafting.harness_target_surface(
        [
            {"harness": "claude", "executor_model": "claude-opus-4-8"},
            {"harness": "opencode", "executor_model": "gateway/lmstudio-qwen"},
        ]
    )
    assert scope["harnesses"] == ["claude", "opencode"]
    assert ".github/" in scope["surface"]
    # A self-hosted executor anywhere in the selection raises the advisory note.
    assert scope["self_hosted"] is True
    assert "self-hosted" in scope["note"].lower()


def test_harness_target_surface_no_harness_defaults_to_shared() -> None:
    scope = drafting.harness_target_surface([{"executor_model": "x"}])
    assert scope["harnesses"] == []
    assert ".github/" in scope["surface"]


def test_build_group_user_message_states_the_target_surface() -> None:
    # Single-harness items → the directive names that harness's adapter so the
    # model's affected_artifacts land on the right files.
    message = drafting.build_group_user_message(
        [{"run_id": "r", "title": "T", "body": "b", "harness": "claude"}], {}
    )
    assert "Target surface" in message
    assert "CLAUDE.md" in message


def test_build_group_user_message_includes_proposals_and_audits() -> None:
    message = drafting.build_group_user_message(
        _items(), {"dbg-K-001": "audit one text", "dbg-K-002": "audit two text"}
    )
    assert "Add retry budget" in message
    assert "Reset hypotheses" in message
    assert "hypothesis churn" in message
    assert "audit one text" in message
    assert "audit two text" in message
    assert len(message) <= 40000


def test_build_group_user_message_masks_run_ids_before_the_llm() -> None:
    # Run ids ride into the prompt via proposal bodies and audit excerpts. They
    # must be purged there — the model cannot echo what it never sees, so the
    # output backstop stops being a routine blocker.
    message = drafting.build_group_user_message(
        _items(), {"dbg-K-001": "audit text mentioning dbg-K-001 inline"}
    )
    assert "dbg-K-001" not in message
    assert "dbg-K-002" not in message
    # Replaced by stable pseudonyms, and the audit stays tied to its run.
    assert "run-1" in message
    assert "Audit excerpt (run-1)" in message
    # Surrounding signal survives the masking.
    assert "hypothesis churn" in message


def test_build_group_user_message_clips_long_bodies() -> None:
    items = [{"run_id": "r", "title": "T", "body": "x" * 20000}]
    message = drafting.build_group_user_message(items, {})
    assert len(message) < 20000


def test_synthesize_group_issue_is_sanitized(tmp_path: Path) -> None:
    run_dirs = [
        _run_dir(tmp_path, "dbg-K-001", "sha256:abc"),
        _run_dir(tmp_path, "dbg-K-002", "sha256:def"),
    ]
    markdown, form = drafting.synthesize_group_issue(
        _items(),
        run_dirs,
        repository="marcosdh1987/ml-python-base",
        created_at=FIXED,
        gateway=lambda *_args: json.dumps(_DRAFT),
    )
    # Allowlisted content and multi-run evidence are present...
    assert "Reset the hypothesis after a retry budget." in markdown
    assert "sha256:abc" in markdown
    assert "sha256:def" in markdown
    source_runs = next(f.value for f in form.fields if f.id == "source_runs")
    assert "x2 runs" in source_runs
    # ...and private evidence (run ids / local paths) is not surfaced.
    for haystack in (markdown, *[f.value for f in form.fields]):
        assert "dbg-K-001" not in haystack
        assert "dbg-K-002" not in haystack
        assert "/Users/marcos" not in haystack


def test_synthesize_group_issue_prepends_target_surface(tmp_path: Path) -> None:
    # _items() mixes claude + opencode → the issue must name the shared layer,
    # deterministically, regardless of what the LLM returned.
    run_dirs = [
        _run_dir(tmp_path, "dbg-K-001", "sha256:abc"),
        _run_dir(tmp_path, "dbg-K-002", "sha256:def"),
    ]
    markdown, _form = drafting.synthesize_group_issue(
        _items(),
        run_dirs,
        repository="marcosdh1987/ml-python-base",
        created_at=FIXED,
        gateway=lambda *_args: json.dumps(_DRAFT),
    )
    assert "Target harness surface" in markdown
    assert ".github/" in markdown
    assert "claude" in markdown and "opencode" in markdown
    # The deterministic scope block carries no private run ids.
    header = markdown.split("Reset the hypothesis", 1)[0]
    assert "dbg-K-001" not in header


def test_synthesize_group_issue_backstop_rejects_echoed_run_id(tmp_path: Path) -> None:
    run_dirs = [_run_dir(tmp_path, "dbg-K-001", "sha256:abc")]
    leaky = dict(_DRAFT, suggested_change="Fix the loop seen in dbg-K-001.")
    with pytest.raises(sanitizer.SanitizationError, match="run id"):
        drafting.synthesize_group_issue(
            _items()[:1],
            run_dirs,
            repository="marcosdh1987/ml-python-base",
            created_at=FIXED,
            gateway=lambda *_args: json.dumps(leaky),
        )


def test_synthesize_group_issue_skips_runs_without_metadata(tmp_path: Path) -> None:
    complete = _run_dir(tmp_path, "dbg-K-001", "sha256:abc")
    bare = tmp_path / "data" / "runs" / "dbg-K-003"
    bare.mkdir(parents=True)
    markdown, _form = drafting.synthesize_group_issue(
        _items(),
        [complete, bare],
        repository="marcosdh1987/ml-python-base",
        created_at=FIXED,
        gateway=lambda *_args: json.dumps(_DRAFT),
    )
    assert "sha256:abc" in markdown
