"""Materialize each tool's native skills view (symlink, copy, or none).

Reconciliation is declarative: the engine computes the *desired* set of skill
directories for a tool and removes anything else it owns under the native dir
(stale cleanup), then (re)creates the desired entries. This replaces the brittle
``find -type l -delete`` + manifest-diff bash while preserving identical output
for a clean skill set.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ml_python_base.skills_sync.errors import UnsafeTargetError
from ml_python_base.skills_sync.hashing import folder_hash
from ml_python_base.skills_sync.models import (
    KIND_INTERNAL,
    LINK_COPY,
    LINK_SYMLINK,
    Skill,
    ToolSpec,
)

# Files the engine must never delete during reconciliation.
_PROTECTED_NAMES = frozenset({".generated-manifest.tsv"})


def link_tool(root: Path, tool: ToolSpec, skills: list[Skill]) -> dict[str, str]:
    """Project ``skills`` into ``tool``'s native view.

    Returns a ``{skill_name: folder_hash}`` mapping for tools that need a
    manifest; an empty mapping otherwise.
    """
    if not tool.has_native_view:
        return {}

    dest_root = root / tool.native_skills_dir
    dest_root.mkdir(parents=True, exist_ok=True)
    desired = {skill.name for skill in skills}
    _remove_stale(dest_root, desired)

    hashes: dict[str, str] = {}
    for skill in skills:
        if tool.link_strategy == LINK_SYMLINK:
            _link_symlink(root, dest_root, tool, skill)
        elif tool.link_strategy == LINK_COPY:
            hashes[skill.name] = _link_copy(dest_root, skill)
    return hashes


def _remove_stale(dest_root: Path, desired: set[str]) -> None:
    for child in dest_root.iterdir():
        if child.name in _PROTECTED_NAMES:
            continue
        if child.name in desired:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _relative_prefix(native_skills_dir: str) -> str:
    """Compute the ``../`` prefix from ``<native>/<skill>/`` back to repo root.

    e.g. ``.claude/skills`` (2 parts) + the per-skill dir => ``../../../``.
    """
    depth = len(Path(native_skills_dir).parts) + 1
    return "../" * depth


def _link_symlink(root: Path, dest_root: Path, tool: ToolSpec, skill: Skill) -> None:
    prefix = _relative_prefix(tool.native_skills_dir)
    skill_dest = dest_root / skill.name
    _ensure_owned_dir(skill_dest)
    skill_dest.mkdir(parents=True, exist_ok=True)

    if skill.kind == KIND_INTERNAL:
        # .github/skills/<name>.md -> <native>/<name>/SKILL.md
        target = f"{prefix}{_repo_relative(root, skill.source_path)}"
        _symlink(skill_dest / "SKILL.md", target)
        return

    # External: link every top-level item of the source directory.
    for item in sorted(skill.source_path.iterdir(), key=lambda p: p.name):
        target = f"{prefix}{_repo_relative(root, item)}"
        _symlink(skill_dest / item.name, target)


def _link_copy(dest_root: Path, skill: Skill) -> str:
    skill_dest = dest_root / skill.name
    _ensure_owned_dir(skill_dest)
    if skill_dest.exists():
        shutil.rmtree(skill_dest)
    skill_dest.mkdir(parents=True)

    if skill.kind == KIND_INTERNAL:
        shutil.copyfile(skill.source_path, skill_dest / "SKILL.md")
    else:
        _copy_tree(skill.source_path, skill_dest)
    return folder_hash(skill_dest)


def _copy_tree(source_dir: Path, dest_dir: Path) -> None:
    """Replicate ``cp -R <src>/. <dest>/`` (contents, not the dir itself)."""
    for entry in sorted(source_dir.iterdir(), key=lambda p: p.name):
        target = dest_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copyfile(entry, target)


def _ensure_owned_dir(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise UnsafeTargetError(f"Refusing to overwrite non-directory {path}")


def _symlink(link_path: Path, target: str) -> None:
    if link_path.is_symlink():
        link_path.unlink()
    elif link_path.exists():
        raise UnsafeTargetError(f"Refusing to overwrite non-symlink {link_path}")
    os.symlink(target, link_path)


def _repo_relative(root: Path, path: Path) -> str:
    """Return ``path`` relative to the repo root as a POSIX string.

    Symlink targets must be repo-relative (e.g. ``.github/skills/foo.md``), not
    absolute, so the links stay valid after clone/move.
    """
    return path.relative_to(root).as_posix()
