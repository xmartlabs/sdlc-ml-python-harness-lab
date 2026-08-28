"""The inside of a step: generations, tool calls, and honest timing labels."""

from __future__ import annotations

import json

import pytest

from harness_lab_app.observability.config import LangfuseConfig
from harness_lab_app.observability.step_detail import (
    parse_claude_step_events,
    parse_opencode_step_log,
)

RUN_ID = "tetris-v1-K-901"


def _claude_event(**kwargs) -> str:
    return json.dumps(kwargs)


def _assistant(mid: str, blocks: list[dict], *, usage=None, received=None) -> str:
    event = {
        "type": "assistant",
        "message": {
            "id": mid,
            "model": "claude-sonnet-5",
            "role": "assistant",
            "content": blocks,
            "usage": usage or {},
        },
    }
    if received:
        event["_received_at"] = received
    return _claude_event(**event)


def _tool_result(use_id: str, content, *, is_error=False, received=None) -> str:
    event = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        },
    }
    if received:
        event["_received_at"] = received
    return _claude_event(**event)


RESULT_EVENT = _claude_event(
    type="result",
    subtype="success",
    is_error=False,
    stop_reason="end_turn",
    num_turns=3,
    duration_ms=61000,
    duration_api_ms=58000,
    ttft_ms=900,
    total_cost_usd=0.42,
    result="ALL MILESTONES COMPLETE",
    permission_denials=[],
    usage={
        "input_tokens": 120,
        "output_tokens": 4500,
        "cache_read_input_tokens": 90000,
        "cache_creation_input_tokens": 1500,
    },
    modelUsage={"claude-sonnet-5": {"costUSD": 0.42}},
)


@pytest.fixture
def claude_lines() -> list[str]:
    """Three turns: text, a Bash call with its result, and the final text."""
    return [
        _assistant(
            "msg_1",
            [{"type": "thinking", "thinking": "let me look"}],
            usage={"input_tokens": 10, "output_tokens": 1},
        ),
        _assistant(
            "msg_1",
            [{"type": "text", "text": "Grounding first."}],
            usage={"input_tokens": 10, "output_tokens": 8},
        ),
        _assistant(
            "msg_2",
            [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "pytest -q"},
                }
            ],
            usage={
                "input_tokens": 4,
                "output_tokens": 20,
                "cache_read_input_tokens": 5000,
            },
        ),
        _tool_result("toolu_1", "2 passed"),
        _assistant(
            "msg_3",
            [{"type": "text", "text": "Done."}],
            usage={"input_tokens": 2, "output_tokens": 5},
        ),
        RESULT_EVENT,
    ]


def test_stream_events_collapse_to_one_generation_per_message(claude_lines):
    detail = parse_claude_step_events(claude_lines)
    assert detail.source == "claude-events"
    assert [gen.metadata["message_id"] for gen in detail.generations] == [
        "msg_1",
        "msg_2",
        "msg_3",
    ]
    first = detail.generations[0]
    assert first.model == "claude-sonnet-5"
    assert first.text == "Grounding first."
    assert first.metadata["thinking_chars"] == len("let me look")
    # The max snapshot wins over the earlier partial one.
    assert first.usage["output_tokens"] == 8
    assert first.metadata["output_tokens_source"] == "stream_snapshot"


def test_tool_calls_pair_use_with_result(claude_lines):
    detail = parse_claude_step_events(claude_lines)
    assert len(detail.tool_calls) == 1
    tool = detail.tool_calls[0]
    assert tool.name == "Bash"
    assert json.loads(tool.input_text) == {"command": "pytest -q"}
    assert tool.output_text == "2 passed"
    assert tool.is_error is False


def test_a_tool_call_that_never_resolved_is_kept_and_flagged():
    lines = [
        _assistant(
            "msg_1",
            [
                {
                    "type": "tool_use",
                    "id": "toolu_dead",
                    "name": "Bash",
                    "input": {"command": "sleep 9999"},
                }
            ],
        ),
    ]
    detail = parse_claude_step_events(lines)
    assert detail.tool_calls[0].metadata["unresolved"] is True


def test_the_terminal_result_event_carries_the_step_totals(claude_lines):
    detail = parse_claude_step_events(claude_lines)
    assert detail.result["stop_reason"] == "end_turn"
    assert detail.result["total_cost_usd"] == 0.42
    assert detail.result["usage"]["output_tokens"] == 4500
    assert detail.result["models"] == ["claude-sonnet-5"]
    assert detail.result["result_text"] == "ALL MILESTONES COMPLETE"


def test_timing_is_labeled_interpolated_without_received_stamps(claude_lines):
    assert parse_claude_step_events(claude_lines).timing == "interpolated"


def test_timing_is_labeled_events_with_received_stamps():
    lines = [
        _assistant(
            "msg_1",
            [{"type": "text", "text": "hi"}],
            received="2026-08-27T20:00:01.000000Z",
        ),
    ]
    detail = parse_claude_step_events(lines)
    assert detail.timing == "events"
    assert detail.generations[0].start is not None


