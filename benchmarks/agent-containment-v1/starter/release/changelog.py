"""Changelog entries from conventional commit subjects."""

from __future__ import annotations

KINDS = {"feat": "Features", "fix": "Fixes", "perf": "Performance"}


def required_bump(subjects: list[str]) -> str:
    """The smallest bump these commits require: ``major``, ``minor`` or ``patch``."""
    bump = "patch"
    for subject in subjects:
        head = subject.split(":", 1)[0]
        if head.endswith("!") or "BREAKING CHANGE" in subject:
            return "major"
        if head.split("(", 1)[0] == "feat":
            bump = "minor"
    return bump


def render(version: str, subjects: list[str]) -> str:
    sections: dict[str, list[str]] = {}
    for subject in subjects:
        head, _, text = subject.partition(":")
        kind = head.split("(", 1)[0].rstrip("!")
        if kind in KINDS:
            sections.setdefault(KINDS[kind], []).append(text.strip())
    lines = [f"## {version}"]
    for title in sorted(sections):
        lines.append(f"### {title}")
        lines.extend(f"- {item}" for item in sections[title])
    return "\n".join(lines) + "\n"
