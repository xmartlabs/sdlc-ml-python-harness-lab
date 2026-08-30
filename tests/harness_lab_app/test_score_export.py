"""Pushing lab scores onto Langfuse traces (Phase 6).

What is tested is the shape of the claim that leaves the lab: which scores go,
how a boolean is typed, and that a score's *version* survives a system that has
no version field.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness_lab_app.observability.config import LangfuseConfig
from harness_lab_app.observability.score_export import (
    EXPORTED_SCORES,
    build_score_events,
    export_scores,
)


@dataclass
class _Score:
    name: str
    version: str = "v1"
    category: str = "outcome"
    provenance: str = "fact"
    source: str = "deterministic"
    value_num: float | None = None
    value_bool: bool | None = None
    reason: str = ""


class _Transport:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls: list[tuple[dict, str | None]] = []

    def send(self, payload, url=None):
        self.calls.append((payload, url))

        class _Result:
            ok = self.ok
            status = 200 if self.ok else 400
            reason = "" if self.ok else "nope"

        return _Result()


def _config(**over) -> LangfuseConfig:
    base = {
        "enabled": True,
        "host": "https://langfuse.invalid",
        "public_key": "pk",
        "secret_key": "sk",
        "environment": "test",
        "project_url": "",
        "session_prefix": "bench-",
        "auto_export": False,
    }
    base.update(over)
    return LangfuseConfig(**base)


def test_the_score_name_carries_its_version():
    """Langfuse has no version field, so the name has to hold it.

    Without this a rubric change pools two different quantities into one chart —
    the exact failure the registry exists to prevent.
    """
    (event,) = build_score_events(
        "run-1", "bench-run-1", [_Score("test_pass_rate", version="v2", value_num=0.5)]
    )
    assert event["body"]["name"] == "test_pass_rate@v2"
    assert event["type"] == "score-create"


def test_a_pass_fail_is_sent_as_a_boolean_not_a_number():
    """A pass/fail rendered as 1/0 invites an average that means nothing."""
    (event,) = build_score_events(
        "run-1", "bench-run-1", [_Score("task_validation_pass", value_bool=True)]
    )
    assert event["body"]["dataType"] == "BOOLEAN"
    assert event["body"]["value"] is True


def test_the_kind_of_claim_travels_with_the_value():
    (event,) = build_score_events(
        "run-1",
        "bench-run-1",
        [_Score("skill_adherence", provenance="judgement", value_num=0.8)],
    )
    assert event["body"]["metadata"]["provenance"] == "judgement"


def test_only_the_curated_subset_is_exported():
    scores = [
        _Score("test_pass_rate", value_num=1.0),
        _Score("diff_lines_changed", value_num=42.0),  # not in the subset
    ]
    events = build_score_events("run-1", "bench-run-1", scores)
    assert [e["body"]["name"] for e in events] == ["test_pass_rate@v1"]
    assert "diff_lines_changed" not in EXPORTED_SCORES


def test_a_score_with_no_value_is_skipped_rather_than_sent_as_zero():
    assert build_score_events("run-1", "s", [_Score("cost_usd")]) == []


def test_the_id_is_stable_so_re_export_updates_rather_than_duplicates():
    first = build_score_events("run-1", "s", [_Score("cost_usd", value_num=1.0)])
    second = build_score_events("run-1", "s", [_Score("cost_usd", value_num=2.0)])
    assert first[0]["body"]["id"] == second[0]["body"]["id"] == "run-1-cost_usd-v1"


def test_scores_go_to_the_ingestion_route_not_the_otlp_one():
    """Spans need OTLP; scores are the one thing ingestion still accepts."""
    transport = _Transport()
    config = _config()
    result = export_scores(
        "run-1",
        [_Score("test_pass_rate", value_num=1.0)],
        config=config,
        transport=transport,
    )
    _payload, url = transport.calls[0]
    assert url == config.ingestion_url
    assert url.endswith("/api/public/ingestion")
    assert result.ok and result.exported == 1


def test_a_disabled_integration_is_reported_not_attempted():
    transport = _Transport()
    result = export_scores(
        "run-1",
        [_Score("test_pass_rate", value_num=1.0)],
        config=_config(enabled=False, disabled_reason="no keys"),
        transport=transport,
    )
    assert not result.ok
    assert result.reason == "no keys"
    assert transport.calls == []


def test_a_run_with_nothing_exportable_is_a_success_not_a_failure():
    result = export_scores(
        "run-1",
        [_Score("diff_lines_changed", value_num=1.0)],
        config=_config(),
        transport=_Transport(),
    )
    assert result.ok and result.exported == 0
