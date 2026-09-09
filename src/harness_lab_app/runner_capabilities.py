"""What the runner can execute, and what one command needs to run.

The lab used to answer "can we evaluate this repository?" with a single
question about the repository: which language is it written in, and is that
language Python or Node. That conflates two different facts. A repository
written in Shell may propose a case that runs ``pytest``; a repository written
in Python may propose a case that only checks whether ``AGENTS.md`` exists. The
language of the tree is not the execution requirement of the case.

Three small pieces, kept deliberately flat:

- :class:`RunnerCapability` — one runtime the runner can execute, and what is
  known about it. ``source`` says how that was learned: ``declared`` is the
  runner image's build recipe, ``probed`` is the image answering for itself.
  A capability that was never probed does not claim a version, because a
  version nobody measured is a guess dressed as a fact (ADR-0048).
- :func:`runtime_for_command` — the requirement *derived* from a command, from
  the leading binary it names. Nothing is stored: a requirement that lived in
  the database could disagree with the command beside it.
- :func:`compare_runtime_version` — how a required version relates to the one
  present. ``bash 3.2`` against ``bash 5.2.37`` is ``partial``, never exact,
  and nothing here ever returns a word like "certified".

Classification never needs a container: :func:`declared_capabilities` is pure,
so the audit's verdict is the same in a unit test and in the worker. The probe
only enriches what is already known with real version strings.
"""

from __future__ import annotations

import re
import shlex
import shutil
import time
from dataclasses import asdict, dataclass
from typing import Any

# Runtimes `Dockerfile.runner` installs, and the language each one executes.
# bash is not installed by any line of that file: it arrives with the Debian
# base of `python:3.11-slim` (5.2.37 at the time of writing), which is why
# adding Shell support needed no image change. `/bin/sh` there is dash, so a
# command using bash syntax must be run under `bash -c`, not `sh -c`.
DECLARED_RUNTIMES: tuple[str, ...] = ("python", "node", "bash", "sh", "git")

# The binary each runtime is probed through, in the order the probe prints.
_PROBE_BINARIES: dict[str, str] = {
    "python": "python3",
    "node": "node",
    "bash": "bash",
    "sh": "sh",
    "git": "git",
}

# One POSIX line, no dependencies, safe to run under dash. Each available
# runtime prints `<runtime>\t<path>\t<first line of --version>`; anything
# missing prints nothing at all, which is the answer.
PROBE_SCRIPT = (
    "for r in python3 node bash sh git; do "
    'p=$(command -v "$r") || continue; '
    'v=$("$p" --version 2>&1 | head -n 1); '
    'printf \'%s\\t%s\\t%s\\n\' "$r" "$p" "$v"; '
    "done"
)

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")
_CLEAN_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,2}$")

# How long a probe's answer is trusted. The image does not change under a
# running worker, so this is about not paying for a container on every audit
# rather than about staleness.
_PROBE_TTL_SECONDS = 300.0
_probe_cache: tuple[float, str, dict[str, str]] | None = None


@dataclass(frozen=True)
class RunnerCapability:
    """One runtime the runner offers, and how well it is known."""

    runtime: str
    available: bool
    version: str = ""
    executable: str = ""
    source: str = "declared"  # "declared" | "probed"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# What the runner offers
# --------------------------------------------------------------------------


def declared_capabilities() -> dict[str, RunnerCapability]:
    """The runner image's build recipe, as capabilities. Pure, no I/O.

    This is what classification runs on, so an audit reaches the same verdict
    in a unit test, in the API process (which has no docker socket, ADR-0002)
    and in the worker. Versions are absent on purpose — see the module note.
    """
    return {
        runtime: RunnerCapability(runtime=runtime, available=True, source="declared")
        for runtime in DECLARED_RUNTIMES
    }


def parse_probe_output(text: str) -> dict[str, RunnerCapability]:
    """Turn :data:`PROBE_SCRIPT` output into capabilities. Pure.

    Kept separate from the probe itself so the parsing of real ``--version``
    banners — which differ wildly, from ``v20.11.0`` to ``GNU bash, version
    5.2.37(1)-release`` — is unit-tested against literal strings instead of
    against whatever happens to be installed on the machine running the tests.
    """
    binary_to_runtime = {binary: runtime for runtime, binary in _PROBE_BINARIES.items()}
    found: dict[str, RunnerCapability] = {}
    for line in (text or "").splitlines():
        parts = line.rstrip("\r").split("\t")
        if len(parts) < 2:
            continue
        binary, executable = parts[0].strip(), parts[1].strip()
        banner = parts[2].strip() if len(parts) > 2 else ""
        runtime = binary_to_runtime.get(binary)
        if not runtime or not executable:
            continue
        found[runtime] = RunnerCapability(
            runtime=runtime,
            available=True,
            version=version_from_banner(banner),
            executable=executable,
            source="probed",
        )
    return found


