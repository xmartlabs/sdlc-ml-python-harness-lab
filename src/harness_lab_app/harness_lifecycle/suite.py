"""Canonical regression suite: parse the definition and expand the run plan.

Phase 5 (this module, pure slice) loads ``benchmarks/harness-regression-v1/suite.yaml``
into a :class:`SuiteDefinition` and expands it into the flat list of child runs to
execute — every case, under both the ``baseline`` and ``candidate`` arm, repeated as
declared. Actual run creation and Celery orchestration live in a later slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from harness_lab_app.harness_lifecycle.models import SuiteDefinition

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_SUITE_ID = "harness-regression-v1"
ARMS: tuple[str, ...] = ("baseline", "candidate")


@dataclass(frozen=True)
class PlannedRun:
    """One child run in the expanded plan: a (case, arm, repetition) triple."""

    case_id: str
    role: str
    arm: str
    repetition: int


def suite_path(root: Path, suite_id: str = DEFAULT_SUITE_ID) -> Path:
    return root / "benchmarks" / suite_id / "suite.yaml"


def load_suite(path: Path) -> SuiteDefinition:
    return SuiteDefinition.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def load_default_suite(root: Path, suite_id: str = DEFAULT_SUITE_ID) -> SuiteDefinition:
    return load_suite(suite_path(root, suite_id))


def planned_runs(suite: SuiteDefinition) -> list[PlannedRun]:
    """Expand the suite into baseline+candidate child runs, one per repetition."""
    runs: list[PlannedRun] = []
    for case in suite.cases:
        for arm in ARMS:
            for repetition in range(1, case.repetitions + 1):
                runs.append(PlannedRun(case.id, case.role, arm, repetition))
    return runs


def child_runs_per_arm(suite: SuiteDefinition) -> int:
    """Total child runs an arm executes (sum of per-case repetitions)."""
    return sum(case.repetitions for case in suite.cases)
