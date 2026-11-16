"""Why a run failed, read from the evidence it left behind.

A failed run's verdict is one line, and the evidence that explains it sits in
``steps/step-N.events.jsonl``: megabytes of raw stream-json nobody reads from
the run page. racing3d-v1-K-004-C-001 is the case that motivated this module.
Its verdict said the agent was "blocked in a tool call that will never return",
and the events said something else. The command had been running a legitimate
eight-minute playtest, every test had passed just before, and the usage limit
was ``allowed``. The operator could not tell a retry from a wasted hour.

Two layers, kept apart on purpose:

* :func:`failure_evidence` is deterministic and free. It condenses the files
  into the few facts a diagnosis turns on: each recent command and how long it
  ran, the command the run was inside when it stopped, the usage-limit state,
  the API errors the harness reported, and the task. The run page shows it
  for every failed run.
* :func:`diagnose` hands that evidence, never the raw log, to a model on
  request and caches the answer in ``data/runs/<id>/diagnosis.json``, keyed by
  the attempt. It is advice for the operator. It never touches a score, a
  verdict or the run's measurement.

Everything that leaves the process goes through
:func:`harness_lab_app.observability.redaction.redact`: command output routinely
echoes environment variables.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from harness_lab_app import llm_transport, run_failures, run_progress
from harness_lab_app.config import read_env_file_value
from harness_lab_app.observability.redaction import redact

if TYPE_CHECKING:
    from pathlib import Path

DIAGNOSIS_FILE = "diagnosis.json"

#: Commands kept from the end of the run. Enough to see what led up to the
#: failure, few enough to keep the model's input small.
RECENT_COMMANDS = 12
_COMMAND_CHARS = 300
_RESULT_CHARS = 400
_LOG_TAIL_LINES = 40
_TASK_CHARS = 2000
_REASON_CHARS = 2000
DIAGNOSIS_TIMEOUT_SECONDS = 180

_STEP_FILE_RE = re.compile(r"^step-(\d+)\.(?:events\.jsonl|log)$")


class DiagnosisUnavailable(RuntimeError):
    """No model could be asked. Nothing is invented in its place."""


# --- evidence ---------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    """An aware UTC datetime, from an event stamp or a database column."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # The database stores naive UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 1)


def _clip(text: str, limit: int, *, tail: bool = False) -> str:
    text = redact(" ".join(str(text).split()) if not tail else str(text).strip())
    if len(text) <= limit:
        return text
    return "…" + text[-limit:] if tail else text[:limit] + "…"


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _step_numbers(steps_dir: Path) -> list[int]:
    if not steps_dir.is_dir():
        return []
    numbers = {
        int(match.group(1))
        for path in steps_dir.iterdir()
        if (match := _STEP_FILE_RE.match(path.name))
    }
    return sorted(numbers)


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        handle = path.open(errors="replace")
    except OSError:
        return events
    with handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _step_summary(steps_dir: Path, number: int) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        meta = json.loads((steps_dir / f"step-{number}.meta.json").read_text())
    events = steps_dir / f"step-{number}.events.jsonl"
    activity = (
        run_progress.step_activity(events)
        if events.is_file()
        else run_progress.log_activity(steps_dir / f"step-{number}.log")
    )
    return {
        "step": number,
        # A step's meta is written only when it ends normally.
        "finished": bool(meta),
        "seconds": meta.get("seconds"),
        "turns": activity.get("turns"),
        "output_tokens": activity.get("output_tokens"),
        "cost_usd": activity.get("cost_usd"),
    }


