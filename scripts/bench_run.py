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
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

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
        default="opencode",
        choices=["opencode", "codex", "claude"],
        help="Agent CLI that drives the run. All harnesses (opencode, codex, "
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
        effective.append(
            Skill(name=name, kind=KIND_EXTERNAL, source_path=source, description="")
        )
    return effective


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


def restore_full_skill_views() -> None:
    """Re-sync every native view from the governed source (idempotent)."""
    from ml_python_base.skills_sync.discovery import (
        discover_skills as discover_governed,
    )

    apply_skill_views(discover_governed(REPO_ROOT))


def staged_skill_hashes(extra: list[str]) -> dict[str, str]:
    """Content hash per staged skill — a staged-skill edit must flip the arm."""
    from harness_lab_app.provenance import hash_file

    out: dict[str, str] = {}
    for name in sorted(set(extra)):
        digest = hash_file(SKILLS_STAGING_DIR / name / "SKILL.md")
        if digest is not None:
            out[name] = digest
    return out


def discover_subagents() -> list[str]:
    d = REPO_ROOT / ".github" / "agents"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md") if p.stem != "README")


def generate_prompt(
    run_id: str,
    prompt_short: str,
    prompt_source: Path,
    planner_model: str | None,
    executor_model: str | None,
    session_id: str,
    out_dir: Path,
    repo_note: str | None = None,
) -> Path:
    """Write the final prompt: metadata header + location constraint + body.

    `repo_note` overrides the default build-from-scratch constraint (workspace
    main.py) for repo-task/SWE runs, where the correct wording depends on the
    agent's working directory.
    """
    constraint = repo_note or (
        f"> All files MUST be created in `workspace/{run_id}/`\n"
        f"> Entry point: `workspace/{run_id}/main.py`\n"
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
        f"---\n\n"
    )
    prompt_path = out_dir / "prompt.md"
    prompt_path.write_text(header + prompt_source.read_text())
    return prompt_path


def generate_continuation_prompt(
    run_id: str, step_n: int, steps_dir: Path, workspace_dir: Path | None = None
) -> Path:
    """Write a continuation prompt for step N (step > 1).

    If PLAN.md exists in the workspace (SDLC mode), injects the plan so the model
    resumes from documented state rather than rediscovering code from scratch.
    Otherwise falls back to a generic increment prompt.
    """
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
            f"## Current plan (from `workspace/{run_id}/PLAN.md`)\n\n"
            f"{plan_md}\n\n"
            f"---\n\n"
            f"## Your task for this session\n\n"
            f"1. Read the plan above — find the first unchecked milestone (`[ ]`).\n"
            f"2. Implement **exactly that ONE milestone**. Do not implement more than\n"
            f"   one milestone this session, and do not touch completed milestones.\n"
            f"3. Validate headlessly (a hanging window or a `timeout` is NOT success):\n"
            f'   `BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python workspace/{run_id}/main.py; echo "exit=$?"`\n'
            f"   A correct build prints `exit=0`.\n"
            f"4. Update `workspace/{run_id}/PLAN.md`:\n"
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
            f"1. Inspect `workspace/{run_id}/main.py` and understand the current state.\n"
            f"2. Implement only the next missing Tetris milestone (exactly one).\n"
            f"3. Validate headlessly (a `timeout` is NOT success):\n"
            f'   `BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy python workspace/{run_id}/main.py; echo "exit=$?"`\n'
            f"4. Stop after ONE verifiable increment.\n"
            f"5. State clearly what you implemented and what the next recommended increment is.\n"
            f"6. If and ONLY if the game is fully complete, end with: ALL MILESTONES COMPLETE\n"
        )
    path = steps_dir / f"step-{step_n}-prompt.md"
    path.write_text(text)
    return path


def generate_swe_continuation_prompt(
    run_id: str,
    step_n: int,
    steps_dir: Path,
    spec: dict,
    last_eval: dict | None,
    in_repo_cwd: bool = False,
) -> Path:
    """Continuation prompt for a SWE-bench step (no Tetris/game framing).

    Keeps the agent on the issue: shows the live F2P/P2P status from the
    previous objective evaluation and forbids scope creep. `in_repo_cwd` means
    the agent's working directory IS the subject repo (clean-context claude
    runs), so path references drop the workspace/<run_id>/ prefix.
    """
    f2p = spec.get("fail_to_pass") or []
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
    path.write_text(text)
    return path


