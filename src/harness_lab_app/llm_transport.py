"""The lab's single outbound LLM transport.

Every in-process LLM call the lab makes — the behavioural auditor, the SWE-bench
Pro judge, the rubric judge, the case-summary polish, the DeepEval judge — is the
same request: one system message, one user message, one string back. That request
used to be written out four times, so a lab whose credentials were not an
OpenAI-compatible gateway had four places to fix. This module is the one place.

Two transports are supported and they are tried in a fixed order:

1. **gateway** — an OpenAI-compatible endpoint (`GATEWAY_BASE_URL` +
   `GATEWAY_TOKEN`). This stays first on purpose: a lab that already routes
   several models through LiteLLM keeps doing exactly that, unchanged.
2. **claude_cli** — the Claude Code CLI driven headless (`claude -p`). This is
   what makes the lab usable end to end for someone whose only credential is a
   Claude account token, which is also the credential the `claude` harness lane
   already runs on.

A caller that gets neither is told so (:class:`TransportNotConfigured`); it is
never handed a fabricated answer. Failures raise, and the callers turn a raise
into their own documented outcome — a skipped metric, a manual-scoring fallback,
a script exit. **A measurement that did not happen is not a zero**, and that rule
is enforced by never returning a value this module did not actually receive.

Settings are read from the process environment and then from the repo's `.env`,
because the API and worker containers do not export `.env` into their
environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - the Claude CLI is spawned with a fixed argv
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

from harness_lab_app.gateway_models import GATEWAY_USER_AGENT, normalize_model_name

Transport = Literal["gateway", "claude_cli", "none"]

#: Mirrors the auditor's timeout; every caller passes its own.
DEFAULT_TIMEOUT = 120
DEFAULT_TEMPERATURE = 0.2
#: Deliberately short: a Langfuse host that is unreachable already costs the
#: completion itself a socket timeout, so tracing must not add a second, much
#: longer one to every judge/audit call. An operator working away from the
#: Langfuse host should feel no stall from turning tracing on.
CALL_TRACE_TIMEOUT = 2.0


class TransportError(RuntimeError):
    """A chat call was attempted and did not produce an answer."""


class TransportNotConfigured(TransportError):
    """No LLM transport is configured at all — no gateway, no Claude CLI."""


class GatewayHTTPError(TransportError):
    """The gateway answered with a non-2xx status.

    Carries the status and body so a caller can react to them — the auditor
    turns a 400 that mentions the model into a list of available models.
    """

    def __init__(self, code: int, body: str) -> None:
        super().__init__(f"Gateway HTTP {code}: {body}")
        self.code = code
        self.body = body


def setting(name: str) -> str:
    """A setting from the process env, falling back to the repo's `.env`."""
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        from harness_lab_app.config import read_env_file_value

        return (read_env_file_value(name) or "").strip()
    except Exception:
        return ""


def gateway_config() -> tuple[str, str] | None:
    """`(base_url, token)` for the OpenAI-compatible gateway, or None."""
    base = setting("GATEWAY_BASE_URL")
    token = setting("GATEWAY_TOKEN")
    if not base or not token:
        return None
    # Inside a container "localhost" is the container itself, not the host that
    # actually runs the gateway.
    if Path("/.dockerenv").exists():
        try:
            from harness_lab_app.config import get_settings
            from harness_lab_app.executors import rewrite_localhost

            base = rewrite_localhost(base, get_settings().docker_host_alias)
        except Exception:
            pass
    return (base.rstrip("/"), token)


def claude_cli_credential() -> tuple[str, str] | None:
    """`(env_var, value)` for the Claude CLI, or None when there is none.

    The same two credentials the `claude` harness lane accepts, in the same
    order: an account token first, then an API key.
    """
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        value = setting(name)
        if value:
            return (name, value)
    return None


def claude_cli_path() -> str:
    """The `claude` executable, or "" when it is not on PATH."""
    return shutil.which("claude") or ""


def claude_cli_ready() -> bool:
    """True when the Claude CLI is both installed and holds a credential."""
    return bool(claude_cli_path()) and claude_cli_credential() is not None


def available_transport() -> Transport:
    """Which transport `chat` would use right now.

    The gateway wins whenever it is configured. That ordering is the whole
    compatibility guarantee: adding the CLI transport cannot change what an
    existing gateway deployment does.
    """
    if gateway_config() is not None:
        return "gateway"
    if claude_cli_ready():
        return "claude_cli"
    return "none"


