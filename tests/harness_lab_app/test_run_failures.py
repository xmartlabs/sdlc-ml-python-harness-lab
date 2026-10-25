"""Telling "the lab broke" apart from "the agent did badly".

Runs K-043/044/045 are the case this guards: the runner image had gone missing,
all three died in under a second having asked the model nothing, and the lab
recorded three attempts against that model. A benchmark that counts runs which
never started is worse than one that counts nothing, because it looks
authoritative.
"""

from __future__ import annotations

import pytest

from harness_lab_app.run_failures import (
    awaits_model_response,
    classify_failure,
    failure_label,
    is_model_request,
    is_retry_noise,
    is_stream_error,
    is_unmeasured,
)

# Verbatim from the runs named in the tests below, because the whole mechanism
# is prefix matching on opencode's log format and a paraphrase would test
# nothing.
STREAM_START_LINE = (
    "timestamp=2026-08-13T17:08:29.555Z level=INFO run=0aa06ee9 message=stream "
    "providerID=lmstudio modelID=qwen/qwen3.6-35b-a3b session.id=ses_003ec87a"
)
RUNTIME_SELECTED_LINE = (
    "timestamp=2026-08-13T17:08:29.557Z level=INFO run=0aa06ee9 "
    'message="llm runtime selected" llm.runtime=ai-sdk llm.provider=lmstudio '
    "llm.model=qwen/qwen3.6-35b-a3b"
)
STREAM_ERROR_LINE = (
    "timestamp=2026-08-11T22:23:08.812Z level=ERROR run=1cf7bf13 "
    'message="stream error" providerID=gateway modelID=nim-nemotron-super-120b '
    'error.error="AI_APICallError: Cannot connect to API: Unable to connect."'
)
TOOL_CALL_LINE = "$ ls /app/workspace/tetris-v1-K-050/ 2>/dev/null"


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        # Exactly what K-043/044/045 recorded.
        (
            'Executor error: RuntimeError("Runner image '
            "'harness-lab-runner:latest' was not found locally.\")",
            "lab",
        ),
        ("Worker restarted while the run was in flight; retry to resume", "lab"),
        ("Run stalled: no output for 10 minutes, so it was stopped", "stalled"),
        ("Run timed out after 7200s", "timeout"),
        # K-029: 123 failed provider requests, none of them the model's fault.
        ("Model endpoint failing: 5 provider requests failed", "lab"),
        # K-050: stopped while the model was still processing the prompt.
        ("No model response: the endpoint accepted the request", "lab"),
        # The tail of the agent's own output — a real, bad, result.
        ("Traceback (most recent call last): SyntaxError", "agent"),
        ("", None),
        (None, None),
    ],
)
def test_failures_are_classified_by_who_caused_them(reason, kind):
    assert classify_failure(reason) == kind


def test_only_lab_failures_count_as_unmeasured():
    """A stalled or crashed run still tells you something; a missing image does not."""
    assert is_unmeasured("Executor error: RuntimeError('no image')") is True
    assert is_unmeasured("Worker restarted while the run was in flight") is True
    assert is_unmeasured("Run stalled: no output for 10 minutes") is False
    assert is_unmeasured("Run timed out after 7200s") is False
    assert is_unmeasured("AttributeError: 'Game' object has no attribute") is False
    assert is_unmeasured(None) is False


def test_a_run_the_model_never_answered_is_not_an_attempt():
    """The leaderboard must not read an endpoint problem as a model's score.

    K-029 spent 70 minutes failing to reach the gateway and was filed as a
    plain timeout, i.e. as nemotron trying and failing. K-050 was stopped mid
    prompt-processing and filed as a stall, i.e. as qwen wedging the harness.
    Neither model was ever given the chance to be wrong.
    """
    assert is_unmeasured("Model endpoint failing: 5 provider requests failed") is True
    assert is_unmeasured("No model response: the endpoint accepted") is True
    # But a hung tool call is the agent's own doing, so it still counts.
    assert is_unmeasured("Run stalled: no output for 10 minutes") is False


def test_labels_are_short_enough_for_a_table_cell():
    assert failure_label("Executor error: boom") == "lab error"
    assert failure_label("Run stalled: quiet") == "stalled"
    assert failure_label("Run timed out after 1s") == "timed out"
    assert failure_label("Model endpoint failing: 5 requests") == "endpoint down"
    assert failure_label("No model response: 40 minutes") == "no response"
    assert failure_label("some traceback") == "agent error"
    assert failure_label(None) is None


def test_a_request_in_flight_is_told_apart_from_a_tool_call():
    """The one distinction the whole fix rests on, against real log lines."""
    assert awaits_model_response(STREAM_START_LINE, "opencode") is True
    assert awaits_model_response(RUNTIME_SELECTED_LINE, "opencode") is True
    assert awaits_model_response(TOOL_CALL_LINE, "opencode") is False
    # The trap: "stream error" is the opposite of a request in flight, and it
    # contains the word the marker matches on.
    assert awaits_model_response(STREAM_ERROR_LINE, "opencode") is False