def version_from_banner(banner: str) -> str:
    """The first dotted number in a ``--version`` line, or an empty string."""
    match = _VERSION_RE.search(banner or "")
    return match.group(1) if match else ""


def clean_version(raw: object) -> str:
    """A version string safe to report, or empty.

    Applied to anything a model supplies. A required version can only ever
    *degrade* a verdict (exact to partial), never make a blocked case runnable,
    so clamping the shape is enough — but it is clamped anyway so nothing
    free-form from a model reaches the report.
    """
    text = str(raw or "").strip()
    return text if _CLEAN_VERSION_RE.match(text) else ""


def runner_capabilities(*, refresh: bool = False) -> dict[str, RunnerCapability]:
    """Declared capabilities, enriched with probed versions where possible.

    Never raises and never blocks a caller that has no execution backend: a
    probe that cannot run leaves the declared answer standing, marked as such.
    Cached per runner image, in the shape of ``health_service.worker_facts``.
    """
    capabilities = declared_capabilities()
    for runtime, probed in _probe_versions(refresh=refresh).items():
        if runtime in capabilities:
            capabilities[runtime] = probed
    return capabilities


def capabilities_as_dicts(
    capabilities: dict[str, RunnerCapability] | None = None,
) -> list[dict[str, Any]]:
    """Capabilities as plain dicts, for evidence and for the API."""
    resolved = capabilities if capabilities is not None else declared_capabilities()
    return [resolved[name].as_dict() for name in sorted(resolved)]


def capabilities_from_dicts(raw: object) -> dict[str, RunnerCapability]:
    """Rebuild capabilities from stored evidence, ignoring anything malformed."""
    rebuilt: dict[str, RunnerCapability] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        runtime = str(item.get("runtime") or "").strip()
        if not runtime:
            continue
        rebuilt[runtime] = RunnerCapability(
            runtime=runtime,
            available=bool(item.get("available")),
            version=str(item.get("version") or ""),
            executable=str(item.get("executable") or ""),
            source=str(item.get("source") or "declared"),
        )
    return rebuilt


def _probe_versions(*, refresh: bool = False) -> dict[str, RunnerCapability]:
    """Ask the execution backend what it actually has. Empty when it cannot."""
    global _probe_cache
    from harness_lab_app.config import get_settings

    try:
        image = get_settings().runner_image
    except Exception:
        return {}
    now = time.monotonic()
    if not refresh and _probe_cache is not None:
        cached_at, cached_image, cached_text = _probe_cache
        if cached_image == image and now - cached_at < _PROBE_TTL_SECONDS:
            return parse_probe_output(_render_cached(cached_text))
    try:
        text = _run_probe()
    except Exception:
        return {}
    _probe_cache = (now, image, {"text": text})
    return parse_probe_output(text)


def _render_cached(cached: dict[str, str]) -> str:
    return cached.get("text", "")


def _run_probe() -> str:
    """Execute :data:`PROBE_SCRIPT` through the configured run executor."""
    from harness_lab_app.executors import get_executor

    handle = get_executor().start("runner-capabilities", ["sh", "-c", PROBE_SCRIPT])
    lines = [message for _level, message in handle.iter_lines()]
    handle.wait()
    return "\n".join(lines)


# --------------------------------------------------------------------------
# What one command needs
# --------------------------------------------------------------------------

# The leading binary of a command, mapped to the runtime that must exist for
# it. Deliberately the same vocabulary the readiness inference already
# proposes, so a command inferred from a repository and a command typed by a
# person resolve identically.
_COMMAND_RUNTIME: dict[str, str] = {
    "python": "python",
    "python3": "python",
    "pytest": "python",
    "uv": "python",
    "uvx": "python",
    "pip": "python",
    "poetry": "python",
    "tox": "python",
    "ruff": "python",
    "mypy": "python",
    "node": "node",
    "npm": "node",
    "npx": "node",
    "pnpm": "node",
    "yarn": "node",
    "bash": "bash",
    "sh": "sh",
    "make": "make",
    "just": "make",
    "git": "git",
    "go": "go",
    "cargo": "rust",
    "mvn": "java",
    "gradle": "java",
    "dotnet": "dotnet",
    # Named so a case asking for them is *blocked* rather than mistaken for a
    # check that needs nothing. The validation lane has no docker socket by
    # design (ADR-0002), so these can never resolve to a capability.
    "docker": "docker",
    "docker-compose": "docker",
    "podman": "docker",
    "kubectl": "kubernetes",
}

