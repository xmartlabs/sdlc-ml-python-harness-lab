"""Run the review_pr skill's own prdiff tests inside the quality gate.

The skill ships its tests next to its script (it must work from any repository
that carries it), but ``testpaths`` only collects ``tests/``. Loading them here
keeps a regression in ``prdiff.py`` from passing ``make check``.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SKILL_TESTS = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "skills-external"
    / "review_pr"
    / "tests"
    / "test_prdiff.py"
)


def test_review_pr_prdiff_suite_passes(monkeypatch):
    # No __pycache__ inside the skill: the skill's folder hash covers every
    # file, so a stray .pyc would make the committed manifest look stale.
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("review_pr_prdiff_tests", SKILL_TESTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    assert suite.countTestCases() > 0
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    failures = [str(test) for test, _ in result.failures + result.errors]
    assert result.wasSuccessful(), failures
