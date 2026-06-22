"""Shared fixtures for skills_sync tests."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root (where governed sources live)."""
    return REPO_ROOT
