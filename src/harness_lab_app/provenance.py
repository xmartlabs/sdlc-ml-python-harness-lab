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

# The surfaces an agent actually reads from its working directory. Used when the
# harness lives in a *workspace* rather than in the lab (ADR-0031): a cloned
# repository has no `skills-lock.json`, so the fingerprint has to be taken from
# what is materialized on disk, not from the lock file that describes the lab.
NATIVE_SKILL_DIRS = (
    ".claude/skills",
    ".opencode/skills",
    ".codex/skills",
    ".agents/skills",
)
ADAPTER_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "OPENCODE.md",
    ".github/copilot-instructions.md",
)

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


def _hash_tree(root: Path) -> str | None:
    """Content hash of a directory tree, or None when it does not exist.

    Deliberately local rather than reusing ``skills_sync.hashing``: provenance
    must stay importable with nothing but the standard library, so a run can
    always record what it ran under.
    """
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return "sha256:" + digest.hexdigest()


def build_workspace_harness_fingerprint(root: Path) -> dict[str, Any]:
    """Hashable identity of the harness *as materialized in a workspace*.

    The distinction from :func:`build_harness_fingerprint` is the whole point of
    the `native` / `injected` comparison. The lab-root version reads
    ``skills-lock.json``, which a cloned repository never has — so using it for
    both arms would hash them identically, the two conditions would collapse
    into one ``condition_hash``, and the comparison would silently stop being a
    comparison. This one hashes the directories and files the agent can actually
    read from its cwd, so "nothing there" and "the whole harness there" are
    different identities, and a target that ships governance of its own is
    different again.
    """
    core: dict[str, Any] = {
        "native_skills": {
            rel: digest
            for rel in NATIVE_SKILL_DIRS
            if (digest := _hash_tree(root / rel)) is not None
        },
        "native_agents": {
            rel: digest
            for rel in (".claude/agents", ".opencode/agents", ".codex/agents")
            if (digest := _hash_tree(root / rel)) is not None
        },
        "adapter_files": {
            rel: digest
            for rel in ADAPTER_FILES
            if (digest := hash_file(root / rel)) is not None
        },
        "governance_docs": _hash_named_files(root, GOVERNANCE_DOCS),
        "hooks": _hash_named_files(root, HOOK_FILES),
        "opencode_version": _tool_version(["opencode", "--version"]),
    }
    harness_hash = hash_text(json.dumps(core, sort_keys=True, separators=(",", ":")))
    return {"harness_hash": harness_hash, "harness_root": "workspace", **core}


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


def _harness_git_dirty(root: Path, exclude: tuple[str, ...] = ()) -> bool | None:
    tracked = tuple(p for p in HARNESS_TRACKED_PATHS if p not in exclude)
    out = _git(root, "status", "--porcelain", "--", *tracked)
    if out is None:
        return None
    return bool(out.strip())


