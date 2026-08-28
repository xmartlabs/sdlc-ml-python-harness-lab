"""Reconstruct a run's Langfuse traces from `data/runs/<run_id>/`.

Everything a trace needs is already on disk when a run finishes — the prompt
per step, the harness log with its `# <harness> run started <iso>Z` header, the
usage totals, the diff and the report — so the exporter reads the run directory
rather than instrumenting the hot path. Two things follow from that:

* an export can never hang or fail a run, and
* **every run already on disk is exportable**, so turning the integration on
  backfills history instead of starting from empty.

Shape (plan decision 2): one Langfuse **session per run**, one **trace per
step**, plus a `run:<id>` trace for run-level work so provisioning, validation
and scoring never distort step timings. A single-prompt case therefore yields
exactly one step trace; a ten-step case yields ten, in the same session.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_lab_app.observability.config import LangfuseConfig
from harness_lab_app.observability.events import (
    as_text,
    build_payload,
    build_span,
    span_id,
    trace_id,
)
from harness_lab_app.observability.redaction import redact

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_lab_app.observability.client import LangfuseTransport

# Payload caps. Langfuse stores large strings happily; the point is to keep one
# run's export a few hundred KB rather than a few hundred MB when a harness log
# runs to 40k lines.
INPUT_LIMIT = 24_000
OUTPUT_LIMIT = 24_000
DIFF_LIMIT = 24_000

_HEADER_RE = re.compile(r"^#\s+(\S+)\s+run started\s+(\S+?)Z?\s*$", re.MULTILINE)
# opencode writes `timestamp=<iso>Z` on every line; the last one is the closest
# thing to a real end time the log carries.
_LOG_TS_RE = re.compile(r"timestamp=(\d{4}-\d{2}-\d{2}T[\d:.]+)Z")
_STEP_LOG_RE = re.compile(r"^step-(\d+)\.log$")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _clip_head(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[clipped {len(text) - limit} chars]"


def _clip_tail(text: str, limit: int) -> str:
    """Keep the *end* of a log: that is where the outcome is."""
    if len(text) <= limit:
        return text
    return f"…[clipped {len(text) - limit} leading chars]\n{text[-limit:]}"


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().rstrip("Z")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _to_ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


@dataclass(frozen=True)
class StepFacts:
    """One iteration of the run loop, as recoverable from disk."""

    number: int
    log_path: Path
    prompt: str
    log: str
    start: datetime
    end: datetime
    harness: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExportResult:
    run_id: str
    session_id: str
    ok: bool
    spans: int = 0
    traces: int = 0
    reason: str = ""
    session_url: str = ""


def _step_log_paths(run_dir: Path) -> list[tuple[int, Path]]:
    """Step logs in order, falling back to the single-step `opencode.log`."""
    steps_dir = run_dir / "steps"
    found: list[tuple[int, Path]] = []
    if steps_dir.is_dir():
        for path in steps_dir.iterdir():
            match = _STEP_LOG_RE.match(path.name)
            if match:
                found.append((int(match.group(1)), path))
    if found:
        return sorted(found)
    combined = run_dir / "opencode.log"
    # `opencode.log` is written for every harness; multi-step runs also append
    # the combined transcript to it, which is why the per-step logs win above.
    return [(1, combined)] if combined.is_file() else []


def _step_prompt(run_dir: Path, number: int) -> str:
    """The prompt actually sent for this step.

    Step 1 of a multi-step run reuses the case prompt (`prompt.md`); only
    continuations get their own `steps/step-N-prompt.md`.
    """
    per_step = run_dir / "steps" / f"step-{number}-prompt.md"
    if per_step.is_file():
        return _read_text(per_step)
    if number == 1:
        return _read_text(run_dir / "prompt.md")
    return ""


def _log_window(
    log_text: str, log_path: Path
) -> tuple[datetime | None, datetime | None]:
    """(start, end) recovered from a harness log."""
    start = None
    header = _HEADER_RE.search(log_text)
    if header is not None:
        start = _parse_iso(header.group(2))
    stamps = _LOG_TS_RE.findall(log_text)
    end = _parse_iso(stamps[-1]) if stamps else None
    if end is None:
        # No in-log clock (the claude harness writes none): the log file's last
        # write is when the step stopped producing output.
        end = _mtime(log_path)
    return start, end


def _collect_steps(run_dir: Path, metadata: Mapping[str, Any]) -> list[StepFacts]:
    harness = str(metadata.get("harness") or "")
    fallback_start = _parse_iso(str(metadata.get("created_at") or "")) or datetime.now(
        UTC
    )
    raw: list[tuple[int, Path, str, datetime, datetime | None]] = []
    for number, path in _step_log_paths(run_dir):
        text = _read_text(path)
        start, end = _log_window(text, path)
        raw.append((number, path, text, start or fallback_start, end))

    steps: list[StepFacts] = []
    for index, (number, path, text, start, end) in enumerate(raw):
        # A step's true end is bounded by the next step's start; use it when the
        # log gave us nothing better, and never let a clipped clock run backwards.
        next_start = raw[index + 1][3] if index + 1 < len(raw) else None
        resolved_end = end or next_start or start
        if next_start is not None and resolved_end > next_start:
            resolved_end = next_start
        if resolved_end < start:
            resolved_end = start
        header = _HEADER_RE.search(text)
        steps.append(
            StepFacts(
                number=number,
                log_path=path,
                prompt=_step_prompt(run_dir, number),
                log=text,
                start=start,
                end=resolved_end,
                harness=(header.group(1) if header else harness),
                metadata={
                    "step": number,
                    "log_bytes": len(text),
                    "log_path": str(path.relative_to(run_dir)),
                },
            )
        )
    return steps


def run_tags(metadata: Mapping[str, Any]) -> list[str]:
    """The tag vocabulary `harnesses/metadata-standard.md` defines."""
    pairs = (
        ("benchmark", metadata.get("benchmark_id") or metadata.get("case")),
        ("harness", metadata.get("harness")),
        ("method", metadata.get("methodology")),
        ("planner", metadata.get("planner_model")),
        ("executor", metadata.get("executor_model")),
        ("prompt", metadata.get("prompt_variant")),
    )
    return [f"{key}:{value}" for key, value in pairs if value]


def _run_metadata(
    metadata: Mapping[str, Any],
    usage: Mapping[str, Any],
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_id": metadata.get("run_id"),
        "case": metadata.get("case"),
        "benchmark_id": metadata.get("benchmark_id"),
        "cell_id": metadata.get("cell_id"),
        "harness": metadata.get("harness"),
        "methodology": metadata.get("methodology"),
        "planner_model": metadata.get("planner_model"),
        "executor_model": metadata.get("executor_model"),
        "prompt_variant": metadata.get("prompt_variant"),
        "score_mode": metadata.get("score_mode"),
        "max_steps": metadata.get("max_steps"),
        "base_commit": metadata.get("base_commit"),
        "condition_hash": metadata.get("condition_hash"),
        "skills_ablation": metadata.get("skills_ablation"),
        "continues_run": metadata.get("continues_run"),
    }
    for key in (
        "turns",
        "steps",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "tokens_reasoning",
        "cost_usd",
        "time_minutes",
    ):
        if usage.get(key) is not None:
            out[f"usage_{key}"] = usage[key]
    for key in ("state", "exit_code", "failure_reason", "score", "interventions"):
        if extra.get(key) is not None:
            out[key] = extra[key]
    return {key: value for key, value in out.items() if value not in (None, "", [])}


def _usage_details(usage: Mapping[str, Any]) -> dict[str, int]:
    details: dict[str, int] = {}
    for source, target in (("input_tokens", "input"), ("output_tokens", "output")):
        value = usage.get(source)
        if isinstance(value, int) and value > 0:
            details[target] = value
    return details


def _run_level(extra: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(level, status_message) for the run envelope span."""
    failure = extra.get("failure_reason")
    exit_code = extra.get("exit_code")
    state = str(extra.get("state") or "")
    failed = bool(failure) or state == "failed" or bool(exit_code)
    if not failed:
        return None, None
    message = str(failure or f"exit code {exit_code}" or state)
    return "ERROR", message


