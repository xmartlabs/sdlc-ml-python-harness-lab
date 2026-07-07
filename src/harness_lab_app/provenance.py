"""Trial provenance and harness fingerprinting.

Every benchmark run must record a complete, hashable description of the
*condition* under which it ran, so trials are comparable and reproducible and
so ablation studies can treat the harness as the independent variable.

The key output is ``condition_hash``: a deterministic id of the experimental
arm, computed over the agent-facing harness (skills + agents + governance docs
+ hooks), the model settings, the prompt template, and the case. Two trials
with the same ``condition_hash`` are repetitions of the same arm; a diff of two
fingerprints is the exact description of an ablation (which skill/doc changed).

Provenance-only fields (``lab_git_commit``, ``harness_git_dirty``,
``runner_image``) are recorded but deliberately excluded from
``condition_hash`` — they describe *how* the trial was produced, not the arm's
identity.

All functions are best-effort and pure where possible: missing files, absent
git, or an uninstalled opencode degrade to ``None`` rather than raising, so
enriching a run's metadata can never break the run itself.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

# Governance documents the agent reads as part of the engineering harness.
GOVERNANCE_DOCS = (
    ".github/architecture.md",
    ".github/standards.md",
    ".github/domain-boundaries.md",
    "OPENCODE.md",
    "LOCAL_AGENT.md",
)

# Hook configuration that shapes agent behavior (project-level only; user-local
# settings are intentionally excluded so the fingerprint is machine-independent).
HOOK_FILES = (".claude/settings.json",)

# Paths whose modification vs the committed tree makes a run's harness "dirty".
HARNESS_TRACKED_PATHS = (
    ".github/skills",
    ".github/skills-external",
    ".github/agents",
    ".claude/settings.json",
    "OPENCODE.md",
    "LOCAL_AGENT.md",
    ".github/architecture.md",
    ".github/standards.md",
    ".github/domain-boundaries.md",
)


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def hash_file(path: Path) -> str | None:
    """Content hash of a file, or None if it cannot be read."""
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _hash_named_files(root: Path, rels: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in rels:
        digest = hash_file(root / rel)
        if digest is not None:
            out[Path(rel).name] = digest
    return out


def read_skill_hashes(skills_lock_path: Path) -> dict[str, str]:
    """Skill name -> canonical content hash from skills-lock.json."""
    try:
        data = json.loads(skills_lock_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    skills = data.get("skills", {})
    if not isinstance(skills, dict):
        return {}
    return {
        name: str(info.get("computedHash", "")) for name, info in sorted(skills.items())
    }


def _agent_hashes(root: Path) -> dict[str, str]:
    agents_dir = root / ".github" / "agents"
    out: dict[str, str] = {}
    if not agents_dir.is_dir():
        return out
    for path in sorted(agents_dir.glob("*.md")):
        if path.stem == "README":
            continue
        digest = hash_file(path)
        if digest is not None:
            out[path.stem] = digest
    return out


def _tool_version(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or result.stderr.strip() or None


def _conductor_plugin_version(root: Path) -> str | None:
    candidates = [
        Path.home() / ".config" / "opencode",
        root / ".config" / "opencode",
    ]
    for base in candidates:
        pkg = base / "node_modules" / "opencode-conductor-plugin" / "package.json"
        try:
            version = json.loads(pkg.read_text()).get("version")
        except (OSError, json.JSONDecodeError):
            continue
        if version:
            return str(version)
    return None


def build_harness_fingerprint(repo_root: Path) -> dict[str, Any]:
    """Hashable identity of the engineering harness at run time."""
    core: dict[str, Any] = {
        "skills": read_skill_hashes(repo_root / "skills-lock.json"),
        "agents": _agent_hashes(repo_root),
        "governance_docs": _hash_named_files(repo_root, GOVERNANCE_DOCS),
        "hooks": _hash_named_files(repo_root, HOOK_FILES),
        "opencode_version": _tool_version(["opencode", "--version"]),
        "conductor_plugin_version": _conductor_plugin_version(repo_root),
    }
    harness_hash = hash_text(json.dumps(core, sort_keys=True, separators=(",", ":")))
    return {"harness_hash": harness_hash, **core}


def build_model_condition(
    run: dict[str, Any], executor_model: str | None, planner_model: str | None
) -> dict[str, Any]:
    gateway_alias = None
    if executor_model and "/" in executor_model:
        gateway_alias = executor_model.split("/", 1)[1]
    return {
        "executor_model": executor_model,
        "planner_model": planner_model,
        "temperature": run.get("temperature"),
        "context_tokens": run.get("executor_context"),
        "gateway_alias": gateway_alias,
    }


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _harness_git_dirty(root: Path) -> bool | None:
    out = _git(root, "status", "--porcelain", "--", *HARNESS_TRACKED_PATHS)
    if out is None:
        return None
    return bool(out.strip())


def compute_condition_hash(
    harness_hash: str,
    model_condition: dict[str, Any],
    prompt_hash: str | None,
    case_hash: str | None,
) -> str:
    """Deterministic id of the experimental arm (excludes provenance-only fields)."""
    identity = {
        "harness_hash": harness_hash,
        "model_condition": {
            key: model_condition.get(key)
            for key in (
                "executor_model",
                "planner_model",
                "temperature",
                "context_tokens",
            )
        },
        "prompt_hash": prompt_hash,
        "case_hash": case_hash,
    }
    return hash_text(json.dumps(identity, sort_keys=True, separators=(",", ":")))


def build_condition(
    *,
    repo_root: Path,
    run: dict[str, Any],
    executor_model: str | None,
    planner_model: str | None,
    prompt_source: Path | None,
    task_path: Path | None,
    runner_image: str | None = None,
) -> dict[str, Any]:
    """Assemble the full, hashable trial condition manifest."""
    fingerprint = build_harness_fingerprint(repo_root)
    model_condition = build_model_condition(run, executor_model, planner_model)
    prompt_hash = hash_file(prompt_source) if prompt_source is not None else None
    case_hash = hash_file(task_path) if task_path is not None else None
    return {
        "condition_hash": compute_condition_hash(
            fingerprint["harness_hash"], model_condition, prompt_hash, case_hash
        ),
        "harness_fingerprint": fingerprint,
        "model_condition": model_condition,
        "prompt_hash": prompt_hash,
        "case_hash": case_hash,
        "lab_git_commit": _git(repo_root, "rev-parse", "HEAD"),
        "harness_git_dirty": _harness_git_dirty(repo_root),
        "runner_image": runner_image,
    }


def enrich_metadata(
    meta: dict[str, Any],
    *,
    repo_root: Path,
    run: dict[str, Any],
    executor_model: str | None,
    planner_model: str | None,
    prompt_source: Path | None,
    task_path: Path | None,
    runner_image: str | None = None,
) -> dict[str, Any]:
    """Add the condition manifest to a metadata dict; never raises."""
    with contextlib.suppress(Exception):
        meta.update(
            build_condition(
                repo_root=repo_root,
                run=run,
                executor_model=executor_model,
                planner_model=planner_model,
                prompt_source=prompt_source,
                task_path=task_path,
                runner_image=runner_image,
            )
        )
    return meta