def _scan_events(
    steps_dir: Path, numbers: list[int], run_end: datetime | None
) -> dict[str, Any]:
    """Commands, usage-limit state and API errors, over every step's events."""
    commands: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    limits: list[dict[str, Any]] = []
    api_errors: list[dict[str, Any]] = []
    for number in numbers:
        for event in _read_events(steps_dir / f"step-{number}.events.jsonl"):
            kind = event.get("type")
            stamp = _parse_time(event.get("_received_at"))
            content = (event.get("message") or {}).get("content")
            if kind == "assistant" and isinstance(content, list):
                for item in content:
                    if not (isinstance(item, dict) and item.get("type") == "tool_use"):
                        continue
                    data = item.get("input") or {}
                    detail = (
                        data.get("command")
                        or data.get("file_path")
                        or data.get("pattern")
                        or ""
                    )
                    call: dict[str, Any] = {
                        "step": number,
                        "tool": str(item.get("name") or "tool"),
                        "command": _clip(detail, _COMMAND_CHARS),
                        "started_at": stamp,
                        "seconds": None,
                        "result": None,
                    }
                    commands.append(call)
                    by_id[str(item.get("id"))] = call
            elif kind == "user" and isinstance(content, list):
                for item in content:
                    if not (
                        isinstance(item, dict) and item.get("type") == "tool_result"
                    ):
                        continue
                    answered = by_id.pop(str(item.get("tool_use_id")), None)
                    if answered is not None:
                        answered["seconds"] = _seconds(answered["started_at"], stamp)
                        answered["result"] = _clip(
                            _result_text(item.get("content")), _RESULT_CHARS, tail=True
                        )
            elif kind == "rate_limit_event":
                info = event.get("rate_limit_info") or {}
                limits.append(info)
            elif kind == "result" and event.get("is_error"):
                status = event.get("api_error_status")
                api_errors.append(
                    {
                        "step": number,
                        "status": str(status) if status is not None else None,
                        "message": _clip(event.get("result") or "", _RESULT_CHARS),
                    }
                )
    # The oldest call still open when the stream ended: what the run was in.
    open_call = None
    if by_id:
        first = next(iter(by_id.values()))
        open_call = {
            "step": first["step"],
            "tool": first["tool"],
            "command": first["command"],
            "seconds": _seconds(first["started_at"], run_end),
        }
    usage_limit = None
    if limits:
        statuses = [str(info.get("status") or "") for info in limits]
        usage_limit = {
            # One refusal anywhere is the fact that matters.
            "status": "rejected" if "rejected" in statuses else statuses[-1],
            "overage": any(bool(info.get("isUsingOverage")) for info in limits),
        }
    recent = [
        {key: value for key, value in call.items() if key != "started_at"}
        for call in commands[-RECENT_COMMANDS:]
    ]
    return {
        "recent_commands": recent,
        "longest_wait": _longest_wait(commands, run_end),
        "open_command": open_call,
        "usage_limit": usage_limit,
        "api_errors": api_errors,
    }


#: How Claude Code answers a Bash call that outlived its cap.
_BACKGROUNDED_MARKER = "moved to the background"


def _longest_wait(
    commands: list[dict[str, Any]], run_end: datetime | None
) -> dict[str, Any] | None:
    """The one tool call the run waited on longest, open calls included.

    This, not the call open at the very end, is what the stall clock measured.
    Stopping a container takes seconds, and an agent can open a new command in
    between (racing3d-v1-K-004-C-001), which then looks like the culprit.
    """
    longest: dict[str, Any] | None = None
    for call in commands:
        seconds = call["seconds"]
        if seconds is None:
            seconds = _seconds(call["started_at"], run_end)
        if seconds is None or (longest is not None and seconds <= longest["seconds"]):
            continue
        longest = {
            "step": call["step"],
            "tool": call["tool"],
            "command": call["command"],
            "seconds": seconds,
            "answered": call["result"] is not None,
            "backgrounded": _BACKGROUNDED_MARKER in (call["result"] or ""),
        }
    return longest


def _task_excerpt(run_dir: Path) -> str:
    for name in ("_prompt_source.md", "prompt.md"):
        try:
            text = (run_dir / name).read_text(errors="replace")
        except OSError:
            continue
        start = text.find("## Your task")
        return _clip(text[start:] if start >= 0 else text, _TASK_CHARS, tail=False)
    return ""


def _log_tail(steps_dir: Path, numbers: list[int]) -> str:
    if not numbers:
        return ""
    try:
        lines = (steps_dir / f"step-{numbers[-1]}.log").read_text(errors="replace")
    except OSError:
        return ""
    return redact("\n".join(lines.splitlines()[-_LOG_TAIL_LINES:]).strip())


def failure_evidence(run_dir: Path, facts: dict[str, Any]) -> dict[str, Any]:
    """The facts a diagnosis of this run turns on, condensed from its files.

    ``facts`` is what the database knows about the run (state, failure reason,
    harness, model, timestamps, limits). Missing files give empty sections,
    never an error: a run that died before writing anything still has a
    failure reason worth reading.
    """
    steps_dir = run_dir / "steps"
    numbers = _step_numbers(steps_dir)
    started = _parse_time(facts.get("started_at"))
    ended = _parse_time(facts.get("ended_at"))
    duration = _seconds(started, ended)
    reason = str(facts.get("failure_reason") or "")
    run = {
        **{key: value for key, value in facts.items() if key != "failure_reason"},
        "failure_reason": _clip(reason, _REASON_CHARS, tail=False),
        "failure_kind": run_failures.classify_failure(reason) if reason else None,
        "duration_minutes": round(duration / 60, 1) if duration is not None else None,
    }
    return {
        "run": run,
        "task_excerpt": _task_excerpt(run_dir),
        "steps": [_step_summary(steps_dir, number) for number in numbers],
        **_scan_events(steps_dir, numbers, ended),
        "log_tail": _log_tail(steps_dir, numbers),
    }


# --- the model's reading ----------------------------------------------------

_LANGUAGES = {"en": "English", "es": "Spanish (Rioplatense, using voseo)"}