def workspace_fingerprint(workspace: Path) -> str | None:
    """Cheap change detector: hash of the workspace's git status + diff vs HEAD.

    Used as the SWE stop fallback when no objective per-step evaluation is
    possible — two consecutive identical fingerprints mean the agent's step
    made no edits. Returns None (never raises) when git state is unreadable.
    """
    import hashlib

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
        "skills_bundle": discover_skills(),
        "skills_disabled": sorted(set(getattr(args, "disable_skill", []) or [])),
        "skills_experimental": sorted(set(getattr(args, "extra_skill", []) or [])),
        "subagents": discover_subagents(),
        "base_commit": task.get("base_commit", ""),
        "workspace": f"workspace/{args.run}",
        # Friendly adopted-governance version at run time (leaderboard axis).
        "template_ref": read_template_ref(REPO_ROOT),
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
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
        skills_extra_hashes=staged_skill_hashes(
            list(getattr(args, "extra_skill", []) or [])
        ),
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

    diff_path = out_dir / "diff.patch"
    if (dest / ".git").exists():
        # Repo task: the meaningful diff is the agent's changes vs the
        # provisioned commit (base_commit + test files), not diff-vs-empty.
        # Diff against that commit, not HEAD: agents sometimes `git commit`
        # their fix inside the workspace, which would make diff-vs-HEAD empty.
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
        result = _run_git(
            ["git", "diff", "--no-index", "/dev/null", str(dest)], cwd=REPO_ROOT
        )
    diff_path.write_text(result.stdout)
    diff_lines = result.stdout.count("\n")
    print(f"  diff.patch: {diff_lines} lines")

    shutil.rmtree(workspace)
    if WORKSPACE_ROOT.exists() and not any(WORKSPACE_ROOT.iterdir()):
        WORKSPACE_ROOT.rmdir()
    print(f"  Removed workspace/{run_id}/")
    return diff_lines


def _load_case_spec(out_dir: Path) -> dict | None:
    """Return the repo-task provisioning spec written by the worker, or None."""
    path = out_dir / "case_spec.json"
    if not path.exists():
        return None
    try:
        spec = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    # Guard: only treat as a repo task when `repo` looks like a cloneable
    # "owner/repo" (a bare name like a provenance subject_repo is not).
    repo = spec.get("repo", "")
    return spec if "/" in repo else None


def provision_workspace(workspace: Path, spec: dict) -> None:
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

    The web form's executor models are gateway-namespaced (``gateway/…``) and do
    not map to native Codex/Claude model ids, so those are ignored (the CLI uses
    its configured default). A bare id (no ``gateway/`` prefix) is treated as an
    explicit native override.
    """
    if not model or model.startswith("gateway/"):
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


def build_claude_cmd(executor_model: str | None, message: str) -> list[str]:
    """Assemble a non-interactive Claude Code CLI command (host, native auth).

    -p is print/non-interactive; --dangerously-skip-permissions avoids blocking
    on tool approvals in a headless run. stream-json (requires --verbose in -p
    mode) emits one JSON event per assistant message / tool call / result —
    the observability layer that gives claude runs real tool-level attribution
    and usage numbers (the plain CLI only prints a final summary).
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
    cmd.append(message)
    return cmd


def build_harness_cmd(
    harness: str, executor_model: str | None, agent: str | None, message: str
) -> list[str]:
    """Dispatch to the per-harness command builder (opencode | codex | claude)."""
    if harness == "codex":
        return build_codex_cmd(executor_model, message)
    if harness == "claude":
        return build_claude_cmd(executor_model, message)
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
    return None  # user/tool_result events: noise for the readable log


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
                events_fh.write(line)
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

    # Detected model
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


