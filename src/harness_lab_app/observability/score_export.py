"""Push the lab's scores onto the Langfuse traces they belong to.

The division of ownership is permanent and worth restating where it is
implemented: **the lab database is the source of truth for the experiment
domain; Langfuse is the evidence store for traces and telemetry.** Langfuse has
no concept of harness version, skill ablation, target repository, git ref or
sandbox fingerprint, and must not be asked to acquire one. What it does have is
a score model and analytics over it, which is worth borrowing for the handful of
numbers a human wants to filter and trend on.

Transport note, learned the hard way (ADR-0027): on Langfuse v4 the ingestion
endpoint rejects every event type except ``score-create`` and ``sdk-log``. That
rejection is the reason spans go over OTLP — and the reason scores do *not* need
to.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from harness_lab_app.observability.config import LangfuseConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_lab_app.observability.client import LangfuseTransport

# Scores worth pushing. Deliberately a short list rather than "everything":
# Langfuse analytics is for the numbers a human filters and trends on, and a
# hundred metrics per run makes that surface useless rather than richer.
EXPORTED_SCORES = (
    "task_validation_pass",
    "test_pass_rate",
    "forbidden_files_modified",
    "total_tokens",
    "cost_usd",
    "agent_steps",
    "skill_adherence",
)


@dataclass
class ScoreExportResult:
    run_id: str
    session_id: str
    ok: bool
    exported: int = 0
    reason: str = ""
    names: list[str] = field(default_factory=list)


def build_score_events(
    run_id: str,
    session_id: str,
    scores: Sequence[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Ingestion events for the exportable subset of a run's scores.

    A boolean is sent as Langfuse's ``BOOLEAN`` type rather than as 1/0: a
    pass/fail rendered as a number invites an average that means nothing.

    The score **name carries its version** (``test_pass_rate@v1``). Langfuse has
    no version field, so without this a rubric change would pool two different
    quantities into one chart — the exact failure the registry exists to prevent
    (ADR-0034).
    """
    stamp = (now or datetime.now(UTC)).isoformat()
    events: list[dict[str, Any]] = []
    for score in scores:
        name = getattr(score, "name", None)
        if name not in EXPORTED_SCORES:
            continue
        value = getattr(score, "value_bool", None)
        data_type = "BOOLEAN"
        if value is None:
            value = getattr(score, "value_num", None)
            data_type = "NUMERIC"
        if value is None:
            continue
        version = getattr(score, "version", "v1")
        events.append(
            {
                "id": str(uuid.uuid4()),
                "type": "score-create",
                "timestamp": stamp,
                "body": {
                    "id": f"{run_id}-{name}-{version}",
                    "name": f"{name}@{version}",
                    "value": bool(value) if data_type == "BOOLEAN" else float(value),
                    "dataType": data_type,
                    "sessionId": session_id,
                    "comment": getattr(score, "reason", "") or None,
                    "metadata": {
                        "run_id": run_id,
                        "category": getattr(score, "category", ""),
                        # The claim's kind travels with it. A judgement charted
                        # beside a fact borrows authority it does not have.
                        "provenance": getattr(score, "provenance", ""),
                        "source": getattr(score, "source", ""),
                    },
                },
            }
        )
    return events


def export_scores(
    run_id: str,
    scores: Sequence[Any],
    *,
    config: LangfuseConfig | None = None,
    transport: LangfuseTransport | None = None,
) -> ScoreExportResult:
    """Send a run's exportable scores. Never raises."""
    from harness_lab_app.observability.client import get_transport

    config = config or LangfuseConfig.from_env()
    session = config.session_id(run_id)
    if not config.enabled:
        return ScoreExportResult(
            run_id=run_id,
            session_id=session,
            ok=False,
            reason=config.disabled_reason or "Langfuse is disabled.",
        )

    events = build_score_events(run_id, session, scores)
    if not events:
        return ScoreExportResult(
            run_id=run_id,
            session_id=session,
            ok=True,
            reason="No exportable scores for this run yet.",
        )

    transport = transport or get_transport(config)
    result = transport.send({"batch": events}, url=config.ingestion_url)
    return ScoreExportResult(
        run_id=run_id,
        session_id=session,
        ok=result.ok,
        exported=len(events) if result.ok else 0,
        reason=result.reason,
        names=[event["body"]["name"] for event in events],
    )
