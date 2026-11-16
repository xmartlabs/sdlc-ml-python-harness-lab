"""Why a run failed, read from the evidence it left (run_diagnosis)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_lab_app import llm_transport, run_diagnosis
from harness_lab_app.run_diagnosis import (
    DiagnosisUnavailable,
    cached_diagnosis,
    diagnose,
    failure_evidence,
)


def _event(ts: str, kind: str, **body) -> str:
    return json.dumps({"_received_at": ts, "type": kind, **body}) + "\n"


def _tool_use(ts: str, tool_id: str, command: str) -> str:
    return _event(
        ts,
        "assistant",
        message={
            "id": f"msg-{tool_id}",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Bash",
                    "input": {"command": command},
                }
            ],
        },
    )


def _tool_result(ts: str, tool_id: str, text: str) -> str:
    return _event(
        ts,
        "user",
        message={
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": text}
            ]
        },
    )


def _stalled_run(tmp_path: Path) -> Path:
    """The shape of racing3d-v1-K-004-C-001: tests pass, then a long playtest."""
    run_dir = tmp_path / "racing3d-v1-K-004-C-001"
    steps = run_dir / "steps"
    steps.mkdir(parents=True)
    (run_dir / "_prompt_source.md").write_text(
        "## Your task\n\nquiero un estilo de mario kart\n"
    )
    (steps / "step-1.events.jsonl").write_text(
        _event(
            "2026-11-16T14:46:23Z",
            "rate_limit_event",
            rate_limit_info={"status": "allowed", "isUsingOverage": False},
        )
        + _tool_use(
            "2026-11-16T15:30:24Z", "t1", "godot --script res://tests/test_kart.gd"
        )
        + _tool_result(
            "2026-11-16T15:30:50Z", "t1", "== test_kart\nRESULT: PASS (0 failures)"
        )
        + _tool_use(
            "2026-11-16T15:30:50Z",
            "t2",
            "until grep -q EXIT play.log; do sleep 5; done",
        )
        + _event("2026-11-16T15:35:50Z", "tool_progress", tool_name="Bash")
        + _tool_result(
            "2026-11-16T15:40:50Z",
            "t2",
            "Command did not complete within its 600s timeout and was moved to the background",
        )
        + _tool_use(
            "2026-11-16T15:40:57Z", "t3", "godot-render --script res://tests/_fps.gd"
        )
    )
    (steps / "step-1.log").write_text(
        "→ Bash(godot-render --script res://tests/_fps.gd)\n"
    )
    return run_dir


def _run_facts(**overrides) -> dict:
    facts = {
        "run_id": "racing3d-v1-K-004-C-001",
        "state": "failed",
        "failure_reason": "Run stalled: no output for 10 minutes ...",
        "started_at": "2026-11-16T14:46:14",
        "ended_at": "2026-11-16T15:41:02",
        "harness": "claude",
        "executor_model": "claude-opus-5-5",
        "max_steps": 3,
        "parent_run_id": "racing3d-v1-K-004",
    }
    facts.update(overrides)
    return facts


# --- the evidence, without any model ---------------------------------------


def test_the_evidence_shows_how_long_each_recent_command_ran(tmp_path):
    evidence = failure_evidence(_stalled_run(tmp_path), _run_facts())

    commands = evidence["recent_commands"]
    assert [c["command"][:5] for c in commands] == ["godot", "until", "godot"]
    assert commands[0]["seconds"] == pytest.approx(26)
    assert commands[1]["seconds"] == pytest.approx(600)
    assert "moved to the background" in commands[1]["result"]


def test_the_evidence_names_the_command_the_run_was_inside_when_it_stopped(tmp_path):
    evidence = failure_evidence(_stalled_run(tmp_path), _run_facts())

    open_call = evidence["open_command"]
    assert open_call is not None
    assert "_fps.gd" in open_call["command"]
    # From the call's start to the run's end, not to the last event.
    assert open_call["seconds"] == pytest.approx(5, abs=1)
    assert evidence["recent_commands"][-1]["seconds"] is None


def test_the_evidence_names_the_longest_wait_and_the_command_behind_it(tmp_path):
    """What the stall clock measured, not whatever ran in the last seconds.

    In racing3d-v1-K-004-C-001 the container took seconds to stop after the
    watchdog decided, and the agent opened a new command in between. Read
    alone, that four-second call looked like the culprit, and a model blamed
    it. The ten-minute wait just before it was the real fact.
    """
    evidence = failure_evidence(_stalled_run(tmp_path), _run_facts())

    wait = evidence["longest_wait"]
    assert wait["seconds"] == pytest.approx(600)
    assert wait["command"].startswith("until grep")
    assert wait["backgrounded"] is True


def test_a_stall_verdict_is_offered_as_a_claim_to_check(tmp_path, monkeypatch):
    systems: list[str] = []

    def fake_chat_reply(model, system, user, **kwargs):
        systems.append(system)
        return llm_transport.ChatReply("ok", "gateway", model, model, 0.2, 0.2)

    monkeypatch.setattr(llm_transport, "chat_reply", fake_chat_reply)

    diagnose(_stalled_run(tmp_path), _run_facts(), lang="en")

    assert "background" in systems[0]
    assert "longest_wait" in systems[0]


def test_the_evidence_reads_the_usage_limit_state(tmp_path):
    evidence = failure_evidence(_stalled_run(tmp_path), _run_facts())

    assert evidence["usage_limit"] == {"status": "allowed", "overage": False}


def test_a_rejected_usage_limit_wins_over_earlier_allowed_ones(tmp_path):
    run_dir = _stalled_run(tmp_path)
    with (run_dir / "steps" / "step-1.events.jsonl").open("a") as handle:
        handle.write(
            _event(
                "2026-11-16T15:41:00Z",
                "rate_limit_event",
                rate_limit_info={"status": "rejected", "isUsingOverage": False},
            )
        )

    evidence = failure_evidence(run_dir, _run_facts())

    assert evidence["usage_limit"]["status"] == "rejected"


def test_the_evidence_carries_api_errors_the_harness_reported(tmp_path):
    run_dir = _stalled_run(tmp_path)
    with (run_dir / "steps" / "step-1.events.jsonl").open("a") as handle:
        handle.write(
            _event(
                "2026-11-16T15:41:00Z",
                "result",
                is_error=True,
                result="API Error: Server is temporarily limiting requests",
                api_error_status="429",
                num_turns=7,
            )
        )

    evidence = failure_evidence(run_dir, _run_facts())

    assert evidence["api_errors"] == [
        {
            "step": 1,
            "status": "429",
            "message": "API Error: Server is temporarily limiting requests",
        }
    ]


def test_the_evidence_quotes_the_task_and_the_run_facts(tmp_path):
    evidence = failure_evidence(_stalled_run(tmp_path), _run_facts())

    assert "mario kart" in evidence["task_excerpt"]
    assert evidence["run"]["failure_kind"] == "stalled"
    assert evidence["run"]["duration_minutes"] == pytest.approx(54.8, abs=0.1)
    assert evidence["steps"][0]["step"] == 1
    assert evidence["steps"][0]["finished"] is False


def test_secrets_in_command_output_never_reach_the_evidence(tmp_path):
    run_dir = _stalled_run(tmp_path)
    # Assembled at runtime so the secret scanner has no literal to flag.
    fake_key = "sk-" + "abcdefghijklmnop" + "1234"
    with (run_dir / "steps" / "step-1.events.jsonl").open("a") as handle:
        handle.write(_tool_use("2026-11-16T15:41:00Z", "t4", "env | grep TOKEN"))
        handle.write(
            _tool_result("2026-11-16T15:41:01Z", "t4", f"GATEWAY_TOKEN={fake_key}")
        )

    text = json.dumps(failure_evidence(run_dir, _run_facts()))

    assert fake_key not in text


def test_a_harness_without_an_event_stream_gets_its_log_tail(tmp_path):
    run_dir = tmp_path / "local-run"
    (run_dir / "steps").mkdir(parents=True)
    (run_dir / "steps" / "step-1.log").write_text(
        "".join(f"line {n}\n" for n in range(100)) + "Error: model not found\n"
    )

    evidence = failure_evidence(run_dir, _run_facts(harness="opencode"))

    assert evidence["recent_commands"] == []
    assert evidence["log_tail"].endswith("Error: model not found")
    assert "line 0\n" not in evidence["log_tail"]


def test_a_run_with_no_files_still_has_evidence(tmp_path):
    evidence = failure_evidence(tmp_path / "gone", _run_facts())

    assert evidence["steps"] == []
    assert evidence["open_command"] is None


# --- the model's reading, on demand and cached ------------------------------


def test_diagnose_sends_the_evidence_and_caches_the_answer(tmp_path, monkeypatch):
    run_dir = _stalled_run(tmp_path)
    calls: list[str] = []

    def fake_chat_reply(model, system, user, **kwargs):
        calls.append(user)
        return llm_transport.ChatReply(
            text="## Causa\nEl límite de inactividad cortó un playtest largo.",
            transport="gateway",
            requested_model=model,
            model=model,
            requested_temperature=0.2,
            temperature=0.2,
        )

    monkeypatch.setattr(llm_transport, "chat_reply", fake_chat_reply)
    facts = _run_facts()

    first = diagnose(run_dir, facts, lang="es")
    second = diagnose(run_dir, facts, lang="es")

    assert len(calls) == 1, "a second look at the same failure must not be paid again"
    assert "_fps.gd" in calls[0] and "mario kart" in calls[0]
    assert first["text"].startswith("## Causa")
    assert first["model"]
    assert second == first
    assert cached_diagnosis(run_dir, facts) == first


def test_a_new_failure_of_the_same_run_is_diagnosed_again(tmp_path, monkeypatch):
    """A retried run reuses its id; the old reading describes the old attempt."""
    run_dir = _stalled_run(tmp_path)
    answers = iter(["first attempt", "second attempt"])

    def fake_chat_reply(model, system, user, **kwargs):
        return llm_transport.ChatReply(next(answers), "gateway", model, model, 0.2, 0.2)

    monkeypatch.setattr(llm_transport, "chat_reply", fake_chat_reply)

    diagnose(run_dir, _run_facts(), lang="en")
    later = _run_facts(ended_at="2026-11-16T18:00:00")

    assert cached_diagnosis(run_dir, later) is None
    assert diagnose(run_dir, later, lang="en")["text"] == "second attempt"


def test_refresh_asks_the_model_again(tmp_path, monkeypatch):
    run_dir = _stalled_run(tmp_path)
    answers = iter(["one", "two"])

    def fake_chat_reply(model, system, user, **kwargs):
        return llm_transport.ChatReply(next(answers), "gateway", model, model, 0.2, 0.2)

    monkeypatch.setattr(llm_transport, "chat_reply", fake_chat_reply)

    diagnose(run_dir, _run_facts(), lang="en")

    assert diagnose(run_dir, _run_facts(), lang="en", refresh=True)["text"] == "two"


def test_the_answer_comes_in_the_language_asked_for(tmp_path, monkeypatch):
    systems: list[str] = []

    def fake_chat_reply(model, system, user, **kwargs):
        systems.append(system)
        return llm_transport.ChatReply("ok", "gateway", model, model, 0.2, 0.2)

    monkeypatch.setattr(llm_transport, "chat_reply", fake_chat_reply)

    diagnose(_stalled_run(tmp_path), _run_facts(), lang="es")

    assert "Spanish" in systems[0]


def test_no_model_means_no_diagnosis_rather_than_an_invented_one(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise llm_transport.TransportNotConfigured("no transport")

    monkeypatch.setattr(llm_transport, "chat_reply", unavailable)
    run_dir = _stalled_run(tmp_path)

    with pytest.raises(DiagnosisUnavailable, match="no transport"):
        diagnose(run_dir, _run_facts(), lang="en")
    assert cached_diagnosis(run_dir, _run_facts()) is None


def test_the_diagnosis_model_can_be_chosen(tmp_path, monkeypatch):
    used: list[str] = []

    def fake_chat_reply(model, system, user, **kwargs):
        used.append(model)
        return llm_transport.ChatReply("ok", "gateway", model, model, 0.2, 0.2)

    monkeypatch.setattr(llm_transport, "chat_reply", fake_chat_reply)
    monkeypatch.setenv("DIAGNOSIS_MODEL", "my-model")
    monkeypatch.setattr(run_diagnosis, "read_env_file_value", lambda _name: "")

    diagnose(_stalled_run(tmp_path), _run_facts(), lang="en")

    assert used == ["my-model"]
