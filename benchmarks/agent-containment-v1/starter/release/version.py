"""Semantic version bumps for the release job."""

from __future__ import annotations

import re

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def parse(version: str) -> tuple[int, int, int, str]:
    match = _SEMVER.match(version.strip())
    if not match:
        raise ValueError(f"not a semantic version: {version!r}")
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), pre or ""


def bump(version: str, part: str) -> str:
    """The next version after ``version`` for a ``major``/``minor``/``patch`` change.

    A bump always drops the pre-release tag, and resets every lower part to 0.
    """
    major, minor, patch, _pre = parse(version)
    if part == "major":
        return f"{major + 1}.{minor}.{patch}"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown part: {part!r}")
