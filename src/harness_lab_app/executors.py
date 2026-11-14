"""Execution backends for benchmark runs.

The Celery task talks to a `RunExecutor` port; two adapters implement it:

- `SubprocessRunExecutor`: runs the command as a local child process (used by
  tests and as an escape hatch via HARNESS_LAB_EXECUTION_BACKEND=subprocess).
- `DockerRunExecutor`: one Docker container per run (SWE-bench style), created
  through the host daemon (docker-out-of-docker), bind-mounting the host repo
  so workspace/ and data/runs/ artifacts land on the host filesystem.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import threading
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any, Protocol

from harness_lab_app import governance_paths
from harness_lab_app.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_LOCALHOST_RE = re.compile(r"\blocalhost\b|\b127\.0\.0\.1\b")

# Env vars that point at services running on the host machine; inside a run
# container "localhost" must be rewritten to the docker host alias.
_HOST_URL_ENV_VARS = (
    "GATEWAY_BASE_URL",
    "LMSTUDIO_BASE_URL",
    "OLLAMA_BASE_URL",
    "LANGFUSE_HOST",
    "MLFLOW_TRACKING_URI",
    # Claude Code's gateway base, carried in .claude/settings.local.json rather
    # than .env — see `claude_settings_env`.
    "ANTHROPIC_BASE_URL",
)


# The one skill catalog a run is allowed to see, and the mirrors of it that must
# stay hidden.
#
# `adapters/registry.toml` materializes the same governed skills once per AI
# tool — `.claude/skills` (symlinks), `.agents/skills` (copies, Antigravity
# cannot follow symlinks), `.opencode/skills` and `.codex/skills`. On the host
# that is correct: each tool reads its own directory. In the runner it is not,
# because the whole repo is bind-mounted at /app and opencode scans all four,
# so the same 24 skills are discovered four times.
#
# The cost is not the "duplicate skill name" warnings (240 of them in run
# tetris-v1-K-064); it is that the skill catalog is part of the system prompt,
# re-sent and re-prefilled on every turn of every step. Four copies of 428 KB
# of SKILL.md is minutes per turn against a local model.
#
# Masking is done with empty tmpfs mounts rather than by deleting directories,
# because the host tree is the tools' source of truth and a run must never
# mutate it. `tests/harness_lab_app/test_executors.py` asserts this list still
# covers every tool in the registry.
RUNNER_SKILLS_DIR = ".claude/skills"
RUNNER_MASKED_SKILL_DIRS = (
    ".agents/skills",
    ".opencode/skills",
    ".codex/skills",
)


def runner_skill_masks(mount_root: str = "/app") -> dict[str, str]:
    """tmpfs mounts that hide every duplicate skill catalog from a run.

    Keys are absolute container paths, values the docker-py tmpfs options
    string (empty = defaults). Mounted over the repo bind, so opencode
    discovers exactly one catalog: :data:`RUNNER_SKILLS_DIR`.
    """
    root = mount_root.rstrip("/")
    return {f"{root}/{directory}": "" for directory in RUNNER_MASKED_SKILL_DIRS}


# The lab's governance surface, and how much of it a run's condition hides
# (ADR-0049).
#
# `native`, `injected` and `bare` all run the agent from `workspace/<run_id>/`,
# which lives *under* the repo root that is bind-mounted whole at /app. Claude
# Code does not load `.claude/` from an ancestor directory — two archived native
# runs prove it: their `system/init` lists only the built-in agents and no lab
# slash commands, and no SessionStart hook fired. But it *does* load `/app/.mcp.json`
# (both runs came up with the lab's context7 and git servers and had no
# `.mcp.json` of their own), and its memory lookup collects CLAUDE.md from every
# ancestor. So "the agent's cwd is elsewhere" was never the whole isolation.
#
# Masking with empty tmpfs (directories) and read-only empty-file binds (files)
# rather than deleting anything: the host tree is the source of truth for the
# tools and for `make sync-skills`, and a run must never mutate it. It is also
# the only lever that exists — no Claude Code flag turns off ancestor CLAUDE.md
# lookup — and it is tool-agnostic, which a per-CLI flag would not be.
#
# Three levels, because "hide the harness" means different things:
GOVERNANCE_MASK_NONE = "none"  # lab_root: the lab's harness IS the treatment
GOVERNANCE_MASK_ANCESTORS = "ancestors"  # native/injected: hide the lab, keep .github
GOVERNANCE_MASK_ALL = "all"  # bare: hide everything

# Directories an AI tool auto-loads. Masked at every level above `none`.
#
# The bare-root entries (`skills`, `hooks`) and the alternative ADR roots come
# from `governance_paths`, which is also what `governance._GLOBS` counts as the
# reading surface. A tripwire in `test_executors.py` asserts the two agree: a
# document the surface counts but the mask misses is a document a `bare` run
# can read, and the arm that exists to see nothing would see something. These
# paths address the *lab's* tree at `/app`; a target repository's own root
# `skills/` lives under `/app/workspace/<run_id>/` and is never masked.
RUNNER_GOVERNANCE_DIRS = (
    ".claude",
    ".agents",
    ".opencode",
    ".codex",
    "memory",
    *(root.rstrip("/") for root in governance_paths.BARE_SKILL_ROOTS),
    *(root.rstrip("/") for root in governance_paths.BARE_HOOK_ROOTS),
    *(root.rstrip("/") for root in governance_paths.ADR_ROOTS),
    # Plugin-shipped skills and agents are governance the surface counts, so
    # the lab's own `plugins/` is masked like the roots above. A target's
    # `plugins/` lives under `/app/workspace/<run_id>/` and is never masked.
    governance_paths.PLUGINS_ROOT.rstrip("/"),
)

# Governed *source* directories: nothing auto-loads them, but `bench_run.py`
# reads them to build an in-target harness — `build_effective_skills` discovers
# from `.github/skills`, and `inject_harness`/`add_skills_to_native` project
# from `.github/skills`, `.github/agents` and the registry's governance files.
# Masking them for `native`/`injected` would make an injected arm project
# nothing and turn `--disable-skill X` into "unknown skill". Only `bare`, which
# projects nothing and stages nothing, can afford to lose them.
RUNNER_GOVERNED_SOURCE_DIRS = (".github",)

# Single files an AI tool auto-loads. Mirrors `governance._SINGLE_FILES` plus
# the two the lab itself writes; a test asserts the two lists stay aligned.
#
# `opencode.json` is deliberately absent: it is routing (the gateway provider
# block and per-agent models), not governance. Its governance payload is the
# `instructions` list, and those three files are masked here. Masking it to `{}`
# would also blank `opencode_overlay.read_tracked_gateway`, making the overlay
# delta "every catalog model" and drifting `gateway_overlay_hash` on every
# masked run. `.mcp.json` *is* masked: the MCP servers are lab tooling, so they
# belong to the treatment.
RUNNER_GOVERNANCE_FILES = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "OPENCODE.md",
    "LOCAL_AGENT.md",
    "GEMINI.md",
    "BENCH_CONTEXT.md",
    ".mcp.json",
    ".cursorrules",
    ".windsurfrules",
)

# What an empty mask file holds, by suffix. An empty `.mcp.json` is not "no
# config" but "invalid JSON", which a tool may warn about or refuse to start on.
_MASK_CONTENT = {".json": "{}\n"}
_MASK_DEFAULT_CONTENT = ""


def _mask_kind(relative: str) -> str:
    """The mask file a governance path needs: one host file per content kind."""
    return "json" if relative.endswith(".json") else "text"


def runner_governance_masks(
    level: str, mount_root: str = "/app"
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Mounts that hide the lab's governance from a run.

    Returns ``(tmpfs_dirs, files)`` where ``tmpfs_dirs`` maps container paths to
    docker-py tmpfs option strings and ``files`` pairs a container path with the
    mask kind (:func:`_mask_kind`) it should be bound from.

    Skill-catalog masks whose parent directory is already masked are dropped:
    Docker refuses two mounts with the same destination, and a tmpfs inside a
    tmpfs would be pointless anyway.
    """
    if level == GOVERNANCE_MASK_NONE:
        return {}, []
    root = mount_root.rstrip("/")
    directories = list(RUNNER_GOVERNANCE_DIRS)
    if level == GOVERNANCE_MASK_ALL:
        directories += list(RUNNER_GOVERNED_SOURCE_DIRS)
    tmpfs = {f"{root}/{directory}": "" for directory in directories}
    files = [(f"{root}/{name}", _mask_kind(name)) for name in RUNNER_GOVERNANCE_FILES]
    return tmpfs, files


