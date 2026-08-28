"""The trace shape: one session per run, one trace per step, ids that repeat."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from harness_lab_app.observability.config import LangfuseConfig
from harness_lab_app.observability.events import attributes, build_span, trace_id
from harness_lab_app.observability.run_export import (
    build_run_payload,
    build_run_spans,
    export_run,
    run_tags,
)

RUN_ID = "tetris-v1-K-900"


@pytest.fixture(autouse=True)
def _clean_langfuse_env(monkeypatch):
    for name in (
        "LANGFUSE_ENABLED",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_TRACING_ENVIRONMENT",
        "LANGFUSE_PROJECT_URL",
        "LANGFUSE_SESSION_PREFIX",
        "LANGFUSE_AUTO_EXPORT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config(monkeypatch) -> LangfuseConfig:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "unit-test")
    return LangfuseConfig.from_env()


def _step_log(harness: str, started: str, lines: str = "") -> str:
    return (
        f"# {harness} run started {started}Z\n# cmd: {harness} run <prompt>\n\n{lines}"
    )


@pytest.fixture
def run_dir(tmp_path):
    """A two-step opencode run, laid out exactly as bench_run.py writes it."""
    root = tmp_path / "runs" / RUN_ID
    (root / "steps").mkdir(parents=True)
    (root / "metadata.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "benchmark_id": "tetris-v1",
                "case": "tetris-v1",
                "cell_id": "K",
                "harness": "opencode",
                "methodology": "local-only",
                "planner_model": "",
                "executor_model": "lmstudio/qwen3.5-9b",
                "prompt_variant": "conductor",
                "max_steps": 10,
                "created_at": "2026-08-15T03:15:51.173437Z",
                "condition_hash": "sha256:abc",
            }
        )
    )
    (root / "prompt.md").write_text(
        "Build Tetris.\nGATEWAY_TOKEN=sk-live-shouldnotleak\n"
    )
    (root / "usage.json").write_text(
        json.dumps({"input_tokens": 2101101, "output_tokens": 21669, "steps": 2})
    )
    (root / "diff.patch").write_text("--- a/main.py\n+++ b/main.py\n+print()\n")
    (root / "report.md").write_text("# Report\nIt runs.\n")
    (root / "steps" / "step-1.log").write_text(
        _step_log(
            "opencode",
            "2026-08-15T03:15:51.799466",
            'timestamp=2026-08-15T03:17:43.949Z level=INFO message="done"\n',
        )
    )
    (root / "steps" / "step-2-prompt.md").write_text("Continue from step 1.")
    (root / "steps" / "step-2.log").write_text(
        _step_log(
            "opencode",
            "2026-08-15T03:18:00.000000",
            "timestamp=2026-08-15T03:20:00.000Z level=INFO message=loop\n",
        )
    )
    return root


def test_one_trace_per_step_plus_a_run_envelope(run_dir, config):
    spans = build_run_spans(run_dir, config=config)
    names = [span["name"] for span in spans]
    assert names == ["step-1", "step-2", f"run:{RUN_ID}", "diff"]
    # Three traces: two steps and the run envelope. `diff` is a child of the
    # envelope, so it shares that trace rather than opening a fourth.
    assert len({span["traceId"] for span in spans}) == 3


def test_every_root_span_carries_the_same_session(run_dir, config):
    spans = build_run_spans(run_dir, config=config)
    sessions = {
        _attr(span, "langfuse.session.id")
        for span in spans
        if "parentSpanId" not in span
    }
    assert sessions == {f"bench-{RUN_ID}"}


def test_child_spans_hang_off_the_run_envelope(run_dir, config):
    spans = {span["name"]: span for span in build_run_spans(run_dir, config=config)}
    assert spans["diff"]["parentSpanId"] == spans[f"run:{RUN_ID}"]["spanId"]
    assert spans["diff"]["traceId"] == spans[f"run:{RUN_ID}"]["traceId"]


def test_step_input_is_the_prompt_that_step_was_given(run_dir, config):
    spans = {span["name"]: span for span in build_run_spans(run_dir, config=config)}
    # Step 1 reuses the case prompt; only continuations get their own file.
    assert "Build Tetris." in _attr(spans["step-1"], "langfuse.observation.input")
    assert _attr(spans["step-2"], "langfuse.observation.input") == (
        "Continue from step 1."
    )


def test_timestamps_come_from_the_log_headers(run_dir, config):
    spans = {span["name"]: span for span in build_run_spans(run_dir, config=config)}
    start = datetime.fromtimestamp(
        int(spans["step-1"]["startTimeUnixNano"]) / 1e9, tz=UTC
    )
    end = datetime.fromtimestamp(int(spans["step-1"]["endTimeUnixNano"]) / 1e9, tz=UTC)
    assert start.isoformat().startswith("2026-08-15T03:15:51")
    assert end.isoformat().startswith("2026-08-15T03:17:43")
    # The run envelope spans from the first step's start to the last one's end.
    assert (
        spans[f"run:{RUN_ID}"]["startTimeUnixNano"]
        == (spans["step-1"]["startTimeUnixNano"])
    )
    assert (
        spans[f"run:{RUN_ID}"]["endTimeUnixNano"]
        == (spans["step-2"]["endTimeUnixNano"])
    )


def test_a_step_never_ends_after_the_next_one_starts(run_dir, config):
    """A stale mtime must not make step 1 swallow step 2."""
    (run_dir / "steps" / "step-1.log").write_text(
        _step_log("claude", "2026-08-15T03:15:51.799466")  # no in-log clock
    )
    spans = {span["name"]: span for span in build_run_spans(run_dir, config=config)}
    assert int(spans["step-1"]["endTimeUnixNano"]) <= int(
        spans["step-2"]["startTimeUnixNano"]
    )


def test_tags_follow_the_metadata_standard(run_dir, config):
    root = next(
        span
        for span in build_run_spans(run_dir, config=config)
        if span["name"] == f"run:{RUN_ID}"
    )
    assert _attr(root, "langfuse.trace.tags") == [
        "benchmark:tetris-v1",
        "harness:opencode",
        "method:local-only",
        "executor:lmstudio/qwen3.5-9b",
        "prompt:conductor",
    ]


def test_run_tags_skip_missing_fields():
    assert run_tags({"harness": "codex"}) == ["harness:codex"]


def test_usage_totals_land_on_the_run_envelope(run_dir, config):
    root = next(
        span
        for span in build_run_spans(run_dir, config=config)
        if span["name"] == f"run:{RUN_ID}"
    )
    assert json.loads(_attr(root, "langfuse.observation.usage_details")) == {
        "input": 2101101,
        "output": 21669,
    }
    assert _attr(root, "langfuse.observation.metadata.usage_steps") == "2"


def test_a_failed_run_is_marked_as_an_error(run_dir, config):
    spans = build_run_spans(
        run_dir,
        config=config,
        extra={"state": "failed", "exit_code": 1, "failure_reason": "timeout"},
    )
    root = next(span for span in spans if span["name"] == f"run:{RUN_ID}")
    assert root["status"]["code"] == 2
    assert root["status"]["message"] == "timeout"
    assert _attr(root, "langfuse.observation.level") == "ERROR"


def test_a_successful_run_is_not(run_dir, config):
    root = next(
        span
        for span in build_run_spans(run_dir, config=config)
        if span["name"] == f"run:{RUN_ID}"
    )
    assert root["status"]["code"] == 1


def test_ids_are_deterministic_so_re_export_updates_in_place(run_dir, config):
    first = build_run_spans(run_dir, config=config)
    second = build_run_spans(run_dir, config=config)
    assert [(s["traceId"], s["spanId"]) for s in first] == [
        (s["traceId"], s["spanId"]) for s in second
    ]
    # Ids are salted with the condition hash so two *different* runs that
    # happen to share a run_id cannot overwrite each other in Langfuse.
    assert first[0]["traceId"] == trace_id(f"{RUN_ID}@sha256:abc", "step:1")


def test_two_runs_sharing_a_run_id_get_distinct_ids(run_dir, config, tmp_path):
    import shutil

    other = tmp_path / "elsewhere" / RUN_ID
    shutil.copytree(run_dir, other)
    metadata = json.loads((other / "metadata.json").read_text())
    metadata["condition_hash"] = "sha256:different"
    (other / "metadata.json").write_text(json.dumps(metadata))

    ids_a = {s["traceId"] for s in build_run_spans(run_dir, config=config)}
    ids_b = {s["traceId"] for s in build_run_spans(other, config=config)}
    assert ids_a.isdisjoint(ids_b)
    # Same session either way: they still group under the run in the UI.


def test_ids_have_the_otel_widths(run_dir, config):
    for span in build_run_spans(run_dir, config=config):
        assert len(span["traceId"]) == 32
        assert len(span["spanId"]) == 16


def test_no_credential_reaches_the_payload(run_dir, config):
    body = json.dumps(build_run_payload(run_dir, config=config))
    assert "sk-live-shouldnotleak" not in body
    assert "GATEWAY_TOKEN=<redacted>" in body


def test_single_step_runs_fall_back_to_the_combined_log(tmp_path, config):
    root = tmp_path / "runs" / "single-1"
    root.mkdir(parents=True)
    (root / "metadata.json").write_text(json.dumps({"run_id": "single-1"}))
    (root / "prompt.md").write_text("Do one thing.")
    (root / "opencode.log").write_text(
        _step_log("opencode", "2026-08-15T03:15:51.799466")
    )
    spans = build_run_spans(root, config=config)
    assert [span["name"] for span in spans] == ["step-1", "run:single-1"]


def test_a_run_that_died_before_its_first_step_still_exports_its_envelope(
    tmp_path, config
):
    """The failure is the thing worth seeing; do not swallow it."""
    root = tmp_path / "runs" / "empty-1"
    root.mkdir(parents=True)
    (root / "metadata.json").write_text(
        json.dumps({"run_id": "empty-1", "created_at": "2026-08-15T03:15:51.173437Z"})
    )
    spans = build_run_spans(
        root, config=config, extra={"state": "failed", "failure_reason": "no image"}
    )
    assert [span["name"] for span in spans] == ["run:empty-1"]
    assert spans[0]["status"]["message"] == "no image"


class _ExplodingTransport:
    """Proves a code path made no HTTP call: reaching it is the failure."""

    def send(self, payload):
        raise AssertionError("Langfuse must not be contacted here")


def test_export_is_a_no_op_when_disabled(run_dir):
    result = export_run(
        run_dir, config=LangfuseConfig.from_env(), transport=_ExplodingTransport()
    )
    assert result.ok is False
    assert "LANGFUSE_ENABLED" in result.reason


def test_export_reports_what_it_sent(run_dir, config):
    class _Ok:
        def send(self, payload):
            assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
            from harness_lab_app.observability.client import SendResult

            return SendResult(ok=True, status=207)

    result = export_run(run_dir, config=config, transport=_Ok())
    assert result.ok is True
    assert result.spans == 4
    assert result.traces == 3
    assert result.session_id == f"bench-{RUN_ID}"


def test_export_survives_a_missing_run_directory(tmp_path, config):
    result = export_run(
        tmp_path / "nope", config=config, transport=_ExplodingTransport()
    )
    assert result.ok is False
    assert "No run directory" in result.reason


def test_attribute_encoding_covers_the_types_langfuse_reads():
    encoded = {
        item["key"]: item["value"]
        for item in attributes(
            {
                "s": "text",
                "b": True,
                "i": 7,
                "f": 1.5,
                "list": ["a", "b"],
                "obj": {"k": "v"},
                "dropped": None,
                "empty": "",
            }
        )
    }
    assert encoded["s"] == {"stringValue": "text"}
    # bool before int: True must not arrive as 1.
    assert encoded["b"] == {"boolValue": True}
    assert encoded["i"] == {"intValue": "7"}
    assert encoded["f"] == {"doubleValue": 1.5}
    assert encoded["list"]["arrayValue"]["values"] == [
        {"stringValue": "a"},
        {"stringValue": "b"},
    ]
    assert json.loads(encoded["obj"]["stringValue"]) == {"k": "v"}
    assert "dropped" not in encoded
    assert "empty" not in encoded


def test_a_child_span_does_not_claim_the_session():
    span = build_span(
        trace="a" * 32,
        span="b" * 16,
        parent="c" * 16,
        name="child",
        start_ns=1,
        end_ns=2,
        session_id="bench-x",
        tags=["harness:opencode"],
    )
    keys = {item["key"] for item in span["attributes"]}
    assert "langfuse.session.id" not in keys
    assert "langfuse.trace.tags" not in keys


def test_an_end_before_its_start_is_clamped():
    span = build_span(trace="a" * 32, span="b" * 16, name="x", start_ns=10, end_ns=5)
    assert span["endTimeUnixNano"] == "10"


def _attr(span: dict, key: str):
    for item in span["attributes"]:
        if item["key"] == key:
            value = item["value"]
            if "stringValue" in value:
                return value["stringValue"]
            if "arrayValue" in value:
                return [v["stringValue"] for v in value["arrayValue"]["values"]]
            return next(iter(value.values()))
    return None
