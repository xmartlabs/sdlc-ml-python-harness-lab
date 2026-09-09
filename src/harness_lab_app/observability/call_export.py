"""Trace one in-process LLM call: one Langfuse trace, one `generation` span.

`run_export` reconstructs traces after the fact, from artifacts a run already
wrote to disk. Not every LLM call in this lab belongs to a run, though — the
repository harness audit, the case draft assistant, the experiment audit and
the judges all fire outside the runner container, and today none of them leave
a trace anywhere. This module is the per-call counterpart: it is called once,
synchronously, right after (or instead of) an answer comes back, so it needs no
artifact to read and produces exactly one trace with exactly one observation.

Shaped like :func:`harness_lab_app.observability.run_export.export_run`: pure
builder plus a thin send, never raises, and returns a small result whose
``reason`` explains any no-op so a caller can log the truth instead of
guessing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness_lab_app.observability.config import LangfuseConfig
from harness_lab_app.observability.events import (
    build_payload,
    build_span,
    span_id,
    trace_id,
)
from harness_lab_app.observability.redaction import redact

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_lab_app.observability.client import LangfuseTransport

# A per-call prompt can carry a whole CLAUDE.md or a repository's rule set;
# these caps keep one trace a few KB rather than letting a single audit prompt
# dominate the payload. Mirrors run_export's `_clip_head`/`_clip_tail` shape
# (a visible truncation marker), just tighter — this fires on every call, not
# once per run.
SYSTEM_LIMIT = 4_000
USER_LIMIT = 4_000
OUTPUT_LIMIT = 4_000


def _clip_head(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…[clipped {len(text) - limit} chars]"


@dataclass(frozen=True)
class CallExportResult:
    """What happened when a single call was (or was not) traced."""

    ok: bool
    trace_id: str = ""
    session_id: str = ""
    reason: str = ""


def export_call(
    kind: str,
    *,
    model: str,
    system: str,
    user: str,
    start_ns: int,
    end_ns: int,
    output: str | None = None,
    error: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    config: LangfuseConfig | None = None,
    transport: LangfuseTransport | None = None,
) -> CallExportResult:
    """Build and send one trace for a single completion. Never raises.

    ``kind`` is both the trace name and the tag alongside ``llm-call`` (e.g.
    ``"repository_audit"``, ``"case_draft"``, ``"experiment_audit"``,
    ``"judge"``, ``"summary"``) — it is what lets an operator filter these
    apart from the per-run traces in the same project.

    Exactly one of ``output``/``error`` should be given: a call that raised is
    traced at ``level="ERROR"`` with the exception text and no output, because
    a failed audit is exactly what someone opens Langfuse to look at.
    """
    config = config or LangfuseConfig.from_env()
    if not config.enabled:
        return CallExportResult(
            ok=False, reason=config.disabled_reason or "Langfuse is disabled."
        )

    # Ids must be unique per call: two audits of the same repository (same
    # model, same kind) must not collapse into one trace, so the seed is
    # salted with a uuid4 rather than derived from the model/kind alone.
    ident = f"{kind}:{uuid.uuid4()}"
    tid = trace_id(ident, "trace")
    sid = span_id(ident, "span")
    # One Langfuse session per *kind*, not per call: every repository audit
    # (etc.) lands in the same session, so it is browsable as a group rather
    # than as one-off, unrelated traces.
    session = config.session_id(kind)

    failed = error is not None
    clean_metadata = {
        key: value for key, value in (metadata or {}).items() if value not in (None, "")
    }
    span = build_span(
        trace=tid,
        span=sid,
        name=kind,
        trace_name=kind,
        start_ns=start_ns,
        end_ns=end_ns,
        observation_type="generation",
        session_id=session,
        tags=["llm-call", kind],
        environment=config.environment,
        input_value={
            "system": redact(_clip_head(system, SYSTEM_LIMIT)) if system else "",
            "user": redact(_clip_head(user, USER_LIMIT)) if user else "",
        },
        output_value=redact(_clip_head(output, OUTPUT_LIMIT)) if output else None,
        metadata=clean_metadata,
        level="ERROR" if failed else None,
        status_message=error if failed else None,
        model=model or None,
    )

    try:
        from harness_lab_app.observability.client import get_transport

        transport = transport or get_transport(config)
        sent = transport.send(build_payload([span], environment=config.environment))
    except Exception as exc:  # observability must never break its caller
        return CallExportResult(
            ok=False,
            trace_id=tid,
            session_id=session,
            reason=f"Could not reach Langfuse: {exc!r}",
        )
    return CallExportResult(
        ok=sent.ok, trace_id=tid, session_id=session, reason=sent.reason
    )
