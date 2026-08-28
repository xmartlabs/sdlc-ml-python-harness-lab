"""Pure builders for OTLP/JSON spans carrying Langfuse semantics.

No I/O and no configuration lookups live here, so the whole trace shape is
testable by comparing dictionaries.

Two conventions are worth stating once:

* **Langfuse fields are span attributes.** A Langfuse *trace* is the OTEL trace
  its root span opens; `langfuse.trace.name`, `langfuse.session.id` and
  `langfuse.trace.tags` on that root become the trace's name, session and tags.
  Observation fields (`input`, `output`, `level`, `metadata`) are per span.
* **Ids are derived, never random.** ``sha256(run_id:key)`` truncated to the
  OTEL id widths means re-exporting a run overwrites its traces instead of
  duplicating them, which is what makes the exporter safe to call from the
  worker, from a re-validation, and from a manual backfill of the same run.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

# OTLP span kinds; every span the lab writes is internal work.
SPAN_KIND_INTERNAL = 1
# OTLP status codes.
STATUS_UNSET = 0
STATUS_OK = 1
STATUS_ERROR = 2

SERVICE_NAME = "harness-lab"
SCOPE_NAME = "harness_lab_app.observability"


def trace_id(run_id: str, key: str) -> str:
    """32 hex chars — an OTEL 16-byte trace id, stable for (run_id, key)."""
    return hashlib.sha256(f"{run_id}:{key}".encode()).hexdigest()[:32]


def span_id(run_id: str, key: str) -> str:
    """16 hex chars — an OTEL 8-byte span id, stable for (run_id, key)."""
    return hashlib.sha256(f"{run_id}:span:{key}".encode()).hexdigest()[:16]


def _attr_value(value: Any) -> dict[str, Any] | None:
    """Encode one Python value as an OTLP AnyValue, or None to drop it."""
    if value is None:
        return None
    # bool before int: bool is a subclass of int and would encode as 0/1.
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, str):
        return {"stringValue": value} if value else None
    if isinstance(value, int):
        # OTLP/JSON encodes int64 as a string.
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (list, tuple)):
        values = [_attr_value(item) for item in value]
        kept = [item for item in values if item is not None]
        return {"arrayValue": {"values": kept}} if kept else None
    return {"stringValue": json.dumps(value, ensure_ascii=False, default=str)}


def attributes(mapping: Mapping[str, Any]) -> list[dict[str, Any]]:
    """OTLP KeyValue list, with empty and null entries dropped."""
    out: list[dict[str, Any]] = []
    for key, raw in mapping.items():
        encoded = _attr_value(raw)
        if encoded is not None:
            out.append({"key": key, "value": encoded})
    return out


def as_text(value: Any) -> str:
    """Render an input/output payload as the string Langfuse expects."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def build_span(
    *,
    trace: str,
    span: str,
    name: str,
    start_ns: int,
    end_ns: int,
    parent: str | None = None,
    observation_type: str = "span",
    session_id: str | None = None,
    trace_name: str | None = None,
    tags: Sequence[str] | None = None,
    environment: str | None = None,
    input_value: Any = None,
    output_value: Any = None,
    metadata: Mapping[str, Any] | None = None,
    level: str | None = None,
    status_message: str | None = None,
    model: str | None = None,
    usage_details: Mapping[str, Any] | None = None,
    cost_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One OTLP span with Langfuse attributes applied.

    `trace_name`, `session_id` and `tags` are only meaningful on a root span
    (`parent is None`); passing them on a child is harmless but ignored by
    Langfuse.
    """
    payload: dict[str, Any] = {
        "langfuse.observation.type": observation_type,
        "langfuse.environment": environment,
        "langfuse.observation.level": level,
        "langfuse.observation.status_message": status_message,
    }
    if parent is None:
        payload["langfuse.trace.name"] = trace_name or name
        payload["langfuse.session.id"] = session_id
        payload["langfuse.trace.tags"] = list(tags) if tags else None
    if input_value is not None:
        payload["langfuse.observation.input"] = as_text(input_value)
    if output_value is not None:
        payload["langfuse.observation.output"] = as_text(output_value)
    if model:
        payload["langfuse.observation.model.name"] = model
    if usage_details:
        payload["langfuse.observation.usage_details"] = json.dumps(dict(usage_details))
    if cost_details:
        payload["langfuse.observation.cost_details"] = json.dumps(dict(cost_details))
    for key, value in (metadata or {}).items():
        # The metadata prefix is what makes a key filterable in the Langfuse UI;
        # plain OTEL attributes stay nested and unqueryable.
        payload[f"langfuse.observation.metadata.{key}"] = value

    out: dict[str, Any] = {
        "traceId": trace,
        "spanId": span,
        "name": name,
        "kind": SPAN_KIND_INTERNAL,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(max(end_ns, start_ns)),
        "attributes": attributes(payload),
    }
    if parent:
        out["parentSpanId"] = parent
    if level == "ERROR":
        out["status"] = {
            "code": STATUS_ERROR,
            **({"message": status_message} if status_message else {}),
        }
    else:
        out["status"] = {"code": STATUS_OK}
    return out


def build_payload(
    spans: Iterable[dict[str, Any]],
    *,
    environment: str = "",
    service_name: str = SERVICE_NAME,
) -> dict[str, Any]:
    """Wrap spans in the OTLP ExportTraceServiceRequest envelope."""
    resource_attrs: dict[str, Any] = {"service.name": service_name}
    if environment:
        resource_attrs["deployment.environment.name"] = environment
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": attributes(resource_attrs)},
                "scopeSpans": [
                    {
                        "scope": {"name": SCOPE_NAME},
                        "spans": list(spans),
                    }
                ],
            }
        ]
    }
