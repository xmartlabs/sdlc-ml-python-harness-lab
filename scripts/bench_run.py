#!/usr/bin/env python3
"""Unified benchmark runner: prepare → execute → score → close.

This is the CLI backend a future web UI would call. It replaces the manual
bench-start / paste / bench-close flow with a single reproducible command.

Usage (prefer make):
    make bench-run CASE=tetris-v1 RUN=tetris-v1-A-003 PROMPT=incremental \\
        EXECUTOR_MODEL=gateway/lmstudio-qwen3.6-35b-build SCORE_MODE=manual

    # Multi-step: run up to N opencode sessions automatically
    make bench-run ... MAX_STEPS=6 SCORE_MODE=interactive

    # Resume a run whose workspace already exists
    make bench-run ... RESUME=1 SCORE_MODE=interactive

    # Dry run: see everything that would happen, writes nothing
    make bench-run ... DRY_RUN=1

Direct:
    uv run python scripts/bench_run.py \\
        --case tetris-v1 --run tetris-v1-A-003 --prompt incremental \\
        --executor-model gateway/lmstudio-qwen3.6-35b-build --score-mode none
"""

from __future__ import annotations

import argparse
import datetime
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

sys.path.insert(0, str(Path(__file__).parent))

from bench_close import _run_git, update_runs_yaml
from bench_start import (
    BENCH_CONTEXT,
    BENCH_CONTEXT_PLACEHOLDER,
    WORKSPACE_ROOT,
    copy_to_clipboard,
    patch_env_token,
    restore_env_token,
    write_bench_context,
)
from create_run import (
    DATA_DIR,
    REPO_ROOT,
    RUNS_FILES,
    _load_env,
    create_litellm_key,
    scaffold_report,
    set_status_running,
    update_results_csv,
)

from harness_lab_app.provenance import enrich_metadata

try:
    import yaml
except ImportError:
    sys.exit("PyYAML not found. Run with: uv run python scripts/bench_run.py ...")

# The harness a run uses when neither the flag nor the runs.yaml cell names one.
DEFAULT_HARNESS = "opencode"

# ---------------------------------------------------------------------------
# Prompt variant → short name mapping
# ---------------------------------------------------------------------------

VARIANT_TO_SHORT: dict[str, str] = {
    "tetris_naive_v1": "naive",
    "tetris_incremental_v1": "incremental",
    "tetris_sdlc_v1": "sdlc",
    "tetris_sdlc_full_v1": "sdlc_full",
    "tetris_conductor_v1": "conductor",
    "tetris_conductor_small_steps_v1": "conductor",
    "nemotron_planner_v1": "conductor",
    "nemotron_planner_small_steps_v1": "conductor",
}

# ---------------------------------------------------------------------------
# Score sanity check: lightweight patterns to warn on implausible scores.
# (non-blocking by default; warn + confirm in interactive mode)
# ---------------------------------------------------------------------------

SCORE_CHECKS: list[tuple[int, list[str], str]] = [
    (4, ["pygame", "surface", "blit", "draw", "screen"], "pygame / drawing code"),
    (
        5,
        ["tick", "drop", "fall", "gravity", "clock", "move_down"],
        "falling / tick logic",
    ),
    (
        6,
        ["K_LEFT", "K_RIGHT", "pygame.key", "event.key", "KEYDOWN"],
        "left/right key handling",
    ),
    (7, ["rotat"], "rotation logic"),
    (
        8,
        ["collision", "lock", "freeze", "land", "check_collision"],
        "collision / lock logic",
    ),
    (
        9,
        ["clear", "line_clear", "lines_cleared", "score", "level"],
        "line clear / scoring logic",
    ),
]