def build_run_spans(
    run_dir: Path,
    *,
    config: LangfuseConfig,
    extra: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every OTLP span for one run: the `run:<id>` envelope plus one per step.

    Pure: it reads the run directory and returns dictionaries. Nothing here
    touches the network, so the whole trace shape is assertable in a test.
    """
    run_dir = Path(run_dir)
    extra = dict(extra or {})
    metadata = _load_json(run_dir / "metadata.json")
    usage = _load_json(run_dir / "usage.json")
    run_id = str(metadata.get("run_id") or extra.get("run_id") or run_dir.name)
    session = config.session_id(run_id)
    tags = run_tags(metadata)
    env = config.environment

    steps = _collect_steps(run_dir, metadata)
    spans: list[dict[str, Any]] = []

    for step in steps:
        step_trace = trace_id(run_id, f"step:{step.number}")
        spans.append(
            build_span(
                trace=step_trace,
                span=span_id(run_id, f"step:{step.number}"),
                name=f"step-{step.number}",
                trace_name=f"{run_id} · step {step.number}",
                start_ns=_to_ns(step.start),
                end_ns=_to_ns(step.end),
                session_id=session,
                tags=tags,
                environment=env,
                input_value=redact(_clip_head(step.prompt, INPUT_LIMIT)),
                output_value=redact(_clip_tail(step.log, OUTPUT_LIMIT)),
                metadata={
                    **step.metadata,
                    "harness": step.harness,
                    "run_id": run_id,
                    "executor_model": metadata.get("executor_model"),
                },
                model=str(metadata.get("executor_model") or "") or None,
            )
        )

    run_trace = trace_id(run_id, "run")
    run_span = span_id(run_id, "run")
    run_start = (
        steps[0].start
        if steps
        else _parse_iso(str(metadata.get("created_at") or "")) or datetime.now(UTC)
    )
    run_end = steps[-1].end if steps else run_start
    level, status_message = _run_level(extra)
    spans.append(
        build_span(
            trace=run_trace,
            span=run_span,
            name=f"run:{run_id}",
            trace_name=f"{run_id} · run",
            start_ns=_to_ns(run_start),
            end_ns=_to_ns(run_end),
            session_id=session,
            tags=tags,
            environment=env,
            input_value=redact(
                _clip_head(_read_text(run_dir / "prompt.md"), INPUT_LIMIT)
            ),
            output_value=redact(
                _clip_tail(_read_text(run_dir / "report.md"), OUTPUT_LIMIT)
            ),
            metadata=_run_metadata(metadata, usage, extra),
            level=level,
            status_message=status_message,
            model=str(metadata.get("executor_model") or "") or None,
            usage_details=_usage_details(usage),
        )
    )
    spans.extend(
        _run_child_spans(
            run_dir,
            run_id=run_id,
            trace=run_trace,
            parent=run_span,
            environment=env,
            start=run_end,
            extra=extra,
        )
    )
    return spans


def _run_child_spans(
    run_dir: Path,
    *,
    run_id: str,
    trace: str,
    parent: str,
    environment: str,
    start: datetime,
    extra: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Run-level artifacts that exist after the agent stopped.

    They carry no clock of their own, so they are pinned to the end of the run
    as zero-length observations rather than invented durations.
    """
    at = _to_ns(start)
    spans: list[dict[str, Any]] = []

    diff = _read_text(run_dir / "diff.patch")
    if diff:
        spans.append(
            build_span(
                trace=trace,
                span=span_id(run_id, "diff"),
                name="diff",
                parent=parent,
                observation_type="event",
                start_ns=at,
                end_ns=at,
                environment=environment,
                output_value=redact(_clip_head(diff, DIFF_LIMIT)),
                metadata={
                    "diff_bytes": len(diff),
                    "diff_lines": diff.count("\n") + 1,
                },
            )
        )

    findings = _load_json(run_dir / "audit_findings.json")
    if findings:
        spans.append(
            build_span(
                trace=trace,
                span=span_id(run_id, "audit"),
                name="audit",
                parent=parent,
                observation_type="event",
                start_ns=at,
                end_ns=at,
                environment=environment,
                output_value=redact(as_text(findings)),
            )
        )

    validation = extra.get("validation")
    if validation:
        spans.append(
            build_span(
                trace=trace,
                span=span_id(run_id, "validation"),
                name="validation",
                parent=parent,
                observation_type="event",
                start_ns=at,
                end_ns=at,
                environment=environment,
                output_value=redact(as_text(validation)),
            )
        )
    return spans


def build_run_payload(
    run_dir: Path,
    *,
    config: LangfuseConfig,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The complete OTLP request body for one run."""
    spans = build_run_spans(run_dir, config=config, extra=extra)
    return build_payload(spans, environment=config.environment)


def export_run(
    run_dir: Path,
    *,
    config: LangfuseConfig | None = None,
    transport: LangfuseTransport | None = None,
    extra: Mapping[str, Any] | None = None,
) -> ExportResult:
    """Build and send one run's traces. Never raises.

    Returns an :class:`ExportResult` whose ``reason`` explains any no-op, so
    callers can log the truth ("disabled", "no run directory") instead of
    guessing.
    """
    from harness_lab_app.observability.client import get_transport

    run_dir = Path(run_dir)
    config = config or LangfuseConfig.from_env()
    run_id = run_dir.name
    session = config.session_id(run_id)

    if not config.enabled:
        return ExportResult(
            run_id=run_id,
            session_id=session,
            ok=False,
            reason=config.disabled_reason or "Langfuse is disabled.",
        )
    if not run_dir.is_dir():
        return ExportResult(
            run_id=run_id,
            session_id=session,
            ok=False,
            reason=f"No run directory at {run_dir}.",
        )

    try:
        spans = build_run_spans(run_dir, config=config, extra=extra)
    except Exception as exc:  # observability must never break its caller
        return ExportResult(
            run_id=run_id,
            session_id=session,
            ok=False,
            reason=f"Could not build spans: {exc!r}",
        )
    # No emptiness guard: a run that died before its first step still yields
    # the `run:<id>` envelope, and that failure is exactly what an operator
    # opens Langfuse to see.
    transport = transport or get_transport(config)
    sent = transport.send(build_payload(spans, environment=config.environment))
    return ExportResult(
        run_id=run_id,
        session_id=session,
        ok=sent.ok,
        spans=len(spans),
        traces=len({span["traceId"] for span in spans}),
        reason=sent.reason,
        session_url=config.session_url(run_id),
    )