# Tokens that stand between the start of a command and the binary that matters.
_SKIPPED_TOKENS = frozenset(
    {
        "cd",
        "&&",
        "||",
        "|",
        ";",
        "then",
        "do",
        "done",
        "fi",
        "for",
        "in",
        "if",
        "while",
        "exec",
        "env",
        "set",
        "-e",
        "-x",
        "-eu",
        "-euo",
    }
)
_TIMEOUT_TOKENS = frozenset({"timeout", "gtimeout"})

# Syntax `/bin/sh` (dash, in the runner image) does not understand. A command
# using any of it has to be handed to bash or it fails for a reason that has
# nothing to do with the code under test.
_BASHISMS = ("[[", "<(", ">(", "pipefail", "local ", "declare ", "function ", "$'")


def runtime_for_command(command: str) -> str:
    """The runtime a command needs, derived from the binary it names.

    Walks past the wrappers a validation command conventionally carries — a
    ``timeout N`` budget, an env-assignment prefix, a ``cd <dir> &&`` hop, a
    ``for … do`` loop — and returns the runtime of the first binary it
    recognises. Anything it does not recognise is ``sh``: a plain shell line
    the runner can always execute, which keeps an unknown command runnable
    rather than blocking it on a name this table has not heard of.
    """
    text = (command or "").strip()
    if not text:
        return ""
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    index = 0
    while index < len(tokens):
        token = tokens[index].strip().strip(";")
        index += 1
        if not token or token in _SKIPPED_TOKENS:
            continue
        if token in _TIMEOUT_TOKENS:
            # `timeout -k 5 900 cmd`: step over the flags and the budget.
            while index < len(tokens) and (
                tokens[index].startswith("-") or tokens[index].isdigit()
            ):
                index += 1
            continue
        if "=" in token and not token.startswith(("-", "/", "./")):
            continue  # FOO=1 prefix
        name = token.rsplit("/", 1)[-1]
        runtime = _COMMAND_RUNTIME.get(name)
        if runtime:
            return runtime
    return "bash" if has_bashism(text) else "sh"


def declared_runtime_for_binary(command: str) -> str:
    """The runtime named by a command's *leading* binary, or an empty string.

    Deliberately narrower than :func:`runtime_for_command`, which walks past
    wrappers and falls back to a shell. Here only the first token counts and
    there is no fallback, so a prose sentence that happens to mention ``go``
    halfway through does not acquire a runtime requirement — while
    ``docker build .`` does.

    That distinction is what keeps the audit honest about a hint the
    executability heuristic drops: without it, a command naming a runtime the
    runner will never have is filed as "settled by reading the repository",
    which promises a check nothing is ever going to run.
    """
    text = (command or "").strip()
    if not text:
        return ""
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return ""
    return _COMMAND_RUNTIME.get(tokens[0].strip().strip(";").rsplit("/", 1)[-1], "")


def has_bashism(command: str) -> bool:
    """Whether a command uses syntax dash cannot parse."""
    return any(token in (command or "") for token in _BASHISMS)


def shell_for_command(command: str) -> str:
    """The interpreter that should run this command: ``bash`` or ``sh``.

    ``-c`` either way — a *login* shell resets PATH and would drop the runner
    image's ``/opt/venv/bin``. Only ``argv[0]`` varies, and only when the
    command needs it: everything that ran under ``sh`` before still does.
    """
    text = command or ""
    if not (has_bashism(text) or runtime_for_command(text) == "bash"):
        return "sh"
    # Same portability rule `validation._portable_timeout` applies: prefer the
    # right tool, fall back rather than fail when the host lacks it.
    return "bash" if shutil.which("bash") else "sh"


def compare_runtime_version(required: str, available: str) -> str:
    """How a required runtime version relates to the one present.

    ``exact`` only when the available version starts with every component the
    requirement names, so ``3.2`` against ``3.2.57`` is exact and ``3.2``
    against ``5.2.37`` is ``partial``. ``unknown`` when either side is silent —
    a comparison nobody can make is not a pass.
    """
    want, have = clean_version(required), (available or "").strip()
    if not want or not have:
        return "unknown"
    wanted = want.split(".")
    present = have.split(".")
    if len(wanted) > len(present):
        return "partial"
    return "exact" if present[: len(wanted)] == wanted else "partial"