#: The model each transport reaches for when nothing was configured explicitly.
#: They have to differ: this lab's LiteLLM registers OpenAI-family names, and
#: none of them mean anything to the Claude CLI (or the reverse). An explicit
#: ``AUDITOR_MODEL`` / ``DEEPEVAL_MODEL`` / ``CASE_SUMMARY_MODEL`` always wins
#: over both — these are only the "nothing was said" answer.
_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    # gpt-5.4-mini: mini tier, ~6.7x cheaper than gpt-5.5 with enough judgment
    # for log audits. gpt-5.4-nano: summaries are extraction, not reasoning.
    "gateway": {"audit": "gpt-5.4-mini", "summary": "gpt-5.4-nano"},
    # Sonnet judges, Haiku summarizes — the same split, one tier per job.
    "claude_cli": {"audit": "claude-sonnet-5", "summary": "claude-haiku-4-5"},
}


def default_audit_model(transport: str | None = None) -> str:
    """The default model for the auditor and the LLM judges."""
    return _default_model("audit", transport)


def default_summary_model(transport: str | None = None) -> str:
    """The default model for case-description polish."""
    return _default_model("summary", transport)


def _default_model(role: str, transport: str | None = None) -> str:
    # An unconfigured lab still has to name a model somewhere (an argparse help
    # string, a --dry-run render); the gateway's names are the historical answer.
    table = _DEFAULT_MODELS.get(transport or available_transport())
    return (table or _DEFAULT_MODELS["gateway"])[role]


def chat(
    model: str,
    system: str,
    user: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    temperature: float = DEFAULT_TEMPERATURE,
    trace_kind: str = "",
    trace_metadata: dict | None = None,
) -> str:
    """One chat completion over whichever transport is available.

    `system` may be empty, in which case no system message is sent at all.
    Raises :class:`TransportError` (or a subclass) on any failure; never
    returns a placeholder.

    `trace_kind`, when set, emits one Langfuse trace for this call (e.g.
    ``"repository_audit"``, ``"case_draft"``, ``"judge"`` — see
    :mod:`harness_lab_app.observability.call_export`), tagged with
    ``trace_metadata``. Left at the default "", this costs nothing extra: no
    import, no clock read, no chance of a second network call — this module is
    the lowest layer and must stay usable in a lab with no observability
    configured. Tracing never changes what `chat` returns or raises.
    """
    transport = available_transport()
    start_ns = time.time_ns() if trace_kind else 0
    try:
        if transport == "gateway":
            base, token = gateway_config()  # type: ignore[misc]
            answer = gateway_chat(
                base,
                token,
                model,
                system,
                user,
                timeout=timeout,
                temperature=temperature,
            )
        elif transport == "claude_cli":
            answer = _claude_cli_chat(model, system, user, timeout=timeout)
        else:
            raise TransportNotConfigured(
                "No LLM transport is configured: set GATEWAY_BASE_URL + "
                "GATEWAY_TOKEN, or install the Claude Code CLI and set "
                "CLAUDE_CODE_OAUTH_TOKEN."
            )
    except Exception as exc:
        if trace_kind:
            _trace_call(
                trace_kind,
                model=model,
                system=system,
                user=user,
                transport=transport,
                temperature=temperature,
                metadata=trace_metadata,
                start_ns=start_ns,
                error=str(exc),
            )
        raise
    if trace_kind:
        _trace_call(
            trace_kind,
            model=model,
            system=system,
            user=user,
            transport=transport,
            temperature=temperature,
            metadata=trace_metadata,
            start_ns=start_ns,
            output=answer,
        )
    return answer