# Signals that indicate the *entire game* is complete — triggers early exit from the
# multi-step loop.  Must NOT match normal per-increment completion messages like
# "DONE: Increment 1 complete" or "NEXT: open a new chat for increment 2", which the
# incremental prompt instructs the agent to emit after every single step.
_STOP_RE = re.compile(
    r"tetris is (fully )?complete"
    r"|game is (fully )?complete"
    r"|all (8 )?milestones (done|complete|achieved|finished)"
    r"|level 10 (reached|achieved|complete)"
    r"|playable tetris (is )?complete"
    r"|all increments (done|complete|finished)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--case", required=True, help="Benchmark case id, e.g. tetris-v1")
    p.add_argument("--run", required=True, help="Run id, e.g. tetris-v1-A-003")
    p.add_argument(
        "--prompt",
        help="Short prompt name: naive | incremental | conductor. "
        "Auto-detected from runs.yaml if omitted.",
    )
    p.add_argument(
        "--planner-model", default=None, help="Gateway model alias for planner"
    )
    p.add_argument(
        "--executor-model", default=None, help="Gateway model alias for executor"
    )
    p.add_argument(
        "--harness",
        default=None,
        choices=["opencode", "codex", "claude"],
        help=f"Agent CLI that drives the run (default: {DEFAULT_HARNESS}). All harnesses (opencode, codex, "
        "claude) run inside the Docker runner under `make app-up`; claude auth "
        "comes from .env (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY). Host mode "
        "(`make app-host`, subprocess backend) is an alternative for driving a "
        "host CLI's interactive/native auth. See docs/harness-adapters.md.",
    )
    p.add_argument(
        "--score-mode",
        choices=["manual", "interactive", "none"],
        default="manual",
        help="manual=prepare+execute only; interactive=prompt for score after run; "
        "none=prepare only, no execution",
    )
    p.add_argument(
        "--score", type=int, default=None, help="Score 0-10 (skips interactive prompt)"
    )
    p.add_argument(
        "--interventions", type=int, default=None, help="Human interventions count"
    )
    p.add_argument("--notes", default="", help="Optional notes string")
    p.add_argument(
        "--max-steps",
        type=int,
        default=1,
        metavar="N",
        help="Max number of opencode execution steps (default: 1). "
        "For incremental benchmarks, set to 6-10 to chain multiple increments automatically.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Allow running into an existing workspace (skip workspace existence check). "
        "Use when a previous run was interrupted and you want to continue.",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help="Managed/automated run (used by the web runner): reset a stale "
        "workspace on start instead of exiting, and archive+clean the workspace "
        "after a manual-scored run (scoring is owned by the API). CLI manual runs "
        "omit this so `make bench-close` can still find the workspace.",
    )
    p.add_argument(
        "--disable-skill",
        action="append",
        default=[],
        metavar="NAME",
        help="Hide a governed skill from the agent for this run (repeatable). "
        "The native skill views and adapter files are regenerated without it "
        "and restored after the run (ablation lane).",
    )
    p.add_argument(
        "--extra-skill",
        action="append",
        default=[],
        metavar="NAME",
        help="Add a staged skill from data/skills_staging/<NAME>/ for this run "
        "only (repeatable). Restored after the run.",
    )
    p.add_argument(
        "--invoke-skill",
        action="append",
        default=[],
        metavar="NAME",
        help="Tell the agent, at the start of every step, to invoke this skill "
        "and follow it (repeatable). The skill must be available to the run: "
        "governed and not disabled, or added with --extra-skill. Available is "
        "not invoked (ADR-0041).",
    )
    p.add_argument(
        "--create-litellm-key",
        action="store_true",
        help="Attempt to create a LiteLLM virtual key for this run",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Print what would happen; write nothing"
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_task_yaml(runs_path: Path) -> dict:
    task_path = runs_path.parent / "task.yaml"
    if not task_path.exists():
        sys.exit(f"task.yaml not found at {task_path}")
    return yaml.safe_load(task_path.read_text())


def resolve_prompt_file(
    task: dict, short_name: str | None, run: dict
) -> tuple[str, Path]:
    """Return (resolved_short_name, absolute_path_to_prompt_file)."""
    if short_name is None:
        variant = run.get("prompt_variant", "")
        short_name = VARIANT_TO_SHORT.get(variant)
        if not short_name:
            sys.exit(
                f"Cannot auto-detect prompt from variant '{variant}'. "
                f"Pass --prompt explicitly (naive | incremental | conductor)."
            )
    prompts: dict = task.get("prompts", {})
    if short_name not in prompts:
        sys.exit(
            f"Prompt '{short_name}' not found in task.yaml. "
            f"Available: {list(prompts.keys())}"
        )
    return short_name, REPO_ROOT / prompts[short_name]


# Provider error codes and phrases worth naming on their own line when a
# harness dies: the raw error can be kilobytes long (LiteLLM echoes it with its
# fallback table), and the worker keeps only the last 1000 characters.
_CAUSE_MARKERS = (
    "claude_code_version_too_old",
    "Invalid model name",
    "model_not_found",
    "authentication_error",
    "permission_error",
    "rate_limit_error",
    "overloaded_error",
)
_CAUSE_MAX = 300


def harness_error_cause(step_log: Path) -> str | None:
    """One short line naming why a harness CLI died, or ``None``.

    Reads the first ``API Error`` line of the step log and keeps the provider's
    own sentence and error code, so the run's stored failure reason carries the
    cause even when the full error is too long to fit.
    """
    try:
        text = step_log.read_text(errors="replace")
    except OSError:
        return None
    line = next((ln for ln in text.splitlines() if "API Error" in ln), "")
    if not line:
        return None
    code = next((marker for marker in _CAUSE_MARKERS if marker in line), "")
    message = ""
    start = line.find('"message":"')
    if start != -1:
        rest = line[start + len('"message":"') :]
        message = rest[: rest.find('"')] if '"' in rest else rest
    elif code:
        at = line.find(code)
        message = line[at : at + 160]
    summary = " — ".join(part for part in (code, message.strip()) if part)
    if not summary:
        summary = line.strip()
    return f"Cause: {summary}"[:_CAUSE_MAX]


def extract_cell_id(run_id: str) -> str:
    parts = run_id.split("-")
    return parts[2] if len(parts) >= 4 else "?"


def discover_skills() -> list[str]:
    candidates = [
        REPO_ROOT / ".opencode" / "skills",
        REPO_ROOT / ".claude" / "skills",
        REPO_ROOT / ".github" / "skills",
    ]
    found: list[str] = []
    for d in candidates:
        if d.is_dir():
            found.extend(p.name for p in d.iterdir() if p.is_dir())
    return sorted(set(found))


# ---------------------------------------------------------------------------
# Per-run skill ablation (--disable-skill / --extra-skill)
#
# The agent discovers skills from the repo's native views (.opencode/skills,
# CLAUDE.md/OPENCODE.md generated blocks, ...), which are GENERATED artifacts of
# the skills_sync engine. A true ablation therefore regenerates those views
# (symlinks AND adapter-file skill blocks) from a filtered/extended skill list
# before launching the agent, and restores the full views afterwards. The
# source of truth (.github/skills*) is never touched. Sequential-safe because
# the worker runs one container at a time (ADR-0002); a crash is healed by the
# restore-at-start guard on the next run (or a manual `make sync-skills`).
# ---------------------------------------------------------------------------

SKILLS_STAGING_DIR = REPO_ROOT / "data" / "skills_staging"


def _require_masked_lab_root() -> None:
    """Refuse a `bare` run whose lab tree is still readable (ADR-0049).

    A bare arm is bare only because the runner container masks the lab's
    governance: the workspace lives *under* the repo root, so Claude Code would
    otherwise collect `CLAUDE.md` from an ancestor directory and the control arm
    would quietly be a second treatment arm. On the host lane there is nothing
    to mask with, so the run must not proceed rather than record a condition it
    does not have.
    """
    for relative in ("CLAUDE.md", ".claude"):
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        occupied = any(path.iterdir()) if path.is_dir() else path.stat().st_size > 0
        if occupied:
            sys.exit(
                f"❌ harness_source='bare' but {relative} is readable at "
                f"{REPO_ROOT}: this run would see the lab's governance. Bare "
                "runs need the docker backend, where the container masks it."
            )


def build_effective_skills(disabled: list[str], extra: list[str]) -> list:
    """Governed skills minus `disabled`, plus staged `extra`. Fails fast on
    unknown names or collisions, BEFORE any view is mutated."""
    from ml_python_base.skills_sync.discovery import (
        discover_skills as discover_governed,
    )
    from ml_python_base.skills_sync.models import KIND_EXTERNAL, Skill

    governed = discover_governed(REPO_ROOT)
    known = {skill.name for skill in governed}
    unknown = sorted(set(disabled) - known)
    if unknown:
        sys.exit(f"❌ --disable-skill: unknown skill(s): {', '.join(unknown)}")
    collisions = sorted((set(extra) & known) - set(disabled))
    if collisions:
        sys.exit(
            f"❌ --extra-skill: name(s) collide with governed skills: "
            f"{', '.join(collisions)} (disable the governed one first)"
        )
    effective = [skill for skill in governed if skill.name not in set(disabled)]
    for name in sorted(set(extra)):
        source = SKILLS_STAGING_DIR / name
        if not (source / "SKILL.md").is_file():
            sys.exit(
                f"❌ --extra-skill '{name}': no SKILL.md at "
                f"{source.relative_to(REPO_ROOT)}/"
            )
        # The linker projects every top-level item of an external skill, so a
        # symlink here would be copied into each tool's native view and could
        # point anywhere on the host. Staging trees are written from the DB and
        # never contain one; if they do, something else wrote them.
        escaping = sorted(
            item.name
            for item in source.rglob("*")
            if item.is_symlink() and not _within(item.resolve(), source)
        )
        if escaping:
            sys.exit(
                f"❌ --extra-skill '{name}': symlink(s) pointing outside the "
                f"skill: {', '.join(escaping)}"
            )
        effective.append(
            Skill(name=name, kind=KIND_EXTERNAL, source_path=source, description="")
        )
    return effective


def _within(target: Path, root: Path) -> bool:
    resolved = root.resolve()
    return target == resolved or resolved in target.parents


def apply_skill_views(skills: list) -> None:
    """Project `skills` into every tool's native view + adapter files."""
    from ml_python_base.skills_sync.config import DEFAULT_REGISTRY_PATH, load_registry
    from ml_python_base.skills_sync.linker import link_tool
    from ml_python_base.skills_sync.manifest import write_manifest
    from ml_python_base.skills_sync.renderer import render_tool

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    for tool in registry.tools:
        hashes = link_tool(REPO_ROOT, tool, skills)
        if tool.needs_manifest and tool.manifest_path:
            write_manifest(REPO_ROOT / tool.manifest_path, hashes)
        render_tool(REPO_ROOT, registry, tool, skills=skills)


def add_skills_to_native(workspace: Path, skills: list, tool_id: str) -> dict:
    """Put named skills inside a `native` workspace, changing nothing else.

    Under `native` the agent's cwd is the cloned repository, so a skill sitting
    in the lab is invisible to it — which made "their repo, plus this skill" an
    arm that measured nothing at all (the same failure ADR-0041 documents for
    availability-versus-use, one level down). The skills a run stages therefore
    have to be *placed* in the workspace.

    Unlike `inject_harness` this is not the lab's harness: no adapter file, no
    agents, no governance documents, and nothing already in the repository's own
    skills directory is removed. The arm stays "their setup plus these skills",
    which is the only reading under which the comparison means anything.
    """
    from ml_python_base.skills_sync.config import DEFAULT_REGISTRY_PATH, load_registry
    from ml_python_base.skills_sync.linker import add_skills

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    placed: list[str] = []
    for tool in registry.tools:
        if tool.id != tool_id:
            continue
        placed = sorted(add_skills(workspace, tool, skills))
    if placed:
        print(f"  ✓ Skills added to the native workspace: {', '.join(placed)}")
    return {"mode": "native_skills", "tool": tool_id, "skills": placed}


def inject_harness(
    workspace: Path, skills: list, tool_id: str, adapter_root: Path | None = None
) -> dict:
    """Project the lab harness into a target workspace (ADR-0031).

    This is the `injected` arm. The agent runs inside the cloned repository, so
    the harness has to be *there* — a governance surface in the lab is invisible
    from a cwd the agent never visits.

    ``skills`` is the run's **effective** set, so an ablated arm stays ablated
    inside the target. Passing the full governed set here would silently undo
    the ablation for exactly the runs meant to measure it.

    ``adapter_root`` is where the adapter files are read from. It matters
    because this container masks the lab's own `CLAUDE.md` / `AGENTS.md` to
    empty stubs (ADR-0049) — the agent's cwd is under the lab root, so they
    have to be hidden there — while the projection needs their real bytes to
    splice into. The worker stages them under the run directory, which no mask
    covers. Left unset (a lane that masks nothing) the repo root is read as
    before.
    """
    from ml_python_base.skills_sync.config import DEFAULT_REGISTRY_PATH, load_registry
    from ml_python_base.skills_sync.projection import project_harness

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    governance = (
        *registry.governance.files,
        registry.governance.automation,
        registry.governance.orchestration,
    )
    result = project_harness(
        REPO_ROOT,
        workspace,
        registry,
        skills=skills,
        tool_ids=(tool_id,),
        governance_files=tuple(doc for doc in governance if doc),
        adapter_root=adapter_root,
    )
    print(
        f"  ✓ Harness projected into the target: {len(result.skills)} skill(s), "
        f"{result.agents} agent(s), tool={tool_id}"
    )
    if result.collisions:
        # Replacing a repository's own governance changes the thing being
        # measured, so it is announced and recorded, never silent.
        print(
            "  ⚠️  Replaced the target's own governance: "
            f"{', '.join(result.collisions)} (kept as *.native.bak)"
        )
    return {
        "tools": result.tools,
        "skills": sorted(result.skills),
        "agents": result.agents,
        "files_written": sorted(result.files_written),
        "collisions": sorted(result.collisions),
    }


def restore_full_skill_views() -> None:
    """Re-sync every native view from the governed source (idempotent)."""
    from ml_python_base.skills_sync.discovery import (
        discover_skills as discover_governed,
    )

    apply_skill_views(discover_governed(REPO_ROOT))


@functools.cache
def detected_context_windows(models: tuple[str, ...] = ()) -> tuple:
    """The served context window of every self-hosted model this run uses.

    Read off the serving host itself (LM Studio ``/api/v0/models``, Ollama
    ``/api/ps``) the way the run form does, so the limit opencode is told is
    the window the operator actually loaded — 41k today, 120k tomorrow — with
    no variable to keep in sync. Cached per model set for the same reason as
    :func:`apply_gateway_overlay`; empty when detection is off or nothing
    answered, which leaves every model exactly as undeclared as before.
    """
    from harness_lab_app.context_window import autodetect_enabled, detect_windows

    if not models or not autodetect_enabled():
        return ()
    try:
        return tuple(detect_windows(models))
    except Exception as exc:
        print(f"  ⚠️  context window detection skipped: {exc!r}")
        return ()


@functools.cache
def apply_gateway_overlay(
    direct_models: tuple[str, ...] = (), run_models: tuple[str, ...] = ()
) -> str | None:
    """Export the opencode routing overlay and return its content hash.

    A model enabled from the web lives in ``data/app/gateway-models.json``, not
    in the git-tracked ``opencode.json``; without this the harness cannot
    resolve ``gateway/<id>`` and the run burns every step on a routing error.
    ``direct_models`` does the same for ``lmstudio/<id>`` executors, which have
    no tracked provider block at all.

    Injected here rather than in ``executors.build_run_environment`` because
    ``SubprocessRunExecutor.start`` passes no ``env=`` — host-mode runs would
    silently miss it. Both lanes reach this function.

    ``functools.cache`` makes it idempotent, so the metadata writer and the harness
    launcher can each call it without caring which runs first. It is keyed by
    argument rather than capped at one entry because a resumed run learns its
    executor from the stored metadata *after* the first call, and that later,
    better-informed call must be able to rewrite the overlay. Returns ``None``
    when nothing is added to the tracked map, which is what keeps every
    historical ``condition_hash`` byte-identical.
    """
    from harness_lab_app.context_window import describe, limits_by_model
    from harness_lab_app.opencode_overlay import build_overlay

    windows = detected_context_windows(run_models)
    for window in windows:
        print(f"  ✓ {describe(window)}")
    env, digest = build_overlay(REPO_ROOT, direct_models, limits_by_model(windows))
    os.environ.update(env)
    return digest


def run_models(args: argparse.Namespace) -> tuple[str, ...]:
    """Every model id this run addresses, in a stable order."""
    candidates = (
        getattr(args, "executor_model", None),
        getattr(args, "planner_model", None),
    )
    return tuple(dict.fromkeys(str(m) for m in candidates if m))


def usage_models(
    args: argparse.Namespace, executor_model: str | None
) -> tuple[str, ...]:
    """The models a run's usage is attributed to: the resolved executor first.

    ``executor_model`` may come from the run record rather than the command
    line, so :func:`run_models` alone can miss it.
    """
    candidates = (executor_model, getattr(args, "planner_model", None))
    return tuple(dict.fromkeys(str(m) for m in candidates if m))


def direct_models(args: argparse.Namespace) -> tuple[str, ...]:
    """Models this run needs a direct provider block for.

    Both host lanes: ``lmstudio/<id>`` and ``ollama/<id>``. The prefixes are
    kept — they are what tells the overlay which provider block to write.
    """
    from harness_lab_app.lmstudio_direct import is_direct_model as is_lmstudio
    from harness_lab_app.ollama_direct import is_direct_model as is_ollama

    candidates: tuple[str | None, ...] = (
        getattr(args, "executor_model", None),
        getattr(args, "planner_model", None),
    )
    return tuple(sorted({str(m) for m in candidates if is_lmstudio(m) or is_ollama(m)}))


STAGED_SKILL_HASH_ALGO = "folder_sha256_v2"


def staged_skill_hashes(extra: list[str]) -> dict[str, str]:
    """Digest per staged skill — any edit to the tree must flip the arm.

    Whole-tree, not just SKILL.md (ADR-0017): a staged skill can now ship
    ``scripts/`` and ``references/``, and changing one of those changes the
    experiment. The consequence is deliberate and recorded: every historical
    staged-skill arm re-bins, which is why ``metadata.json`` also carries
    ``skills_extra_hash_algo`` so the two generations stay legible.
    """
    from ml_python_base.skills_sync.hashing import folder_hash

    out: dict[str, str] = {}
    for name in sorted(set(extra)):
        source = SKILLS_STAGING_DIR / name
        if source.is_dir():
            out[name] = folder_hash(source)
    return out


def discover_subagents() -> list[str]:
    d = REPO_ROOT / ".github" / "agents"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md") if p.stem != "README")


def agent_workspace_path(run_id: str) -> str:
    """The run's workspace as an absolute path the agent must write to.

    Absolute, not relative, and substituted into the prompt rather than left as
    a placeholder — both halves of the failure this fixes.

    Only ``<repo>/workspace/`` is bind-mounted into the runner (``.:/app``), so
    a file written anywhere else lives on the container's overlay filesystem and
    is destroyed with the container. A relative ``workspace/<run>/`` is correct
    only while the shell sits in ``/app``, and agents routinely "normalize" it
    to ``/workspace/<run>/`` — the container root, outside the mount. Runs
    tetris-v1-K-037 and K-039 did exactly that: 895 and 116 writes to the
    ephemeral path, 85 minutes and an entire build lost respectively, while the
    lab archived an empty or stale directory and recorded the run as a normal
    completion.
    """
    return str((WORKSPACE_ROOT / run_id).resolve())


def render_prompt_body(text: str, run_id: str) -> str:
    """Substitute the run placeholders a case prompt ships with.

    ``benchmarks/*/prompt_*.md`` are templates carrying ``{run_id}``, but the
    body used to be concatenated verbatim, so the model was handed the literal
    string ``workspace/{run_id}/main.py`` and had to guess. Plain ``replace``
    rather than ``str.format``: prompt bodies contain braces of their own (code
    blocks, dict literals) that ``format`` would choke on or silently eat.
    """
    return text.replace("{run_id}", run_id).replace(
        "{workspace}", agent_workspace_path(run_id)
    )


def skill_activation_block(invoke_skills: Sequence[str]) -> str:
    """The lines that turn an available skill into an invoked one (ADR-0041).

    Every step gets it, not just the first: each step is a fresh agent session
    with no memory of having been asked. The wording names the mechanism
    ("invoke", "read its SKILL.md") because "use caveman" alone reads as a
    style hint, and the whole point is a Skill call that leaves a trace.
    """
    names = sorted({name for name in invoke_skills if name})
    if not names:
        return ""
    listed = ", ".join(f"`{name}`" for name in names)
    plural = "s" if len(names) > 1 else ""
    return (
        f"> **Skill activation (required):** before doing anything else, invoke "
        f"the {listed} skill{plural} — read its SKILL.md and follow it for the "
        f"whole task, in this step and every later one. This is part of the "
        f"task, not a suggestion.\n\n"
    )


def generate_prompt(
    run_id: str,
    prompt_short: str,
    prompt_source: Path,
    planner_model: str | None,
    executor_model: str | None,
    session_id: str,
    out_dir: Path,
    repo_note: str | None = None,
    invoke_skills: Sequence[str] = (),
    cwd_note: bool = False,
) -> Path:
    """Write the final prompt: metadata header + location constraint + body.

    `repo_note` overrides the default build-from-scratch constraint (workspace
    main.py) for repo-task/SWE runs, where the correct wording depends on the
    agent's working directory. `cwd_note` keeps that default constraint but adds
    that the workspace already *is* the working directory, which is true for
    every condition that launches the agent from it (ADR-0031, ADR-0049).
    """
    workspace = agent_workspace_path(run_id)
    constraint = repo_note or (
        f"> All files MUST be created in `{workspace}/` — that exact absolute\n"
        f"> path. It is the only directory preserved after the run; anything\n"
        f"> written elsewhere (for example `/workspace/{run_id}/`) is discarded\n"
        f"> when the container exits.\n"
        f"> Entry point: `{workspace}/main.py`\n"
        + ("> That directory is your current working directory.\n" if cwd_note else "")
    )
    header = (
        f"<!-- BENCHMARK RUN METADATA\n"
        f"BENCHMARK_ID: {run_id.rsplit('-', 2)[0]}\n"
        f"RUN_ID: {run_id}\n"
        f"SESSION_ID: {session_id}\n"
        f"PROMPT_VARIANT: {prompt_short}\n"
        f"PLANNER_MODEL: {planner_model or ''}\n"
        f"EXECUTOR_MODEL: {executor_model or ''}\n"
        f"-->\n\n"
        f"{constraint}\n"
        f"{skill_activation_block(invoke_skills)}"
        f"---\n\n"
    )
    prompt_path = out_dir / "prompt.md"
    prompt_path.write_text(
        header + render_prompt_body(prompt_source.read_text(), run_id)
    )
    return prompt_path


def generate_continuation_prompt(
    run_id: str,
    step_n: int,
    steps_dir: Path,
    workspace_dir: Path | None = None,
    invoke_skills: Sequence[str] = (),
) -> Path:
    """Write a continuation prompt for step N (step > 1).

    If PLAN.md exists in the workspace (SDLC mode), injects the plan so the model
    resumes from documented state rather than rediscovering code from scratch.
    Otherwise falls back to a generic increment prompt.
    """
    # Absolute, for the same reason the first-step prompt is: a continuation
    # that says "workspace/<run>/" invites the agent to resolve it outside the
    # bind mount and quietly lose the session's work.
    workspace = agent_workspace_path(run_id)
    plan_md: str | None = None
    if workspace_dir is not None:
        plan_file = workspace_dir / "PLAN.md"
        if plan_file.exists():
            plan_md = plan_file.read_text().strip()

    if plan_md:
        text = (
            f"<!-- SDLC continuation step {step_n} for RUN_ID={run_id} -->\n\n"
            f"# Tetris — Session {step_n}\n\n"
            f"You are continuing an ongoing build. Your plan and decision records are below.\n\n"
            f"---\n\n"
            f"## Current plan (from `{workspace}/PLAN.md`)\n\n"
            f"{plan_md}\n\n"
            f"---\n\n"
            f"## Your task for this session\n\n"
            f"1. Read the plan above — find the first unchecked milestone (`[ ]`).\n"
            f"2. Implement **exactly that ONE milestone**. Do not implement more than\n"
            f"   one milestone this session, and do not touch completed milestones.\n"
            f"3. Validate headlessly (a hanging window or a `timeout` is NOT success):\n"
            f'   `BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python {workspace}/main.py; echo "exit=$?"`\n'
            f"   A correct build prints `exit=0`.\n"
            f"4. Update `{workspace}/PLAN.md`:\n"
            f"   - Mark the milestone as `[x]` (done)\n"
            f"   - Add an ADR if you made any non-obvious architecture decision\n"
            f"   - Append to Session Log: `Session {step_n}: ✅ Milestone N — <what you built>`\n"
            f"5. End your response:\n"
            f"   - If milestones still remain `[ ]`: `DONE: Milestone N complete.`\n"
            f"   - If and ONLY if every milestone is now `[x]`: `ALL MILESTONES COMPLETE`\n"
        )
    else:
        text = (
            f"<!-- Continuation step {step_n} for RUN_ID={run_id} -->\n\n"
            f"This is continuation step {step_n} for RUN_ID={run_id}.\n\n"
            f"Continue from the existing workspace. Do NOT restart from scratch.\n"
            f"Do NOT rewrite unrelated code.\n\n"
            f"1. Inspect `{workspace}/main.py` and understand the current state.\n"
            f"2. Implement only the next missing Tetris milestone (exactly one).\n"
            f"3. Validate headlessly (a `timeout` is NOT success):\n"
            f'   `BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python {workspace}/main.py; echo "exit=$?"`\n'
            f"4. Stop after ONE verifiable increment.\n"
            f"5. State clearly what you implemented and what the next recommended increment is.\n"
            f"6. If and ONLY if the game is fully complete, end with: ALL MILESTONES COMPLETE\n"
        )
    path = steps_dir / f"step-{step_n}-prompt.md"
    path.write_text(skill_activation_block(invoke_skills) + text)
    return path


def generate_case_step_prompt(
    run_id: str,
    step_n: int,
    steps_dir: Path,
    step_text: str,
    invoke_skills: Sequence[str] = (),
) -> Path:
    """Write step N's prompt from the case's own ``step_prompts`` list.

    A multi-step case authored in the web UI carries one instruction per step.
    Neither generic continuation prompt fits it: one is Tetris-framed, the
    other SWE-bench-framed. Use the author's text and add only the continuity
    the agent needs (which step it is, and where its work lives).
    """
    workspace = agent_workspace_path(run_id)
    text = (
        f"<!-- Case step {step_n} for RUN_ID={run_id} -->\n\n"
        f"This is step {step_n} of an ongoing task (RUN_ID={run_id}).\n\n"
        f"Continue from the existing workspace at `{workspace}`. Do NOT restart "
        f"from scratch and do NOT rewrite unrelated code.\n\n"
        f"## Your task for this step\n\n"
        f"{step_text.strip()}\n\n"
        f"When this step is done, state what you changed. If the whole task is "
        f"complete, end with: ALL MILESTONES COMPLETE\n"
    )
    path = steps_dir / f"step-{step_n}-prompt.md"
    path.write_text(skill_activation_block(invoke_skills) + text)
    return path


def render_continuation_prompt_body(
    run_id: str, parent_run_id: str, instruction: str
) -> str:
    """Frame a follow-up instruction for a run that inherits a workspace.

    A continuation run starts with the parent's final workspace already in
    place (ADR-0026), and the agent has no memory of having produced it. Without
    this framing the first thing it does is decide the directory is somebody
    else's and start over, which throws away the exact thing the continuation
    exists to keep.

    The workspace path is absolute for the same reason every other prompt's is:
    a relative ``workspace/<run>/`` gets "normalized" to ``/workspace/<run>/``,
    outside the bind mount, and the work is lost with the container.
    """
    workspace = agent_workspace_path(run_id)
    return (
        f"<!-- Continuation of RUN_ID={parent_run_id} -->\n\n"
        f"You are continuing work that is already in progress. The workspace at "
        f"`{workspace}` holds the current state, produced by an earlier session "
        f"(run `{parent_run_id}`). It is yours to build on.\n\n"
        f"Before you change anything, read what is there. Do NOT restart from "
        f"scratch, do NOT recreate files that already exist, and do NOT rewrite "
        f"code unrelated to the instruction below.\n\n"
        f"## Your task\n\n"
        f"{instruction.strip()}\n\n"
        f"When you are done, state what you changed and what you left alone. If "
        f"the task is fully complete, end with: ALL MILESTONES COMPLETE\n"
    )


def generate_generic_continuation_prompt(
    run_id: str, step_n: int, steps_dir: Path, invoke_skills: Sequence[str] = ()
) -> Path:
    """Continuation prompt for step N of a case with no framing of its own.

    ``generate_continuation_prompt`` is Tetris/pygame-framed: it names
    ``main.py``, runs a ``SDL_VIDEODRIVER=dummy`` smoke test and talks about
    game milestones. That is correct for the curated benchmark and actively
    misleading for a case authored in the web UI, which reached this point only
    because it ran out of ``step_prompts``.
    """
    workspace = agent_workspace_path(run_id)
    text = (
        f"<!-- Continuation step {step_n} for RUN_ID={run_id} -->\n\n"
        f"This is continuation step {step_n} for RUN_ID={run_id}.\n\n"
        f"Continue from the existing workspace at `{workspace}`. Do NOT restart "
        f"from scratch and do NOT rewrite unrelated code.\n\n"
        f"1. Review the current state of the workspace and the task's remaining "
        f"gaps.\n"
        f"2. Implement exactly ONE verifiable increment toward finishing the "
        f"task.\n"
        f"3. Verify it the way this task is meant to be verified (run the tests "
        f"or the entry point you have been working with) and report the "
        f"result.\n"
        f"4. Stop after that one increment and state what the next one should "
        f"be.\n"
        f"5. If and ONLY if the whole task is complete, end with: "
        f"ALL MILESTONES COMPLETE\n"
    )
    path = steps_dir / f"step-{step_n}-prompt.md"
    path.write_text(skill_activation_block(invoke_skills) + text)
    return path


class SweSpecError(RuntimeError):
    """A SWE-bench run whose spec does not name the tests that define it."""


def generate_swe_continuation_prompt(
    run_id: str,
    step_n: int,
    steps_dir: Path,
    spec: dict,
    last_eval: dict | None,
    in_repo_cwd: bool = False,
    invoke_skills: Sequence[str] = (),
) -> Path:
    """Continuation prompt for a SWE-bench step (no Tetris/game framing).

    Keeps the agent on the issue: shows the live F2P/P2P status from the
    previous objective evaluation and forbids scope creep. `in_repo_cwd` means
    the agent's working directory IS the subject repo (clean-context claude
    runs), so path references drop the workspace/<run_id>/ prefix.
    """
    f2p = spec.get("fail_to_pass") or []
    if not f2p:
        # A SWE-bench task IS its failing tests. Without them this prompt used
        # to render an empty bullet list under "Target failing test(s):" and a
        # bare `python -m pytest` under "Run them with:" — instructions to run
        # nothing, in a repository that may have no Python at all. That is a
        # broken spec, and it must stop the run rather than reach the agent.
        raise SweSpecError(
            "SWE-bench continuation asked for a spec that names no "
            "fail_to_pass tests; there is no objective to continue toward."
        )
    repo_ref = "the current directory" if in_repo_cwd else f"`workspace/{run_id}/`"
    repo_mount = "$(pwd)" if in_repo_cwd else f"$(pwd)/workspace/{run_id}"
    if spec.get("case_type") == "swe_pro":
        # Pro lane: the test env only exists inside the instance image.
        image = spec.get("dockerhub_image", "<instance image>")
        target_tests = " ".join(f2p[:3])
        test_cmd = (
            f'docker run --rm -v "{repo_mount}":/app {image} '
            f"bash -lc 'cd /app && python -m pytest {target_tests}' "
            f"# (skip if docker is unavailable)"
        )
    else:
        test_cmd = f"python -m pytest {' '.join(f2p)}"
    status = (
        f"resolved={last_eval['resolved']} "
        f"F2P={last_eval['f2p_passed']}/{last_eval['f2p_total']} "
        f"P2P={last_eval['p2p_passed']}/{last_eval['p2p_total']}"
        if last_eval
        else "not evaluated yet"
    )
    text = (
        f"<!-- SWE-bench continuation step {step_n} for RUN_ID={run_id} -->\n\n"
        f"# Resolve the GitHub issue — continuation (step {step_n})\n\n"
        f"You are continuing work on the SWE-bench task in "
        f"{repo_ref} (a real library repo — there is NO main.py to "
        f"build; do NOT create applications or demos).\n\n"
        f"Objective status after the previous step: {status}\n\n"
        f"Target failing test(s):\n"
        + "\n".join(f"  - {t}" for t in f2p)
        + (
            f"\n\nRun them with:\n    {test_cmd}\n\n"
            if spec.get("case_type") == "swe_pro" or in_repo_cwd
            else f"\n\nRun them with:\n    cd workspace/{run_id} && {test_cmd}\n\n"
        )
        + "Rules:\n"
        "1. Make the target test(s) pass by editing the **library source only** "
        "(never the test files).\n"
        "2. Keep the change minimal — do not refactor, fix deprecations, or "
        "touch anything unrelated to the issue.\n"
        "3. After your edit, run the target test(s) and the surrounding test "
        "file to check for regressions.\n"
        "4. Stop after the target test(s) pass.\n"
        + (
            "5. This sandbox does NOT have the project's dependencies — do NOT "
            "install packages, build the environment, or hunt for interpreters. "
            "If the tests cannot run here, verify by reading them; the official "
            "containerized validation runs after you finish.\n"
            if spec.get("case_type") == "swe_pro"
            else ""
        )
    )
    path = steps_dir / f"step-{step_n}-prompt.md"
    path.write_text(skill_activation_block(invoke_skills) + text)
    return path


def _tree_fingerprint(workspace: Path) -> str:
    """Hash of every file's relative path and bytes under ``workspace``.

    Symlinks are hashed by target, not followed, so a link loop cannot hang it.
    """
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(workspace.rglob("*")):
        if ".git" in path.relative_to(workspace).parts:
            continue
        rel = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            digest.update(f"L {rel} -> {os.readlink(path)}\n".encode())
        elif path.is_file():
            digest.update(f"F {rel}\n".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def workspace_fingerprint(workspace: Path) -> str | None:
    """Cheap change detector: two identical fingerprints in a row mean the
    agent's step made no edits.

    A workspace that is its own git repository (SWE-bench, target clones) is
    fingerprinted by git status + diff vs HEAD. A build-from-scratch workspace
    has no ``.git`` and sits inside the lab repo, where ``/workspace/`` is
    ignored, so git would answer for the enclosing repo and never change; its
    file tree is hashed instead. Returns None (never raises) when unreadable.
    """
    import hashlib

    if workspace.is_dir() and not (workspace / ".git").exists():
        try:
            return _tree_fingerprint(workspace)
        except OSError:
            return None
    try:
        status = _run_git(
            ["git", "-C", str(workspace), "status", "--porcelain"], cwd=REPO_ROOT
        )
        diff = _run_git(["git", "-C", str(workspace), "diff", "HEAD"], cwd=REPO_ROOT)
    except Exception:
        return None
    if status.returncode != 0 or diff.returncode != 0:
        return None
    return hashlib.sha256((status.stdout + diff.stdout).encode()).hexdigest()


# A snapshot is a copy of what the agent wrote, not of what it installed. The
# same exclusions as diff.patch (DIFF_EXCLUDED_DIRS) plus .git, and a size cap:
# a workspace that grew past it is snapshotted as a diff instead, never skipped
# silently (ADR-0076).
SNAPSHOT_MAX_BYTES = 50 * 1024 * 1024


def _snapshot_ignore(_dir: str, names: list[str]) -> list[str]:
    return [n for n in names if n in DIFF_EXCLUDED_DIRS or n == ".git"]


def _tree_bytes(root: Path, limit: int) -> int:
    """Bytes under ``root`` outside the excluded dirs, stopping past ``limit``."""
    total = 0
    for path in root.rglob("*"):
        rel = path.relative_to(root).parts
        if any(part in DIFF_EXCLUDED_DIRS or part == ".git" for part in rel):
            continue
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
            if total > limit:
                break
    return total


def _snapshot_git_diff(workspace: Path, steps_dir: Path, step_n: int) -> dict:
    """``steps/step-N.diff``: the working tree against HEAD, new files included.

    Done on a **throwaway copy of the index** (``GIT_INDEX_FILE``), never the
    agent's own: `git add -N` there makes untracked files visible to the diff,
    and the agent's staged changes — which a reset of the real index would
    silently drop between two of its steps — are never touched. Bytes in, bytes
    out: a ``--binary`` patch decoded as text with replacement characters would
    no longer apply.
    """
    git_dir = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
    )
    if git_dir.returncode != 0:
        return {"step": step_n, "kind": "error", "error": git_dir.stderr[-200:]}
    real_index = Path(git_dir.stdout.strip()) / "index"
    with tempfile.TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        if real_index.is_file():
            shutil.copyfile(real_index, index)
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        subprocess.run(
            ["git", "-C", str(workspace), "add", "-N", "."],
            capture_output=True,
            env=env,
        )
        diff = subprocess.run(
            ["git", "-C", str(workspace), "diff", "--binary", "HEAD"],
            capture_output=True,
            env=env,
        )
    if diff.returncode != 0:
        return {
            "step": step_n,
            "kind": "error",
            "error": diff.stderr.decode(errors="replace")[-200:],
        }
    dest = steps_dir / f"step-{step_n}.diff"
    dest.write_bytes(diff.stdout)
    return {"step": step_n, "kind": "diff", "path": dest.name}


def snapshot_step(
    workspace: Path, steps_dir: Path, step_n: int, *, git_workspace: bool | None = None
) -> dict:
    """Record the workspace as it stood when step ``step_n`` ended (ADR-0076).

    The per-step reward grades these after the run, in the worker, with the
    case's own checks — the agent never sees them, so measuring cannot change
    what is measured. Two forms:

    * ``snapshot`` — a copy of the tree at ``steps/step-N.snapshot/`` for a
      build-from-scratch workspace (no ``.git``), small enough to copy.
    * ``diff`` — ``steps/step-N.diff`` against the repository's HEAD for a
      cloned repository (SWE-bench, a target), where copying the clone every
      step would cost more than the run. Replayable onto the base commit.

    Never raises: a missing snapshot is a gap in the reward curve, recorded as
    such, not a reason to stop a run that is working.
    """
    if not workspace.is_dir():
        return {"step": step_n, "kind": "missing"}
    is_git = (workspace / ".git").exists() if git_workspace is None else git_workspace
    try:
        if not is_git and _tree_bytes(workspace, SNAPSHOT_MAX_BYTES) <= (
            SNAPSHOT_MAX_BYTES
        ):
            dest = steps_dir / f"step-{step_n}.snapshot"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(
                workspace,
                dest,
                symlinks=True,
                ignore_dangling_symlinks=True,
                ignore=_snapshot_ignore,
            )
            return {"step": step_n, "kind": "snapshot", "path": dest.name}
        if is_git:
            return _snapshot_git_diff(workspace, steps_dir, step_n)
        return {"step": step_n, "kind": "too_large"}
    except Exception as exc:  # a snapshot must never stop the run
        return {"step": step_n, "kind": "error", "error": repr(exc)[:200]}


def write_step_meta(steps_dir: Path, meta: dict) -> None:
    """Write ``steps/step-N.meta.json``: the step's snapshot, clock and stop.

    The step's own cost is *not* computed here: claude records it in the step's
    ``result`` event and the worker reads it from there
    (``step_reward.claude_step_cost``); opencode sessions are per step and
    priced from its database. What only the runner knows is the step clock,
    which snapshot form it took, and the SWE lane's live verdict.
    """
    path = steps_dir / f"step-{meta['step']}.meta.json"
    try:
        path.write_text(json.dumps(meta, indent=2, sort_keys=True))
    except OSError as exc:
        print(f"  ⚠️  could not write {path.name}: {exc!r}")


def swe_step_evaluation(workspace: Path, spec: dict) -> dict | None:
    """Objective F2P/P2P check against the provisioned install (never raises).

    This is the SWE-bench resolution criterion evaluated live between steps so
    the loop can stop as soon as the issue is actually resolved, instead of
    trusting the agent's own completion claims.
    """
    f2p = spec.get("fail_to_pass") or []
    p2p = spec.get("pass_to_pass") or []
    if not f2p or not workspace.is_dir():
        return None
    if spec.get("case_type") == "swe_pro":
        # Pro lane: the suite only runs inside the instance image. Without
        # docker (containerized runner) skip the per-step check — validation
        # still runs worker-side after the run (design doc, test-execution §).
        import swebench_pro_common as swe_pro

        if not swe_pro.docker_available():
            print("  ⚠️  docker unavailable — skipping per-step Pro evaluation.")
            return None
        try:
            return swe_pro.evaluate_containerized(workspace, spec)
        except Exception as exc:
            print(f"  ⚠️  SWE-bench Pro step evaluation failed: {exc!r}")
            return None

    import swebench_common as swe

    try:
        return swe.evaluate(workspace, sys.executable, f2p, p2p)
    except Exception as exc:
        print(f"  ⚠️  SWE-bench step evaluation failed: {exc!r}")
        return None


def read_template_ref(repo_root: Path) -> str:
    """The adopted ml-python-base release ref (``v0.4.0``) at run time, or "".

    Reads the first ``ref:`` line of ``.harness-version.yaml`` (falling back to
    the legacy ``.template-version`` mirror) with a plain regex — bench_run runs
    standalone inside the runner container, so no yaml dep and no imports from
    ``src/``. Best-effort: any miss returns "" and the leaderboard falls back to
    the harness fingerprint hash.
    """
    for name in (".harness-version.yaml", ".template-version"):
        path = repo_root / name
        try:
            match = re.search(
                r"^\s*ref:\s*['\"]?([^'\"\s]+)", path.read_text(), re.MULTILINE
            )
        except OSError:
            continue
        if match:
            return match.group(1)
    return ""


def write_metadata(
    out_dir: Path,
    args: argparse.Namespace,
    task: dict,
    run: dict,
    prompt_short: str,
    prompt_source: Path | None = None,
    task_path: Path | None = None,
    target: dict | None = None,
    harness_source: str = "",
    harness_root: Path | None = None,
    harness_projection: dict | None = None,
) -> Path:
    meta = {
        "project": "ai-sdlc-lab",
        "benchmark_id": task.get("task_id", args.case),
        "case": args.case,
        "run_id": args.run,
        "cell_id": extract_cell_id(args.run),
        "session_id": run.get("langfuse_session_id", f"bench-{args.run}"),
        "methodology": run.get("methodology", ""),
        "harness": run.get("harness", ""),
        "prompt_variant": prompt_short,
        "planner_model": args.planner_model,
        "executor_model": args.executor_model,
        "score_mode": args.score_mode,
        "max_steps": args.max_steps,
        # Exclusions are applied to the native views BEFORE metadata is written,
        # so discover_skills() already reflects the effective bundle; the
        # explicit lists below record the ablation itself.
        # A bare run has no catalog and no subagents whatever the masked tree
        # happens to look like from here (ADR-0049): stating it explicitly keeps
        # the record right even if the masks were ever misconfigured.
        "skills_bundle": [] if harness_source == "bare" else discover_skills(),
        "skills_disabled": sorted(set(getattr(args, "disable_skill", []) or [])),
        "skills_experimental": sorted(set(getattr(args, "extra_skill", []) or [])),
        # Skills the prompt told the agent to invoke (ADR-0041). Part of the
        # arm, so it also enters the condition hash below.
        "skills_invoke": sorted(set(getattr(args, "invoke_skill", []) or [])),
        "subagents": [] if harness_source == "bare" else discover_subagents(),
        "base_commit": task.get("base_commit", ""),
        "workspace": f"workspace/{args.run}",
        # Which external repository this run worked in, if any (ADR-0029).
        "target_repo_id": (target or {}).get("target_id", ""),
        "target_commit": (target or {}).get("commit", ""),
        # Which harness the agent could see, and from where (ADR-0031).
        "harness_source": harness_source or "lab_root",
        # Lineage for a continuation run (ADR-0026); "" for an original run.
        # Carried in the archived artifact so a chain stays reconstructable
        # from disk alone (ADR-0021).
        "continues_run": run.get("continues_run", ""),
        # Friendly adopted-governance version at run time (leaderboard axis).
        "template_ref": read_template_ref(REPO_ROOT),
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        # The served window each self-hosted model had at launch and the limit
        # opencode was told (ADR-0063). Empty when nothing was detected.
        "context_windows": [
            w.as_dict() for w in detected_context_windows(run_models(args))
        ],
    }
    # Add the hashable trial condition manifest (condition_hash, harness
    # fingerprint, model condition, prompt/case hashes). Best-effort: if
    # provenance can't be computed the base metadata is still written.
    enrich_metadata(
        meta,
        repo_root=REPO_ROOT,
        run=run,
        executor_model=args.executor_model,
        planner_model=args.planner_model,
        prompt_source=prompt_source,
        task_path=task_path,
        runner_image=os.environ.get("HARNESS_LAB_RUNNER_IMAGE"),
        skills_disabled=tuple(meta["skills_disabled"]),
        target=target,
        harness_source=harness_source,
        harness_root=harness_root,
        harness_projection=harness_projection,
        skills_extra_hashes=staged_skill_hashes(
            list(getattr(args, "extra_skill", []) or [])
        ),
        skills_extra_hash_algo=STAGED_SKILL_HASH_ALGO,
        gateway_overlay_hash=apply_gateway_overlay(
            direct_models(args), run_models(args)
        ),
        skills_invoke=tuple(meta["skills_invoke"]),
    )
    path = out_dir / "metadata.json"
    path.write_text(json.dumps(meta, indent=2))
    return path


def write_run_env(out_dir: Path, args: argparse.Namespace, session_id: str) -> Path:
    lines = [
        f"AI_SDLC_RUN_ID={args.run}",
        f"AI_SDLC_SESSION_ID={session_id}",
        f"AI_SDLC_BENCHMARK_ID={args.case}",
        f"OPENCODE_MODEL={args.executor_model or ''}",
        f"OPENCODE_MODEL_PLAN={args.planner_model or ''}",
        f"LANGFUSE_SESSION_ID={session_id}",
    ]
    path = out_dir / "run.env"
    path.write_text("\n".join(lines) + "\n")
    return path


def validate_workspace(
    workspace: Path, run_id: str, resume: bool, fresh: bool = False
) -> None:
    """Guard against clobbering an existing workspace.

    - --resume: keep the existing workspace and continue from it.
    - --fresh (managed/web run): reset a stale workspace instead of exiting, so
      an automated run is never blocked by a leftover dir (ADR-0006). With
      auto-incremental ids this rarely fires; it is a defensive fallback.
    - neither (interactive CLI): refuse and exit, so the user decides.
    """
    if workspace.exists() and any(workspace.iterdir()):
        if resume:
            print(
                f"  ⚠️  workspace/{run_id}/ exists — resuming from current state "
                "(--resume)."
            )
            return
        if fresh:
            print(
                f"  ⚠️  workspace/{run_id}/ exists — resetting for a fresh managed "
                "run (--fresh)."
            )
            shutil.rmtree(workspace)
            return
        sys.exit(
            f"❌ workspace/{run_id}/ already exists and is not empty.\n"
            f"   Pass RESUME=1 to continue from the existing state, or remove it:\n"
            f"     rm -rf workspace/{run_id}/"
        )


def resolve_or_synthesize_run(
    runs_path: Path, run_id: str, args: argparse.Namespace
) -> dict:
    """Return the run dict from runs.yaml, or synthesize one from CLI args.

    The web lane (ADR-0006) creates runs whose ids are not registered in
    benchmarks/<case>/runs.yaml. Rather than exit (as find_run does), build a
    minimal run dict from the CLI args so the runner can proceed. When the id
    *is* registered, behavior is unchanged. The API never writes to the shared
    runs.yaml, avoiding write races against the CLI.
    """
    data = yaml.safe_load(runs_path.read_text())
    for run in data.get("runs", []):
        if run["run_id"] == run_id:
            return run
    return {
        "run_id": run_id,
        "status": "pending",
        "prompt_variant": args.prompt or "",
        "executor_model": args.executor_model or "",
        "planner_model": args.planner_model or "",
        "langfuse_session_id": f"bench-{run_id}",
    }


def finalize_workspace(out_dir: Path, workspace: Path, run_id: str) -> int:
    """Archive the live workspace into the run's data dir and remove it.

    Returns the diff line count (0 when the workspace is missing). The
    destructive rmtree runs only after a successful copy. Used both by the CLI
    close_run and by the managed web/manual flow, which never reaches Phase 5.
    """
    if not workspace.exists():
        print(f"  ⚠️  workspace/{run_id}/ not found — skipping artifact copy.")
        return 0
    dest = out_dir / "workspace"
    if dest.exists():
        shutil.rmtree(dest)
    # symlinks=True: real repos (e.g. openlibrary vendoring infogami) contain
    # internal/dangling symlinks; following them either crashes copytree or
    # duplicates trees. Copy links as links — that is also what git records.
    shutil.copytree(workspace, dest, symlinks=True, ignore_dangling_symlinks=True)
    file_count = sum(1 for _ in dest.rglob("*") if _.is_file())
    print(f"  Copied workspace → data/runs/{run_id}/workspace/ ({file_count} files)")
    if file_count == 0:
        # An empty archive is not an empty result — it means the agent wrote
        # somewhere that is not preserved (see `agent_workspace_path`). Say so
        # here, loudly, because the alternative is a run that looks completed
        # and leaves a human with nothing to score.
        print(
            f"  ❌ Nothing was written to {workspace} — the agent built outside "
            "the preserved workspace, so this run produced no artifact."
        )

    diff_lines = write_diff_patch(out_dir, dest)

    shutil.rmtree(workspace)
    if WORKSPACE_ROOT.exists() and not any(WORKSPACE_ROOT.iterdir()):
        WORKSPACE_ROOT.rmdir()
    print(f"  Removed workspace/{run_id}/")
    return diff_lines


# Directories an agent creates by installing or running things, not by writing
# code. They stay in the archive; they are kept out of diff.patch, which the
# judge and the auditor read as "the agent's code".
DIFF_EXCLUDED_DIRS = frozenset(
    {
        "venv",
        ".venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "site-packages",
    }
)


def _diffable_copy(dest: Path, into: Path) -> Path:
    """A copy of ``dest`` without :data:`DIFF_EXCLUDED_DIRS`, for diffing."""
    shutil.copytree(
        dest,
        into,
        symlinks=True,
        ignore_dangling_symlinks=True,
        ignore=lambda _dir, names: [n for n in names if n in DIFF_EXCLUDED_DIRS],
    )
    return into


def write_diff_patch(out_dir: Path, dest: Path) -> int:
    """Write ``diff.patch`` for an archived workspace and return its line count.

    Separate from the archive step so a run recorded with a wrong patch can
    have it rewritten from its archive without re-running anything.
    """
    diff_path = out_dir / "diff.patch"
    if (dest / ".git").exists():
        # Repo task: the meaningful diff is the agent's changes vs the
        # provisioned commit (base_commit + test files), not diff-vs-empty.
        # Diff against that commit, not HEAD: agents sometimes `git commit`
        # their fix inside the workspace, which would make diff-vs-HEAD empty.
        #
        # Intent-to-add first: `git diff <ref>` shows tracked files only, so a
        # run whose whole deliverable was a NEW file shipped an empty patch —
        # two talos arms each wrote tests/test_install_and_hooks.sh, the judge
        # was told "(no diff produced)", and scored one of them 0 for "not
        # adding the test" that a PASS check had just executed. `-N` stages
        # nothing; it only lets the diff see the file. `.gitignore` still
        # applies, so build output stays out.
        _run_git(
            ["git", "-C", str(dest), "add", "--intent-to-add", "--all"], cwd=REPO_ROOT
        )
        provisioned = _run_git(
            [
                "git",
                "-C",
                str(dest),
                "log",
                "--format=%H",
                "--grep",
                "provisioned",
                "-n",
                "1",
            ],
            cwd=REPO_ROOT,
        ).stdout.strip()
        diff_ref = provisioned or "HEAD"
        result = _run_git(["git", "-C", str(dest), "diff", diff_ref], cwd=REPO_ROOT)
    else:
        # Diff the archive against an empty directory, NOT against /dev/null.
        # `git diff --no-index /dev/null <dir>` looks like it means "everything
        # is new" but git pairs the basename and dies with
        # `Could not access '<dir>/null'`, writing zero bytes to stdout while
        # the error goes to a stderr nobody reads. Every build-from-scratch run
        # therefore shipped an empty diff.patch — and `bench_audit` gates its
        # "Código producido" section on that file being non-empty, so the audit
        # LLM judged runs it had never seen the code of.
        with (
            tempfile.TemporaryDirectory() as empty,
            tempfile.TemporaryDirectory() as scratch,
        ):
            code = _diffable_copy(dest, Path(scratch) / dest.name)
            result = _run_git(
                ["git", "diff", "--no-index", empty, str(code)], cwd=REPO_ROOT
            )
        # The copy's path appears in the patch headers; name the archive instead.
        result.stdout = result.stdout.replace(str(code), str(dest))
    diff_path.write_text(result.stdout)
    diff_lines = result.stdout.count("\n")
    print(f"  diff.patch: {diff_lines} lines")
    return diff_lines


def _load_case_spec(out_dir: Path) -> dict | None:
    """Return the case as the worker staged it from the DB, or None.

    The worker writes this for every case type (ADR-0009), so its presence
    means "the DB described this run", not "this is a repo task" — use
    :func:`spec_provisions_repo` for that question.
    """
    path = out_dir / "case_spec.json"
    if not path.exists():
        return None
    try:
        spec = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return spec if isinstance(spec, dict) else None


def spec_provisions_repo(spec: dict | None) -> bool:
    """Whether this run needs a repository cloned before the agent runs.

    Two independent reasons to provision. An external **target repository**
    (ADR-0029) says so explicitly through ``source``; that is the discriminator
    ADR-0030 introduced, and it is checked first because a target is orthogonal
    to the case type — the same case may run locally or against a clone.

    Otherwise a cloneable ``owner/repo`` counts, and only that: a case may carry
    a bare ``subject_repo`` for provenance (Tetris says ``ml-python-base``), and
    a DB-native case leaves the field empty.
    """
    if (spec or {}).get("source") == "git_target":
        return True
    return "/" in (spec or {}).get("repo", "")


def spec_is_swe_bench(spec: dict | None) -> bool:
    """Whether this run is a SWE-bench task, as opposed to merely needing a clone.

    Separate from :func:`spec_provisions_repo` because the two questions have
    different answers and conflating them cost two whole experiments. That
    function asks *"does a repository have to be cloned first?"*, and
    ``source: "git_target"`` answers yes for **every** case paired with an
    external target (ADR-0029/0030) — a documentation review, a shell test
    suite, anything. It was also being read as *"is this SWE-bench?"*, so from
    step 2 onward every target-paired run was handed a continuation prompt
    telling it to resolve a GitHub issue, listing an empty set of target
    failing tests, and instructing it to run ``python -m pytest`` in a
    repository that has no Python. Both talos experiments ran that way, in both
    arms, and the arm with more governance to reconcile reacted worse — which
    read as a treatment effect.

    A SWE-bench task is one that says so: a SWE case type, or a spec carrying
    the failing tests that define the objective. Nothing else.
    """
    data = spec or {}
    case_type = str(data.get("case_type") or "")
    # Both spellings: `_write_case_spec` stamps the enum value
    # (`swe_bench_instance`) for the bare lane and the short name (`swe_pro`)
    # for the pro one, and a spec written by hand may use either.
    if case_type in {
        "swe_bench",
        "swe_pro",
        "swe_bench_instance",
        "swe_pro_instance",
    }:
        return True
    return bool(data.get("fail_to_pass"))


def _provision_git_target(
    workspace: Path, spec: dict, on_provisioned: object = None
) -> None:
    """Materialize an external target repository at its pinned commit (ADR-0029).

    The commit — never the ref — is what gets checked out. It was resolved once,
    at run creation, and shipped in the spec; resolving a ref here would let the
    run execute against a commit its own provenance does not name.

    Setup commands run before the agent and their output is committed along with
    the checkout, so the agent's later ``git diff HEAD`` contains only the
    agent's work. Build artifacts a repo already ignores (``.venv``,
    ``node_modules``) stay ignored — ``git add -A`` honors ``.gitignore``, and an
    ignored file never appears in the diff anyway.
    """
    import swebench_common as swe

    target = spec.get("target") or {}
    repo_url = str(target.get("repo_url") or "")
    commit = str(target.get("commit") or "")
    target_id = str(target.get("target_id") or "?")
    if not repo_url or not commit:
        sys.exit(
            f"❌ Target '{target_id}' is missing a repo_url or a resolved commit. "
            "The worker stages both into case_spec.json — the run should never "
            "have started without them."
        )

    if workspace.exists():
        workspace.rmdir()  # let git clone create it
    print(f"  Provisioning target {target_id} @ {commit[:10]} …")
    swe.clone_checkout(workspace, repo_url, commit)

    for command in [str(item) for item in target.get("setup", []) if str(item).strip()]:
        rendered = command.format(workspace=str(workspace))
        print(f"  setup: {rendered}")
        # Shell is intentional and is the contract: setup entries are
        # operator-authored shell lines (`uv sync && make build`), exactly like
        # a case's validation_commands. They come from the lab's own database,
        # not from the agent or the target repository, and they already run
        # inside the per-run container.
        result = subprocess.run(  # nosec B602 - operator-authored setup command
            rendered,
            shell=True,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(result.stdout[-1500:])
            print(result.stderr[-1500:])
            sys.exit(
                f"❌ Target setup failed (rc={result.returncode}): {rendered}\n"
                "The agent would have started against a half-built repository, so "
                "the run is stopped instead."
            )

    # The harness lands BEFORE the baseline commit on purpose: projected into a
    # committed tree it would show up in the agent's `git diff HEAD` as though
    # the agent had written it, and every diff-based measurement would be wrong.
    if callable(on_provisioned):
        on_provisioned(workspace)

    _run_git(["git", "-C", str(workspace), "add", "-A"], cwd=REPO_ROOT)
    _run_git(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.email=lab@harness.local",
            "-c",
            "user.name=harness-lab",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            f"target: provisioned {target_id} @ {commit[:10]}",
        ],
        cwd=REPO_ROOT,
    )
    print(f"  ✓ Target {target_id} provisioned at {commit[:10]}.")


def seed_workspace(workspace: Path, spec: dict | None) -> int:
    """Copy a case's starter files into an empty workspace (ADR-0068).

    A bug-fix case needs the broken code to exist before the agent runs; a
    workspace otherwise starts empty, from a git target, or from a SWE-bench
    Pro image. ``starter_dir`` is a path under ``benchmarks/`` of this
    repository — anything else is refused, because the spec is a file the
    worker writes and a path outside the corpus would copy host files into a
    run. Symlinks are skipped, not followed. A workspace that already holds
    files (a resumed or continued run) is left alone. Returns the number of
    files copied.
    """
    starter = str((spec or {}).get("starter_dir") or "").strip()
    if not starter:
        return 0
    root = (REPO_ROOT / "benchmarks").resolve()
    source = (REPO_ROOT / starter).resolve()
    if Path(starter).is_absolute() or root not in source.parents:
        sys.exit(
            f"❌ starter_dir '{starter}' is not inside benchmarks/; refusing to seed."
        )
    if not source.is_dir():
        sys.exit(f"❌ starter_dir '{starter}' does not exist.")
    if workspace.exists() and any(workspace.iterdir()):
        return 0
    workspace.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        target = workspace / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    print(f"  Seeded workspace/{workspace.name}/ with {copied} starter file(s).")
    return copied


def provision_workspace(
    workspace: Path, spec: dict, on_provisioned: object = None
) -> None:
    """Clone + set up a subject repo in the workspace before the agent runs.

    Repo-task / SWE-bench flow (ADR-0009): clone @ base_commit, apply the
    test_patch (so the failing test is present and the agent can self-check),
    commit that provisioned state (so the agent's later `git diff HEAD` is only
    its own changes), and editable-install the repo + test deps into the runner
    interpreter. Fails the run loudly if the install fails.
    """
    import swebench_common as swe

    if (workspace / ".git").exists():
        print(f"  workspace/{workspace.name}/ already provisioned — skipping clone.")
        return
    if workspace.exists() and any(workspace.iterdir()):
        sys.exit(f"❌ workspace/{workspace.name}/ is not empty; cannot provision repo.")

    if spec.get("source") == "git_target":
        _provision_git_target(workspace, spec, on_provisioned)
        return

    if spec.get("case_type") == "swe_pro":
        # Pro lane: copy the repo out of the instance's prebuilt image (needs
        # docker). In the web lane the worker pre-provisions before launching
        # the DB-blind runner, so the .git short-circuit above already fired.
        import swebench_pro_common as swe_pro

        if not swe_pro.docker_available():
            sys.exit(
                "❌ SWE-bench Pro provisioning needs docker, which this "
                "environment lacks. In the web lane the worker must "
                "pre-provision the workspace before the runner starts."
            )
        print(f"  Provisioning from image {spec.get('dockerhub_image', '?')} …")
        swe_pro.provision(workspace, spec)
        print("  ✓ Pro workspace provisioned (repo + test files, committed).")
        return

    if workspace.exists():
        workspace.rmdir()  # let git clone create it

    repo = spec.get("repo", "")
    repo_url = spec.get("repo_url") or swe.repo_url_from(repo)
    base_commit = spec.get("base_commit", "")
    print(f"  Provisioning {repo} @ {base_commit[:10]} …")
    swe.clone_checkout(workspace, repo_url, base_commit)

    if spec.get("apply_test_patch", True) and spec.get("test_patch"):
        print("  Applying test_patch (adds the failing test) …")
        swe.apply_patch(workspace, spec["test_patch"])

    # Commit the provisioned state so finalize's `git diff HEAD` isolates the
    # agent's edits from base_commit + test_patch.
    _run_git(["git", "-C", str(workspace), "add", "-A"], cwd=REPO_ROOT)
    _run_git(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.email=lab@harness.local",
            "-c",
            "user.name=harness-lab",
            "commit",
            "-q",
            "-m",
            "swebench: provisioned (base + test_patch)",
        ],
        cwd=REPO_ROOT,
    )

    python = spec.get("python", "3.11")
    print(f"  Installing repo + test deps (python={python}) …")
    installed = swe.install_repo(workspace, sys.executable, spec.get("install_pins"))
    if installed.returncode != 0:
        print(installed.stdout[-1500:])
        print(installed.stderr[-1500:])
        sys.exit(f"❌ repo install failed (rc={installed.returncode}).")
    print("  ✓ Repo provisioned and installed.")


