"""Transport to Langfuse: OTLP over HTTP with a JSON body.

Why not the `langfuse` SDK, and why not `/api/public/ingestion`:

* On Langfuse **v4** the ingestion endpoint rejects span events (400) and the
  REST trace endpoints are gone (404). OTLP over HTTP is the supported path,
  and it accepts JSON as well as protobuf — gRPC is not offered.
* The official SDK's `start_observation()` takes no `start_time`/`end_time`; it
  stamps spans from the live OTEL clock. A post-run exporter must backdate
  spans to when the run actually happened, which only explicit
  `startTimeUnixNano` values can express.

So the payload is built by hand and posted with `urllib`, like every other
outbound call in this repo. `Dockerfile.runner` needs no new dependency, which
is what keeps this importable from `scripts/bench_run.py` inside a container.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from harness_lab_app.observability.config import LangfuseConfig

DEFAULT_TIMEOUT = 15.0
PROBE_TIMEOUT = 5.0

# Read-only endpoint used to verify host + credentials without writing spans.
PROJECTS_PATH = "/api/public/projects"


@dataclass(frozen=True)
class SendResult:
    ok: bool
    status: int = 0
    reason: str = ""


def auth_headers(config: LangfuseConfig) -> dict[str, str]:
    """Basic auth plus the header Langfuse v4 needs to route OTLP payloads."""
    token = base64.b64encode(
        f"{config.public_key}:{config.secret_key}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "x-langfuse-ingestion-version": "4",
    }


class LangfuseTransport(Protocol):
    def send(self, payload: dict[str, Any], url: str | None = None) -> SendResult:
        """``url`` overrides the default OTLP endpoint.

        Spans go over OTLP; scores go to the ingestion route, which is the
        only other thing v4 accepts (ADR-0027). One transport, two
        destinations — the auth and error handling are identical.
        """
        ...


class NullTransport:
    """What a disabled integration uses: no sockets, no work, no surprises."""

    def __init__(self, reason: str = "Langfuse is disabled.") -> None:
        self._reason = reason

    def send(self, payload: dict[str, Any], url: str | None = None) -> SendResult:  # noqa: ARG002
        return SendResult(ok=False, reason=self._reason)


class OtlpHttpTransport:
    """POST OTLP/JSON spans to `{host}/api/public/otel/v1/traces`.

    Fails open: any transport error is reported in the result and never raised,
    because a Langfuse outage must cost observability and nothing else.
    """

    def __init__(
        self,
        config: LangfuseConfig,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Any = None,
    ) -> None:
        self._config = config
        self._timeout = timeout
        # Injection seam for tests; production uses urllib.request.urlopen.
        self._opener = opener or urllib.request.urlopen

    def _headers(self) -> dict[str, str]:
        return auth_headers(self._config)

    def send(self, payload: dict[str, Any], url: str | None = None) -> SendResult:
        body = json.dumps(payload).encode()
        # The URL is the operator's own LANGFUSE_HOST, not user input.
        request = urllib.request.Request(
            url or self._config.traces_url,
            data=body,
            headers=self._headers(),
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                status = int(getattr(response, "status", 0) or 0)
                return SendResult(ok=200 <= status < 300, status=status)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode(errors="replace")[:300]
            except Exception:
                detail = ""
            return SendResult(
                ok=False,
                status=exc.code,
                reason=f"Langfuse returned {exc.code}: {detail or exc.reason}",
            )
        except Exception as exc:
            return SendResult(ok=False, reason=f"Could not reach Langfuse: {exc!r}")


def check_connection(
    config: LangfuseConfig,
    *,
    timeout: float = PROBE_TIMEOUT,
    opener: Any = None,
) -> SendResult:
    """Verify host and credentials without ingesting anything.

    Used by the health panel, so it answers in `reason` whether the probe
    succeeded or failed — an operator needs the sentence either way.
    """
    if not config.enabled:
        return SendResult(
            ok=False, reason=config.disabled_reason or "Langfuse is disabled."
        )
    open_url = opener or urllib.request.urlopen
    url = f"{config.host.rstrip('/')}{PROJECTS_PATH}"
    request = urllib.request.Request(url, headers=auth_headers(config), method="GET")
    try:
        with open_url(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            names = _project_names(response)
    except urllib.error.HTTPError as exc:
        hint = " (check LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)"
        return SendResult(
            ok=False,
            status=exc.code,
            reason=f"Langfuse returned {exc.code}{hint if exc.code in (401, 403) else ''}.",
        )
    except Exception as exc:
        return SendResult(ok=False, reason=f"Could not reach {config.host}: {exc!r}")
    if not 200 <= status < 300:
        return SendResult(
            ok=False, status=status, reason=f"Langfuse returned {status}."
        )
    where = f" (project {', '.join(names)})" if names else ""
    return SendResult(ok=True, status=status, reason=f"{config.host} reachable{where}.")


def _project_names(response: Any) -> list[str]:
    """Project names from a /api/public/projects body; [] if it is not that."""
    try:
        body = json.loads(response.read().decode(errors="replace"))
        return [str(item["name"]) for item in body.get("data", []) if item.get("name")]
    except Exception:
        return []


def get_transport(config: LangfuseConfig) -> LangfuseTransport:
    """The transport a config implies — Null when tracing is off."""
    if not config.enabled:
        return NullTransport(config.disabled_reason or "Langfuse is disabled.")
    return OtlpHttpTransport(config)