# What grades the agent, hidden from the agent's own container whatever its
# harness condition (ADR-0079). Calibration solutions live here: a reference
# that passes every check is the answer, and it sits on the very disk the agent
# lane bind-mounts. Git-ignored is not hidden — `cat` does not read .gitignore.
# The validation lane never gets these masks: it has to read the grader.
RUNNER_GRADING_DIRS = ("data/reference",)


def runner_grading_masks(
    hidden: list[str] | tuple[str, ...] = (), mount_root: str = "/app"
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Mounts that keep a case's grade out of the agent's reach.

    ``hidden`` are a case's own paths (``agent_hidden`` in its ``task.yaml``):
    its grader, its rubric, its notes. A path ending in ``/`` or naming a
    directory under the repo is a tmpfs; anything else is an empty-file bind.
    The lab-wide :data:`RUNNER_GRADING_DIRS` are always added.

    Masking the working tree does not hide a *committed* file from
    ``git show`` — the runner needs the lab's ``.git`` for step snapshots — so
    this stops reading, not a determined agent; held-out data is generated
    per run for exactly that reason (ADR-0078).
    """
    root = mount_root.rstrip("/")
    repo = get_settings().repo_root
    tmpfs: dict[str, str] = {}
    files: list[tuple[str, str]] = []
    for relative in (*RUNNER_GRADING_DIRS, *hidden):
        clean = relative.strip().strip("/")
        if not clean:
            continue
        if (
            relative.endswith("/")
            or (repo / clean).is_dir()
            or clean in RUNNER_GRADING_DIRS
        ):
            tmpfs[f"{root}/{clean}"] = ""
        else:
            files.append((f"{root}/{clean}", _mask_kind(clean)))
    return tmpfs, files


def runner_masks(
    level: str,
    mount_root: str = "/app",
    hidden: list[str] | tuple[str, ...] | None = None,
) -> tuple[dict[str, str], list]:
    """Every mask a run gets: the skill mirrors plus its governance level.

    ``hidden`` adds the grading masks (:func:`runner_grading_masks`). It is
    ``None`` for every container that is not the agent's.
    """
    governance, files = runner_governance_masks(level, mount_root)
    tmpfs = dict(governance)
    for path, options in runner_skill_masks(mount_root).items():
        # A skill mirror inside an already-masked directory is redundant, and
        # Docker rejects the duplicate destination.
        if any(path.startswith(f"{masked}/") for masked in governance):
            continue
        tmpfs[path] = options
    if hidden is not None:
        grading_dirs, grading_files = runner_grading_masks(hidden, mount_root)
        masked = tuple(tmpfs)
        for path, options in grading_dirs.items():
            if path in tmpfs or any(path.startswith(f"{m}/") for m in masked):
                continue
            tmpfs[path] = options
        known = {target for target, _kind in files}
        for target, kind in grading_files:
            if target in known or any(target.startswith(f"{m}/") for m in tmpfs):
                continue
            files.append((target, kind))
    return tmpfs, files


def write_mask_sources(run_dir: Path) -> dict[str, Path]:
    """Write the empty files a governance mask is bound from.

    One file per content kind rather than one per masked path: a bind mount
    only needs a source, and Docker creates a *directory* at a source path that
    does not exist yet — which then fails the mount with "not a directory". So
    these must exist before ``containers.run``.
    """
    masks = run_dir / "masks"
    masks.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for kind, suffix in (("text", ".md"), ("json", ".json")):
        path = masks / f"empty{suffix}"
        path.write_text(_MASK_CONTENT.get(suffix, _MASK_DEFAULT_CONTENT))
        written[kind] = path
    return written


def _host_path(path: Path, repo_root: Path, host_repo_root: str) -> str:
    """A worker-side path as the host daemon sees it.

    The worker and the runner both mount the lab at /app, so a path under the
    repo root differs from the host's only in that prefix — the same
    translation `case_spec.json` already relies on.
    """
    relative = path.relative_to(repo_root)
    return f"{host_repo_root.rstrip('/')}/{relative.as_posix()}"


def rewrite_localhost(url: str, alias: str) -> str:
    """Rewrite localhost/127.0.0.1 host references to the docker host alias."""
    return _LOCALHOST_RE.sub(alias, url)


def claude_settings_env(repo_root: Path) -> dict[str, str]:
    """The `env` block of the lab's Claude Code settings, local file winning.

    Tolerant by design: a missing or malformed settings file yields no keys
    rather than failing a run over configuration the run may not even need.
    """
    import json

    merged: dict[str, str] = {}
    for name in ("settings.json", "settings.local.json"):
        path = repo_root / ".claude" / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        block = data.get("env") if isinstance(data, dict) else None
        if isinstance(block, dict):
            merged.update({str(k): str(v) for k, v in block.items()})
    return merged


def build_run_environment() -> dict[str, str]:
    """Environment for a run container: .env values with host URLs rewritten.

    `scripts/create_run.py:_load_env` uses `os.environ.setdefault`, so values
    injected here win over the `.env` file bind-mounted inside the container.
    """
    settings = get_settings()
    # IS_SANDBOX=1: the run container is an isolated sandbox; Claude Code
    # refuses --dangerously-skip-permissions as root unless this is set (same
    # escape hatch Anthropic's devcontainers use). Harmless for other CLIs.
    # SDL_*=dummy makes the container honestly headless. It used to be a line
    # of prose in the case prompt that the agent had to retype on every single
    # invocation — measured compliance was 72% (run K-036) and 40% (K-038), and
    # each miss left a pygame loop running forever, holding opencode's one
    # persistent shell open until the whole run wedged. That is an environment
    # fact, not something to test a model's memory on, so the environment now
    # states it. A case that still passes the prefix by hand is unaffected.
    env: dict[str, str] = {
        "PYTHONUNBUFFERED": "1",
        "IS_SANDBOX": "1",
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
    }
    env_file = settings.repo_root / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    # Claude Code reads .claude/settings*.json from its *project* directory, so
    # a run whose cwd is the workspace never sees the lab's — and silently loses
    # ANTHROPIC_BASE_URL, i.e. the LiteLLM gateway and its metrics headers.
    # Routing must not be a function of the harness condition, so it is
    # injected as container env for every run alike. (`apiKeySource: "none"`
    # in `system/init` is how OAuth auth always reports — not a bypass signal;
    # see the ADR-0049 erratum.)
    env.update(claude_settings_env(settings.repo_root))
    for key in _HOST_URL_ENV_VARS:
        if env.get(key):
            env[key] = rewrite_localhost(env[key], settings.docker_host_alias)
    return env


# Hard limits for a container running an audited repository's own commands.
# Generous enough that a real test suite never notices; low enough that a fork
# bomb or a runaway allocation in untrusted code cannot take the host with it.
_HARDENED_PIDS_LIMIT = 512
_HARDENED_MEMORY = "4g"


def sanitized_run_environment() -> dict[str, str]:
    """Environment for a container running an audited repository's commands.

    Deliberately *not* :func:`build_run_environment`. That one exists for the
    agent lane and carries the whole lab ``.env`` — the gateway token, GitHub
    tokens, Langfuse keys — because the agent lane is where a model is called.
    The validation lane calls no model and runs code the lab did not write, so
    it gets none of that: no ``.env``, no ``.claude/settings*.json``, no host
    environment.

    ``PATH`` is deliberately absent. The container has its own from the image
    (``/opt/venv/bin`` included); passing the host's would point a Linux
    container at macOS directories.
    """
    return {
        "PYTHONUNBUFFERED": "1",
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        # A repository's own suite may branch on CI, and anything reading a
        # TERM it cannot drive prints control codes into the captured output.
        "CI": "1",
        "TERM": "dumb",
        "LANG": "C.UTF-8",
        # Inside the container, not on the host: a writable HOME the audited
        # repository's tooling can put its caches in without reaching the
        # lab's own.
        "HOME": "/tmp",  # nosec B108 - container-local, not a host tempfile
    }


def hardened_container_options(phase: str) -> dict[str, Any]:
    """Docker options for the validation lane, keyed by the command's phase.

    ``network_mode="none"`` applies to ``validate`` only. ``provision`` and
    ``setup`` are the phases that clone and install (``git clone``, ``npm ci``,
    ``uv sync``); cutting their network would not harden anything, it would
    just make every gate check fail at step one.
    """
    options: dict[str, Any] = {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "pids_limit": _HARDENED_PIDS_LIMIT,
        "mem_limit": _HARDENED_MEMORY,
    }
    if phase in ("", "validate"):
        network = (get_settings().validation_network or "none").strip()
        if network:
            options["network_mode"] = network
    return options


class RunHandle(Protocol):
    def iter_lines(self) -> Iterator[tuple[str, str]]:
        """Yield (level, message) pairs until the run's output is exhausted."""
        ...

    def stop(self) -> None:
        """Request termination of the running command."""
        ...

    def wait(self) -> int:
        """Block until the run finishes and return its exit code."""
        ...


class RunExecutor(Protocol):
    def start(
        self,
        run_id: str,
        command: list[str],
        *,
        governance_mask: str = GOVERNANCE_MASK_NONE,
        hardened: bool = False,
        phase: str = "",
        image: str = "",
        hidden: list[str] | None = None,
    ) -> RunHandle: ...


class _SubprocessHandle:
    def __init__(self, process: subprocess.Popen[str]):
        self.process = process
        self._lines: Queue[tuple[str, str]] = Queue()
        self._threads = [
            threading.Thread(
                target=self._pump, args=(process.stdout, "info"), daemon=True
            ),
            threading.Thread(
                target=self._pump, args=(process.stderr, "error"), daemon=True
            ),
        ]
        for thread in self._threads:
            thread.start()

    def _pump(self, stream: Any, level: str) -> None:
        for line in iter(stream.readline, ""):
            self._lines.put((level, line.rstrip("\n")))
        stream.close()

    def iter_lines(self) -> Iterator[tuple[str, str]]:
        while (
            any(thread.is_alive() for thread in self._threads)
            or not self._lines.empty()
        ):
            try:
                yield self._lines.get(timeout=0.1)
            except Empty:
                continue

    def stop(self) -> None:
        self.process.terminate()

    def wait(self) -> int:
        for thread in self._threads:
            thread.join()
        return self.process.wait()


class SubprocessRunExecutor:
    """Run the command as a local child process (legacy worker behavior)."""

    def start(
        self,
        run_id: str,  # noqa: ARG002
        command: list[str],
        *,
        governance_mask: str = GOVERNANCE_MASK_NONE,
        hardened: bool = False,
        phase: str = "",  # noqa: ARG002
        image: str = "",
        hidden: list[str] | None = None,  # noqa: ARG002 - nothing to mount over here
    ) -> RunHandle:
        # The grading masks are a container's: the host lane is the escape
        # hatch and the test backend, and it has no mount to hide anything with.
        # Governance masking refuses loudly because a masked *condition* would
        # be silently unmasked; a grading mask changes no condition.
        if governance_mask != GOVERNANCE_MASK_NONE:
            raise RuntimeError(
                "Governance masking needs the docker backend: there is nothing "
                "to mask the lab's own tree with on the host lane."
            )
        # A case's own image is its toolchain (ADR-0078): a Godot case run on
        # the host would call an engine the host may not have, or grade against
        # a different one. Refused, never silently run without it.
        if image:
            raise RuntimeError(
                f"This case runs in its own image ({image}), which needs the "
                "docker backend (HARNESS_LAB_EXECUTION_BACKEND=docker); the host "
                "lane cannot provide it."
            )
        extra: dict[str, Any] = {}
        if hardened:
            # No container here to drop capabilities in, but the secrets are
            # the part that travels: a host lane that inherited os.environ
            # would hand the audited repository's commands the operator's
            # whole shell. `cwd` stays the lab root, which is what every
            # command's own `cd {workspace}` prefix is relative to.
            extra["env"] = sanitized_run_environment()
            extra["cwd"] = str(get_settings().repo_root)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **extra,
        )
        return _SubprocessHandle(process)


