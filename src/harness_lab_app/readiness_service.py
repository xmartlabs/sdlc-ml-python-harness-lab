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


# Ecosystems the lab can tell apart, keyed by the marker file at the repo root,
# and whether the `Dockerfile.runner` image ships that toolchain. The runner has
# Python (pip/uv) and Node (npm/pnpm/yarn) + git; everything else needs a custom
# runner image, so a run against it will very likely fail at setup.
_ECOSYSTEMS: tuple[tuple[str, str, bool], ...] = (
    ("pyproject.toml", "Python", True),
    ("setup.py", "Python", True),
    ("setup.cfg", "Python", True),
    ("package.json", "Node.js", True),
    ("go.mod", "Go", False),
    ("Cargo.toml", "Rust", False),
    ("pom.xml", "Java (Maven)", False),
    ("build.gradle", "Java/Kotlin (Gradle)", False),
    ("Gemfile", "Ruby", False),
    ("composer.json", "PHP", False),
)

_PYTHON_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")

# Commands that install *this repository* as a package of a given ecosystem, so
# they cannot succeed unless that ecosystem's marker is present. This is the
# exact class of failure that aborts provisioning before the agent runs (a
# `pip install -e .` on a repo with no pyproject.toml, say).
_MISMATCH_PATTERNS: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (re.compile(r"\bpip3?\s+install\b.*(?:-e\s+)?\.\s*$"), "Python", _PYTHON_MARKERS),
    (
        re.compile(r"\buv\s+pip\s+install\b.*(?:-e\s+)?\.\s*$"),
        "Python",
        _PYTHON_MARKERS,
    ),
    (re.compile(r"\buv\s+sync\b"), "Python", _PYTHON_MARKERS),
    (re.compile(r"\bpoetry\s+install\b"), "Python", _PYTHON_MARKERS),
    (
        re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:install|ci|i|add)\b"),
        "Node.js",
        ("package.json",),
    ),
)


def runtime_compatibility(
    paths: list[str] | None,
    setup_commands: list[str],
    validation_commands: list[str],
) -> Check:
    """Would the runner image be able to build and test this repository?

    A soft warning, never a gate (the lab fails open — see the audit philosophy):
    it catches the failure mode where provisioning aborts *before* the agent runs
    because a configured command's toolchain is not present. Two things are wrong
    in that world and both are reported here: (1) the repository's ecosystem is
    one the runner image does not ship, or (2) a setup/validation command installs
    the repo as a package of an ecosystem the repo does not actually declare.
    """
    label = "Runtime compatibility"
    if paths is None:
        return Check(
            label,
            "unknown",
            "The repository could not be read, so its runtime cannot be confirmed.",
        )
    path_set = set(paths)
    present = {
        label_: supported
        for marker, label_, supported in _ECOSYSTEMS
        if marker in path_set
    }
    supported = sorted(name for name, ok in present.items() if ok)
    unsupported = sorted(name for name, ok in present.items() if not ok)

    commands = [*(setup_commands or []), *(validation_commands or [])]
    mismatches: list[str] = []
    for cmd in commands:
        low = cmd.strip().lower()
        for pattern, ecosystem, markers in _MISMATCH_PATTERNS:
            if pattern.search(low) and not any(m in path_set for m in markers):
                need = (
                    markers[0] if ecosystem != "Python" else "pyproject.toml/setup.py"
                )
                mismatches.append(
                    f"`{cmd.strip()}` needs a {ecosystem} project ({need}), "
                    "which this repository does not have"
                )
                break

    if mismatches:
        return Check(
            label,
            "missing",
            "A configured command assumes a toolchain this repository does not "
            "declare, so provisioning would abort before the agent runs — exactly "
            "the failure this check exists to catch.",
            mismatches,
        )
    if supported:
        detail = (
            f"{'/'.join(supported)} project — the runner image ships this toolchain "
            "(Python via pip/uv, Node via npm/pnpm/yarn)."
        )
        if unsupported:
            detail += (
                f" It also carries {'/'.join(unsupported)}, whose toolchain the "
                "runner does not include; commands needing it would fail."
            )
        return Check(label, "ok", detail, supported)
    if unsupported:
        return Check(
            label,
            "missing",
            f"Detected {'/'.join(unsupported)}. The runner image ships only Python "
            "and Node toolchains, so a run here will very likely fail at setup "
            "unless the runner image is extended first.",
            unsupported,
        )
    return Check(
        label,
        "unknown",
        "No project marker (pyproject.toml, setup.py, package.json, …) was found, "
        "so the runtime cannot be confirmed and a run may fail at setup.",
    )


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

    runtime = runtime_compatibility(
        paths,
        list(target.setup_commands),
        list(target.validation_commands),
    )

    checks = [
        reachable,
        ref_check,
        gates,
        setup,
        runtime,
        *_governance_checks(detected),
    ]
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
        from harness_lab_app.target_service import TargetService

        target = TargetService(self.session).get_target(target_id)
        return self._report_for(target)

    def inspect(
        self,
        repository_url: str,
        default_ref: str = "",
        setup_commands: list[str] | None = None,
        validation_commands: list[str] | None = None,
    ) -> dict[str, Any]:
        """Readiness for a repository that is not yet a saved target.

        The Add-a-repository form calls this before creation so a user learns the
        repository's runtime is unsupported (or its commands mismatch it) *before*
        launching a run that would abort in provisioning. The form's own draft
        commands are passed through so the mismatch check reflects what would be
        saved. The transient target is never persisted; ``validate_repository_url``
        still guards the URL.
        """
        import hashlib

        from harness_lab_app.models import TargetRepository
        from harness_lab_app.target_service import validate_repository_url

        url = validate_repository_url(repository_url)
        # A stable per-URL mirror slug so two inspections of different repos do
        # not clobber each other's mirror directory.
        slug = (
            "inspect-"
            + hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:12]
        )
        target = TargetRepository(
            target_id=slug,
            name="(inspect)",
            repository_url=url,
            default_ref=default_ref.strip() or "main",
            setup_commands=setup_commands or [],
            validation_commands=validation_commands or [],
        )
        return self._report_for(target)

    def _report_for(self, target: TargetRepository) -> dict[str, Any]:
        from harness_lab_app.case_derivation import sync_mirror
        from harness_lab_app.config import get_settings
        from harness_lab_app.target_service import clone_source, resolve_ref

        source = clone_source(target)

        commit: str | None = None
        ref_error = ""
        if target.pinned_commit:
            # A pinned target is read at its pin, not at whatever its default
            # branch points to now: the readiness report and the runs have to
            # describe the same tree, or the report is about a repository nobody
            # evaluated.
            commit = target.pinned_commit
        else:
            try:
                commit = resolve_ref(source, target.default_ref)
            except Exception as error:
                # Unreachable is a finding about the repository, not a crash: the
                # page still has to render the rest of what it knows.
                ref_error = str(error)

        paths: list[str] | None = None
        read_error = ""
        suggested: dict[str, list[str]] | None = None
        try:
            mirror = sync_mirror(
                source,
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
