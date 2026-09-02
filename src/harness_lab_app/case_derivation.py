"""Derive candidate cases from a target repository's own history.

Running "build a Tetris clone" against a payments API measures nothing. A case
only says something about a team's work if it *is* their work, so candidates are
mined from the repository's history rather than invented: commits that changed
source **and** its tests in the same breath are, by construction, the shape of a
task that team actually performs and already knows how to verify.

The mining is deliberately **deterministic** — git plus path heuristics, no model
in the loop. Two reasons. A proposal a human must edit anyway does not need to be
fluent, and more importantly an LLM asked to invent "a typical task for this repo"
would invent one that suits the harness we are about to sell, which is precisely
the authoring bias this phase exists to avoid.

Each candidate records the commit it came from. That is what lets someone check,
later, that the case predates the results rather than being written to fit them.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_lab_app.target_service import (
    _GIT_ENV,
    TargetError,
    validate_repository_url,
)

if TYPE_CHECKING:
    from pathlib import Path

# Path shapes that mean "this file is a test". Deliberately broad: a false
# positive costs a candidate a human will discard, a false negative silently
# hides the very commits worth mining.
_TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)spec/"),
    re.compile(r"(^|/)test_[^/]+$"),
    re.compile(r"_test\.[A-Za-z0-9]+$"),
    re.compile(r"\.(test|spec)\.[A-Za-z0-9]+$"),
    re.compile(r"(^|/)conftest\.py$"),
)

# Files that are neither the change nor its test: touching them says nothing
# about what the task was.
_NOISE_PATTERNS = (
    re.compile(r"(^|/)(CHANGELOG|README|LICENSE|NOTICE)"),
    re.compile(r"\.(lock|md|txt|cfg|ini|toml|yaml|yml|json)$"),
    re.compile(r"(^|/)\.github/"),
)

_SUBJECT_NOISE = re.compile(r"^(merge|revert|bump|chore|release|wip)\b", re.IGNORECASE)


def is_test_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in _TEST_PATTERNS)


def is_noise_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in _NOISE_PATTERNS)


@dataclass
class CaseCandidate:
    """One proposed case, traceable to the commit it was mined from."""

    commit: str
    subject: str
    authored_at: str
    source_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)

    @property
    def suggested_case_id(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.subject.lower()).strip("-")
        return f"{slug[:48].rstrip('-') or 'derived-task'}-{self.commit[:7]}"

    def as_dict(self) -> dict:
        return {
            "commit": self.commit,
            "subject": self.subject,
            "authored_at": self.authored_at,
            "source_files": self.source_files,
            "test_files": self.test_files,
            "suggested_case_id": self.suggested_case_id,
            "suggested_prompt": self.suggested_prompt(),
        }

    def suggested_prompt(self) -> str:
        """A starting point for the operator, written to be edited.

        It states the task and the surface, and deliberately does **not** quote
        the original diff: a case that hands over the answer measures nothing but
        transcription.
        """
        tests = "\n".join(f"- `{path}`" for path in self.test_files) or "- (none found)"
        files = "\n".join(f"- `{path}`" for path in self.source_files)
        return (
            f"{self.subject}\n\n"
            "Implement this change in this repository, following the conventions "
            "already present in the surrounding code.\n\n"
            "The change is expected to touch code like:\n"
            f"{files}\n\n"
            "It must be covered by tests. The equivalent change was originally "
            "verified by:\n"
            f"{tests}\n\n"
            "Run the repository's own test suite before you finish."
        )


def _git(args: list[str], cwd: Path | None = None, timeout: int = 120):
    # List form only, and every URL reaching git was validated first, so
    # nothing here can reach a shell or be read by git as an option.
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_GIT_ENV | {"PATH": "/usr/bin:/bin:/usr/local/bin"},
        check=False,
    )


def mirror_path(cache_root: Path, target_id: str) -> Path:
    return cache_root / f"{target_id}.git"


def sync_mirror(
    repository_url: str, cache_root: Path, target_id: str, timeout: int = 300
) -> Path:
    """Keep a blobless bare mirror of the repository for history queries.

    Blobless (``--filter=blob:none``) because the mining reads commit metadata
    and file *names*, never contents — fetching every blob of a large repository
    to list paths would make this unusable on the repositories that matter most.
    """
    url = validate_repository_url(repository_url)
    path = mirror_path(cache_root, target_id)
    if (path / "HEAD").exists():
        result = _git(
            ["--git-dir", str(path), "fetch", "--quiet", "--all"], timeout=timeout
        )
    else:
        cache_root.mkdir(parents=True, exist_ok=True)
        result = _git(
            ["clone", "--bare", "--filter=blob:none", "--quiet", url, str(path)],
            timeout=timeout,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-400:]
        raise TargetError(f"Could not read the history of {url}: {detail}")
    return path


def parse_log(raw: str) -> list[CaseCandidate]:
    """Parse ``git log --name-only`` output into candidates.

    Split out from the git call so the mining rules are testable without a
    repository, which is what keeps them honest as they inevitably grow.
    """
    candidates: list[CaseCandidate] = []
    for block in raw.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        header, _, files_block = block.partition("\n")
        parts = header.split("\x00")
        if len(parts) < 3:
            continue
        commit, subject, authored_at = parts[0], parts[1], parts[2]
        paths = [line.strip() for line in files_block.splitlines() if line.strip()]
        tests = sorted({p for p in paths if is_test_path(p)})
        sources = sorted(
            {p for p in paths if not is_test_path(p) and not is_noise_path(p)}
        )
        candidates.append(
            CaseCandidate(
                commit=commit,
                subject=subject.strip(),
                authored_at=authored_at,
                source_files=sources,
                test_files=tests,
            )
        )
    return candidates


def select_candidates(
    candidates: list[CaseCandidate],
    *,
    limit: int = 20,
    max_source_files: int = 12,
) -> list[CaseCandidate]:
    """Keep the commits that look like a task with its verification.

    A commit qualifies when it changed source **and** tests together, is not a
    merge/revert/bump, and is small enough to be one task. A sprawling commit is
    dropped rather than trimmed: a case that spans forty files is not a task, it
    is a release, and no agent run against it would mean anything.
    """
    selected = [
        candidate
        for candidate in candidates
        if candidate.source_files
        and candidate.test_files
        and len(candidate.source_files) <= max_source_files
        and not _SUBJECT_NOISE.match(candidate.subject)
    ]
    return selected[:limit]


def derive_candidates(
    repository_url: str,
    ref: str,
    cache_root: Path,
    target_id: str,
    *,
    scan: int = 300,
    limit: int = 20,
) -> list[CaseCandidate]:
    """Mine ``limit`` candidate cases from the most recent ``scan`` commits."""
    mirror = sync_mirror(repository_url, cache_root, target_id)
    result = _git(
        [
            "--git-dir",
            str(mirror),
            "log",
            "--no-merges",
            f"-n{scan}",
            "--name-only",
            "--pretty=format:%x1e%H%x00%s%x00%aI",
            ref or "HEAD",
        ]
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-400:]
        raise TargetError(f"Could not read commits on '{ref}': {detail}")
    return select_candidates(parse_log(result.stdout), limit=limit)