def pull_usage_opencode_db(run_id: str) -> dict | None:
    """Query the local opencode SQLite DB for token usage summed across all sessions for this run.

    Matches sessions where the title contains run_id and directory is the repo root.
    time_minutes = sum of per-session durations (excludes user breaks between steps).
    The gateway always reports cost=0.0, so we leave cost_usd null.
    """
    import sqlite3

    db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) as session_count,
                SUM(tokens_input) as total_input,
                SUM(tokens_output) as total_output,
                SUM(tokens_cache_read) as total_cache_read,
                SUM(tokens_cache_write) as total_cache_write,
                SUM(tokens_reasoning) as total_reasoning,
                SUM(time_updated - time_created) as total_active_ms
            FROM session
            WHERE title LIKE ?
              AND directory = ?
              AND (tokens_input > 0 OR tokens_output > 0)
            """,
            (f"%{run_id}%", str(REPO_ROOT)),
        )
        row = cur.fetchone()
        con.close()
        if not row or row[0] == 0:
            return None
        (
            session_count,
            total_input,
            total_output,
            cache_read,
            cache_write,
            reasoning,
            total_active_ms,
        ) = row
        result: dict = {
            "input_tokens": int(total_input or 0) or None,
            "output_tokens": int(total_output or 0) or None,
        }
        if reasoning:
            result["tokens_reasoning"] = int(reasoning)
        if cache_read:
            result["tokens_cache_read"] = int(cache_read)
        if cache_write:
            result["tokens_cache_write"] = int(cache_write)
        if session_count and session_count > 1:
            result["turns"] = int(session_count)
        if total_active_ms and total_active_ms > 0:
            # Sum of per-session durations = actual LLM execution time (excludes user breaks between steps)
            result["time_minutes"] = round(total_active_ms / 60000.0, 1)
        return result
    except Exception:
        return None


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
        "cost_usd": None,
        "time_minutes": None,
        "files_modified": [],
        "diff_lines": None,
        "detected_model": None,
        "detected_session": None,
    }
    if usage:
        skeleton.update(usage)
    path = out_dir / "usage.json"
    path.write_text(json.dumps(skeleton, indent=2))
    return path


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

    BENCH_CONTEXT.write_text(BENCH_CONTEXT_PLACEHOLDER)
    print("  BENCH_CONTEXT.md reset")

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
    print(
        f"    git add data/runs/{args.run}/ benchmarks/tetris/runs.yaml benchmarks/tetris/results.csv BENCH_CONTEXT.md"
    )
    print(f'    git commit -m "{commit_msg}"')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _load_env()

    out_dir = DATA_DIR / args.run
    case_spec = _load_case_spec(out_dir)

    if case_spec is not None:
        # Repo-task / SWE-bench lane (ADR-0009): everything the runner needs is
        # in case_spec.json — no benchmarks/<case>/ task.yaml/prompts required.
        runs_path = None
        task = {
            "subject_repo": case_spec.get("repo", ""),
            "base_commit": case_spec.get("base_commit", ""),
            "prompts": {},
        }
        run = {
            "run_id": args.run,
            "status": "pending",
            "prompt_variant": case_spec.get("prompt_variant", "swe_default"),
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
        runs_path = RUNS_FILES.get(args.case)
        if runs_path is None:
            sys.exit(
                f"Unknown case '{args.case}'. Known cases: {list(RUNS_FILES.keys())}"
            )
        task = load_task_yaml(runs_path)
        run = resolve_or_synthesize_run(runs_path, args.run, args)
        if run.get("status") == "completed":
            sys.exit(f"Run {args.run} is already completed. Use a new run_id.")
        prompt_short, prompt_source = resolve_prompt_file(task, args.prompt, run)

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
    # Web/CLI runs pass the harness on the command line; runs.yaml rarely carries
    # it, so record the actual harness in metadata (used by the improvement cycle).
    if not run.get("harness"):
        run["harness"] = args.harness

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
    skills_ablation = bool(args.disable_skill or args.extra_skill)
    try:
        restore_full_skill_views()
    except Exception as exc:
        if skills_ablation:
            sys.exit(f"❌ Could not sync skill views before ablation: {exc!r}")
        print(f"  ⚠️  Skill view sync skipped: {exc!r}")
    if skills_ablation:
        effective_skills = build_effective_skills(args.disable_skill, args.extra_skill)
        apply_skill_views(effective_skills)
        import atexit

        atexit.register(restore_full_skill_views)
        print(
            f"  Skills:      ablation active — disabled={sorted(set(args.disable_skill))} "
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

    if case_spec is not None:
        provision_workspace(workspace, case_spec)

    # Control-arm hygiene (harness A/B): Claude Code auto-loads CLAUDE.md and
    # .claude/skills/ from its cwd, so a "bare" default-variant run launched
    # from the repo root silently inherits the whole lab harness. The default
    # arm therefore runs from inside the subject repo (truly bare context);
    # the harness variant keeps the repo-root cwd so the surface is available.
    agent_cwd: Path | None = None
    if (
        args.harness == "claude"
        and case_spec is not None
        and prompt_short == "swe_default"
    ):
        agent_cwd = workspace

    repo_note: str | None = None
    if case_spec is not None:
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
    )
    print(f"  Prompt:      {prompt_path.relative_to(REPO_ROOT)}")

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
    )
    print(f"  Metadata:    {(out_dir / 'metadata.json').relative_to(REPO_ROOT)}")

    write_run_env(out_dir, args, session_id)
    print(f"  Env:         {(out_dir / 'run.env').relative_to(REPO_ROOT)}")

    if not (out_dir / "report.md").exists():
        scaffold_report(run, out_dir)
    print(f"  Report:      {(out_dir / 'report.md').relative_to(REPO_ROOT)}")

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
                elif case_spec is not None:
                    # SWE-bench lane: keep the agent on the issue (the generic
                    # continuation prompt is game-build framed and derails it).
                    step_prompt_path = generate_swe_continuation_prompt(
                        args.run,
                        step_n,
                        steps_dir,
                        case_spec,
                        last_swe_eval,
                        in_repo_cwd=agent_cwd is not None,
                    )
                else:
                    step_prompt_path = generate_continuation_prompt(
                        args.run,
                        step_n,
                        steps_dir,
                        workspace_dir=WORKSPACE_ROOT / args.run,
                    )
                    # Also copy step prompt to clipboard so user can paste if needed
                    copy_to_clipboard(step_prompt_path.read_text())
                step_log = steps_dir / f"step-{step_n}.log"
                print(f"\n  --- Step {step_n}/{max_steps} ---")
            else:
                step_prompt_path = prompt_path
                step_log = opencode_log

            prompt_text = step_prompt_path.read_text()
            cmd = build_harness_cmd(args.harness, executor_model, None, prompt_text)
            print(f"  Harness:     {args.harness}")
            print(f"  Model:       {executor_model or '(harness default)'}")
            print(f"  Log:         {step_log.relative_to(REPO_ROOT)}")
            if agent_cwd is not None:
                print(
                    f"  Agent cwd:   {agent_cwd.relative_to(REPO_ROOT)} (clean context)"
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
            rc = run_opencode(
                cmd,
                step_log,
                also_append=also_append,
                label=args.harness,
                cwd=agent_cwd,
                events_path=events_path,
            )
            completed_steps += 1

            print("-" * 66)
            if rc != 0:
                print(
                    f"  ⚠️  {args.harness} exited with code {rc} — see {step_log.relative_to(REPO_ROOT)}"
                )
                break

            if case_spec is not None:
                # SWE-bench lane: the stop criterion is objective — evaluate the
                # F2P/P2P subset now and stop the loop the moment it resolves.
                swe_eval = swe_step_evaluation(workspace, case_spec)
                if swe_eval is not None:
                    last_swe_eval = swe_eval
                    print(
                        f"  SWEBENCH: resolved={swe_eval['resolved']} "
                        f"F2P={swe_eval['f2p_passed']}/{swe_eval['f2p_total']} "
                        f"P2P={swe_eval['p2p_passed']}/{swe_eval['p2p_total']}"
                    )
                    if swe_eval["resolved"]:
                        print(
                            f"  ✅ SWE-bench resolved after step {step_n} — stopping."
                        )
                        break
                elif max_steps > 1:
                    # No objective signal (Pro lane in the docker-less runner):
                    # fall back to a no-progress detector so the loop doesn't
                    # burn every remaining step re-summarizing finished work. A
                    # step that changed nothing in the workspace is the
                    # strongest done-signal available; validation still renders
                    # the real verdict after the run.
                    fp = workspace_fingerprint(workspace)
                    if fp is not None and fp == last_workspace_fp:
                        print(
                            f"  ⏹  Step {step_n} changed nothing in the "
                            "workspace and no objective eval is available — "
                            "stopping (agent appears done)."
                        )
                        break
                    last_workspace_fp = fp

            if max_steps > 1:
                step_text = step_log.read_text()
                if case_spec is None and detect_stop_signal(step_text):
                    print(f"  Agent signaled completion at step {step_n}.")
                    break
                if step_n < max_steps:
                    print(
                        f"  Step {step_n} complete — continuing to step {step_n + 1}..."
                    )
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
        log_usage = parse_log_usage(opencode_log)
        remote_usage = pull_usage_litellm(args.run) or pull_usage_langfuse(session_id)
        db_usage = pull_usage_opencode_db(args.run)
        usage: dict = {}
        # claude stream-json events are the ground truth for claude runs.
        usage.update(collect_claude_usage(out_dir))
        if log_usage:
            usage.update(log_usage)
        if db_usage:
            usage.update(db_usage)
        if remote_usage:
            usage.update(remote_usage)
        if completed_steps:
            usage["steps"] = completed_steps
        write_usage_json(out_dir, usage or None)
        if args.fresh:
            # Managed web run: the API owns scoring (POST /score), so Phase 5
            # close_run never runs. Archive the workspace into the run's data dir
            # and clean it up here so workspaces don't accumulate (ADR-0006).
            print()
            print("  [5/5] Archiving workspace (managed run)...")
            finalize_workspace(out_dir, workspace, args.run)
        print("=" * 66)
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
            print("    # Reference: benchmarks/tetris/scoring.md")
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

    log_usage = parse_log_usage(opencode_log)
    remote_usage = pull_usage_litellm(args.run) or pull_usage_langfuse(session_id)
    db_usage = pull_usage_opencode_db(args.run)

    # Merge priority (highest wins): remote API > opencode DB > log estimates >
    # claude stream-json events (claude runs: the only populated source).
    merged_usage: dict = {}
    merged_usage.update(collect_claude_usage(out_dir))
    if log_usage:
        merged_usage.update(log_usage)
    if db_usage:
        merged_usage.update(db_usage)
    if remote_usage:
        merged_usage.update(remote_usage)
    if completed_steps > 1:
        merged_usage["steps"] = completed_steps

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