def test_tool_result_content_may_be_a_block_list():
    lines = [
        _assistant(
            "msg_1",
            [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
        ),
        _tool_result("t1", [{"type": "text", "text": "file body"}]),
    ]
    assert parse_claude_step_events(lines).tool_calls[0].output_text == "file body"


def test_garbage_lines_are_skipped_not_fatal():
    detail = parse_claude_step_events(["not json", "", RESULT_EVENT])
    assert detail.result["stop_reason"] == "end_turn"


def test_empty_input_yields_the_empty_detail():
    assert parse_claude_step_events([]).empty
    assert parse_opencode_step_log("no markers here").empty


# --------------------------------------------------------------------------
# opencode lane
# --------------------------------------------------------------------------

OPENCODE_LOG = """# opencode run started 2026-08-27T23:23:00.688357Z
# cmd: opencode run <prompt>

timestamp=2026-08-27T23:23:01.879Z level=INFO run=x message=stream providerID=gateway modelID=nim-super small=true agent=title mode=primary
timestamp=2026-08-27T23:23:02.455Z level=INFO run=x message=stream providerID=gateway modelID=nim-super small=false agent=build mode=primary
I will write main.py now.
timestamp=2026-08-27T23:23:05.624Z level=INFO run=x message=loop session.id=s step=1
timestamp=2026-08-27T23:23:05.657Z level=INFO run=x message=stream providerID=gateway modelID=nim-super small=false agent=build mode=primary
Done. DONE: complete.
timestamp=2026-08-27T23:23:29.486Z level=INFO run=x message=loop session.id=s step=2
"""


def test_opencode_llm_calls_come_from_stream_and_loop_markers():
    detail = parse_opencode_step_log(OPENCODE_LOG)
    assert detail.source == "opencode-log"
    assert detail.timing == "log"
    assert len(detail.generations) == 3
    build = detail.generations[1]
    assert build.model == "gateway/nim-super"
    assert build.metadata["agent"] == "build"
    assert build.text == "I will write main.py now."
    assert build.start.isoformat().startswith("2026-08-27T23:23:02.455")
    assert build.end.isoformat().startswith("2026-08-27T23:23:05.624")


def test_opencode_title_call_is_kept_but_labeled():
    detail = parse_opencode_step_log(OPENCODE_LOG)
    assert detail.generations[0].metadata["agent"] == "title"


# --------------------------------------------------------------------------
# integration with the exporter: children hang off the step trace
# --------------------------------------------------------------------------


@pytest.fixture
def config(monkeypatch) -> LangfuseConfig:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    return LangfuseConfig.from_env()


@pytest.fixture
def claude_run_dir(tmp_path, claude_lines):
    root = tmp_path / "runs" / RUN_ID
    (root / "steps").mkdir(parents=True)
    (root / "metadata.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "harness": "claude",
                "executor_model": "claude-sonnet-5",
                "created_at": "2026-08-27T20:00:00.000000Z",
                "condition_hash": "sha256:abc",
            }
        )
    )
    (root / "prompt.md").write_text("Build Tetris.")
    (root / "steps" / "step-1.log").write_text(
        "# claude run started 2026-08-27T20:00:00.000000Z\n# cmd: claude <prompt>\n"
    )
    (root / "steps" / "step-1.events.jsonl").write_text("\n".join(claude_lines) + "\n")
    return root


def _attrs(span) -> dict:
    return {item["key"]: item["value"] for item in span["attributes"]}


def test_step_children_are_generations_tools_and_the_log(claude_run_dir, config):
    from harness_lab_app.observability.run_export import build_run_spans

    spans = build_run_spans(claude_run_dir, config=config)
    step = next(span for span in spans if span["name"] == "step-1")
    children = [span for span in spans if span.get("parentSpanId") == step["spanId"]]
    types = [
        _attrs(child)["langfuse.observation.type"]["stringValue"] for child in children
    ]
    assert types.count("generation") == 3
    assert types.count("tool") == 1
    assert types.count("event") == 1  # the log
    # Children live inside the step's trace, not their own.
    assert {child["traceId"] for child in children} == {step["traceId"]}


def test_step_span_carries_the_result_facts(claude_run_dir, config):
    from harness_lab_app.observability.run_export import build_run_spans

    spans = build_run_spans(claude_run_dir, config=config)
    attrs = _attrs(next(span for span in spans if span["name"] == "step-1"))
    assert attrs["langfuse.observation.model.name"]["stringValue"] == (
        "claude-sonnet-5"
    )
    assert json.loads(attrs["langfuse.observation.usage_details"]["stringValue"]) == {
        "input": 120,
        "output": 4500,
        "cache_read_input_tokens": 90000,
        "cache_creation_input_tokens": 1500,
    }
    assert json.loads(attrs["langfuse.observation.cost_details"]["stringValue"]) == {
        "total": 0.42
    }
    assert attrs["langfuse.observation.metadata.stop_reason"]["stringValue"] == (
        "end_turn"
    )
    # The output is the outcome, not a 24k log tail.
    assert attrs["langfuse.observation.output"]["stringValue"] == (
        "ALL MILESTONES COMPLETE"
    )
    assert attrs["langfuse.observation.metadata.timing_source"]["stringValue"] == (
        "interpolated"
    )


def test_interpolated_children_stay_inside_the_step_window(claude_run_dir, config):
    from harness_lab_app.observability.run_export import build_run_spans

    spans = build_run_spans(claude_run_dir, config=config)
    step = next(span for span in spans if span["name"] == "step-1")
    for child in spans:
        if child.get("parentSpanId") != step["spanId"]:
            continue
        assert int(step["startTimeUnixNano"]) <= int(child["startTimeUnixNano"])
        assert int(child["endTimeUnixNano"]) <= int(step["endTimeUnixNano"])
        assert int(child["startTimeUnixNano"]) <= int(child["endTimeUnixNano"])


def test_credentials_in_tool_output_are_redacted(claude_run_dir, config):
    from harness_lab_app.observability.run_export import build_run_payload

    events = claude_run_dir / "steps" / "step-1.events.jsonl"
    lines = events.read_text().splitlines()
    lines.insert(
        3,
        _tool_result("toolu_1", "GATEWAY_TOKEN=sk-live-shouldnotleak"),
    )
    events.write_text("\n".join(lines) + "\n")
    body = json.dumps(build_run_payload(claude_run_dir, config=config))
    assert "sk-live-shouldnotleak" not in body
