"""The shared-tree lease that keeps a skill ablation off the other lane (ADR-0082).

The worker side (a run waiting, cancelled, refreshed) is in test_tasks.py, next
to the fakes it runs on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from harness_lab_app.shared_tree import rewrites_shared_tree


class MemoryLeases:
    """The lease rules of `RedisTreeLeases`, in memory and without expiry."""

    def __init__(self) -> None:
        self.writer: str | None = None
        self.readers: set[str] = set()
        self.waiting: str | None = None
        self.calls: list[tuple[str, str, bool]] = []

    def try_acquire(self, run_id: str, exclusive: bool) -> str | None:
        self.calls.append(("acquire", run_id, exclusive))
        if self.writer and self.writer != run_id:
            return self.writer
        if exclusive:
            if self.readers:
                self.waiting = run_id
                return sorted(self.readers)[0]
            self.writer = run_id
            if self.waiting == run_id:
                self.waiting = None
            return None
        if self.waiting and self.waiting != run_id and run_id not in self.readers:
            return self.waiting
        self.readers.add(run_id)
        return None

    def refresh(self, run_id: str, exclusive: bool) -> bool:
        return self.writer == run_id if exclusive else run_id in self.readers

    def release(self, run_id: str, exclusive: bool) -> None:
        self.calls.append(("release", run_id, exclusive))
        if exclusive:
            if self.writer == run_id:
                self.writer = None
            if self.waiting == run_id:
                self.waiting = None
        else:
            self.readers.discard(run_id)


def _config(**fields: Any) -> SimpleNamespace:
    base = {"skills_disabled": [], "skills_extra": [], "harness_source": "lab_root"}
    return SimpleNamespace(**{**base, **fields})


def test_only_an_ablation_of_the_lab_root_rewrites_the_shared_tree():
    assert rewrites_shared_tree(_config(skills_disabled=["tdd"])) is True
    assert rewrites_shared_tree(_config(skills_extra=["new-skill"])) is True
    assert rewrites_shared_tree(_config()) is False
    # A run in a target workspace projects its skills there, not into the lab.
    assert (
        rewrites_shared_tree(
            _config(skills_disabled=["tdd"], harness_source="injected")
        )
        is False
    )


def test_two_model_runs_share_the_tree_at_once():
    leases = MemoryLeases()
    assert leases.try_acquire("opus", exclusive=False) is None
    assert leases.try_acquire("local", exclusive=False) is None


def test_a_waiting_ablation_keeps_new_shared_runs_out():
    """Or a steady stream of model runs would hold it off forever."""
    leases = MemoryLeases()
    leases.try_acquire("model-1", exclusive=False)
    assert leases.try_acquire("ablation", exclusive=True) == "model-1"
    assert leases.try_acquire("model-2", exclusive=False) == "ablation"
    # One already holding it keeps it.
    assert leases.try_acquire("model-1", exclusive=False) is None
