"""Maps active lab runs to the serving host an action is about to touch.

Loading, unloading and testing from *Tune your local model* act on a server
that a lab run may be using right now. This module answers "is anything on
that host?" (:func:`active_runs_on_host`), resolves which host a model string
serves from (:func:`host_of_model`), writes the warning left on *Continue
anyway* (:func:`record_override`), and backs *New evaluation*'s *Check
configuration* guard (:func:`model_load_check`), per the spec's "Host guard".

Application layer: depends on ``models`` (the ORM) and ``gateway_models``
(routing), never on FastAPI or a particular server adapter.
"""

from __future__ import annotations

import ipaddress
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from sqlmodel import select

from harness_lab_app.local_tuning.contracts import ActiveRunRef, GuardReport
from harness_lab_app.models import Run, RunConfig, RunEvent, RunState

if TYPE_CHECKING:
    from sqlmodel import Session

    from harness_lab_app.local_tuning.contracts import ServerKind

# host.docker.internal, *.local, *.lan, *.internal and *.home.arpa are
# LAN-reachable from the API process even though they are not a literal
# private/loopback IP (spec item 7).
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa")
# CGNAT: shared address space a carrier hands a LAN behind its own NAT
# (RFC 6598), still "local" from this process's point of view.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
# Spellings that all mean "the machine the API process itself runs on",
# folded to one ("localhost") so the guard compares them as the same host.
# Spellings recognised as "this machine"; nothing binds to them here.
_LOOPBACK_SPELLINGS = ("127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0")  # nosec B104


def _fold_loopback(host: str) -> str:
    return (
        "localhost"
        if host in _LOOPBACK_SPELLINGS or host == "host.docker.internal"
        else host
    )