def find_next_run(runs_path: Path, current_run_id: str) -> dict | None:
    """Return the next pending run after current_run_id, or None."""
    data = yaml.safe_load(runs_path.read_text())
    runs = data.get("runs", [])
    found = False
    for run in runs:
        if run["run_id"] == current_run_id:
            found = True
            continue
        if found and run.get("status") == "pending":
            return run
    # fallback: any other pending run
    for run in runs:
        if run.get("status") == "pending" and run["run_id"] != current_run_id:
            return run
    return None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def detect_run_subcommand() -> bool:
    """Return True if `opencode run` (headless) subcommand is available."""
    try:
        result = subprocess.run(
            ["opencode", "run", "--help"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0 and "message" in (result.stdout + result.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def build_opencode_cmd(
    executor_model: str | None, agent: str | None, message: str
) -> list[str]:
    """Assemble the non-interactive `opencode run` command.

    --dangerously-skip-permissions is required for headless runs so the agent
    does not block waiting for interactive permission approvals.
    """
    cmd = ["opencode", "run", "--dangerously-skip-permissions", "--print-logs"]
    if executor_model:
        cmd += ["-m", executor_model]
    if agent:
        cmd += ["--agent", agent]
    cmd.append(message)
    return cmd


def _native_model(model: str | None) -> str | None:
    """Return a native model id for codex/claude, or None to use the CLI default.

    The web form's executor models are namespaced by provider (``gateway/…`` for
    LiteLLM, ``lmstudio/…`` for the direct LM Studio route) and do not map to
    native Codex/Claude model ids, so those are ignored (the CLI uses its
    configured default). A bare id (no provider prefix) is treated as an
    explicit native override.
    """
    if not model or model.startswith(("gateway/", "lmstudio/")):
        return None
    return model


def build_codex_cmd(executor_model: str | None, message: str) -> list[str]:
    """Assemble a non-interactive OpenAI Codex CLI command (host, native auth).

    --quiet prints only the final output; --full-auto auto-approves edits and
    commands in the sandbox so a headless run does not block on approvals. The
    prompt carries the ``workspace/<run_id>/`` path, so cwd stays REPO_ROOT like
    opencode.
    """
    cmd = ["codex", "--quiet", "--full-auto"]
    native = _native_model(executor_model)
    if native:
        cmd += ["-m", native]
    cmd.append(message)
    return cmd


def build_claude_cmd(
    executor_model: str | None, message: str, add_dirs: Sequence[Path] = ()
) -> list[str]:
    """Assemble a non-interactive Claude Code CLI command (host, native auth).

    -p is print/non-interactive; --dangerously-skip-permissions avoids blocking
    on tool approvals in a headless run. stream-json (requires --verbose in -p
    mode) emits one JSON event per assistant message / tool call / result —
    the observability layer that gives claude runs real tool-level attribution
    and usage numbers (the plain CLI only prints a final summary).

    ``add_dirs`` are passed as ``--add-dir``, which is the only way Claude Code
    loads ``.claude/skills`` (and agents) from a directory that is not the cwd
    or one of its parents *inside the same repository*. See
    :func:`harness_add_dirs` for when the lab root has to be one.
    """
    cmd = [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    native = _native_model(executor_model)
    if native:
        cmd += ["--model", native]
    for directory in add_dirs:
        cmd += ["--add-dir", str(directory)]
    cmd.append(message)
    return cmd


def needs_staged_catalog(
    harness: str, agent_cwd: Path | None, lab_root_visible: bool
) -> bool:
    """Whether this run has to hand the CLI a skill catalog of its own (ADR-0061).

    A ``lab_root`` claude run whose agent starts inside the workspace — the
    SWE lane's control-arm hygiene — starts inside a *clone*. Claude Code walks
    ``.claude/skills`` from the cwd up to the nearest repository root, and the
    clone is one, so the walk stops there and the lab's catalog at the real
    root is never registered: every ``Skill`` call answers ``Unknown skill``
    and the run records "asked and not invoked" about a skill that was never
    on the menu. (``CLAUDE.md`` is collected from every ancestor regardless,
    which is what made the half-loaded harness look whole.)

    False under every masked condition: there the lab's views are empty stubs
    by design (ADR-0049), and an ``injected`` arm already carries its skills
    inside the workspace.
    """
    if harness != "claude" or agent_cwd is None or not lab_root_visible:
        return False
    return agent_cwd.resolve() != REPO_ROOT.resolve()


def stage_skill_catalog(out_dir: Path, skills: list, tool_id: str) -> Path | None:
    """Copy the run's effective catalog into a directory of its own.

    What ``--add-dir`` registers is ``<dir>/.claude/skills``, so the directory
    named has to be one whose *only* content is that catalog. Naming the lab
    root instead hung every run: Claude Code indexes and watches an added
    directory, the repo root is 4.7 GB across 141k files, and it contains
    ``workspace/`` — the clone the agent is working in — so the add was also
    recursive. The CLI never reached session init and the watchdog killed the
    run at ten minutes with an empty event stream.

    A per-run staging directory is also the honest surface: the agent gets the
    skills the condition grants it and not read access to every other run's
    artifacts. Copied, not symlinked, so an archived run still explains itself.
    """
    from ml_python_base.skills_sync.config import DEFAULT_REGISTRY_PATH, load_registry
    from ml_python_base.skills_sync.linker import add_skills

    if not skills:
        return None
    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    tool = registry.tool(tool_id)
    if not tool.has_native_view:
        return None
    root = out_dir / "harness_skills"
    # Rebuilt every run: the catalog is derived from this run's ablation, and a
    # survivor from a previous attempt would silently undo it on a retry.
    if root.exists():
        shutil.rmtree(root)
    placed = sorted(add_skills(root, tool, skills))
    print(f"  ✓ Skill catalog staged for the CLI: {len(placed)} skill(s)")
    return root


def build_harness_cmd(
    harness: str,
    executor_model: str | None,
    agent: str | None,
    message: str,
    add_dirs: Sequence[Path] = (),
) -> list[str]:
    """Dispatch to the per-harness command builder (opencode | codex | claude)."""
    if harness == "codex":
        return build_codex_cmd(executor_model, message)
    if harness == "claude":
        return build_claude_cmd(executor_model, message, add_dirs=add_dirs)
    return build_opencode_cmd(executor_model, agent, message)


def _print_manual_fallback(
    args: argparse.Namespace, executor_model: str, prompt_path: Path, clipped: bool
) -> None:
    print()
    print("-" * 66)
    print("  NEXT STEP — run manually:")
    print("-" * 66)
    print("  1. make opencode            # launches the TUI with .env + plugins loaded")
    print(f"  2. Verify model:   {executor_model or '(set EXECUTOR_MODEL)'}")
    print(
        "  3. Paste prompt:   Cmd+V" + (" (already in clipboard ✓)" if clipped else "")
    )
    if not clipped:
        print(f"     or: cat {prompt_path.relative_to(REPO_ROOT)} | pbcopy")
    print("  4. When done, close the run:")
    print(f"     make bench-close RUN={args.run} SCORE=<0-10> INTERVENTIONS=<N>")
    print("-" * 66)


def render_claude_event(raw_line: str) -> str | None:
    """One human-readable line for a claude stream-json event (None = drop).

    Keeps step logs legible for the UI/audit while the raw JSONL goes to the
    events file: assistant text passes through, tool calls become one-line
    `→ Tool(arg)` markers, tool results are dropped (noise), and the final
    result event becomes a compact stats line.

    The one exception to "tool results are dropped": each emits a compact
    `# awaiting model` marker. A tool result going back to the model is exactly
    the moment a request is in flight, and it is the only such moment claude
    announces. Without it the run watchdog cannot tell a claude run waiting on
    a silent gateway from one wedged inside a tool call, so it charged every
    silence to the agent. `run_failures.CLAUDE_AWAITING_MARKER` is the reader;
    the two must move together.
    """
    try:
        event = json.loads(raw_line)
    except json.JSONDecodeError:
        return raw_line.rstrip("\n") + "\n"  # not an event — pass through
    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "init":
        return f"# claude session init (model {event.get('model', '?')})\n"
    if etype == "assistant":
        parts: list[str] = []
        for block in (event.get("message") or {}).get("content", []):
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                parts.append(block["text"].rstrip() + "\n")
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input") or {}
                arg = (
                    inp.get("file_path")
                    or inp.get("notebook_path")
                    or inp.get("command")
                    or inp.get("skill")
                    or inp.get("pattern")
                    or inp.get("subagent_type")
                    or ""
                )
                arg = str(arg).replace("\n", " ")[:160]
                parts.append(f"→ {name}({arg})\n")
        return "".join(parts) or None
    if etype == "result":
        cost = event.get("total_cost_usd")
        cost_s = f"${cost:.4f}" if isinstance(cost, (int, float)) else "?"
        return (
            f"# result: {event.get('subtype', '?')} turns={event.get('num_turns', '?')} "
            f"cost={cost_s} duration_ms={event.get('duration_ms', '?')}\n"
        )
    if etype == "user":
        # A `user` event is a tool result going back to the model. Its blocks
        # carry a tool_use_id rather than a name, so the marker names the tool
        # only when the harness happened to include one.
        names = sorted(
            {
                str(block.get("name"))
                for block in (event.get("message") or {}).get("content", [])
                if block.get("type") == "tool_result" and block.get("name")
            }
        )
        label = ", ".join(names)
        return f"# awaiting model ({label})\n" if label else "# awaiting model\n"
    return None  # anything else: noise for the readable log


def stamp_event_line(line: str, now_iso: str | None = None) -> str:
    """Prepend `_received_at` to a stream-json event line.

    claude stream-json events carry no clock of their own; the arrival stamp is
    what lets the Langfuse exporter place generations and tool calls on a real
    timeline instead of interpolating. Only lines that are clearly JSON objects
    are touched, and readers that do not know the key ignore it.
    """
    if not line.startswith('{"'):
        return line
    import datetime as _dt

    received = now_iso or _dt.datetime.utcnow().isoformat() + "Z"
    return f'{{"_received_at":"{received}",' + line[1:]


def step_clock() -> float:
    """Monotonic seconds for timing harness steps (a seam for tests)."""
    return time.monotonic()


def run_opencode(
    cmd: list[str],
    log_path: Path,
    also_append: Path | None = None,
    label: str = "opencode",
    cwd: Path | None = None,
    events_path: Path | None = None,
) -> int:
    """Run the harness CLI, streaming output to terminal and capturing to log_path.

    If also_append is given, output is also appended to that file (combined log).
    `label` names the harness in the log header (used by the step counter).
    `cwd` overrides the working directory (default REPO_ROOT). When
    `events_path` is given (claude stream-json mode), every raw line is teed
    there and the log receives the human-readable rendering instead.
    """
    import datetime as _dt

    header = f"# {label} run started {_dt.datetime.utcnow().isoformat()}Z\n"
    cmd_line = "# cmd: " + " ".join(cmd[:-1]) + " <prompt>\n\n"

    append_fh = None
    if also_append is not None:
        append_fh = also_append.open("a")
    events_fh = events_path.open("w") if events_path is not None else None

    with log_path.open("w") as log:
        log.write(header + cmd_line)
        log.flush()
        if append_fh:
            append_fh.write(f"\n{'=' * 60}\n{header}{cmd_line}")
            append_fh.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd is not None else REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if events_fh is not None:
                events_fh.write(stamp_event_line(line))
                events_fh.flush()
                rendered = render_claude_event(line)
                if rendered is None:
                    continue
                line = rendered
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            if append_fh:
                append_fh.write(line)
        proc.wait()

    if events_fh:
        events_fh.close()
    if append_fh:
        append_fh.close()

    return proc.returncode


def detect_stop_signal(log_text: str) -> bool:
    """Return True if the agent's output contains a completion/next-chat signal."""
    return bool(_STOP_RE.search(log_text))


# ---------------------------------------------------------------------------
# Usage enrichment (all fail silently)
# ---------------------------------------------------------------------------


def parse_log_usage(log_path: Path) -> dict | None:
    """Extract approximate usage from opencode output log (best-effort)."""
    if not log_path.exists():
        return None
    text = log_path.read_text(errors="replace")
    result: dict = {}

    # Step count (count our own header markers, any harness label)
    steps = len(re.findall(r"^# \w+ run started", text, re.MULTILINE))
    if steps > 1:
        result["steps"] = steps

    # Tool calls (opencode prints tool invocations)
    tool_calls = len(
        re.findall(
            r"(?:Running tool|Tool call|Calling tool|tool_use|ToolUse)",
            text,
            re.IGNORECASE,
        )
    )
    if tool_calls:
        result["tool_calls"] = tool_calls

    # Rough turn count (assistant response markers)
    turns = len(
        re.findall(
            r"(?:^Assistant:|>\s*Assistant|<assistant|role.*assistant)",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
    )
    if turns:
        result["turns"] = turns

    # Files touched (write/edit/create patterns)
    files_touched = set(
        re.findall(
            r"(?:Writing to|Editing|Creating|Wrote|Created)\s+[`\"']?([\w./\-]+\.py)[`\"']?",
            text,
            re.IGNORECASE,
        )
    )
    if files_touched:
        result["files_touched"] = sorted(files_touched)

    # Detected model. opencode's --print-logs names it exactly on every stream
    # line; the loose fallback once read "tracking" out of ordinary prose.
    served = re.search(r"providerID=(\S+) modelID=(\S+)", text)
    if served:
        result["detected_model"] = f"{served.group(1)}/{served.group(2)}"
    else:
        m = re.search(
            r"(?:Using model|model)[:\s]+([a-zA-Z0-9/._:\-]+)", text, re.IGNORECASE
        )
        if m:
            result["detected_model"] = m.group(1)

    # Detected session/provider
    s = re.search(r"session[_\-]?id[:\s]+([a-zA-Z0-9\-]+)", text, re.IGNORECASE)
    if s:
        result["detected_session"] = s.group(1)

    return result if result else None


def pull_usage_litellm(run_id: str) -> dict | None:
    base = os.environ.get("GATEWAY_BASE_URL", "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    master_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not base or not master_key:
        return None
    try:
        url = f"{base}/spend/logs?key_alias={run_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {master_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read())
        if isinstance(data, list) and data:
            total_input = sum(r.get("prompt_tokens", 0) for r in data)
            total_output = sum(r.get("completion_tokens", 0) for r in data)
            total_cost = sum(r.get("spend", 0) for r in data)
            return {
                "requests": len(data),
                "input_tokens": total_input or None,
                "output_tokens": total_output or None,
                "cost_usd": round(total_cost, 6) if total_cost else None,
            }
    except Exception:
        pass
    return None


# Every session opencode opens is announced in its own --print-logs output as
# `message=created id=ses_… … parentID=…` — the step's main session and every
# sub-agent session it spawns. That line is the only join key that survives
# both runner layouts: the session title is invented by the model (opencode's
# `title` agent), and the session directory is `/app` for a full-harness run
# but `/app/workspace/<run>` for a masked bare one (ADR-0049).
_OPENCODE_SESSION_RE = re.compile(r"message=created id=(ses_[A-Za-z0-9]+)")


def opencode_session_ids(log_paths: Sequence[Path]) -> list[str]:
    """Every opencode session id the run's own logs announce, in order, once."""
    seen: dict[str, None] = {}
    for path in log_paths:
        if not path.exists():
            continue
        for match in _OPENCODE_SESSION_RE.finditer(path.read_text(errors="replace")):
            seen.setdefault(match.group(1), None)
    return list(seen)


def opencode_db_path() -> Path:
    """Where opencode keeps its sessions: ``$XDG_DATA_HOME`` or ``~/.local/share``."""
    root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / "opencode" / "opencode.db"


def _opencode_token_totals(cur: Any, ids: Sequence[str]) -> tuple[list[str], tuple]:
    """The run's sessions (its own plus their sub-agents) and their token sums."""
    marks = ",".join("?" for _ in ids)
    cur.execute(
        f"SELECT id FROM session WHERE id IN ({marks}) "  # nosec B608 - placeholders only
        f"OR parent_id IN ({marks})",
        (*ids, *ids),
    )
    every = sorted({row[0] for row in cur.fetchall()})
    if not every:
        return [], ()
    marks = ",".join("?" for _ in every)
    cur.execute(
        f"""
        SELECT
            COALESCE(SUM(tokens_input), 0),
            COALESCE(SUM(tokens_output), 0),
            COALESCE(SUM(tokens_reasoning), 0),
            COALESCE(SUM(tokens_cache_read), 0),
            COALESCE(SUM(tokens_cache_write), 0),
            COALESCE(SUM(cost), 0)
        FROM session WHERE id IN ({marks})
        """,  # nosec B608 - placeholders only
        every,
    )
    return every, cur.fetchone()


def _opencode_work_counts(cur: Any, every: Sequence[str]) -> tuple[int, int, int]:
    """``(turns, model_ms, tool_calls)`` for the given sessions.

    One assistant message is one model turn, and its ``time`` brackets the
    request: the sum is how long the model itself was working, which the step
    clock (harness start-up, tool execution) does not isolate. Zeros when the
    schema or SQLite build cannot answer — these are a bonus over the token
    totals, never a reason to lose them.
    """
    import sqlite3

    marks = ",".join("?" for _ in every)
    try:
        cur.execute(
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(
                    json_extract(data, '$.time.completed')
                    - json_extract(data, '$.time.created')
                ), 0)
            FROM message
            WHERE session_id IN ({marks})
              AND json_extract(data, '$.role') = 'assistant'
            """,  # nosec B608 - placeholders only
            list(every),
        )
        turns, model_ms = cur.fetchone()
        cur.execute(
            f"SELECT COUNT(*) FROM part WHERE session_id IN ({marks}) "  # nosec B608
            "AND json_extract(data, '$.type') = 'tool'",
            list(every),
        )
        (tool_calls,) = cur.fetchone()
    except sqlite3.Error:
        return 0, 0, 0
    return int(turns or 0), int(model_ms or 0), int(tool_calls or 0)


def pull_usage_opencode_db(
    session_ids: Sequence[str], db_path: Path | None = None
) -> dict | None:
    """Token and work totals for exactly the sessions this run opened.

    Joined by session id — never by title or directory, which silently matched
    nothing for every bare arm and for any run whose model titled its session
    without the run id. Sub-agent sessions are included twice over: their
    ``created`` line is in the log, and ``parent_id`` catches one that was not.

    ``output_tokens`` is everything the model generated, reasoning included:
    opencode counts reasoning separately, and a model that thinks for 40k tokens
    has generated them whether or not they reached the answer. The reasoning
    share is kept beside it.

    Never raises: usage is evidence about a run, not a reason to fail it.
    """
    import sqlite3

    ids = list(dict.fromkeys(session_ids))
    path = db_path or opencode_db_path()
    if not ids or not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        cur = con.cursor()
        every, totals = _opencode_token_totals(cur, ids)
        if not every:
            return None
        turns, model_ms, tool_calls = _opencode_work_counts(cur, every)
    except sqlite3.Error:
        return None
    finally:
        con.close()
    tokens_in, tokens_out, reasoning, cache_read, cache_write, cost = totals
    result: dict = {
        "sessions": len(every),
        "input_tokens": int(tokens_in),
        "output_tokens": int(tokens_out) + int(reasoning),
        "reasoning_tokens": int(reasoning),
        "cache_read_tokens": int(cache_read),
        "cache_creation_tokens": int(cache_write),
        "turns": turns or None,
        "tool_calls": tool_calls or None,
        "model_minutes": round(model_ms / 60000.0, 2) if model_ms else None,
        "usage_source": "opencode_db",
    }
    # opencode prices a model from its catalog; a direct-host model has no
    # price there and reports 0. A positive cost is kept; a zero is left to
    # `self_hosted_cost`, which knows whether that zero is a fact about the
    # model or a gap in opencode's catalog.
    if cost:
        result["cost_usd"] = round(float(cost), 6)
    return result


def pull_usage_langfuse(session_id: str) -> dict | None:
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not secret or not public:
        return None
    try:
        import base64

        creds = base64.b64encode(f"{public}:{secret}".encode()).decode()
        url = f"{host.rstrip('/')}/api/public/sessions/{session_id}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
            data = json.loads(resp.read())
        traces = data.get("traces", [])
        if traces:
            input_t = sum(t.get("usage", {}).get("input", 0) for t in traces)
            output_t = sum(t.get("usage", {}).get("output", 0) for t in traces)
            cost = sum(t.get("usage", {}).get("totalCost", 0) for t in traces)
            return {
                "turns": len(traces),
                "input_tokens": input_t or None,
                "output_tokens": output_t or None,
                "cost_usd": round(cost, 6) if cost else None,
            }
    except Exception:
        pass
    return None


def merge_usage(*sources: dict | None) -> dict:
    """Merge usage dicts, lowest priority first; a later source wins per field.

    An empty value (None or "") never overwrites a populated one: a remote
    source that knows the session but has no cost for it must not erase the
    cost a lower-priority source did record.
    """
    merged: dict = {}
    for source in sources:
        for key, value in (source or {}).items():
            if value is None or value == "":
                merged.setdefault(key, value)
            else:
                merged[key] = value
    return merged


def collect_claude_usage(out_dir: Path) -> dict:
    """Aggregate usage from claude stream-json event files (never raises).

    Sums the terminal `result` events (tokens, cost, turns, duration) and
    counts tool_use events / edited files across steps — filling the usage
    fields the plain claude CLI never reported.
    """
    events_files = sorted((out_dir / "steps").glob("step-*.events.jsonl"))
    single = out_dir / "opencode.events.jsonl"
    if single.exists():
        events_files.append(single)
    if not events_files:
        return {}

    input_tokens = output_tokens = 0
    cache_read_tokens = cache_creation_tokens = 0
    cost = 0.0
    turns = tool_calls = 0
    duration_ms = 0
    files_modified: set[str] = set()
    detected_model: str | None = None
    saw_result = False
    for path in events_files:
        for raw in path.read_text(errors="replace").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "system" and event.get("subtype") == "init":
                detected_model = event.get("model") or detected_model
            elif etype == "assistant":
                for block in (event.get("message") or {}).get("content", []):
                    if block.get("type") != "tool_use":
                        continue
                    tool_calls += 1
                    if block.get("name") in {
                        "Edit",
                        "Write",
                        "MultiEdit",
                        "NotebookEdit",
                    }:
                        target = (block.get("input") or {}).get("file_path") or (
                            block.get("input") or {}
                        ).get("notebook_path")
                        if target:
                            files_modified.add(str(target))
            elif etype == "result":
                saw_result = True
                usage = event.get("usage") or {}
                input_tokens += usage.get("input_tokens") or 0
                output_tokens += usage.get("output_tokens") or 0
                # The prompt cache is where a harness actually costs: a 25-skill
                # catalog plus CLAUDE.md is re-read on every turn as
                # `cache_read_input_tokens`, and `input_tokens` counts only the
                # uncached sliver (42-146 in real runs). Summing just the latter
                # once showed a full-harness arm using *fewer* tokens than a
                # bare one while costing 71% more.
                cache_read_tokens += usage.get("cache_read_input_tokens") or 0
                cache_creation_tokens += usage.get("cache_creation_input_tokens") or 0
                if isinstance(event.get("total_cost_usd"), (int, float)):
                    cost += event["total_cost_usd"]
                turns += event.get("num_turns") or 0
                duration_ms += event.get("duration_ms") or 0
    if not saw_result and tool_calls == 0:
        return {}
    collected: dict = {
        "turns": turns or None,
        "tool_calls": tool_calls or None,
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "cache_read_tokens": cache_read_tokens or None,
        "cache_creation_tokens": cache_creation_tokens or None,
        "cost_usd": round(cost, 6) if cost else None,
        "time_minutes": round(duration_ms / 60000, 2) if duration_ms else None,
        "files_modified": sorted(files_modified),
        "detected_model": detected_model,
    }
    return {k: v for k, v in collected.items() if v not in (None, [])}


def write_usage_json(out_dir: Path, usage: dict | None) -> Path:
    skeleton: dict = {
        "turns": None,
        "tool_calls": None,
        "steps": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_creation_tokens": None,
        "cost_usd": None,
        "time_minutes": None,
        "files_modified": [],
        "diff_lines": None,
        "detected_model": None,
        "detected_session": None,
        "stop_reason": None,
        # Which prompt template each step was given ("case", "swe",
        # "case_step", "continuation", "generic"), in order.
        "prompt_kinds": None,
    }
    if usage:
        skeleton.update(usage)
    path = out_dir / "usage.json"
    path.write_text(json.dumps(skeleton, indent=2))
    return path


# Model ids served by a machine the operator owns. Direct lanes carry the
# backend as an opencode provider prefix; gateway aliases name it as the alias
# prefix (``gateway/lmstudio-qwen3.6-35b``), the same convention the gateway
# catalog groups by.
_SELF_HOSTED_DIRECT = ("lmstudio/", "ollama/")
_SELF_HOSTED_ALIASES = ("lmstudio-", "ollama-")


def is_self_hosted_model(model: str | None) -> bool:
    """True when ``model`` runs on a local host — no per-token price exists."""
    if not model:
        return False
    if model.startswith(_SELF_HOSTED_DIRECT):
        return True
    alias = model[len("gateway/") :] if model.startswith("gateway/") else model
    return alias.startswith(_SELF_HOSTED_ALIASES)


def self_hosted_cost(harness: str, models: Sequence[str]) -> float | None:
    """``0.0`` when every model this run addressed is self-hosted, else ``None``.

    Only opencode actually talks to the addressed model: claude and codex drop
    a ``lmstudio/…`` id and fall back to their own cloud default
    (:func:`_native_model`), so for them a local id says nothing about spend.
    """
    if harness != "opencode" or not models:
        return None
    return 0.0 if all(is_self_hosted_model(m) for m in models) else None


def assemble_usage(
    out_dir: Path,
    opencode_log: Path,
    *,
    run_id: str,
    session_id: str,
    harness: str,
    models: Sequence[str],
    harness_seconds: float,
    completed_steps: int,
    stop_reason: str | None,
    prompt_kinds: Sequence[str],
) -> dict:
    """Every usage source merged into the one dict ``usage.json`` records.

    One function for the managed and the interactive path: the two used to
    merge in different orders, so the same run could record different numbers
    depending on how it was launched.

    Two figures are the lab's own instrument and override every source,
    because a comparison is only fair when each arm is measured the same way:

    * ``time_minutes`` — the step clock, the wall time of every harness
      invocation summed. The same clock for opencode, claude and codex, for a
      masked bare arm and a full-harness one. Provisioning, scoring and
      archiving stay outside it.
    * ``cost_usd`` — ``0.0`` when every addressed model is self-hosted
      (:func:`self_hosted_cost`): measured as free, not unknown.
    """
    db_usage = None
    if harness == "opencode":
        logs = [opencode_log, *sorted((out_dir / "steps").glob("step-*.log"))]
        db_usage = pull_usage_opencode_db(opencode_session_ids(logs))
    remote_usage = pull_usage_litellm(run_id) or pull_usage_langfuse(session_id)
    # Lowest priority first: claude stream-json events (claude runs) < log
    # estimates < opencode DB < remote API.
    usage = merge_usage(
        collect_claude_usage(out_dir),
        parse_log_usage(opencode_log),
        db_usage,
        remote_usage,
    )
    if completed_steps:
        usage["steps"] = completed_steps
        usage["time_minutes"] = round(harness_seconds / 60.0, 2)
        usage["time_source"] = "step_clock"
    free = self_hosted_cost(harness, models)
    if free is not None and usage.get("cost_usd") in (None, "", 0, 0.0):
        usage["cost_usd"] = free
        usage["cost_source"] = "self_hosted"
    if stop_reason:
        usage["stop_reason"] = stop_reason
    if prompt_kinds:
        # One entry per executed step, naming the template that wrote its
        # prompt. The SWE-bench leak was invisible in every stored number and
        # turned up only by opening a step prompt by hand; this makes the class
        # of defect readable straight off the artifacts.
        usage["prompt_kinds"] = list(prompt_kinds)
    return usage


# ---------------------------------------------------------------------------
# Score sanity check
# ---------------------------------------------------------------------------


def score_sanity_check(
    workspace: Path, run_id: str, score: int, interactive: bool
) -> bool:
    """Check main.py for evidence matching the claimed score.

    Non-blocking by default. In interactive mode, asks for confirmation when
    patterns are missing.
    """
    main_py = workspace / run_id / "main.py"
    if not main_py.exists():
        main_py = workspace / "main.py"
    if not main_py.exists():
        return True  # can't check, don't block

    text = main_py.read_text(errors="replace")
    text_lower = text.lower()
    warnings = []

    for threshold, patterns, label in SCORE_CHECKS:
        if score >= threshold and not any(p.lower() in text_lower for p in patterns):
            warnings.append(
                f"  ⚠️  score={score} >= {threshold} but no {label} found in main.py"
            )

    if not warnings:
        return True

    print()
    for w in warnings:
        print(w)

    if interactive:
        try:
            ans = (
                input("  Score looks high for the code found — proceed anyway? [y/N]: ")
                .strip()
                .lower()
            )
            return ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            return False

    return True  # non-blocking when not interactive


# ---------------------------------------------------------------------------
# MLflow (optional)
# ---------------------------------------------------------------------------


def log_mlflow(
    args: argparse.Namespace,
    run: dict,
    score_data: tuple[int, int, str] | None,
    usage: dict | None,
    out_dir: Path,
) -> None:
    uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    exp_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "ai-sdlc-lab")
    if not uri:
        return
    try:
        import mlflow
    except ImportError:
        print("  MLflow not installed — skipping (pip install mlflow to enable)")
        return
    try:
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(exp_name)
        with mlflow.start_run(run_name=args.run):
            mlflow.log_params(
                {
                    "case": args.case,
                    "run_id": args.run,
                    "cell_id": extract_cell_id(args.run),
                    "prompt_variant": args.prompt or run.get("prompt_variant", ""),
                    "planner_model": args.planner_model or "",
                    "executor_model": args.executor_model or "",
                    "harness": run.get("harness", ""),
                    "methodology": run.get("methodology", ""),
                    "score_mode": args.score_mode,
                    "max_steps": args.max_steps,
                }
            )
            if score_data:
                s, i, _ = score_data
                mlflow.log_metrics({"functional_score": s, "human_interventions": i})
            if usage:
                mlflow.log_metrics(
                    {
                        k: v
                        for k, v in usage.items()
                        if isinstance(v, (int, float)) and v is not None
                    }
                )
            for fname in ["metadata.json", "prompt.md", "usage.json", "report.md"]:
                fpath = out_dir / fname
                if fpath.exists():
                    mlflow.log_artifact(str(fpath))
        print(f"  MLflow run logged to {uri} / experiment={exp_name}")
    except Exception as e:
        print(f"  MLflow logging failed (non-blocking): {e}")


# ---------------------------------------------------------------------------
# Close phase (mirrors bench_close.py logic)
# ---------------------------------------------------------------------------


def close_run(
    args: argparse.Namespace,
    run: dict,
    runs_path: Path,
    out_dir: Path,
    score: int,
    interventions: int,
    notes: str,
    usage: dict | None = None,
) -> None:
    workspace = WORKSPACE_ROOT / args.run
    captured = workspace.exists()
    diff_lines = finalize_workspace(out_dir, workspace, args.run)
    if captured and score > 3 and diff_lines == 0:
        print(
            f"  ⚠️  WARNING: score={score} but diff.patch is empty — "
            f"workspace/{args.run}/ appears to have no implementation."
        )

    # Best-effort: under a masked harness condition (ADR-0049) this file is a
    # read-only empty bind in the container, and there is nothing to reset
    # because nothing wrote it.
    try:
        BENCH_CONTEXT.write_text(BENCH_CONTEXT_PLACEHOLDER)
        print("  BENCH_CONTEXT.md reset")
    except OSError:
        print("  BENCH_CONTEXT.md not writable (masked run) — nothing to reset")

    env_path = REPO_ROOT / ".env"
    restore_env_token(env_path)
    print("  .env GATEWAY_TOKEN restored")

    update_runs_yaml(runs_path, args.run, score, interventions, notes)
    print(
        f"  runs.yaml: status=completed  score={score}/10  interventions={interventions}"
    )

    results_path = runs_path.parent / "results.csv"
    parts = args.run.split("-")
    u = usage or {}
    update_results_csv(
        results_path,
        {
            "run_id": args.run,
            "cell_id": parts[2] if len(parts) >= 4 else "",
            "date": datetime.date.today().isoformat(),
            "methodology": run.get("methodology", ""),
            "harness": run.get("harness", ""),
            "prompt_variant": run.get("prompt_variant", args.prompt or ""),
            "planner_model": args.planner_model or run.get("planner_model", ""),
            "executor_model": args.executor_model or run.get("executor_model", ""),
            "executor_context": str(run.get("executor_context", "")),
            "temperature": str(run.get("temperature", "")),
            "requests": str(u.get("requests") or ""),
            "input_tokens": str(u.get("input_tokens") or ""),
            "output_tokens": str(u.get("output_tokens") or ""),
            "cost_usd": str(u.get("cost_usd") or ""),
            "time_minutes": str(u.get("time_minutes") or ""),
            "turns": str(u.get("turns") or ""),
            "diff_lines": str(diff_lines) if diff_lines else "",
            "functional_level": str(score),
            "human_interventions": str(interventions),
            "langfuse_session": run.get("langfuse_session_id", ""),
            "branch": run.get("branch", ""),
            "notes": notes,
        },
    )
    print("  results.csv updated")

    notes_suffix = f", {notes}" if notes else ""
    commit_msg = (
        f"bench: close {args.run} "
        f"(score={score}/10, interventions={interventions}{notes_suffix})"
    )
    print("  Suggested commit:")
    print(f"    git add data/runs/{args.run}/ BENCH_CONTEXT.md")
    print(f'    git commit -m "{commit_msg}"')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()
    # Headless by default for everything this process spawns, in both lanes.
    # The docker image and the run container already set these, but a host-mode
    # run (`make app-host`) goes through SubprocessRunExecutor, which passes no
    # env= and would otherwise inherit a real display — where a pygame window
    # opens, never closes, and hangs the agent's shell exactly as it did in the
    # container. `setdefault` so an operator who deliberately exports something
    # else still wins.
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    # Export the routing overlay before anything builds a harness command, so a
    # model enabled from the web — or addressed directly at LM Studio —
    # resolves in both the docker and host lanes.
    apply_gateway_overlay(direct_models(args), run_models(args))

    out_dir = DATA_DIR / args.run
    case_spec = _load_case_spec(out_dir)
    # A spec exists for every DB case; only some of them clone a subject repo.
    # Everything downstream that used to test `case_spec is not None` was really
    # asking this question.
    provisions_repo = spec_provisions_repo(case_spec)
    # A different question, and the reason the two are named apart: the first
    # decides whether a repository is cloned, the second decides which prompts
    # and which stop criterion the run gets. Reading one as the other gave
    # every target-paired case a SWE-bench continuation prompt.
    is_swe_bench = spec_is_swe_bench(case_spec)
    # The harness condition (ADR-0031, ADR-0049) is read here — before anything
    # acts on it — because it decides three things that happen in three
    # different places: whether the lab's own skill views may be regenerated,
    # where the agent runs, and whether provisioning injects the harness.
    # Reading it late is what let a `bare` run mutate the lab tree first.
    #
    # The worker historically staged this only inside the target block; read
    # both so a spec from either generation resolves to the condition the run
    # was actually created with. Falling back to "lab_root" here once silently
    # turned a declared `native` control into a lab-harness arm — the exact
    # collapse ADR-0031 exists to prevent.
    harness_source = str(
        (case_spec or {}).get("harness_source")
        or ((case_spec or {}).get("target") or {}).get("harness_source")
        or "lab_root"
    )
    # The lab root is what the agent reads: its skill views are worth
    # regenerating, and the files it writes there are reachable.
    lab_root_visible = harness_source == "lab_root"
    # The agent runs from the workspace, with the lab's governance masked in the
    # container. Nothing may write to the lab's adapter files under these.
    workspace_cwd = harness_source in ("native", "injected", "bare")
    if harness_source == "bare":
        _require_masked_lab_root()
    # Per-step instructions authored on the case (multi_step in the web UI).
    case_step_prompts: list[str] = [
        str(item) for item in (case_spec or {}).get("step_prompts", []) if str(item)
    ]

    runs_path = RUNS_FILES.get(args.case)
    if runs_path is not None:
        # Curated benchmark lane: benchmarks/<case>/ owns the task, the prompt
        # files and the experiment matrix. Kept first so a case that has both a
        # checked-in benchmark and a DB row keeps its historical semantics.
        task = load_task_yaml(runs_path)
        run = resolve_or_synthesize_run(runs_path, args.run, args)
        if run.get("status") == "completed":
            sys.exit(f"Run {args.run} is already completed. Use a new run_id.")
        prompt_short, prompt_source = resolve_prompt_file(task, args.prompt, run)
    elif case_spec is not None:
        # DB lane (ADR-0009): everything the runner needs is in case_spec.json —
        # no benchmarks/<case>/ task.yaml/prompts required. Serves SWE-bench
        # imports and every case created from the web UI alike.
        task = {
            "task_id": case_spec.get("case_id") or args.case,
            "subject_repo": case_spec.get("repo", ""),
            "base_commit": case_spec.get("base_commit", ""),
            "prompts": {},
        }
        run = {
            "run_id": args.run,
            "status": "pending",
            "prompt_variant": case_spec.get("prompt_variant") or "swe_default",
            "langfuse_session_id": f"bench-{args.run}",
            "executor_model": args.executor_model or "",
            "planner_model": args.planner_model or "",
            "harness": args.harness,
        }
        prompt_short = run["prompt_variant"]
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt_source = out_dir / "_prompt_source.md"
        prompt_source.write_text(case_spec.get("prompt", ""))
    else:
        sys.exit(
            f"Unknown case '{args.case}'. Known benchmarks: "
            f"{list(RUNS_FILES.keys())}. A case that lives only in the DB is "
            "staged by the worker as data/runs/<run-id>/case_spec.json — start "
            "it from the web UI, not by hand."
        )

    # A continuation replaces the task instruction in *every* lane, curated
    # included: `resolve_prompt_file` would hand back the original benchmark
    # prompt and re-issue the task the parent already spent its budget on
    # (ADR-0026).
    continues_run = str((case_spec or {}).get("continues_run") or "")
    if continues_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt_source = out_dir / "_prompt_source.md"
        prompt_source.write_text(
            render_continuation_prompt_body(
                args.run, continues_run, (case_spec or {}).get("prompt", "")
            )
        )
        run["continues_run"] = continues_run

    session_id = run.get("langfuse_session_id", f"bench-{args.run}")

    # Resolve models: CLI args override runs.yaml values
    executor_model = args.executor_model or run.get("executor_model") or ""
    planner_model = args.planner_model or (
        run.get("planner_model")
        if run.get("planner_model") != run.get("executor_model")
        else None
    )
    args.executor_model = executor_model
    args.planner_model = planner_model
    # A resumed run learns its models from stored metadata, after the overlay
    # was first written; re-apply so a direct LM Studio executor is routable.
    apply_gateway_overlay(direct_models(args), run_models(args))
    # opencode resolves its project config from the cwd, so a run launched from
    # the workspace would lose the lab's provider block — its gateway routing —
    # along with the governance it is meant to lose. Routing is not part of the
    # harness condition, so point it back at the tracked file explicitly. The
    # overlay wins when it set the variable itself (see opencode_overlay).
    if workspace_cwd and args.harness == "opencode":
        os.environ.setdefault("OPENCODE_CONFIG", str(REPO_ROOT / "opencode.json"))
    # Precedence mirrors the model flags: an explicit --harness wins over the
    # harness recorded in runs.yaml. It used to be the other way round, and a
    # web run whose id collided with a pre-declared cell was executed by one
    # CLI while metadata.json — the file the corpus, the leaderboard and the
    # improvement cycle all read — named another. A run filed under a harness
    # that never executed it is worse than no record at all, so when the two
    # disagree the flag wins and the divergence is announced rather than
    # resolved in silence.
    declared = str(run.get("harness") or "")
    if args.harness and declared and declared != args.harness:
        print(
            f"  ! runs.yaml declares harness '{declared}' for {args.run}, but "
            f"--harness {args.harness} was passed. Running (and recording) "
            f"{args.harness}."
        )
    run["harness"] = args.harness or declared or DEFAULT_HARNESS
    args.harness = run["harness"]

    workspace = WORKSPACE_ROOT / args.run

    # -----------------------------------------------------------------------
    # DRY RUN
    # -----------------------------------------------------------------------
    if args.dry_run:
        print()
        print("=" * 66)
        print(f"  bench-run DRY RUN: {args.run}")
        print("=" * 66)
        print(f"  Case:            {args.case}")
        print(f"  Run:             {args.run}")
        print(f"  Cell:            {extract_cell_id(args.run)}")
        print(
            f"  Prompt variant:  {prompt_short} ({prompt_source.relative_to(REPO_ROOT)})"
        )
        print(f"  Planner model:   {planner_model or '(none)'}")
        print(f"  Executor model:  {executor_model or '(unset)'}")
        print(f"  Score mode:      {args.score_mode}")
        print(f"  Max steps:       {args.max_steps}")
        print(f"  Resume:          {args.resume}")
        print(f"  Session ID:      {session_id}")
        print()
        out_dir = DATA_DIR / args.run
        print("  Files that would be written:")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/metadata.json")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/prompt.md")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/run.env")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/report.md")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/usage.json")
        print(f"    {out_dir.relative_to(REPO_ROOT)}/opencode.log")
        if args.max_steps > 1:
            for s in range(1, min(args.max_steps, 4) + 1):
                extra = (
                    "..." if s == min(args.max_steps, 4) and args.max_steps > 3 else ""
                )
                print(
                    f"    {out_dir.relative_to(REPO_ROOT)}/steps/step-{s}-prompt.md{extra}"
                )
                print(f"    {out_dir.relative_to(REPO_ROOT)}/steps/step-{s}.log{extra}")
        print(f"    workspace/{args.run}/")
        print()
        preview = build_harness_cmd(args.harness, executor_model, None, "<prompt>")
        if args.harness != "opencode" or detect_run_subcommand():
            print("  Execution: " + " ".join(preview))
            if args.max_steps > 1:
                print(
                    f"  (repeated up to {args.max_steps} times; stops on completion signal)"
                )
        else:
            print("  Execution: `opencode run` not available — manual paste required")
        print()
        print("  LiteLLM metadata that would be registered:")
        print(f"    key_alias: {args.run}   metadata.session_id: {session_id}")
        print("=" * 66)
        return 0

    # -----------------------------------------------------------------------
    # PHASE 1 — PREPARE
    # -----------------------------------------------------------------------
    print()
    print("=" * 66)
    print(f"  bench-run: {args.run}")
    print("=" * 66)
    print()
    print("  [1/5] Preparing run...")

    # Per-run skill ablation. Restore-at-start heals a previous crashed
    # ablation (native views are generated artifacts, so this is idempotent);
    # atexit covers every return/sys.exit path of this run. Runs without
    # ablation flags skip the restore only if it fails (best-effort heal).
    #
    # All of it is gated on the harness condition. `restore_full_skill_views`
    # and `apply_skill_views` write the lab's own adapter files (CLAUDE.md,
    # OPENCODE.md, .claude/skills, …), which under any masked condition are
    # read-only empty binds in the container — the write would raise, and if it
    # succeeded it would refill the very mask that makes the arm bare.
    skills_ablation = bool(args.disable_skill or args.extra_skill)
    effective_skills: list
    if harness_source == "bare":
        # There is no catalog to ablate: every skill directory is masked. A
        # `--disable-skill` here is a declaration that keeps the condition hash
        # honest, nothing more.
        if args.extra_skill or args.invoke_skill:
            sys.exit(
                "❌ harness_source='bare': a bare run has no skills to stage or "
                "invoke — every catalog is masked in the container. Drop "
                "--extra-skill/--invoke-skill, or use a governed condition."
            )
        effective_skills = []
        print("  Skills:      bare — every catalog is masked in the container")
    else:
        if lab_root_visible:
            try:
                restore_full_skill_views()
            except Exception as exc:
                if skills_ablation:
                    sys.exit(f"❌ Could not sync skill views before ablation: {exc!r}")
                print(f"  ⚠️  Skill view sync skipped: {exc!r}")
        # Always resolved, ablated or not: the `injected` harness condition
        # projects this exact set into the target, so a run with no ablation
        # still needs to know what "the harness" currently is.
        effective_skills = build_effective_skills(args.disable_skill, args.extra_skill)
        # A skill the prompt asks for must be one the agent can see, or the run
        # records "asked and not invoked" about a skill that was never there.
        invisible = sorted(
            set(args.invoke_skill) - {skill.name for skill in effective_skills}
        )
        if invisible:
            sys.exit(
                f"❌ --invoke-skill: not available to this run: "
                f"{', '.join(invisible)} "
                "(stage it with --extra-skill, or do not disable it)"
            )
        if skills_ablation and lab_root_visible:
            apply_skill_views(effective_skills)
            import atexit

            atexit.register(restore_full_skill_views)
            print(
                f"  Skills:      ablation active — "
                f"disabled={sorted(set(args.disable_skill))} "
                f"extra={sorted(set(args.extra_skill))}"
            )

    out_dir = DATA_DIR / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pro lane guard: in the web lane the worker pre-provisions the workspace
    # (fresh by construction) right before launching this docker-less runner —
    # the --fresh reset must NOT wipe it, because we could never rebuild it
    # here. When docker IS available (host CLI lane), normal fresh semantics
    # apply and provision_workspace simply re-provisions after the reset.
    keep_preprovisioned = False
    if (
        case_spec is not None
        and case_spec.get("case_type") == "swe_pro"
        and (workspace / ".git").exists()
    ):
        import swebench_pro_common as swe_pro

        keep_preprovisioned = not swe_pro.docker_available()
    if keep_preprovisioned:
        print(
            f"  workspace/{args.run}/ pre-provisioned by the worker (Pro lane) "
            "— keeping it."
        )
    else:
        validate_workspace(workspace, args.run, args.resume, fresh=args.fresh)
    workspace.mkdir(parents=True, exist_ok=True)

    harness_projection: dict | None = None

    def _inject(ws: Path) -> None:
        nonlocal harness_projection
        # The worker stages the unmasked adapter files here; absent (an
        # unmasked lane, or an older run dir) the projection reads the repo
        # root, which is what it always did.
        staged = out_dir / "harness_source"
        harness_projection = inject_harness(
            ws, effective_skills, args.harness, staged if staged.is_dir() else None
        )

    if provisions_repo and case_spec is not None:
        provision_workspace(
            workspace,
            case_spec,
            _inject if harness_source == "injected" else None,
        )
    elif case_spec is not None:
        # A case that starts from files (a broken page to fix) rather than an
        # empty folder or a cloned repository (ADR-0068).
        seed_workspace(workspace, case_spec)

    # A `native` arm that stages skills has to have them placed in the
    # workspace, or the agent — whose cwd is the clone — never sees them and the
    # arm silently equals its own control.
    if harness_source == "native" and provisions_repo:
        wanted = set(args.extra_skill) | set(args.invoke_skill)
        staged = [skill for skill in effective_skills if skill.name in wanted]
        if staged:
            harness_projection = add_skills_to_native(workspace, staged, args.harness)

    # Where the agent runs decides which harness it can see. Claude Code and
    # opencode auto-load their adapter file and native skills from the cwd, so
    # cwd IS the harness condition:
    #
    #   lab_root  — the lab repository: the agent sees the lab's harness and
    #               edits the workspace from outside. The historical default.
    #   native    — inside the target: whatever governance that repo ships, and
    #               nothing else.
    #   injected  — inside the target, with the lab harness projected into it.
    #
    # Running `native` from the repo root would silently hand the agent the lab
    # harness and quietly turn the control arm into a second treatment arm.
    #   bare      — an empty workspace with the lab's governance masked in the
    #               container: the agent sees the prompt and nothing else.
    agent_cwd: Path | None = None
    if workspace_cwd:
        agent_cwd = workspace
    elif args.harness == "claude" and provisions_repo and prompt_short == "swe_default":
        # Pre-existing control-arm hygiene for the SWE lane, unchanged.
        agent_cwd = workspace
    # A cwd inside a clone hides the lab's skill catalog from Claude Code even
    # under `lab_root`; the CLI has to be handed one of its own (ADR-0061).
    add_dirs: tuple[Path, ...] = ()
    if needs_staged_catalog(args.harness, agent_cwd, lab_root_visible):
        staged_catalog = stage_skill_catalog(out_dir, effective_skills, args.harness)
        if staged_catalog is not None:
            add_dirs = (staged_catalog,)

    repo_note: str | None = None
    if provisions_repo:
        repo_note = (
            "> The subject repository is your current working directory — "
            "make all changes inside it.\n"
            if agent_cwd is not None
            else (
                f"> The subject repository is checked out at "
                f"`workspace/{args.run}/` — make ALL changes inside that "
                f"directory and nowhere else.\n"
            )
        )
    prompt_path = generate_prompt(
        args.run,
        prompt_short,
        prompt_source,
        planner_model,
        executor_model,
        session_id,
        out_dir,
        repo_note=repo_note,
        invoke_skills=args.invoke_skill,
        cwd_note=agent_cwd is not None and not provisions_repo,
    )
    print(f"  Prompt:      {prompt_path.relative_to(REPO_ROOT)}")
    print(
        f"  Harness src: {harness_source}"
        + (" — the lab's governance is masked" if harness_source == "bare" else "")
    )

    write_metadata(
        out_dir,
        args,
        task,
        run,
        prompt_short,
        prompt_source=prompt_source,
        task_path=(
            runs_path.parent / "task.yaml"
            if runs_path is not None
            else out_dir / "case_spec.json"
        ),
        target=(case_spec or {}).get("target"),
        harness_source=harness_source,
        # An in-target harness is fingerprinted from the workspace, never from
        # the lab — otherwise `native` and `injected` hash identically and the
        # two arms silently merge into one.
        #
        # Gated on the declared condition, NOT on `agent_cwd`: the SWE lane has
        # set `agent_cwd = workspace` for its bare claude arm since long before
        # harness conditions existed, and moving that arm's fingerprint now
        # would re-bin every historical SWE run against a hash it never had.
        harness_root=(workspace if workspace_cwd else None),
        harness_projection=harness_projection,
    )
    print(f"  Metadata:    {(out_dir / 'metadata.json').relative_to(REPO_ROOT)}")

    write_run_env(out_dir, args, session_id)
    print(f"  Env:         {(out_dir / 'run.env').relative_to(REPO_ROOT)}")

    if not (out_dir / "report.md").exists():
        scaffold_report(run, out_dir)
    print(f"  Report:      {(out_dir / 'report.md').relative_to(REPO_ROOT)}")

    # Read by opencode through the lab's `opencode.json` instructions list, so
    # it is only reachable — and only writable — from a lab-root run. Under any
    # masked condition the file is a read-only empty bind in the container.
    if lab_root_visible:
        write_bench_context(args.run)
    if runs_path is not None:
        set_status_running(runs_path, args.run)

    # Virtual key (optional)
    run_key: str | None = None
    if args.create_litellm_key:
        gateway_base = os.environ.get("GATEWAY_BASE_URL", "")
        master_key = os.environ.get("LITELLM_MASTER_KEY", "")
        if not gateway_base or not master_key:
            print(
                f"  ⚠️  LiteLLM key skipped: GATEWAY_BASE_URL or LITELLM_MASTER_KEY not set.\n"
                f"     Create manually → key_alias={args.run}  metadata.session_id={session_id}"
            )
        else:
            try:
                run_key = create_litellm_key(gateway_base, master_key, run)
                key_file = out_dir / ".gateway_key"
                key_file.write_text(run_key)
                key_file.chmod(0o600)
                patch_env_token(REPO_ROOT / ".env", run_key)
                os.environ["GATEWAY_TOKEN"] = run_key
                print(
                    f"  Key:         written to {key_file.relative_to(REPO_ROOT)} (gitignored)"
                )
            except RuntimeError as e:
                print(f"  ⚠️  LiteLLM key creation failed: {e}")

    clipped = copy_to_clipboard(prompt_path.read_text())

    # -----------------------------------------------------------------------
    # PHASE 2 — EXECUTE (multi-step loop)
    # -----------------------------------------------------------------------
    print()
    print("  [2/5] Executing...")

    opencode_log = out_dir / "opencode.log"
    if args.harness == "opencode":
        has_run_cmd = detect_run_subcommand()
    else:
        # codex/claude are host CLIs; availability = the binary is on PATH.
        has_run_cmd = shutil.which(args.harness) is not None
    # codex/claude can run against their CLI's default model, so a missing
    # executor model is only fatal for opencode (gateway-routed).
    needs_model = args.harness == "opencode"
    max_steps = max(1, args.max_steps)
    steps_dir = out_dir / "steps"
    completed_steps = 0
    # Wall time of every harness invocation, summed: the run's time figure.
    harness_seconds = 0.0
    # Which criterion ended the step loop. Recorded because a cost comparison
    # between two arms is only readable against how each of them terminated:
    # an arm that ran out of budget did not spend more, it was cut off.
    stop_reason: str | None = None
    harness_cause: str | None = None
    # Which template wrote each step's prompt, in order. Recorded in usage.json
    # so a later audit can see from the artifacts alone which framing a run was
    # given — the SWE-bench leak showed up in no stored number and turned up
    # only by opening a step prompt by hand.
    prompt_kinds: list[str] = []

    if args.score_mode == "none":
        print("  score-mode=none: skipping execution.")
    elif needs_model and not executor_model:
        print(
            "  ⚠️  No executor model set — cannot run headless. Pass EXECUTOR_MODEL=..."
        )
        _print_manual_fallback(args, executor_model, prompt_path, clipped)
        if args.score_mode == "manual":
            # A headless (web/manual) run with no executor model produced nothing.
            # Fail loudly (non-zero) instead of the old silent success so the
            # worker records it as failed with a clear reason (see ADR-0006).
            print("  ❌ No executor model — nothing was built. Failing the run.")
            print("=" * 66)
            return 1
    elif has_run_cmd:
        if max_steps > 1:
            steps_dir.mkdir(exist_ok=True)

        last_swe_eval: dict | None = None
        last_workspace_fp: str | None = None
        for step_n in range(1, max_steps + 1):
            if max_steps > 1:
                if step_n == 1:
                    step_prompt_path = prompt_path
                    prompt_kind = "case"
                elif is_swe_bench:
                    # SWE-bench lane: keep the agent on the issue (the generic
                    # continuation prompt is game-build framed and derails it).
                    # Gated on the case being SWE-bench, not on it needing a
                    # clone: those are different questions, and answering the
                    # second one here handed this prompt to every case paired
                    # with an external repository.
                    try:
                        step_prompt_path = generate_swe_continuation_prompt(
                            args.run,
                            step_n,
                            steps_dir,
                            case_spec or {},
                            last_swe_eval,
                            in_repo_cwd=agent_cwd is not None,
                            invoke_skills=args.invoke_skill,
                        )
                        prompt_kind = "swe"
                    except SweSpecError as error:
                        print(f"  ❌ {error}")
                        stop_reason = "harness_error"
                        break
                elif step_n - 1 < len(case_step_prompts):
                    # The case author wrote this step; neither canned
                    # continuation prompt (Tetris / SWE-bench) applies. The list
                    # is 1:1 with steps, and step 1 already used the case prompt.
                    step_prompt_path = generate_case_step_prompt(
                        args.run,
                        step_n,
                        steps_dir,
                        case_step_prompts[step_n - 1],
                        invoke_skills=args.invoke_skill,
                    )
                    prompt_kind = "case_step"
                elif runs_path is not None:
                    # Curated benchmark lane only: this prompt is Tetris/pygame
                    # framed (main.py, an SDL smoke test, game milestones), so
                    # it belongs to the benchmark that owns that framing.
                    step_prompt_path = generate_continuation_prompt(
                        args.run,
                        step_n,
                        steps_dir,
                        workspace_dir=WORKSPACE_ROOT / args.run,
                        invoke_skills=args.invoke_skill,
                    )
                    prompt_kind = "continuation"
                    # Also copy step prompt to clipboard so user can paste if needed
                    copy_to_clipboard(step_prompt_path.read_text())
                else:
                    # A DB case that ran out of authored step_prompts. It has no
                    # framing of its own, and inheriting Tetris's would tell the
                    # agent to smoke-test a main.py that does not exist.
                    step_prompt_path = generate_generic_continuation_prompt(
                        args.run, step_n, steps_dir, invoke_skills=args.invoke_skill
                    )
                    prompt_kind = "generic"
                step_log = steps_dir / f"step-{step_n}.log"
                print(f"\n  --- Step {step_n}/{max_steps} ---")
            else:
                step_prompt_path = prompt_path
                prompt_kind = "case"
                step_log = opencode_log
            prompt_kinds.append(prompt_kind)

            prompt_text = step_prompt_path.read_text()
            cmd = build_harness_cmd(
                args.harness, executor_model, None, prompt_text, add_dirs=add_dirs
            )
            print(f"  Harness:     {args.harness}")
            print(f"  Model:       {executor_model or '(harness default)'}")
            print(f"  Log:         {step_log.relative_to(REPO_ROOT)}")
            if agent_cwd is not None:
                print(
                    f"  Agent cwd:   {agent_cwd.relative_to(REPO_ROOT)} (clean context)"
                )
            if add_dirs:
                print(
                    "  Skills from: "
                    + ", ".join(str(d.relative_to(REPO_ROOT)) for d in add_dirs)
                    + " (--add-dir: the cwd is a clone, its root hides the lab's)"
                )
            print("-" * 66)

            # For multi-step, each step log is primary; combined goes to opencode.log
            also_append = opencode_log if max_steps > 1 else None
            # claude stream-json: raw events per step for attribution/usage.
            events_path = (
                step_log.with_suffix(".events.jsonl")
                if args.harness == "claude"
                else None
            )
            step_started = step_clock()
            rc = run_opencode(
                cmd,
                step_log,
                also_append=also_append,
                label=args.harness,
                cwd=agent_cwd,
                events_path=events_path,
            )
            step_seconds = step_clock() - step_started
            harness_seconds += step_seconds
            completed_steps += 1

            print("-" * 66)
            if rc != 0:
                print(
                    f"  ⚠️  {args.harness} exited with code {rc} — see {step_log.relative_to(REPO_ROOT)}"
                )
                harness_cause = harness_error_cause(step_log)
                stop_reason = "harness_error"
                break

            # The workspace as this step left it, for the per-step reward
            # (ADR-0076). Taken before any stop check so the step that ends the
            # loop is on the curve too, and graded later by the worker — never
            # here, where the agent could come to see its own reward.
            steps_dir.mkdir(exist_ok=True)
            step_meta = snapshot_step(workspace, steps_dir, step_n)
            step_meta["seconds"] = round(step_seconds, 2)
            step_meta["prompt_kind"] = prompt_kind

            if is_swe_bench:
                # SWE-bench lane: the stop criterion is objective — evaluate the
                # F2P/P2P subset now and stop the loop the moment it resolves.
                swe_eval = swe_step_evaluation(workspace, case_spec or {})
                if swe_eval is not None:
                    last_swe_eval = swe_eval
                    # Kept, not only printed: this is the step's reward on the
                    # SWE lane, measured live by the same F2P/P2P criterion.
                    step_meta["swe_eval"] = {
                        key: swe_eval.get(key)
                        for key in (
                            "resolved",
                            "f2p_passed",
                            "f2p_total",
                            "p2p_passed",
                            "p2p_total",
                        )
                    }
                    print(
                        f"  SWEBENCH: resolved={swe_eval['resolved']} "
                        f"F2P={swe_eval['f2p_passed']}/{swe_eval['f2p_total']} "
                        f"P2P={swe_eval['p2p_passed']}/{swe_eval['p2p_total']}"
                    )
                    if swe_eval["resolved"]:
                        print(
                            f"  ✅ SWE-bench resolved after step {step_n} — stopping."
                        )
                        stop_reason = "swe_resolved"
                        write_step_meta(steps_dir, step_meta)
                        break
            write_step_meta(steps_dir, step_meta)

            if max_steps > 1:
                # A step that changed nothing in the workspace is the strongest
                # done-signal available, and — unlike the prose sentinel below —
                # every arm can reach it. It is checked FIRST and for every lane
                # on purpose: when only the sentinel could stop the loop, an arm
                # whose prompt never taught the phrase was structurally unable
                # to terminate early and paid for the full budget in restarts.
                # That turned a prompt-authoring detail into a cost difference
                # and reported it as a treatment effect.
                fp = workspace_fingerprint(workspace)
                if fp is not None and fp == last_workspace_fp:
                    print(
                        f"  ⏹  Step {step_n} changed nothing in the workspace — "
                        "stopping (agent appears done)."
                    )
                    stop_reason = "no_progress"
                    break
                last_workspace_fp = fp

                step_text = step_log.read_text()
                # Every lane but SWE-bench, which has an objective stop of its
                # own. This used to exclude any run that provisioned a
                # repository, so a target-paired case could not signal
                # completion at all and paid for its whole step budget.
                if not is_swe_bench and detect_stop_signal(step_text):
                    print(f"  Agent signaled completion at step {step_n}.")
                    stop_reason = "completion_signal"
                    break
                if step_n < max_steps:
                    print(
                        f"  Step {step_n} complete — continuing to step {step_n + 1}..."
                    )
                else:
                    # Reaching here means the budget ran out with the agent
                    # still editing. Recorded, because a truncated trajectory
                    # is not a finished one and the scores must not pretend
                    # otherwise.
                    stop_reason = "budget_exhausted"
            else:
                print("  opencode run finished.")
    else:
        if args.harness == "opencode":
            print("  ⚠️  `opencode run` not available in this opencode version.")
        else:
            print(
                f"  ⚠️  `{args.harness}` CLI not found on PATH — host mode required "
                "(run `make app-host`)."
            )
        _print_manual_fallback(args, executor_model, prompt_path, clipped)
        if args.score_mode == "manual":
            # Headless run requested but the harness CLI is unavailable, so nothing
            # executed. Fail loudly (non-zero) instead of exiting 0 — a managed run
            # must not look successful when it did no work (ADR-0006).
            print()
            print(f"  ❌ `{args.harness}` unavailable — nothing was built. Failing.")
            print("=" * 66)
            return 1

    # -----------------------------------------------------------------------
    # PHASE 3 — SCORE
    # -----------------------------------------------------------------------
    print()
    print("  [3/5] Scoring...")

    score_data: tuple[int, int, str] | None = None

    if args.score_mode == "none":
        print("  score-mode=none: skipping scoring.")
    elif args.score_mode == "manual":
        print("  Run is prepared. Score it manually, then:")
        print(f"    make bench-close RUN={args.run} SCORE=<0-10> INTERVENTIONS=<N>")
        # Still write usage.json before exiting
        print()
        print("  [4/5] Writing usage skeleton...")
        usage = assemble_usage(
            out_dir,
            opencode_log,
            run_id=args.run,
            session_id=session_id,
            harness=args.harness,
            models=usage_models(args, executor_model),
            harness_seconds=harness_seconds,
            completed_steps=completed_steps,
            stop_reason=stop_reason,
            prompt_kinds=prompt_kinds,
        )
        write_usage_json(out_dir, usage or None)
        if args.fresh:
            # Managed web run: the API owns scoring (POST /score), so Phase 5
            # close_run never runs. Archive the workspace into the run's data dir
            # and clean it up here so workspaces don't accumulate (ADR-0006).
            print()
            print("  [5/5] Archiving workspace (managed run)...")
            finalize_workspace(out_dir, workspace, args.run)
        print("=" * 66)
        if stop_reason == "harness_error":
            # The harness died; the model was never given the chance to fail.
            # Exiting 0 here archived an infrastructure failure as a legitimate
            # attempt, and the worker — which reads the exit code, not
            # usage.json — recorded it completed with a score of 0. The exit
            # comes after usage.json and the archive on purpose: the evidence
            # of *why* is the whole point (ADR-0006, ADR-0046).
            print(
                f"  ⚠️  Exiting non-zero: {args.harness} failed "
                "(stop_reason=harness_error). This run measured the "
                "connection, not the subject."
            )
            # Last on purpose: the worker stores the tail of this output.
            if harness_cause:
                print(f"  {harness_cause}")
            return 2
        return 0
    else:  # interactive
        if args.score is not None and args.interventions is not None:
            # Score sanity check before accepting pre-supplied score
            if not score_sanity_check(
                WORKSPACE_ROOT, args.run, args.score, interactive=True
            ):
                print("  Aborting — re-run with corrected score.")
                return 1
            score_data = (args.score, args.interventions, args.notes)
        else:
            # Show the result before asking for a score — give the user time to test it
            print()
            print("  -" * 33)
            print("  Test the result before scoring:")
            print(f"    python workspace/{args.run}/main.py")
            print("    # Reference: the case's scoring.md (benchmarks/<case>/)")
            print()
            try:
                input("  Press Enter when ready to score (Ctrl+C to leave open)...")
            except (EOFError, KeyboardInterrupt):
                print()
                print("  Run left open. Close manually when ready:")
                print(
                    f"    make bench-close RUN={args.run} SCORE=<0-10> INTERVENTIONS=<N>"
                )
                print("=" * 66)
                return 0
            print()
            print("  Score reference — highest level fully reached:")
            print("    0  no useful code            6  lateral movement works")
            print("    1  files exist but crash     7  rotation works")
            print("    2  runs without crashing     8  collisions + lock work")
            print("    3  opens a window            9  clears lines + tracks score")
            print(
                "    4  shows a piece            10  fully playable (game over, no blocking bugs)"
            )
            print("    5  piece falls")
            print()
            try:
                s = int(input("  Functional score (0-10): "))
                i = int(input("  Human interventions: "))
                n = input("  Notes (optional, Enter to skip): ").strip()
            except (ValueError, EOFError) as e:
                print(f"  ⚠️  Could not read score: {e}. Leaving run open.")
                print(
                    f"     Close manually: make bench-close RUN={args.run} SCORE=? INTERVENTIONS=?"
                )
                print("=" * 66)
                return 1

            if not score_sanity_check(WORKSPACE_ROOT, args.run, s, interactive=True):
                print("  Aborting — re-run with corrected score.")
                return 1

            score_data = (s, i, n)

    # -----------------------------------------------------------------------
    # PHASE 4 — ENRICH (before close so usage lands in report)
    # -----------------------------------------------------------------------
    print()
    print("  [4/5] Enriching with usage data...")

    merged_usage = assemble_usage(
        out_dir,
        opencode_log,
        run_id=args.run,
        session_id=session_id,
        harness=args.harness,
        models=usage_models(args, executor_model),
        harness_seconds=harness_seconds,
        completed_steps=completed_steps,
        stop_reason=stop_reason,
        prompt_kinds=prompt_kinds,
    )

    usage_path = write_usage_json(out_dir, merged_usage or None)
    if merged_usage:
        keys = [k for k, v in merged_usage.items() if v is not None]
        print(f"  Usage fields populated: {keys}")
    else:
        print(
            f"  Usage not available (LITELLM_MASTER_KEY / LANGFUSE_SECRET_KEY not set "
            f"or no data yet). Fields left null in {usage_path.relative_to(REPO_ROOT)}."
        )

    # -----------------------------------------------------------------------
    # PHASE 5 — CLOSE
    # -----------------------------------------------------------------------
    if score_data and runs_path is not None:
        print()
        print("  [5/5] Closing run...")
        s, i, n = score_data
        close_run(args, run, runs_path, out_dir, s, i, n, usage=merged_usage or None)
    else:
        print()
        print("  [5/5] No score available — run left open.")

    log_mlflow(args, run, score_data, merged_usage or None, out_dir)

    # -----------------------------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------------------------
    print()
    print("=" * 66)
    print(f"  bench-run complete: {args.run}")
    print("=" * 66)
    if score_data:
        s, i, n = score_data
        print(f"  Score:         {s}/10")
        print(f"  Interventions: {i}")
        if n:
            print(f"  Notes:         {n}")
    else:
        print("  Status:        prepared (score pending)")
    if completed_steps:
        print(f"  Steps run:     {completed_steps}")
    print(f"  Session ID:    {session_id}")
    print(f"  Artifacts:     data/runs/{args.run}/")

    print()
    print("  Next:")
    print("    make bench-results")
    next_run = find_next_run(runs_path, args.run) if runs_path is not None else None
    if next_run:
        nr_id = next_run["run_id"]
        nr_models = next_run.get("gateway_models", [])
        nr_em = nr_models[-1] if nr_models else executor_model
        nr_prompt = VARIANT_TO_SHORT.get(
            next_run.get("prompt_variant", ""), prompt_short
        )
        nr_pm = nr_models[0] if len(nr_models) > 1 else ""
        pm_part = f" PLANNER_MODEL={nr_pm}" if nr_pm and nr_pm != nr_em else ""
        steps_part = f" MAX_STEPS={max_steps}" if max_steps > 1 else ""
        print(
            f"    make bench-run CASE={args.case} RUN={nr_id}"
            f" PROMPT={nr_prompt} EXECUTOR_MODEL={nr_em}"
            f"{pm_part}{steps_part} SCORE_MODE=interactive"
        )

    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