_SYSTEM = """\
You diagnose why a run of an AI coding agent failed in an evaluation lab, for \
the operator who launched it. You get a JSON summary of the evidence: the run's \
facts and failure reason, the task, each step, the most recent commands with \
how long each ran and the tail of its output, the command still open when the \
run stopped, the usage-limit state, API errors, and the log tail.

How the lab stops a run:
- "Run stalled": no output for `limits.stall_seconds` while no model request was \
in flight. The agent was inside a tool call; it may be hung, or a long command.
- "No model response": a model request got no answer for `limits.model_wait_seconds`.
- "Model endpoint failing" / API errors: the provider or gateway refused requests \
(429 rate limit, 5xx, unknown model).
- "Run timed out" / "Phase too slow": the total or per-phase time budget ran out.
- Lab failures (executor error, worker restarted, lease lost) are not about the agent.
How to weigh the evidence:
- The failure reason is the lab's verdict, written before this evidence was \
read; older verdicts overstate. Check it against the facts, and say so when they \
disagree.
- `longest_wait` is the tool call the run waited on longest: the silence a stall \
measures. `open_command` is only what was running at the very end; stopping takes \
seconds, and the agent may open a new command in between, so a short open \
command is usually not the cause.
- Claude Code caps one Bash call at 600 s, then moves it to the background and \
answers ("moved to the background"). A wait that ends that way is a long command, \
not a hung one. If the stall budget is not clearly longer than the longest \
legitimate command, the lab's configuration stopped a healthy run.
- `usage_limit.status` "rejected" means the plan's quota ran out.
Retries: "resume" reruns all steps over the current workspace; "scratch" starts \
clean; "continue" keeps finished steps (only when a step finished). A \
continuation is a new run seeded from this one's workspace with a new prompt.

Answer in {language}, in Markdown, with these sections and nothing else:
## Cause
One or two sentences. Name the cause; don't just restate the verdict.
## Evidence
Bullets that quote the specific facts (commands, durations, statuses).
## Whose issue
One of: the prompt, the agent, the lab configuration, the provider. One line why.
## Will a retry work?
Yes / No / Partly, and why. Say which retry mode or a continuation fits.
## What to do
Concrete next steps: settings to change, how to split or narrow the prompt. If \
a continuation fits, give a ready-to-paste follow-up prompt in a code block.

Use only the evidence. If it does not settle a point, say so rather than guess.\
"""


def _attempt_key(facts: dict[str, Any]) -> str:
    """Which failure this is. A retried run reuses its id, not its failure."""
    raw = json.dumps(
        [str(facts.get("ended_at") or ""), str(facts.get("failure_reason") or "")]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def diagnosis_model() -> str:
    """``DIAGNOSIS_MODEL``, else the auditor's model, else the transport default."""
    for name in ("DIAGNOSIS_MODEL", "AUDITOR_MODEL"):
        chosen = os.environ.get(name) or read_env_file_value(name)
        if chosen:
            return chosen
    return llm_transport.default_audit_model()


def cached_diagnosis(
    run_dir: Path, facts: dict[str, Any], lang: str | None = None
) -> dict[str, Any] | None:
    """The stored reading of this very failure, if there is one."""
    try:
        stored = json.loads((run_dir / DIAGNOSIS_FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("attempt") != _attempt_key(facts):
        return None
    diagnosis = stored.get("diagnosis")
    if not isinstance(diagnosis, dict):
        return None
    if lang is not None and diagnosis.get("lang") != lang:
        return None
    return diagnosis


def diagnose(
    run_dir: Path,
    facts: dict[str, Any],
    *,
    lang: str = "en",
    refresh: bool = False,
) -> dict[str, Any]:
    """Ask a model why this run failed, once per failure.

    Raises :class:`DiagnosisUnavailable` when no model answers; nothing is
    cached then, so the next request tries again.
    """
    lang = lang if lang in _LANGUAGES else "en"
    if not refresh:
        cached = cached_diagnosis(run_dir, facts, lang)
        if cached is not None:
            return cached
    evidence = failure_evidence(run_dir, facts)
    model = diagnosis_model()
    try:
        reply = llm_transport.chat_reply(
            model,
            _SYSTEM.format(language=_LANGUAGES[lang]),
            json.dumps(evidence, ensure_ascii=False, indent=1, default=str),
            timeout=DIAGNOSIS_TIMEOUT_SECONDS,
            temperature=0.2,
            trace_kind="diagnosis",
            trace_metadata={"run_id": str(facts.get("run_id") or "")},
        )
    except llm_transport.TransportError as exc:
        raise DiagnosisUnavailable(str(exc)) from exc
    text = reply.text.strip()
    if not text:
        raise DiagnosisUnavailable("The model returned an empty answer.")
    diagnosis = {
        "text": text,
        "model": reply.model,
        "transport": reply.transport,
        "lang": lang,
        "created_at": datetime.now(UTC).isoformat(),
    }
    with contextlib.suppress(OSError):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / DIAGNOSIS_FILE).write_text(
            json.dumps(
                {"attempt": _attempt_key(facts), "diagnosis": diagnosis}, indent=2
            )
        )
    return diagnosis
