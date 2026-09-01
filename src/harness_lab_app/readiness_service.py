"""Is this repository ready to be evaluated, and what does it already have?

Step 2 of the guided audit (§7). Everything here is *detected* — read from the
repository itself — or explicitly reported as not detected. There is no third
state where the lab describes a capability it did not observe, because the whole
value of an audit's opening page is that a client can trust it as a mirror of
their repository rather than a brochure.

Detection reads a blobless bare mirror (the one case derivation already keeps),
so it lists paths without fetching contents and works on a repository far too
large to clone for a look.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from harness_lab_app.models import TargetRepository

# Rule files an agent harness reads at the repository root.
RULES_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "OPENCODE.md",
    "GEMINI.md",
    "LOCAL_AGENT.md",
    ".cursorrules",
    ".windsurfrules",
    ".github/copilot-instructions.md",
)

# (prefix, suffix, bucket) — a path in the bucket if it matches both ends.
_BUCKETS = (
    (".claude/skills/", "/SKILL.md", "skills"),
    (".github/skills/", "/SKILL.md", "skills"),
    (".agents/skills/", "/SKILL.md", "skills"),
    (".claude/agents/", ".md", "agents"),
    (".github/agents/", ".md", "agents"),
    (".claude/commands/", ".md", "commands"),
    (".github/workflows/", "", "ci_workflows"),
    ("docs/adr/", ".md", "adrs"),
    ("memory/", ".md", "memory"),
    (".github/", ".md", "governance_docs"),
)

# Files that mean the repository declares how it is tested.
_TEST_MARKERS = (
    "Makefile",
    "justfile",
    "noxfile.py",
    "tox.ini",
    "pytest.ini",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
)


def _name(path: str, prefix: str, suffix: str) -> str:
    trimmed = path[len(prefix) :]
    if suffix == "/SKILL.md":
        return trimmed.rsplit("/", 1)[0]
    return trimmed.removesuffix(suffix)


def classify(paths: list[str]) -> dict[str, Any]:
    """Sort repository paths into the governance a harness would actually see.

    Pure and order-independent, so the detection rules can be argued with in a
    test rather than only observed against a live clone.
    """
    found: dict[str, list[str]] = {
        "rules_files": [],
        "skills": [],
        "agents": [],
        "commands": [],
        "governance_docs": [],
        "memory": [],
        "adrs": [],
        "ci_workflows": [],
        "test_markers": [],
    }
    for path in paths:
        if path in RULES_FILES:
            found["rules_files"].append(path)
        if path in _TEST_MARKERS:
            found["test_markers"].append(path)
        for prefix, suffix, bucket in _BUCKETS:
            if path.startswith(prefix) and path.endswith(suffix):
                found[bucket].append(_name(path, prefix, suffix))
                break
    return {key: sorted(set(values)) for key, values in found.items()}


@dataclass
class Check:
    """One readiness line, in the three states the UI is allowed to render."""

    label: str
    #: "ok" | "missing" | "unknown". Never a fourth, and never inferred.
    status: str
    detail: str
    items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "items": list(self.items),
        }


def _governance_checks(detected: dict[str, Any] | None) -> list[Check]:
    if detected is None:
        return [
            Check(
                "AI governance",
                "unknown",
                "The repository could not be read, so nothing can be said about "
                "the governance it ships.",
            )
        ]
    rules = detected["rules_files"]
    checks = [
        Check(
            "Agent rules file",
            "ok" if rules else "missing",
            ", ".join(rules) if rules else "Not detected.",
            rules,
        ),
        Check(
            "Skills",
            "ok" if detected["skills"] else "missing",
            f"{len(detected['skills'])} skill(s) detected."
            if detected["skills"]
            else "Not detected.",
            detected["skills"],
        ),
        Check(
            "Agents",
            "ok" if detected["agents"] else "missing",
            f"{len(detected['agents'])} agent definition(s) detected."
            if detected["agents"]
            else "Not detected.",
            detected["agents"],
        ),
        Check(
            "Governance docs",
            "ok" if detected["governance_docs"] else "missing",
            f"{len(detected['governance_docs'])} document(s) under .github/."
            if detected["governance_docs"]
            else "Not detected.",
            detected["governance_docs"],
        ),
        Check(
            "CI workflows",
            "ok" if detected["ci_workflows"] else "missing",
            f"{len(detected['ci_workflows'])} workflow(s) detected."
            if detected["ci_workflows"]
            else "Not detected.",
            detected["ci_workflows"],
        ),
    ]
    return checks


def build(
    target: TargetRepository,
    *,
    commit: str | None,
    ref_error: str = "",
    paths: list[str] | None = None,
    read_error: str = "",
    suggested: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """The readiness report for one repository.

    ``commit`` resolved and ``paths`` listed are supplied by the caller so the
    network and the filesystem stay out of the rules — the caller owns the
    failure, this owns what the failure means.
    """
    detected = classify(paths) if paths is not None else None

    reachable = Check(
        "Repository reachable",
        "ok" if commit else "missing",
        f"{target.repository_url} at {target.default_ref}"
        if commit
        else (ref_error or "Could not resolve the repository or its ref."),
    )
    ref_check = Check(
        "Ref pinned to a commit",
        "ok" if commit else "unknown",
        f"{target.default_ref} → {commit[:12]}"
        if commit
        else "No commit resolved, so a run could not be pinned.",
    )
    gates = Check(
        "Validation gate",
        "ok" if target.validation_commands else "missing",
        f"{len(target.validation_commands)} command(s) configured."
        if target.validation_commands
        else (
            "Not configured. Without the repository's own tests a run here can "
            "only be scored by hand, so it cannot enter a success rate."
        ),
        list(target.validation_commands),
    )
    setup = Check(
        "Setup commands",
        "ok" if target.setup_commands else "missing",
        f"{len(target.setup_commands)} command(s) configured."
        if target.setup_commands
        else "Not configured. A repository needing an install step will fail.",
        list(target.setup_commands),
    )

    checks = [reachable, ref_check, gates, setup, *_governance_checks(detected)]
    return {
        "target_id": target.target_id,
        "name": target.name,
        "repository_url": target.repository_url,
        "ref": target.default_ref,
        "commit": commit,
        "detected": detected,
        # Inferred, never adopted silently: a person clicks these into the
        # target's configuration, and can edit them there.
        "suggested_validation_commands": (suggested or {}).get("validation", []),
        "suggested_setup_commands": (suggested or {}).get("setup", []),
        "read_error": read_error,
        "checks": [check.as_dict() for check in checks],
        # "Ready" is the narrow claim that a run would execute and be scored
        # automatically. It says nothing about whether the repository has good
        # governance, which is what the audit is for.
        "ready": bool(commit) and bool(target.validation_commands),
        "blocking": [
            check.label for check in (reachable, gates) if check.status != "ok"
        ],
    }


def infer_commands(paths: list[str], contents: dict[str, str]) -> dict[str, list[str]]:
    """Infer validation and setup commands from what the repository ships.

    An auditor rarely knows a client repository's real test command, and asking
    them to guess defeats the point of an audit. This reads the conventional
    markers instead — a Makefile's ``test:`` target, ``package.json`` scripts,
    a pytest configuration, ``go.mod``, ``Cargo.toml`` — and proposes commands.

    They are **suggestions**: shown as inferred, adopted by a person, editable
    before any run depends on them. Inference that silently became
    configuration would be the lab guessing a client's own quality gate.

    ``contents`` holds the few small files worth reading (fetched lazily from
    the blobless mirror); ``paths`` is the full tree listing.
    """
    path_set = set(paths)
    validation: list[str] = []
    setup: list[str] = []

    # A task runner with an explicit `test` target outranks everything: it is
    # the repository's own word for "this is how you check me".
    makefile = contents.get("Makefile", "")
    if re.search(r"^test\s*:", makefile, re.MULTILINE):
        validation.append("make test")
    justfile = contents.get("justfile", "")
    if re.search(r"^test\b", justfile, re.MULTILINE):
        validation.append("just test")

    # Node: only when scripts.test is real — npm's scaffold placeholder
    # ("no test specified") declares the opposite of a gate.
    package_json = contents.get("package.json", "")
    if package_json:
        try:
            scripts = (json.loads(package_json) or {}).get("scripts") or {}
        except ValueError:
            scripts = {}
        test_script = str(scripts.get("test") or "")
        if test_script and "no test specified" not in test_script:
            if "pnpm-lock.yaml" in path_set:
                setup.append("pnpm install --frozen-lockfile")
                validation.append("pnpm test")
            elif "yarn.lock" in path_set:
                setup.append("yarn install --frozen-lockfile")
                validation.append("yarn test")
            else:
                setup.append(
                    "npm ci" if "package-lock.json" in path_set else "npm install"
                )
                validation.append("npm test")

    # Python: any explicit pytest configuration, or a conventional test tree.
    pyproject = contents.get("pyproject.toml", "")
    has_pytest = (
        "[tool.pytest" in pyproject
        or "pytest.ini" in path_set
        or "tox.ini" in path_set
        or any(
            part.startswith("tests/") and "test" in part.rsplit("/", 1)[-1]
            for part in path_set
        )
    )
    if pyproject or has_pytest:
        if "uv.lock" in path_set:
            setup.append("uv sync")
            if has_pytest:
                validation.append("uv run pytest -x -q")
        elif "poetry.lock" in path_set:
            setup.append("poetry install")
            if has_pytest:
                validation.append("poetry run pytest -x -q")
        elif has_pytest:
            setup.append("pip install -e .")
            validation.append("pytest -x -q")

    if "go.mod" in path_set:
        validation.append("go test ./...")
    if "Cargo.toml" in path_set:
        validation.append("cargo test")

    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return {"validation": _dedupe(validation), "setup": _dedupe(setup)}


#: The files infer_commands wants to read, all small by convention.
_INFERENCE_FILES = ("Makefile", "justfile", "package.json", "pyproject.toml")


def read_file(mirror: Path, ref: str, path: str) -> str:
    """One file's content at ``ref`` from the bare mirror, or "" if unreadable.

    The mirror is blobless, so this triggers a lazy fetch of exactly one blob —
    cheap for the handful of small marker files inference reads, which is why
    inference stays possible on repositories too large to clone for a look.
    """
    from harness_lab_app.case_derivation import _git

    result = _git(["--git-dir", str(mirror), "show", f"{ref or 'HEAD'}:{path}"])
    return result.stdout if result.returncode == 0 else ""


def list_paths(mirror: Path, ref: str) -> list[str]:
    """Every tracked path at ``ref``, from a bare mirror. Names only, no blobs."""
    from harness_lab_app.case_derivation import _git

    result = _git(
        ["--git-dir", str(mirror), "ls-tree", "-r", "--name-only", ref or "HEAD"]
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-300:]
        raise OSError(detail or "git ls-tree failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


class ReadinessService:
    def __init__(self, session: Any):
        self.session = session

    def build(self, target_id: str) -> dict[str, Any]:
        from harness_lab_app.case_derivation import sync_mirror
        from harness_lab_app.config import get_settings
        from harness_lab_app.target_service import TargetService, resolve_ref

        target = TargetService(self.session).get_target(target_id)

        commit: str | None = None
        ref_error = ""
        try:
            commit = resolve_ref(target.repository_url, target.default_ref)
        except Exception as error:
            # Unreachable is a finding about the repository, not a crash: the
            # page still has to render the rest of what it knows.
            ref_error = str(error)

        paths: list[str] | None = None
        read_error = ""
        suggested: dict[str, list[str]] | None = None
        try:
            mirror = sync_mirror(
                target.repository_url,
                get_settings().target_mirrors_dir,
                target.target_id,
            )
            ref = commit or target.default_ref
            paths = list_paths(mirror, ref)
            contents = {
                name: read_file(mirror, ref, name)
                for name in _INFERENCE_FILES
                if name in set(paths)
            }
            suggested = infer_commands(paths, contents)
        except Exception as error:
            read_error = str(error)

        return build(
            target,
            commit=commit,
            ref_error=ref_error,
            paths=paths,
            read_error=read_error,
            suggested=suggested,
        )