def _is_private_ip(host: str) -> bool | None:
    """Whether ``host`` parses as an IP and is private/loopback/link-local.

    ``None`` when ``host`` is not an IP literal at all (a hostname), so the
    caller falls back to the suffix/single-label heuristics.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    # fc00::/7 (unique local) is already covered by ``is_private`` for IPv6.
    return bool(
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or (isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT)
    )


def _normalize_host(url: str) -> str | None:
    """``scheme://host:port`` lowercase, or ``None`` if ``url`` has no host.

    Every loopback spelling (``localhost``, ``127.0.0.1``, ``::1``, ``[::1]``,
    ``0.0.0.0``, ``host.docker.internal``) is the same machine from the API
    process's point of view (ADR-0063's ``rewrite_localhost`` makes the same
    equivalence for the opposite direction), so they fold to one spelling.
    """
    if not url:
        return None
    parts = urlsplit(url if "://" in url else f"http://{url}")
    host = (parts.hostname or "").lower()
    if not host:
        return None
    host = _fold_loopback(host)
    scheme = parts.scheme or "http"
    port = parts.port
    if port is None:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _is_lan_host(url: str) -> bool:
    """True unless ``host`` is a public IP or a public (multi-label) DNS name.

    Guards against the opposite false negative from the spec ("treat as local
    any api_base whose host is NOT a public IP / public DNS name"): a private
    IP, a loopback, CGNAT, a bare single-label hostname (no dot — an internal
    LAN box has no public registered name) and the usual LAN-only TLDs/suffixes
    all count as local.
    """
    parts = urlsplit(url if "://" in url else f"http://{url}")
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    is_private = _is_private_ip(host)
    if is_private is not None:
        return is_private
    # Not an IP literal: a hostname with no dot (single label) cannot be a
    # publicly registered DNS name, so it is local by elimination.
    return "." not in host


def _env_base_url(name: str) -> str:
    from harness_lab_app.config import read_env_file_value

    return read_env_file_value(name) or os.environ.get(name, "")


def host_of_model(model: str, routing: dict | None) -> str | None:
    """The normalized host a model string is served from, or ``None``.

    ``lmstudio/...`` and ``ollama/...`` map to their direct base URL.
    ``gateway/<alias>`` (or a bare alias, which the run form also accepts) maps
    to the LiteLLM card's ``api_base`` through ``routing``, but only when that
    ``api_base`` is a LAN/localhost address — a cloud card (Claude, OpenAI, a
    gateway alias with no LAN backend) never matches, per the spec.
    """
    from harness_lab_app.gateway_models import normalize_model_name

    if model.startswith("lmstudio/"):
        base = _env_base_url("LMSTUDIO_BASE_URL")
        return _normalize_host(base) if base else None
    if model.startswith("ollama/"):
        base = _env_base_url("OLLAMA_BASE_URL")
        return _normalize_host(base) if base else None

    routing = routing or {}
    alias = normalize_model_name(model)
    entry = routing.get(alias)
    if not entry:
        return None
    api_base = entry.get("api_base") or ""
    if not api_base or not _is_lan_host(api_base):
        return None
    return _normalize_host(api_base)


def active_runs_on_host(
    session: Session, host: str, routing: dict | None
) -> GuardReport:
    """Running/queued lab runs whose executor or planner model serves from ``host``."""
    rows = session.exec(
        select(Run, RunConfig)
        .where(Run.run_config_id == RunConfig.id)
        .where(cast("Any", Run.state).in_([RunState.running, RunState.pending]))
    ).all()

    running: list[ActiveRunRef] = []
    queued: list[ActiveRunRef] = []
    for run, config in rows:
        for model in (config.executor_model, config.planner_model):
            if not model:
                continue
            if host_of_model(model, routing) != host:
                continue
            ref = ActiveRunRef(
                run_id=run.run_id,
                state=str(run.state),
                model=model,
                experiment_id=run.experiment_id,
            )
            if run.state == RunState.running:
                running.append(ref)
            else:
                queued.append(ref)
            break
    return GuardReport(host=host, running=running, queued=queued)


def record_override(session: Session, report: GuardReport, action: str) -> int:
    """Write the "Continue anyway" warning on every running run of ``host``.

    Returns the number of events written. Queued runs get no event: nothing
    has started reading from the host yet for them to warn about.
    """
    now = datetime.now(UTC).strftime("%H:%M")
    count = 0
    for ref in report.running:
        session.add(
            RunEvent(
                run_id=ref.run_id,
                level="warning",
                message=(
                    f"Serving host {report.host} reconfigured during this run "
                    f"from Tune your local model ({action}, {now} UTC)."
                ),
            )
        )
        count += 1
    session.commit()
    return count


def _served_model_id(entry: dict) -> str:
    """The id the serving host itself uses, stripped of the LiteLLM backend prefix."""
    target = str(entry.get("target") or "")
    if "/" in target:
        return target.split("/", 1)[1]
    return target


# Routing-target prefix -> the ``ServerKind`` that prefix is served by. Any
# other prefix (``openai/``, ``hosted_vllm/``, a bare cloud alias, ...) is not
# one of ours, so :func:`model_load_check` must not guess LM Studio for it
# (spec item 6).
_TARGET_PREFIX_KIND: dict[str, ServerKind] = {
    "lm_studio/": "lmstudio",
    "ollama_chat/": "ollama",
    "ollama/": "ollama",
}


def _kind_of_target(target: str) -> ServerKind | None:
    for prefix, kind in _TARGET_PREFIX_KIND.items():
        if target.startswith(prefix):
            return kind
    return None


def _default_make_server():
    from harness_lab_app.local_tuning.servers import make_server

    return make_server


def model_load_check(
    session: Session,
    model: str,
    routing: dict | None,
    *,
    make_server=None,
) -> dict:
    """What *New evaluation*'s Check configuration needs before probing ``model``.

    Fail-open: any failure to reach the host leaves ``loaded``/``size_gb`` as
    ``None`` rather than raising, because the probe this backs must still run
    when no host can be resolved (spec "Host guard").

    For a ``gateway/...`` model, the host it lists from is the card's own
    ``api_base`` — never the lab's own ``LMSTUDIO_BASE_URL``/``OLLAMA_BASE_URL``
    env vars, which may point at an entirely different machine — and the
    server kind comes from the routing target's prefix, not an LM-Studio-by-
    default guess (spec item 6). ``make_server`` is injected (default
    :func:`harness_lab_app.local_tuning.servers.make_server`, imported lazily)
    so this module never imports the server adapters at module load time.
    """
    from harness_lab_app.gateway_models import normalize_model_name

    host = host_of_model(model, routing)
    result: dict = {
        "model": model,
        "host": host,
        "server_kind": None,
        "model_bare": None,
        "loaded": None,
        "size_gb": None,
        "guard": None,
    }
    if host is None:
        return result

    routing = routing or {}
    server_kind: ServerKind | None
    api_base: str | None
    if model.startswith("lmstudio/"):
        from harness_lab_app.direct_hosts import strip_provider_prefix

        server_kind = "lmstudio"
        model_bare = strip_provider_prefix(model, "lmstudio/")
        api_base = _env_base_url("LMSTUDIO_BASE_URL")
    elif model.startswith("ollama/"):
        from harness_lab_app.direct_hosts import strip_provider_prefix

        server_kind = "ollama"
        model_bare = strip_provider_prefix(model, "ollama/")
        api_base = _env_base_url("OLLAMA_BASE_URL")
    else:
        entry = routing.get(normalize_model_name(model)) or {}
        target = str(entry.get("target") or "")
        server_kind = _kind_of_target(target)
        model_bare = _served_model_id(entry)
        api_base = str(entry.get("api_base") or "")

    result["server_kind"] = server_kind
    result["model_bare"] = model_bare
    result["guard"] = active_runs_on_host(session, host, routing)

    if server_kind is None or not api_base:
        return result

    try:
        server_factory = make_server or _default_make_server()
        server = server_factory(server_kind, api_base)
        for info in server.list_models():
            if info.id == model_bare:
                result["loaded"] = info.loaded
                result["size_gb"] = (
                    info.size_bytes / 1024**3 if info.size_bytes else None
                )
                break
        else:
            result["loaded"] = False
    except Exception:
        pass
    return result
