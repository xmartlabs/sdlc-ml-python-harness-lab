"""The governance surface a run actually had, resolved from its own condition.

An auditor that judges a run against rules the run never received is not
measuring the run — it is measuring the lab. Under `native` the rules under
evaluation are *the target team's own*; under `injected` they are the harness
this lab projected into their workspace; under `lab_root` they are the lab's.
Three different documents, three different verdicts, and nothing in the stored
audit used to say which one was read.

This module answers one question — **which governance files were available to
this run, and what did they contain?** — and answers it the same way for every
consumer: the LLM auditor, the attribution parser that computes read coverage,
and the API that has to show a reader what a judgement was made against.

The fingerprint is over content, not paths. Two runs of the same condition must
agree; a run whose CLAUDE.md changed between them must not.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Where the agent-visible rulebook lives, per harness. The audited agent read
# *this* file, so rule adherence is judged against it and not against a sibling
# adapter it never saw.
HARNESS_RULES_FILE: dict[str, str] = {
    "opencode": "OPENCODE.md",
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "antigravity": ".agents/rules/GEMINI.md",
    "local": "LOCAL_AGENT.md",
}

#: Fallback when a run's harness is unknown. Named rather than silent.
DEFAULT_RULES_FILE = "OPENCODE.md"

# Single files that count as governance wherever they exist. Deliberately a
# superset of the adapters: a target repository that ships AGENTS.md but is run
# under claude still *has* that governance, and hiding it would misdescribe the
# surface.
_SINGLE_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "OPENCODE.md",
    "LOCAL_AGENT.md",
    "GEMINI.md",
    ".agents/rules/GEMINI.md",
    ".cursorrules",
    ".windsurfrules",
)

# Directories whose markdown is governance. Each entry is (directory, glob).
_GLOBS = (
    (".github", "*.md"),
    ("memory", "*.md"),
    (".claude/skills", "*/SKILL.md"),
    (".claude/agents", "*.md"),
    (".claude/commands", "*.md"),
    (".github/skills", "*/SKILL.md"),
    (".github/skills-external", "*/SKILL.md"),
    (".github/agents", "*.md"),
    (".agents/skills", "*/SKILL.md"),
    (".agents/rules", "*.md"),
    ("docs/adr", "*.md"),
)

# How many files a surface may enumerate. A repository with a thousand markdown
# files under .github would otherwise turn the fingerprint into a directory
# listing; the cap keeps the record readable and the hash cheap.
MAX_FILES = 400

#: `harness_source` values that put the agent inside a materialized workspace.
WORKSPACE_SOURCES = frozenset({"native", "injected"})


@dataclass
class GovernanceSurface:
    """What one run's agent could read, and the proof of which files those were.

    ``source`` is the *provenance* of the surface, not the harness condition:
    ``target_workspace`` means the files came from the run's own archived
    workspace, ``lab_root`` that they came from the repository the lab runs
    from. They are stored separately from ``harness_source`` because they can
    disagree — an archived workspace that was cleaned up forces a fallback, and
    that fallback has to be visible rather than inferred.
    """

    source: str
    root: str
    harness_source: str
    rules_file: str
    files: list[str] = field(default_factory=list)
    fingerprint: str = ""
    truncated: bool = False
    warning: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "root": self.root,
            "harness_source": self.harness_source,
            "rules_file": self.rules_file,
            "files": list(self.files),
            "file_count": len(self.files),
            "fingerprint": self.fingerprint,
            "truncated": self.truncated,
            "warning": self.warning,
        }


def rules_file_for(harness: str | None) -> tuple[str, str]:
    """The rulebook for a harness, plus a warning when there is no mapping."""
    name = HARNESS_RULES_FILE.get(harness or "")
    if name is None:
        return (
            DEFAULT_RULES_FILE,
            f"harness '{harness or 'unknown'}' has no mapped rules file; "
            f"defaulting to {DEFAULT_RULES_FILE} — treat rule-adherence findings "
            "with care",
        )
    return name, ""


def collect_files(root: Path) -> tuple[list[str], bool]:
    """Every governance file under ``root``, repo-relative and sorted.

    Returns ``(paths, truncated)``. Truncation is reported rather than silent:
    a fingerprint over a clipped surface is still reproducible, but a reader
    must know it does not enumerate everything.
    """
    found: set[str] = set()
    for name in _SINGLE_FILES:
        if (root / name).is_file():
            found.add(name)
    for directory, pattern in _GLOBS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.glob(pattern):
            if path.is_file():
                found.add(path.relative_to(root).as_posix())
    ordered = sorted(found)
    if len(ordered) > MAX_FILES:
        return ordered[:MAX_FILES], True
    return ordered, False


def fingerprint_files(root: Path, files: list[str]) -> str:
    """Content hash of a surface.

    Path *and* content, so a renamed file and an edited file both move the
    hash. An unreadable file is hashed as the empty string rather than skipped:
    dropping it would let a permissions change look like no change at all.
    """
    digest = hashlib.sha256()
    for relative in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(hashlib.sha256((root / relative).read_bytes()).digest())
        except OSError:
            digest.update(hashlib.sha256(b"").digest())
    return digest.hexdigest()


def resolve_surface(
    *,
    lab_root: Path,
    run_dir: Path,
    harness: str | None,
    harness_source: str,
) -> GovernanceSurface:
    """The governance surface for one run, from that run's own condition.

    The rule, stated once so every caller obeys the same one:

    ``native``    → the target repository as it was, archived with the run.
    ``injected``  → the same workspace *after* the lab's harness was projected
                    into it, which is what the agent actually saw.
    ``lab_root``  → the repository the lab runs from.

    A `native` or `injected` run whose archived workspace is gone falls back to
    the lab's root and says so in ``warning``. That is a degraded record, not a
    correct one, and callers are expected to surface the warning rather than
    quietly present the verdict as sound.
    """
    rules_file, warning = rules_file_for(harness)
    harness_source = harness_source or "lab_root"

    root = lab_root
    source = "lab_root"
    if harness_source in WORKSPACE_SOURCES:
        archived = run_dir / "workspace"
        if archived.is_dir():
            root = archived
            source = "target_workspace"
        else:
            extra = (
                f"run used harness_source='{harness_source}' but its archived "
                "workspace is gone; this surface describes the lab's own "
                "governance, not the one the run received"
            )
            warning = f"{warning}; {extra}" if warning else extra

    files, truncated = collect_files(root)
    return GovernanceSurface(
        source=source,
        root=str(root),
        harness_source=harness_source,
        rules_file=rules_file,
        files=files,
        fingerprint=fingerprint_files(root, files),
        truncated=truncated,
        warning=warning,
    )


# Catalogue directories: things a harness offers *on demand*. A skill nobody
# needed is not an unread document, and counting all 40 of them would drive read
# coverage toward zero for every run. Whether the catalogue earns its keep is
# `skill_adherence`'s question, judged by the auditor, not this ratio's.
_CATALOGUE_PREFIXES = (
    ".claude/skills/",
    ".claude/agents/",
    ".claude/commands/",
    ".github/skills/",
    ".github/skills-external/",
    ".github/agents/",
    ".agents/skills/",
    "docs/adr/",
)


def reading_surface(surface: GovernanceSurface) -> tuple[str, ...]:
    """The paths read-coverage is measured over, for this run's surface.

    Coverage against a fixed list of the *lab's* documents scores a `native`
    run for failing to read files that were never in its workspace. The
    denominator has to come from the same place the numerator does — but not
    all of it: this keeps the standing rulebook (top-level rules files,
    ``.github`` docs, ``memory``, ``.agents/rules``) and drops the on-demand
    catalogue, because "did the agent open the rules?" and "did the agent need
    this skill?" are different questions with different right answers.
    """
    return tuple(
        path for path in surface.files if not path.startswith(_CATALOGUE_PREFIXES)
    )