def compute_condition_hash(
    harness_hash: str,
    model_condition: dict[str, Any],
    prompt_hash: str | None,
    case_hash: str | None,
    skills_disabled: tuple[str, ...] = (),
    skills_extra_hashes: dict[str, str] | None = None,
    gateway_overlay_hash: str | None = None,
    target: dict[str, Any] | None = None,
    harness_source: str = "",
    skills_invoke: tuple[str, ...] = (),
) -> str:
    """Deterministic id of the experimental arm (excludes provenance-only fields).

    Ablation rule: ``skills_disabled`` / ``skills_extra_hashes`` /
    ``gateway_overlay_hash`` enter the identity ONLY when non-empty. This keeps
    every historical/default-arm hash unchanged while any skill ablation (or a
    staged-skill content edit, or a web model-routing overlay) flips the arm.
    Do not "simplify" this by always including the keys — it would silently
    re-bin every existing arm.
    """
    identity: dict[str, Any] = {
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
    if skills_disabled:
        identity["skills_disabled"] = sorted(set(skills_disabled))
    if skills_extra_hashes:
        identity["skills_extra"] = dict(sorted(skills_extra_hashes.items()))
    if skills_invoke:
        # Being told to use a skill is a different arm from having it on the
        # menu (ADR-0041). Same non-empty-only rule as the fields above.
        identity["skills_invoke"] = sorted(set(skills_invoke))
    if gateway_overlay_hash:
        identity["gateway_overlay"] = gateway_overlay_hash
    if harness_source and harness_source != "lab_root":
        # Same non-default-only rule as the fields above: `lab_root` is what
        # every historical run did, so including it would re-bin all of them.
        identity["harness_source"] = harness_source
    if target and target.get("target_id"):
        # The repository AND the commit, never the ref: two runs of "main" a
        # week apart are different conditions, and recording the ref would let
        # them share an arm (ADR-0029). Same non-empty-only rule as the
        # ablation fields above — an absent target must not re-bin history.
        identity["target"] = {
            "target_id": target.get("target_id"),
            "commit": target.get("commit"),
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
    skills_disabled: tuple[str, ...] = (),
    skills_extra_hashes: dict[str, str] | None = None,
    skills_extra_hash_algo: str = "",
    gateway_overlay_hash: str | None = None,
    target: dict[str, Any] | None = None,
    harness_source: str = "",
    harness_root: Path | None = None,
    harness_projection: dict[str, Any] | None = None,
    skills_invoke: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Assemble the full, hashable trial condition manifest.

    ``harness_root`` moves the fingerprint to where the agent actually reads its
    harness from. Without it a `native` and an `injected` run would both be
    fingerprinted against the lab and share an arm — see
    :func:`build_workspace_harness_fingerprint`.
    """
    fingerprint = (
        build_workspace_harness_fingerprint(harness_root)
        if harness_root is not None
        else build_harness_fingerprint(repo_root)
    )
    model_condition = build_model_condition(run, executor_model, planner_model)
    prompt_hash = hash_file(prompt_source) if prompt_source is not None else None
    case_hash = hash_file(task_path) if task_path is not None else None
    ablation_active = bool(skills_disabled or skills_extra_hashes)
    # During an ablation the runner regenerates OPENCODE.md's managed skills
    # block (a controlled, recorded mutation), so it must not count as "dirty".
    dirty_exclude = ("OPENCODE.md",) if ablation_active else ()
    return {
        "condition_hash": compute_condition_hash(
            fingerprint["harness_hash"],
            model_condition,
            prompt_hash,
            case_hash,
            skills_disabled=skills_disabled,
            skills_extra_hashes=skills_extra_hashes,
            gateway_overlay_hash=gateway_overlay_hash,
            target=target,
            harness_source=harness_source,
            skills_invoke=skills_invoke,
        ),
        "gateway_overlay_hash": gateway_overlay_hash,
        "harness_source": harness_source or "lab_root",
        # What the projection actually placed, and what it displaced. Recorded,
        # not hashed: the fingerprint above already covers the resulting
        # surfaces, while this says how they got there.
        "harness_projection": harness_projection,
        # Recorded in full (url, ref, commit) even though only id+commit are
        # hashed: the ref is how a human reads the run, the commit is what makes
        # it reproducible, and the url is what makes it fetchable again.
        "target_repository": target or None,
        # Which digest produced skills_extra_hashes. Recorded, never hashed:
        # it labels the two generations of staged-skill arms (SKILL.md-only vs
        # whole-tree, ADR-0017) without re-binning anything a second time.
        "skills_extra_hash_algo": skills_extra_hash_algo if skills_extra_hashes else "",
        "harness_fingerprint": fingerprint,
        "model_condition": model_condition,
        "prompt_hash": prompt_hash,
        "case_hash": case_hash,
        "skills_ablation": ablation_active,
        "lab_git_commit": _git(repo_root, "rev-parse", "HEAD"),
        # Only meaningful when the lab tree *is* the harness under test. Every
        # other source runs the agent from the workspace with the lab's
        # governance masked in the container (ADR-0049), so git reports the
        # masked paths as deleted and every such run would record itself dirty.
        # None says "not applicable", which is the honest answer.
        "harness_git_dirty": (
            _harness_git_dirty(repo_root, exclude=dirty_exclude)
            if harness_source in ("", "lab_root")
            else None
        ),
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
    skills_disabled: tuple[str, ...] = (),
    skills_extra_hashes: dict[str, str] | None = None,
    skills_extra_hash_algo: str = "",
    gateway_overlay_hash: str | None = None,
    target: dict[str, Any] | None = None,
    harness_source: str = "",
    harness_root: Path | None = None,
    harness_projection: dict[str, Any] | None = None,
    skills_invoke: tuple[str, ...] = (),
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
                skills_disabled=skills_disabled,
                skills_extra_hashes=skills_extra_hashes,
                skills_extra_hash_algo=skills_extra_hash_algo,
                gateway_overlay_hash=gateway_overlay_hash,
                target=target,
                harness_source=harness_source,
                harness_root=harness_root,
                harness_projection=harness_projection,
                skills_invoke=skills_invoke,
            )
        )
    return meta
