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
from harness_lab_app.native_auth import (
    CONTAINER_AGENT_HOME,
    CONTAINER_AUTH_ROOT,
    NATIVE_SESSION_HARNESSES,
    harness_env,
    return_run_session,
    stage_run_session,
)

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
    ".gemini/skills",
)

# The one catalog each harness reads in the runner. claude and opencode read
# `.claude/skills` (opencode scans it among others). codex reads
# `.codex/skills` *and* `.agents/skills` from the project root down to its cwd,
# and gemini `.gemini/skills` *and* `.agents/skills` (verified with
# `codex debug prompt-input` and `gemini skills list`, codex-cli 0.171.0 /
# gemini-cli 0.70.0). Keeping one directory per harness — and masking the
# shared `.agents/skills` alias — is what makes "one catalog in the system
# prompt" true for every harness, not just the first two. Masking codex's own
# directory too (the old behaviour) left a codex `lab_root` run with **no** lab
# skills at all, while the claude arm had all of them.
RUNNER_SKILLS_DIR_FOR_HARNESS: dict[str, str] = {
    "claude": ".claude/skills",
    "opencode": ".claude/skills",
    "codex": ".codex/skills",
    "gemini": ".gemini/skills",
    # agy discovers workspace skills in `.agents/skills` and nowhere else
    # (not `.gemini/skills`, not `.claude/skills`: verified on agy 1.5.14 by
    # capturing the system prompt it sends), so that mirror is the one kept.
    "antigravity_cli": ".agents/skills",
}


# Files in the lab tree that hold *other* providers' credentials: `.env`
# (gateway token, Claude OAuth token, OpenAI key, Langfuse secret...) and the
# Claude settings whose custom headers carry a gateway key. The whole repo is
# bound at /app, so an agent can read them in any run. An API-key harness
# (antigravity_cli) gets them masked with empty read-only binds: its own key is
# the only secret its container holds. bench_run does not need either file in
# that lane: the executor passes every value it reads through the environment.
API_KEY_HARNESSES: frozenset[str] = frozenset({"antigravity_cli"})
RUNNER_SECRET_FILES: tuple[str, ...] = (
    ".env",
    ".claude/settings.local.json",
    # The lab database (run history, configs) and the SQLite sidecars.
    "data/app/harness_lab.db",
    "data/app/harness_lab.db-wal",
    "data/app/harness_lab.db-shm",
)
# Directories holding credentials or copies of `.env`: the Settings page's
# `.env` backups, the native-connect state (an API-key drop in flight, login
# records) and the lab's own archives (a backup tarball contains `.env`).
# Emptied with a tmpfs. `data/app` itself stays, because bench_run takes its
# skill-views lock there and that lock must be the host's.
RUNNER_SECRET_DIRS: tuple[str, ...] = (
    "data/app/env-backups",
    "data/app/native-connect",
    "backups",
)


def runner_secret_masks(
    harness: str, tmpfs: dict[str, str], mount_root: str = "/app"
) -> list[tuple[str, str]]:
    """Empty-file masks over the lab's credential files, for an API-key harness.

    A file under a directory ``tmpfs`` already masks is skipped (it is gone
    already, and Docker rejects a bind whose parent is a fresh tmpfs).
    """
    if harness not in API_KEY_HARNESSES:
        return []
    root = mount_root.rstrip("/")
    masks: list[tuple[str, str]] = []
    for relative in RUNNER_SECRET_FILES:
        target = f"{root}/{relative}"
        if any(target.startswith(f"{masked}/") for masked in tmpfs):
            continue
        masks.append((target, _mask_kind(relative)))
    return masks


