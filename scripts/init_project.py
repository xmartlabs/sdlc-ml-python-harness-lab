"""Bootstrap a fresh clone of the template as a new project.

Renames the ``ml_python_base`` package to a new name, rewrites every
reference on an explicit allowlist, and prepares the working tree so the
``make init`` chain (install, sync-skills, template-remote-setup, ci) can
finish the job.

Stdlib-only on purpose: this script must run on a fresh clone before any
virtual environment or dev dependency exists (``python3 scripts/init_project.py``).

Lines pointing at the upstream template repository
(``git@github.com:...sdlc-ml-python-template.git`` or the former
``...ml-python-base.git``) are never rewritten — the
template-sync workflow (docs/template-sync.md) depends on them.
"""

from __future__ import annotations

import argparse
import keyword
import re
import shutil
import subprocess
import sys
from pathlib import Path

OLD_PACKAGE = "ml_python_base"
OLD_DIST = "ml-python-base"

# Lines that must keep pointing at the upstream template repo — under its
# current name (sdlc-ml-python-template) and its former one (ml-python-base).
UPSTREAM_URL_PATTERN = re.compile(
    r"git@github\.com:\S*(?:ml-python-base|sdlc-ml-python-template)\.git"
)

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
RESERVED_NAMES = frozenset({"src", "tests", "scripts", OLD_PACKAGE})

# Files rewritten on rename, relative to the repo root. Globs cover the
# package and test sources; missing entries are skipped (mini repos in tests
# do not reproduce the whole tree).
REWRITE_ALLOWLIST: tuple[str, ...] = (
    "pyproject.toml",
    "Makefile",
    "adapters/registry.toml",
    "docs/harness-architecture.md",
    "docs/skills-management.md",
    "docs/template-sync.md",
    ".github/agents/README.md",
    ".github/portability.md",
    "notebooks/base.ipynb",
    ".devcontainer/devcontainer.json",
)
REWRITE_GLOBS: tuple[str, ...] = (
    "src/**/*.py",
    "tests/**/*.py",
)

# The skill selection a new project starts from (ADR-0085). A fresh project
# carries the smallest useful harness; it grows by editing harness.toml, never
# by deleting skills — the governed sources stay complete so template syncs
# keep applying cleanly.
DEFAULT_PROFILE = "minimal"
PROFILES_FILE = ".github/skill-profiles.toml"
SELECTION_FILE = "harness.toml"

# Never rewritten: these test the init mechanism itself and reference the
# template's original package name on purpose.
REWRITE_EXCLUDE_PREFIXES: tuple[str, ...] = ("tests/init_project/",)


class InitError(Exception):
    """Raised when initialization cannot proceed."""


def validate_name(name: str) -> str:
    """Validate the new package name and return it."""
    if not NAME_PATTERN.match(name):
        raise InitError(
            f"invalid name {name!r}: use lowercase letters, digits, and "
            "underscores, starting with a letter (e.g. my_project)"
        )
    if keyword.iskeyword(name):
        raise InitError(f"invalid name {name!r}: it is a Python keyword")
    if name in RESERVED_NAMES:
        raise InitError(f"invalid name {name!r}: reserved")
    return name