def test_failed_requests_are_recognized_and_retry_noise_is_not_progress():
    assert is_stream_error(STREAM_ERROR_LINE, "opencode") is True
    assert is_stream_error(STREAM_START_LINE, "opencode") is False
    # Between two failed attempts opencode prints only its own setup. Counting
    # that as progress would reset the error count forever and the run would
    # retry until its budget ran out — exactly what K-029 did.
    assert is_retry_noise(STREAM_START_LINE, "opencode") is True
    assert is_retry_noise(RUNTIME_SELECTED_LINE, "opencode") is True
    assert is_retry_noise(TOOL_CALL_LINE, "opencode") is False


# --- Per-harness vocabulary --------------------------------------------------

CLAUDE_AWAITING_LINE = "# awaiting model (Bash)"
CLAUDE_TOOL_LINE = "→ Bash(bash tests/run.sh)"


def test_the_claude_lane_can_say_it_is_waiting_on_the_model():
    """Without this the claude lane had no way to be waiting at all.

    Every silence was charged to the agent as a stall, so a gateway that
    accepted a request and never answered came back as a failed attempt by the
    model — four talos runs spanning 122 minutes were recorded that way.
    """
    assert awaits_model_response(CLAUDE_AWAITING_LINE, "claude") is True
    assert awaits_model_response(CLAUDE_TOOL_LINE, "claude") is False


def test_one_harness_never_reads_another_harness_markers():
    """opencode's literals said nothing about a claude run, and vice versa."""
    assert awaits_model_response(STREAM_START_LINE, "claude") is False
    assert awaits_model_response(CLAUDE_AWAITING_LINE, "opencode") is False


def test_an_unknown_harness_never_has_its_silence_charged_to_the_agent():
    """A silence nobody can attribute is unmeasured, not an agent failure."""
    assert awaits_model_response(CLAUDE_TOOL_LINE, "some-new-cli") is True
    assert awaits_model_response("anything at all", None) is True
    # And it claims no request and no error it cannot see.
    assert is_model_request(CLAUDE_AWAITING_LINE, "some-new-cli") is False
    assert is_stream_error(STREAM_ERROR_LINE, "some-new-cli") is False


def test_the_watchdog_verdict_for_a_quiet_gateway_is_a_lab_failure():
    """The two halves of the module must agree end to end.

    `awaiting_model` chooses `model_silent`, which `tasks._model_silence_failure`
    renders with the "No model response:" prefix, which this taxonomy files as
    `lab` and the leaderboard therefore excludes.
    """
    assert classify_failure("No model response: the endpoint accepted") == "lab"
    assert is_unmeasured("No model response: the endpoint accepted") is True


# Verbatim tail of todo-web-v1-K-001 (2026-10-23): the gateway had no card for
# the model, the CLI died on its first request, and the run list called it an
# "agent error" — a failure charged to a model that was never asked anything.
REJECTED_MODEL_TAIL = (
    "essages: Invalid model name passed in model=claude-haiku-5-5. Call "
    "`/v1/models` to view available models for your key.\n"
    "# result: success turns=1 cost=$0.0000 duration_ms=599\n"
    "  ⚠️  Exiting non-zero: claude failed (stop_reason=harness_error). This run "
    "measured the connection, not the subject."
)


def test_a_model_the_provider_rejects_is_a_lab_failure_not_an_agent_error():
    assert classify_failure(REJECTED_MODEL_TAIL) == "lab"
    assert failure_label(REJECTED_MODEL_TAIL) == "model not served"
    assert is_unmeasured(REJECTED_MODEL_TAIL)


def test_a_harness_error_exit_is_never_charged_to_the_agent():
    tail = (
        "API Error: 401 authentication_error\n"
        "  ⚠️  Exiting non-zero: claude failed (stop_reason=harness_error). This run "
        "measured the connection, not the subject."
    )
    assert classify_failure(tail) == "lab"
    assert failure_label(tail) == "harness error"


def test_a_cli_too_old_for_the_model_is_named_as_such():
    tail = (
        'AnthropicException - {"type":"error","error":{"type":"invalid_request_error",'
        '"message":"Claude Code 2.1.197 does not support this model; version 2.1.280 '
        'or newer is required.","details":{"error_code":"claude_code_version_too_old"}}}\n'
        "  ⚠️  Exiting non-zero: claude failed (stop_reason=harness_error)."
    )
    assert classify_failure(tail) == "lab"
    assert failure_label(tail) == "CLI too old"


def test_a_gateway_outage_mid_run_is_named_as_the_endpoint():
    """eda-ml-iris-categ-numerico-K-005 (2026-10-23): the gateway's origin went
    down behind Cloudflare (521) after thirteen turns."""
    tail = (
        "  ⚠️  Exiting non-zero: claude failed (stop_reason=harness_error). This run "
        "measured the connection, not the subject.\n"
        '  Cause: API Error: 521 {"type":"https://developers.cloudflare.com/support/'
        'troubleshooting/http-status-codes/cloudflare-5xx-errors/error-521/",'
        '"title":"Error 521: Web server is down","status":521'
    )
    assert classify_failure(tail) == "lab"
    assert failure_label(tail) == "endpoint down"


@pytest.mark.parametrize(
    "status", ["500", "502", "503", "504", "520", "522", "524", "529"]
)
def test_server_side_api_errors_are_the_endpoint(status):
    assert (
        failure_label(f"Cause: API Error: {status} upstream failure") == "endpoint down"
    )


def test_a_client_error_is_not_called_an_outage():
    assert failure_label("Cause: API Error: 400 bad request\n") != "endpoint down"