def runner_secret_dirs(
    harness: str, run_id: str, tmpfs: dict[str, str], mount_root: str = "/app"
) -> dict[str, str]:
    """tmpfs over the lab's credential directories, for an API-key harness.

    Also covers every *other* run's private provider session that exists when
    this container starts (``data/runs/<id>/session``, a codex/gemini run in
    flight on the parallel lane): a copy of someone's ChatGPT or Google login
    must not be readable by an agent holding an API key, nor the reverse.
    Only the directories that exist are masked (Docker would otherwise create
    them inside the operator's checkout).
    """
    if harness not in API_KEY_HARNESSES:
        return {}
    root = mount_root.rstrip("/")
    repo = get_settings().repo_root
    relatives = list(RUNNER_SECRET_DIRS)
    runs = get_settings().data_runs_dir
    with contextlib.suppress(OSError):
        # Another run's provider session. Another antigravity_cli run's copy
        # of the Google sign-in is masked for every harness alike
        # (agy_account_copy_masks).
        for session in sorted(runs.glob("*/session")):
            if session.parent.name != run_id and session.is_dir():
                relatives.append(str(session.relative_to(repo)))
    masks: dict[str, str] = {}
    for relative in relatives:
        target = f"{root}/{relative}"
        if target in tmpfs or any(target.startswith(f"{m}/") for m in tmpfs):
            continue
        if (repo / relative).is_dir():
            masks[target] = ""
    return masks


def agy_account_copy_masks(
    run_id: str, tmpfs: dict[str, str], mount_root: str = "/app"
) -> dict[str, str]:
    """tmpfs over every *other* run's copy of the Antigravity Google sign-in.

    An antigravity_cli run on the operator's sign-in (ADR-0097) works on a
    private copy under ``data/runs/<id>/agy-home``, inside the repo bind every
    run container shares; a sign-in in progress keeps its home and its code
    exchange there too. No other container — of any harness — may read them,
    so each one that exists when a container starts is emptied in it.
    """
    root = mount_root.rstrip("/")
    repo = get_settings().repo_root
    runs = get_settings().data_runs_dir
    masks: dict[str, str] = {}
    with contextlib.suppress(OSError):
        for pattern in ("*/agy-home", "*/agy-exchange"):
            for copy in sorted(runs.glob(pattern)):
                if copy.parent.name == run_id or not copy.is_dir():
                    continue
                target = f"{root}/{copy.relative_to(repo).as_posix()}"
                if target in tmpfs or any(target.startswith(f"{m}/") for m in tmpfs):
                    continue
                masks[target] = ""
    return masks


def runner_skill_masks(mount_root: str = "/app", harness: str = "") -> dict[str, str]:
    """tmpfs mounts that hide every duplicate skill catalog from a run.

    Keys are absolute container paths, values the docker-py tmpfs options
    string (empty = defaults). Mounted over the repo bind, so the harness
    discovers exactly one catalog: its own
    (:data:`RUNNER_SKILLS_DIR_FOR_HARNESS`, :data:`RUNNER_SKILLS_DIR` when the
    harness is unknown — the historical behaviour).
    """
    root = mount_root.rstrip("/")
    kept = RUNNER_SKILLS_DIR_FOR_HARNESS.get(harness, RUNNER_SKILLS_DIR)
    hidden = {*RUNNER_MASKED_SKILL_DIRS, RUNNER_SKILLS_DIR} - {kept}
    return {f"{root}/{directory}": "" for directory in sorted(hidden)}


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
    ".gemini",
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
# The file `bench_run.py` writes to name the active run (and opencode reads),
# and the per-run copy each agent container binds over it.
BENCH_CONTEXT_FILE = "BENCH_CONTEXT.md"
PRIVATE_CONTEXT_FILE = "bench_context.md"
# What `make bench-start` (scripts/bench_start.py) writes when no run is active;
# the web app cannot import that script, so the text is repeated here.
BENCH_CONTEXT_PLACEHOLDER = (
    "<!-- No active benchmark run. Generated by make bench-start. -->\n"
)

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
    harness: str = "",
) -> tuple[dict[str, str], list]:
    """Every mask a run gets: the skill mirrors plus its governance level.

    ``hidden`` adds the grading masks (:func:`runner_grading_masks`). It is
    ``None`` for every container that is not the agent's. ``harness`` decides
    which skill mirror stays visible (:func:`runner_skill_masks`).
    """
    governance, files = runner_governance_masks(level, mount_root)
    tmpfs = dict(governance)
    for path, options in runner_skill_masks(mount_root, harness).items():
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