def ensure_clean_git_tree(root: Path, force: bool) -> None:
    """Refuse to run on a dirty git tree unless forced."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise InitError(f"not a git repository (or git failed): {root}")
    if result.stdout.strip() and not force:
        raise InitError(
            "git working tree is dirty — commit or stash first, or re-run with --force"
        )


def purge_pycache(root: Path, dry_run: bool) -> int:
    """Remove __pycache__ directories under src/ and tests/."""
    removed = 0
    for base in ("src", "tests"):
        base_dir = root / base
        if not base_dir.is_dir():
            continue
        for cache_dir in sorted(base_dir.rglob("__pycache__")):
            removed += 1
            print(f"  purge {cache_dir.relative_to(root)}")
            if not dry_run:
                shutil.rmtree(cache_dir)
    return removed


def rewrite_text(text: str, name: str, dist_name: str) -> tuple[str, int, int]:
    """Replace package tokens line by line, preserving upstream URLs.

    Returns the new text, the number of replacements, and the number of
    lines skipped because they reference the upstream template repository.
    """
    new_lines: list[str] = []
    replaced = 0
    skipped = 0
    for line in text.splitlines(keepends=True):
        if UPSTREAM_URL_PATTERN.search(line):
            skipped += 1
            new_lines.append(line)
            continue
        replaced += line.count(OLD_PACKAGE) + line.count(OLD_DIST)
        new_lines.append(line.replace(OLD_PACKAGE, name).replace(OLD_DIST, dist_name))
    return "".join(new_lines), replaced, skipped


def iter_rewrite_targets(root: Path) -> list[Path]:
    """Resolve allowlist entries and globs to existing files."""
    targets: list[Path] = []
    for rel in REWRITE_ALLOWLIST:
        path = root / rel
        if path.is_file():
            targets.append(path)
    for pattern in REWRITE_GLOBS:
        targets.extend(
            p
            for p in sorted(root.glob(pattern))
            if p.is_file()
            and not str(p.relative_to(root)).startswith(REWRITE_EXCLUDE_PREFIXES)
        )
    return targets


def rewrite_references(root: Path, name: str, dist_name: str, dry_run: bool) -> None:
    """Rewrite every allowlisted file that mentions the old name."""
    for path in iter_rewrite_targets(root):
        original = path.read_text(encoding="utf-8")
        updated, replaced, skipped = rewrite_text(original, name, dist_name)
        if replaced == 0 and skipped == 0:
            continue
        note = f" ({skipped} upstream URL line(s) preserved)" if skipped else ""
        print(f"  rewrite {path.relative_to(root)}: {replaced} replacement(s){note}")
        if not dry_run and updated != original:
            path.write_text(updated, encoding="utf-8")


def rename_package_dir(root: Path, name: str, dry_run: bool) -> None:
    """Rename src/ml_python_base to src/<name>."""
    old_dir = root / "src" / OLD_PACKAGE
    new_dir = root / "src" / name
    if not old_dir.is_dir():
        raise InitError(
            f"src/{OLD_PACKAGE} not found — this clone looks already initialized"
        )
    if new_dir.exists():
        raise InitError(f"src/{name} already exists")
    print(f"  rename src/{OLD_PACKAGE} -> src/{name}")
    if not dry_run:
        old_dir.rename(new_dir)


def create_env_file(root: Path, dry_run: bool) -> None:
    """Copy .env.example to .env when no .env exists yet."""
    env_file = root / ".env"
    example = root / ".env.example"
    if env_file.exists():
        print("  keep existing .env")
        return
    if not example.is_file():
        print("  skip .env (no .env.example found)")
        return
    print("  create .env from .env.example")
    if not dry_run:
        shutil.copyfile(example, env_file)


def check_skill_profile(root: Path, profile: str) -> bool:
    """Whether a selection can be written; raise on an unknown profile.

    Called before anything is touched, so a typo never leaves a half-renamed
    clone. False when the template predates profiles (no definitions file):
    there is nothing to select from, and the project keeps the full catalog.
    Stdlib-only like the rest of this script (tomllib).
    """
    import tomllib

    definitions = root / PROFILES_FILE
    if not definitions.is_file():
        return False
    if profile == "full":
        return True
    declared = tomllib.loads(definitions.read_text(encoding="utf-8")).get("profile", {})
    if profile not in declared:
        known = ", ".join(sorted({"full", *declared}))
        raise InitError(f"unknown skill profile {profile!r} (defined: {known})")
    return True


def write_skill_selection(root: Path, profile: str, dry_run: bool) -> None:
    """Write harness.toml selecting ``profile``; never overwrite an existing one.

    The views are rebuilt afterwards by the ``make sync-skills`` that
    ``make init`` chains.
    """
    selection = root / SELECTION_FILE
    if selection.exists():
        print(f"  keep existing {SELECTION_FILE}")
        return
    print(f"  write {SELECTION_FILE} (skill profile: {profile})")
    if not dry_run:
        selection.write_text(
            "# Which governed skills this project carries (ADR-0085).\n"
            "# Profiles are defined in .github/skill-profiles.toml. Skills outside the\n"
            "# selection stay in .github/skills*/ but are not linked or listed for the\n"
            "# agent. Change this file, then run `make sync-skills`.\n"
            "[skills]\n"
            f'profile = "{profile}"\n'
            "include = []   # add skills on top of the profile\n"
            "exclude = []   # drop skills the profile carries\n",
            encoding="utf-8",
        )


def run(
    name: str,
    root: Path,
    dry_run: bool,
    force: bool,
    profile: str = DEFAULT_PROFILE,
) -> None:
    """Execute the full initialization flow."""
    validate_name(name)
    dist_name = name.replace("_", "-")
    ensure_clean_git_tree(root, force)
    # Validate the rename precondition before touching anything.
    if not (root / "src" / OLD_PACKAGE).is_dir():
        raise InitError(
            f"src/{OLD_PACKAGE} not found — this clone looks already initialized"
        )
    select_profile = check_skill_profile(root, profile)

    mode = "DRY RUN — no files will be written" if dry_run else "applying changes"
    print(f"Initializing project {name!r} (dist: {dist_name!r}) — {mode}")
    purge_pycache(root, dry_run)
    rename_package_dir(root, name, dry_run)
    rewrite_references(root, name, dist_name, dry_run)
    create_env_file(root, dry_run)
    if select_profile:
        write_skill_selection(root, profile, dry_run)
    else:
        print(f"  skip {SELECTION_FILE} (no {PROFILES_FILE}: full skill catalog)")
    print("Done. Next steps (chained automatically by `make init`):")
    print("  make install && make sync-skills && make template-remote-setup && make ci")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name", required=True, help="new package name (e.g. my_project)"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to this script's repo)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print every action without writing anything",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help=f"skill profile the project starts with (default: {DEFAULT_PROFILE}; "
        "'full' carries every skill)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="proceed even if the git working tree is dirty",
    )
    args = parser.parse_args(argv)
    try:
        run(args.name, args.root.resolve(), args.dry_run, args.force, args.profile)
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