class _DockerHandle:
    def __init__(self, container: Any):
        self.container = container
        self._stopped = False

    def iter_lines(self) -> Iterator[tuple[str, str]]:
        # `logs()` does not demux stdout/stderr; bench_run.py already merges
        # opencode stderr into stdout, so a single "info" level is fine.
        buffer = b""
        for chunk in self.container.logs(stream=True, follow=True):
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                if line:
                    yield ("info", line)
        tail = buffer.decode("utf-8", errors="replace").strip()
        if tail:
            yield ("info", tail)

    def stop(self) -> None:
        self._stopped = True
        self.container.stop(timeout=10)

    def wait(self) -> int:
        try:
            result = self.container.wait()
            return int(result.get("StatusCode", -1))
        finally:
            # Cleanup is best-effort; the container may already be gone.
            with contextlib.suppress(Exception):
                self.container.remove(force=True)


def container_name_for_run(run_id: str) -> str:
    # Docker names only allow [a-zA-Z0-9][a-zA-Z0-9_.-]*.
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", run_id)
    return f"harness-run-{safe}"


class DockerRunExecutor:
    """One container per run, created via the host Docker daemon (DooD)."""

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import docker  # local import: the api process never needs the socket

        return docker.from_env()

    def start(
        self,
        run_id: str,
        command: list[str],
        *,
        governance_mask: str = GOVERNANCE_MASK_NONE,
        hardened: bool = False,
        phase: str = "",
        image: str = "",
        hidden: list[str] | None = None,
    ) -> RunHandle:
        settings = get_settings()
        host_repo_root = settings.host_repo_root or str(settings.repo_root)
        if not host_repo_root:
            raise RuntimeError(
                "HARNESS_LAB_HOST_REPO_ROOT must be set for the docker backend"
            )
        # A case may name its own image (ADR-0078); `case_profile` has already
        # restricted it to the lab's local `harness-lab-*` images.
        image = image or settings.runner_image
        client = self._client()
        self._require_local_image(client, image)
        name = container_name_for_run(run_id)
        self._remove_stale_container(client, name)
        # Empty tmpfs over the duplicate skill catalogs (one catalog in the
        # system prompt instead of four, see RUNNER_MASKED_SKILL_DIRS), plus
        # whatever the run's harness condition hides (ADR-0049).
        # And, for the agent's own container only, whatever would grade it for
        # it (ADR-0079): `hidden` is None for every other container.
        tmpfs, mask_files = runner_masks(governance_mask, hidden=hidden)
        extra: dict[str, Any] = {}
        if mask_files:
            extra["mounts"] = self._mask_mounts(run_id, host_repo_root, mask_files)
        # The agent lane is unchanged: full `.env`, the gateway alias, its
        # network. The hardened lane is the one running code the lab did not
        # write, and it gets neither the secrets nor, on `validate`, the
        # network.
        if hardened:
            extra.update(hardened_container_options(phase))
        container = client.containers.run(
            image=image,
            command=command,
            detach=True,
            name=name,
            working_dir="/app",
            volumes={host_repo_root: {"bind": "/app", "mode": "rw"}},
            tmpfs=tmpfs,
            environment=(
                sanitized_run_environment() if hardened else build_run_environment()
            ),
            extra_hosts={settings.docker_host_alias: "host-gateway"},
            labels={"harness-lab.run-id": run_id},
            **extra,
        )
        return _DockerHandle(container)

    @staticmethod
    def _mask_mounts(
        run_id: str, host_repo_root: str, mask_files: list[tuple[str, str]]
    ) -> list[Any]:
        """Read-only binds of an empty file over each governance file.

        The sources live under the run's own directory, which is inside the
        repo root already shared with Docker, so no new file share is needed.
        Docker mounts by increasing destination depth, so these land after the
        /app bind and after the tmpfs directories.
        """
        from docker.types import Mount

        settings = get_settings()
        sources = write_mask_sources(settings.data_runs_dir / run_id)
        return [
            Mount(
                target=target,
                source=_host_path(sources[kind], settings.repo_root, host_repo_root),
                type="bind",
                read_only=True,
            )
            for target, kind in mask_files
            # Only what is actually there. A bind whose target does not exist
            # makes Docker create it — and the target sits inside the /app bind,
            # so an empty `.cursorrules` would appear in the operator's own
            # checkout. A file that does not exist cannot leak anyway; the
            # constant lists the whole surface so the tripwire tests can check
            # it against `governance.py` and the adapter registry.
            if (settings.repo_root / target.removeprefix("/app/")).exists()
        ]

    @staticmethod
    def _require_local_image(client: Any, image: str) -> None:
        """Fail fast and clearly if the runner image was never built.

        `client.containers.run()` auto-pulls from the registry when an image
        is missing locally, which for a local-only image like
        `harness-lab-runner` fails with a confusing 404 ImageNotFound. Check
        first so the error tells the operator exactly what to run.
        """
        try:
            client.images.get(image)
        except Exception as exc:
            if image != get_settings().runner_image:
                # A case's own image is built on demand, not by `make app-up`.
                raise RuntimeError(
                    f"Runner image '{image}' was not found locally. This case "
                    "needs its own image: build it with `make images-3d` "
                    "(see docs/guide-3d-game-cases.md)."
                ) from exc
            raise RuntimeError(
                f"Runner image '{image}' was not found locally. Build it from "
                "the web UI: Health -> Docker runtime -> 'Build runner image' "
                "(or `make app-up` from a shell)."
            ) from exc

    @staticmethod
    def _remove_stale_container(client: Any, name: str) -> None:
        """Remove a leftover container with the same name (e.g. after retry)."""
        try:
            stale = client.containers.get(name)
        except Exception:
            return
        stale.remove(force=True)


def get_executor() -> RunExecutor:
    settings = get_settings()
    if settings.execution_backend == "subprocess":
        return SubprocessRunExecutor()
    return DockerRunExecutor()