def _trace_call(
    kind: str,
    *,
    model: str,
    system: str,
    user: str,
    transport: str,
    temperature: float,
    metadata: dict | None,
    start_ns: int,
    output: str | None = None,
    error: str | None = None,
) -> None:
    """Best-effort Langfuse trace for one `chat` call.

    Never raises and never lengthens a stall: the whole emit is wrapped in
    `try/except Exception: pass` and posts with a short timeout
    (`CALL_TRACE_TIMEOUT`), not the exporter's normal default. Imports are
    local to this function so a lab with no observability configured pays
    nothing to import `llm_transport`.
    """
    try:
        from harness_lab_app.observability import call_export
        from harness_lab_app.observability.client import OtlpHttpTransport
        from harness_lab_app.observability.config import LangfuseConfig

        config = LangfuseConfig.from_env()
        if not config.enabled:
            return
        call_export.export_call(
            kind,
            model=model,
            system=system,
            user=user,
            start_ns=start_ns,
            end_ns=time.time_ns(),
            output=output,
            error=error,
            metadata={
                **(metadata or {}),
                "transport": transport,
                "temperature": temperature,
            },
            config=config,
            transport=OtlpHttpTransport(config, timeout=CALL_TRACE_TIMEOUT),
        )
    except Exception:
        pass


def gateway_chat(
    base: str,
    token: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: int,
    temperature: float,
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    payload = {
        "model": normalize_model_name(model),
        "temperature": temperature,
        "messages": messages,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The gateway sits behind Cloudflare, which bans the default
            # "Python-urllib/x.y" user-agent (error 1010). Send a normal UA.
            "User-Agent": GATEWAY_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise GatewayHTTPError(
            error.code, error.read().decode("utf-8", errors="replace")
        ) from error
    except Exception as error:
        raise TransportError(f"Gateway request failed: {error}") from error
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as error:
        raise TransportError(
            f"Unexpected gateway response: {json.dumps(data)[:500]}"
        ) from error


def _claude_cli_chat(model: str, system: str, user: str, *, timeout: int) -> str:
    """One completion from the Claude Code CLI running headless.

    Deliberately spartan: `--tools ""` disables every built-in tool, so this is
    a text completion and not an agent — no filesystem access, no permission
    prompts that could block a worker, nothing to approve. The prompt goes over
    stdin rather than argv, because audit prompts carry clipped diffs and would
    otherwise run into the argument-length limit.
    """
    executable = claude_cli_path()
    credential = claude_cli_credential()
    if not executable or credential is None:
        raise TransportNotConfigured("The Claude CLI transport is not available.")
    command = [
        executable,
        "-p",
        "--output-format",
        "json",
        # A judge is a text completion, not an agent.
        "--tools",
        "",
        # Ignore whatever MCP servers the host happens to have configured.
        "--strict-mcp-config",
    ]
    native = native_claude_model(model)
    if native:
        command += ["--model", native]
    if system:
        command += ["--append-system-prompt", system]

    name, value = credential
    # The API and worker containers do not export `.env`, so the credential has
    # to be handed to the subprocess explicitly.
    env = {**os.environ, name: value}
    try:
        # A scratch cwd: the CLI writes session state relative to it, and no
        # part of the repository should be reachable from a judge.
        with tempfile.TemporaryDirectory(prefix="llm-transport-") as workdir:
            completed = subprocess.run(  # nosec B603 - fixed argv, no shell
                command,
                input=user,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir,
                env=env,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise TransportError(f"Claude CLI timed out after {timeout}s") from error
    except OSError as error:
        raise TransportError(f"Claude CLI could not be started: {error}") from error

    stdout = (completed.stdout or "").strip()
    if not stdout:
        detail = (completed.stderr or "").strip()[:500] or "no output"
        raise TransportError(
            f"Claude CLI exited {completed.returncode} without a reply: {detail}"
        )
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise TransportError(f"Unexpected Claude CLI reply: {stdout[:500]}") from error
    # The CLI reports API failures in-band: exit code 1, `is_error` true, and the
    # error text sitting in `result` where an answer would be.
    if completed.returncode != 0 or data.get("is_error"):
        raise TransportError(
            f"Claude CLI call failed: {str(data.get('result') or '')[:500]}"
        )
    text = str(data.get("result") or "")
    if not text.strip():
        raise TransportError("Claude CLI returned an empty reply.")
    return text


def native_claude_model(model: str) -> str:
    """`model` as the Claude CLI understands it, or "" to use its default.

    The lab's model names are gateway names (`gateway/anthropic-claude-sonnet`,
    `gpt-5.4-mini`). Only a Claude id means anything to the CLI; anything else
    is a model this transport cannot serve, and letting the CLI pick its own
    default is a better answer than a guaranteed 404.
    """
    name = normalize_model_name(model).strip()
    _, _, tail = name.rpartition("/")
    tail = tail or name
    return tail if tail.startswith("claude-") else ""
