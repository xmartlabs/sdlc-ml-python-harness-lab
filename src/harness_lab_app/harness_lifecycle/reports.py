"""Validation report and public attestation generation.

Assembles the private validation record (YAML + JSON + Markdown) from the suite's
aggregated metrics and gate results, and derives the sanitized public attestation
(plan §6.4 / §6.5). Private reports live under ``data/harness-validations/``; the
attestation exposes only an allowlisted aggregate and is secret-scanned before it
can be published. Writes are atomic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from harness_lab_app.harness_lifecycle import manifests, sanitizer
from harness_lab_app.harness_lifecycle.gates import all_hard_pass

if TYPE_CHECKING:
    from pathlib import Path

    from harness_lab_app.harness_lifecycle.gates import GateResult
    from harness_lab_app.harness_lifecycle.metrics import AggregatedArm

VALIDATIONS_SUBDIR = "data/harness-validations"


def _gate_dicts(results: list[GateResult]) -> list[dict]:
    return [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in results]


def _by_case(
    per_case_baseline: dict[str, AggregatedArm],
    per_case_candidate: dict[str, AggregatedArm],
) -> dict[str, dict]:
    cases = sorted(set(per_case_baseline) | set(per_case_candidate))
    out: dict[str, dict] = {}
    for case in cases:
        entry: dict = {}
        if case in per_case_baseline:
            entry["baseline"] = asdict(per_case_baseline[case])
        if case in per_case_candidate:
            entry["candidate"] = asdict(per_case_candidate[case])
        out[case] = entry
    return out


def build_report(
    *,
    validation_id: str,
    created_at: str,
    suite_id: str,
    baseline_ref: str,
    baseline_commit: str,
    candidate_ref: str,
    candidate_commit: str,
    hard_gates: list[GateResult],
    warnings: list[GateResult],
    baseline_pooled: AggregatedArm,
    candidate_pooled: AggregatedArm,
    per_case_baseline: dict[str, AggregatedArm],
    per_case_candidate: dict[str, AggregatedArm],
    provenance: dict | None = None,
) -> dict:
    """Assemble the private validation record. Status is derived from hard gates."""
    passed = all_hard_pass(hard_gates)
    return {
        "schema_version": 1,
        "validation_id": validation_id,
        "status": "passed" if passed else "failed",
        "created_at": created_at,
        "suite": {"id": suite_id},
        "baseline": {"ref": baseline_ref, "commit": baseline_commit},
        "candidate": {"ref": candidate_ref, "commit": candidate_commit},
        "gates": {"hard": _gate_dicts(hard_gates), "all_hard_pass": passed},
        "warnings": _gate_dicts(warnings),
        "metrics": {
            "baseline": asdict(baseline_pooled),
            "candidate": asdict(candidate_pooled),
            "by_case": _by_case(per_case_baseline, per_case_candidate),
        },
        "provenance": provenance or {},
    }


def render_markdown(report: dict) -> str:
    """Human review summary of a validation report."""
    status = report["status"].upper()
    icon = "✅" if report["status"] == "passed" else "❌"
    lines = [
        f"# Harness validation {report['validation_id']} — {icon} {status}",
        "",
        f"- suite: `{report['suite']['id']}`",
        f"- baseline: `{report['baseline']['ref']}` ({report['baseline']['commit'][:12]})",
        f"- candidate: `{report['candidate']['ref']}` ({report['candidate']['commit'][:12]})",
        "",
        "## Hard gates",
    ]
    for gate in report["gates"]["hard"]:
        mark = "✅" if gate["passed"] else "❌"
        lines.append(f"- {mark} `{gate['name']}` — {gate['detail']}")
    lines.append("")
    lines.append("## Warnings")
    if report["warnings"]:
        for w in report["warnings"]:
            mark = "•" if w["passed"] else "⚠️"
            lines.append(f"- {mark} `{w['name']}` — {w['detail']}")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def _digest(report: dict) -> str:
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_attestation(
    report: dict,
    *,
    version: str,
    release_url: str | None,
    validated_at: str,
) -> dict:
    """Derive the sanitized public attestation (§6.5) from a private report.

    Exposes only an allowlisted aggregate: version/ref/commit, suite id, status,
    gate names + pass/fail (no private detail), and aggregate medians. Raises
    ``SanitizationError`` if the rendered attestation trips the secret scanner.
    """
    candidate = report["candidate"]
    attestation = {
        "schema_version": 1,
        "sanitized": True,
        "template": {
            "version": version,
            "ref": candidate["ref"],
            "commit": candidate["commit"],
            "release_url": release_url,
            "validated_at": validated_at,
        },
        "suite": {"id": report["suite"]["id"]},
        "status": report["status"],
        "gates": [
            {"name": g["name"], "passed": g["passed"]} for g in report["gates"]["hard"]
        ],
        "aggregate_metrics": {
            "baseline": _public_metrics(report["metrics"]["baseline"]),
            "candidate": _public_metrics(report["metrics"]["candidate"]),
        },
        "private_manifest_sha256": _digest(report),
    }
    sanitizer.assert_clean(json.dumps(attestation))
    return attestation


_PUBLIC_METRIC_KEYS = (
    "pass_ratio",
    "median_tokens",
    "median_cost",
    "median_duration",
    "median_steps",
    "median_composite",
)


def _public_metrics(pooled: dict) -> dict:
    return {k: pooled[k] for k in _PUBLIC_METRIC_KEYS if k in pooled}


def report_dir(root: Path, version: str, validation_id: str) -> Path:
    return root / VALIDATIONS_SUBDIR / version / validation_id


def write_report(root: Path, version: str, report: dict) -> dict[str, Path]:
    """Atomically write validation.yaml, report.json, and summary.md."""
    directory = report_dir(root, version, report["validation_id"])
    paths = {
        "validation": directory / "validation.yaml",
        "report": directory / "report.json",
        "summary": directory / "summary.md",
    }
    manifests.atomic_write_text(paths["validation"], manifests.dump_yaml(report))
    manifests.atomic_write_text(
        paths["report"], json.dumps(report, indent=2, sort_keys=False) + "\n"
    )
    manifests.atomic_write_text(paths["summary"], render_markdown(report))
    return paths
