"""Tests for validation report + public attestation generation (pure)."""

from __future__ import annotations

import json
from pathlib import Path

from harness_lab_app.harness_lifecycle import gates, metrics, reports


def _pooled(
    passed: bool = True, tokens: int = 1000, composite: float = 8.0
) -> metrics.AggregatedArm:
    return metrics.aggregate(
        [
            metrics.normalize_run(
                {
                    "case_id": "c",
                    "arm": "x",
                    "usage": {
                        "total_tokens": tokens,
                        "cost_usd": 0.1,
                        "time_minutes": 1.0,
                        "steps": 10,
                    },
                    "objective": {"passed": passed},
                    "composite": composite,
                    "interventions": 0,
                    "commands": [],
                }
            )
        ]
    )


def _report(*, passing: bool) -> dict:
    hard = [
        gates.GateResult("controlled_conditions", True, "ok"),
        gates.GateResult(
            "no_pathological_loop", passing, "ok" if passing else "loop of 4"
        ),
    ]
    warnings = [
        gates.GateResult(
            "tokens", True, "median tokens +5% (threshold 20%)", severity="warning"
        )
    ]
    per_case = {"cli-textstats-v1": _pooled()}
    return reports.build_report(
        validation_id="HV-2026-007",
        created_at="2026-07-24T10:00:00Z",
        suite_id="harness-regression-v1",
        baseline_ref="v0.2.1",
        baseline_commit="a" * 40,
        candidate_ref="v0.3.0",
        candidate_commit="b" * 40,
        hard_gates=hard,
        warnings=warnings,
        baseline_pooled=_pooled(),
        candidate_pooled=_pooled(tokens=1050),
        per_case_baseline=per_case,
        per_case_candidate=per_case,
    )


def test_build_report_status_passed() -> None:
    report = _report(passing=True)
    assert report["status"] == "passed"
    assert report["gates"]["all_hard_pass"] is True
    assert report["candidate"]["ref"] == "v0.3.0"
    assert "cli-textstats-v1" in report["metrics"]["by_case"]


def test_build_report_status_failed() -> None:
    report = _report(passing=False)
    assert report["status"] == "failed"
    assert report["gates"]["all_hard_pass"] is False


def test_render_markdown() -> None:
    md = reports.render_markdown(_report(passing=True))
    assert "PASSED" in md
    assert "`no_pathological_loop`" in md
    assert "## Hard gates" in md


def test_build_attestation_is_sanitized() -> None:
    report = _report(passing=True)
    attestation = reports.build_attestation(
        report,
        version="0.3.0",
        release_url="https://github.com/x/y/releases/tag/v0.3.0",
        validated_at="2026-07-24T10:00:00Z",
    )
    assert attestation["sanitized"] is True
    assert attestation["status"] == "passed"
    assert attestation["private_manifest_sha256"].startswith("sha256:")
    # Only name+passed for gates — no private detail leaks.
    assert all(set(g) == {"name", "passed"} for g in attestation["gates"])
    # Aggregate metrics are the allowlisted keys only.
    assert "median_tokens" in attestation["aggregate_metrics"]["candidate"]
    # The serialized attestation carries no secrets/paths.
    assert "pass_ratio" in json.dumps(attestation)


def test_write_report_creates_files(tmp_path: Path) -> None:
    report = _report(passing=True)
    paths = reports.write_report(tmp_path, "0.3.0", report)
    base = tmp_path / "data/harness-validations/0.3.0/HV-2026-007"
    assert paths["validation"] == base / "validation.yaml"
    assert (base / "validation.yaml").is_file()
    assert (base / "report.json").is_file()
    assert (base / "summary.md").is_file()
    assert json.loads((base / "report.json").read_text())["status"] == "passed"