def build_run_environment(harness: str = "") -> dict[str, str]:
    """Environment for a run container: .env values with host URLs rewritten.

    `scripts/create_run.py:_load_env` uses `os.environ.setdefault`, so values
    injected here win over the `.env` file bind-mounted inside the container.

    ``harness`` codex/gemini: every variable that would switch the CLI from its
    subscription login to per-token billing is dropped, and the CLI is pointed
    at its provider's session mount (:mod:`harness_lab_app.native_auth`).
    ``harness`` antigravity_cli: every foreign credential is dropped and the
    stored Gemini API key is the only secret added
    (:mod:`harness_lab_app.api_key_auth`). Other harnesses get exactly the
    environment they always had.
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
    # Marks the runner lane, so bench_run picks the container-sandbox flags
    # (codex cannot use its own bubblewrap sandbox inside a container).
    env["HARNESS_LAB_IN_RUNNER"] = "1"
    if harness in NATIVE_SESSION_HARNESSES:
        env = harness_env(
            harness,
            env,
            session_root=f"{CONTAINER_AUTH_ROOT}/{harness}",
            home=CONTAINER_AGENT_HOME,
        )
        env["HARNESS_LAB_AGENT_HOME"] = CONTAINER_AGENT_HOME
    elif harness in API_KEY_HARNESSES:
        # Every other provider's credential out. On the operator's Google
        # sign-in (ADR-0097) no key at all: agy reads its token from the run's
        # private copy of the account home, mounted at CONTAINER_ACCOUNT_HOME.
        # On a Gemini API key (ADR-0096) the stored key is the only secret
        # added, with an empty home inside the container's own filesystem,
        # which dies with it: agy writes its conversations, logs and databases
        # there (api_key_auth).
        from harness_lab_app.agy_account import (
            AUTH_ACCOUNT,
            CONTAINER_ACCOUNT_HOME,
            auth_mode,
        )
        from harness_lab_app.api_key_auth import agy_env, read_key

        if auth_mode() == AUTH_ACCOUNT:
            env = agy_env(env, key="", home=CONTAINER_ACCOUNT_HOME)
            env.pop("GEMINI_API_KEY", None)
            env["HARNESS_LAB_AGENT_HOME"] = CONTAINER_ACCOUNT_HOME
            env["HARNESS_LAB_AGY_AUTH"] = AUTH_ACCOUNT
        else:
            env = agy_env(env, key=read_key(), home=CONTAINER_AGENT_HOME)
            env["HARNESS_LAB_AGENT_HOME"] = CONTAINER_AGENT_HOME
    return env


#: Executor labels of the Antigravity sign-in container (``connect-<h>-login-*``).
AGY_LOGIN_PREFIX = "connect-antigravity_cli-login"


def agy_login_environment(env: dict[str, str]) -> dict[str, str]:
    """The sign-in container's environment: no key, an empty home, the exchange.

    Built from a run's (no foreign credential, auto-update off). The Gemini API
    key is removed: with it agy would not offer the Google sign-in at all.
    """
    from harness_lab_app.agy_account import (
        CODE_LIFETIME_SECONDS,
        CONTAINER_ACCOUNT_HOME,
        CONTAINER_EXCHANGE_DIR,
        URL_WAIT_SECONDS,
    )

    out = {k: v for k, v in env.items() if k != "GEMINI_API_KEY"}
    out.update(
        HOME=CONTAINER_ACCOUNT_HOME,
        HARNESS_LAB_AGENT_HOME=CONTAINER_ACCOUNT_HOME,
        AGY_LOGIN_EXCHANGE=CONTAINER_EXCHANGE_DIR,
        AGY_LOGIN_WORKDIR=f"{CONTAINER_ACCOUNT_HOME}/work",
        AGY_LOGIN_URL_WAIT=str(URL_WAIT_SECONDS),
        AGY_LOGIN_CODE_WAIT=str(CODE_LIFETIME_SECONDS),
        TERM="xterm-256color",
    )
    return out


def agy_account_mounts(run_id: str, host_repo_root: str) -> list[Any]:
    """The account home of an antigravity_cli container on the Google sign-in.

    * A run, a test or a model listing: a private copy of the stored sign-in
      (:func:`agy_account.stage_run_home`) under ``data/runs/<id>/agy-home``,
      bound read-write at ``CONTAINER_ACCOUNT_HOME``. :class:`_DockerHandle`
      returns a refreshed token to the store and deletes the copy on ``wait``.
    * The sign-in itself (``run_id`` starts with :data:`AGY_LOGIN_PREFIX`): an
      empty home, plus the exchange directory the worker drops the pasted
      code into. The worker keeps what the sign-in wrote
      (:func:`agy_account.save_account`).

    Empty when no sign-in is stored and this is not the sign-in: the run is
    on an API key.
    """
    from docker.types import Mount

    from harness_lab_app.agy_account import (
        CONTAINER_ACCOUNT_HOME,
        CONTAINER_EXCHANGE_DIR,
        account_status,
        stage_run_home,
    )

    settings = get_settings()
    base = settings.data_runs_dir / run_id
    home = base / "agy-home"
    mounts: list[Any] = []
    if run_id.startswith(AGY_LOGIN_PREFIX):
        import shutil

        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True)
        home.chmod(0o700)
        exchange = base / "agy-exchange"
        exchange.mkdir(parents=True, exist_ok=True)
        exchange.chmod(0o700)
        mounts.append(
            Mount(
                target=CONTAINER_EXCHANGE_DIR,
                source=_host_path(exchange, settings.repo_root, host_repo_root),
                type="bind",
                read_only=False,
            )
        )
    elif account_status()["connected"]:
        stage_run_home(home)
    else:
        return []
    mounts.append(
        Mount(
            target=CONTAINER_ACCOUNT_HOME,
            source=_host_path(home, settings.repo_root, host_repo_root),
            type="bind",
            read_only=False,
        )
    )
    return mounts


def native_session_mount(harness: str, run_id: str, host_repo_root: str) -> Any | None:
    """A read-write bind of a *private copy* of this provider's session, or None.

    Only the run's own provider is mounted: a codex run never sees the gemini
    session and vice versa. The store itself is never mounted into a run. The
    worker copies the credential and the lab's config into
    ``data/runs/<run>/session/<provider>`` (inside the repo bind the daemon
    already shares), and :class:`_DockerHandle` writes a refreshed credential
    back when the run ends and deletes the copy. Anything else the agent writes
    in its CLI home — a gemini memory in ``GEMINI.md``, a codex ``AGENTS.md`` or
    skill — dies with the run, so it can never reach another run or arm
    (ADR-0094).
    """
    if harness not in NATIVE_SESSION_HARNESSES:
        return None
    from docker.types import Mount

    settings = get_settings()
    staged = stage_run_session(
        harness, settings.data_runs_dir / run_id / "session" / harness
    )
    return Mount(
        target=f"{CONTAINER_AUTH_ROOT}/{harness}",
        source=_host_path(staged, settings.repo_root, host_repo_root),
        type="bind",
        read_only=False,
    )


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
        case_dir: str = "",
        harness: str = "",
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
        hidden: list[str] | None = None,
        case_dir: str = "",  # noqa: ARG002
        harness: str = "",  # noqa: ARG002 - the runner builds its own env on the host
    ) -> RunHandle:
        # The masks are a container's: the host lane is the escape hatch and
        # the test backend, and it has no mount to hide anything with. Both
        # refuse loudly rather than run unmasked: a masked *condition* would be
        # silently unmasked, and a case's hidden grader would be readable by the
        # agent it grades (ADR-0079). An empty list is the default every run
        # passes and hides nothing case-specific, so it is accepted.
        if hidden:
            raise RuntimeError(
                "This case hides its grading from the agent "
                f"({', '.join(hidden)}), which needs the docker backend "
                "(HARNESS_LAB_EXECUTION_BACKEND=docker); the host lane cannot "
                "mask it."
            )
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
    def __init__(
        self,
        container: Any,
        session: tuple[str, Path] | None = None,
        secret: str = "",
        agy_home: Path | None = None,
    ):
        self.container = container
        self._stopped = False
        # (harness, staged session dir) for a codex/gemini run: the refreshed
        # credential goes back to the store once the container is done.
        self._session = session
        # An antigravity_cli run on the Google sign-in: its private home, whose
        # refreshed token returns to the store (agy_account.return_run_home).
        self._agy_home = agy_home
        # The API key an antigravity_cli container was given: never let it
        # through to a RunEvent, a failure reason or a progress record, even if
        # the agent printed it (api_key_auth).
        self._secret = secret

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
                    yield ("info", self._redact(line))
        tail = buffer.decode("utf-8", errors="replace").strip()
        if tail:
            yield ("info", self._redact(tail))

    def _redact(self, line: str) -> str:
        if self._secret and self._secret in line:
            return line.replace(self._secret, "***REDACTED***")
        return line

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
            if self._session is not None:
                harness, staged = self._session
                self._session = None
                return_run_session(harness, staged)
            if self._agy_home is not None:
                from harness_lab_app.agy_account import return_run_home

                home, self._agy_home = self._agy_home, None
                return_run_home(home)


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
        case_dir: str = "",
        harness: str = "",
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
        tmpfs, mask_files = runner_masks(
            governance_mask, hidden=hidden, harness=harness
        )
        # Every container, the validation lane included (it runs an audited
        # repository's own code with /app bound): another run's copy of the
        # Antigravity sign-in or its code exchange is never readable.
        tmpfs.update(agy_account_copy_masks(run_id, tmpfs))
        if not hardened:
            tmpfs.update(runner_secret_dirs(harness, run_id, tmpfs))
            known = {target for target, _kind in mask_files}
            mask_files.extend(
                mask
                for mask in runner_secret_masks(harness, tmpfs)
                if mask[0] not in known
            )
        extra: dict[str, Any] = {}
        mounts: list[Any] = []
        if mask_files:
            mounts.extend(self._mask_mounts(run_id, host_repo_root, mask_files))
        # The agent's container (`hidden` is passed for it alone) gets its own
        # BENCH_CONTEXT.md unless a mask already covers it: two runs at once
        # would otherwise share the file that names the active run. Decided on
        # the mounts actually made, not on the mask list: `_mask_mounts` drops a
        # target that does not exist, which on a clean checkout is this file.
        if hidden is not None and not any(
            mount["Target"] == f"/app/{BENCH_CONTEXT_FILE}" for mount in mounts
        ):
            mounts.append(self._private_context_mount(run_id, host_repo_root))
        # The case's own folder is read-only for its agent (ADR-0087): its
        # task.yaml, prompts and starter define the measurement. A review run
        # appended checks to its own task.yaml when it could not find the
        # (masked) grader. The hidden paths inside it stay tmpfs: Docker mounts
        # by increasing depth, so they land over this bind.
        if hidden is not None and case_dir:
            readonly = self._case_dir_mount(case_dir, host_repo_root)
            if readonly is not None:
                mounts.append(readonly)
        # The run's own provider session, for a subscription-backed CLI only
        # (codex, gemini). Never on the hardened lane: it runs code the lab
        # did not write and calls no model.
        session = (
            None if hardened else native_session_mount(harness, run_id, host_repo_root)
        )
        if session is not None:
            mounts.append(session)
        # antigravity_cli on the operator's Google sign-in (ADR-0097): the
        # run's private copy of the account home, or the sign-in's own empty
        # home and code exchange. Nothing on the hardened lane.
        agy_mounts = (
            agy_account_mounts(run_id, host_repo_root)
            if harness in API_KEY_HARNESSES and not hardened
            else []
        )
        mounts.extend(agy_mounts)
        agy_login = harness in API_KEY_HARNESSES and run_id.startswith(AGY_LOGIN_PREFIX)
        if mounts:
            extra["mounts"] = mounts
        # The agent lane is unchanged: full `.env`, the gateway alias, its
        # network. The hardened lane is the one running code the lab did not
        # write, and it gets neither the secrets nor, on `validate`, the
        # network.
        if hardened:
            extra.update(hardened_container_options(phase))
        environment = (
            sanitized_run_environment() if hardened else build_run_environment(harness)
        )
        if agy_login:
            environment = agy_login_environment(environment)
        container = client.containers.run(
            image=image,
            command=command,
            detach=True,
            name=name,
            working_dir="/app",
            volumes={host_repo_root: {"bind": "/app", "mode": "rw"}},
            tmpfs=tmpfs,
            environment=environment,
            extra_hosts={settings.docker_host_alias: "host-gateway"},
            labels={"harness-lab.run-id": run_id},
            **extra,
        )
        secret = ""
        if (
            harness in API_KEY_HARNESSES
            and not hardened
            and environment.get("GEMINI_API_KEY")
        ):
            from harness_lab_app.api_key_auth import read_key

            secret = read_key()
        # A run on the Google sign-in hands a refreshed token back to the
        # store; the sign-in container's home is the worker's to collect.
        agy_home = (
            settings.data_runs_dir / run_id / "agy-home"
            if agy_mounts and not agy_login
            else None
        )
        return _DockerHandle(
            container,
            session=(
                (harness, settings.data_runs_dir / run_id / "session" / harness)
                if session is not None
                else None
            ),
            secret=secret,
            agy_home=agy_home,
        )

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
    def _case_dir_mount(case_dir: str, host_repo_root: str) -> Any:
        """A read-only bind of ``benchmarks/<case>/`` over itself, or None.

        Only a directory directly under ``benchmarks/`` that exists qualifies;
        anything else is ignored rather than mounted, so a bad value can never
        widen what the agent sees or create a directory in the checkout.
        """
        from docker.types import Mount

        settings = get_settings()
        parts = [part for part in case_dir.strip().split("/") if part]
        if len(parts) != 2 or parts[0] != "benchmarks" or parts[1] in (".", ".."):
            return None
        local = settings.repo_root / parts[0] / parts[1]
        if not local.is_dir():
            return None
        return Mount(
            target=f"/app/{parts[0]}/{parts[1]}",
            source=_host_path(local, settings.repo_root, host_repo_root),
            type="bind",
            read_only=True,
        )

    @staticmethod
    def _private_context_mount(run_id: str, host_repo_root: str) -> Any:
        """A writable, per-run ``BENCH_CONTEXT.md`` bound over the shared one.

        ``bench_run.py`` writes "the active run is <id>, write only in
        workspace/<id>/" to the repo root, and opencode reads it every session.
        One file for every run is safe only while one run executes at a time;
        with two (a parallel model comparison, ADR-0082) the second overwrites
        the first's and an agent can be pointed at the other's workspace. Each
        agent container therefore writes and reads a copy under its own run
        directory, and the repo's file is never touched.

        The file is gitignored, so a clean checkout has none. It is then created
        with the placeholder ``make bench-start`` writes, rather than skipping
        the bind: without it ``bench_run.py`` would create the shared file
        through the ``/app`` bind and two runs at once would share it again. A
        bind target must exist anyway, or Docker creates it as a directory.
        """
        from docker.types import Mount

        settings = get_settings()
        shared = settings.repo_root / BENCH_CONTEXT_FILE
        if not shared.is_file():
            shared.write_text(BENCH_CONTEXT_PLACEHOLDER)
        own = settings.data_runs_dir / run_id / PRIVATE_CONTEXT_FILE
        own.parent.mkdir(parents=True, exist_ok=True)
        if not own.exists():
            own.write_text(shared.read_text())
        return Mount(
            target=f"/app/{BENCH_CONTEXT_FILE}",
            source=_host_path(own, settings.repo_root, host_repo_root),
            type="bind",
            read_only=False,
        )

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
