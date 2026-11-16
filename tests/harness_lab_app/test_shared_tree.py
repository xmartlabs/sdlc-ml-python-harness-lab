"""The shared-tree lease that keeps a skill ablation off the other lane (ADR-0082).

The worker side (a run waiting, cancelled, refreshed) is in test_tasks.py, next
to the fakes it runs on.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from harness_lab_app.shared_tree import rewrites_shared_tree


class MemoryLeases:
    """The lease rules of `RedisTreeLeases`, in memory and without expiry."""

    def __init__(self) -> None:
        self.writer: str | None = None
        self.readers: set[str] = set()
        self.waiting: str | None = None
        self.calls: list[tuple[str, str, bool]] = []

    def _wait_in_line(self, run_id: str) -> str | None:
        if self.waiting is None or self.waiting == run_id:
            self.waiting = run_id
            return None
        return self.waiting

    def try_acquire(self, run_id: str, exclusive: bool) -> str | None:
        self.calls.append(("acquire", run_id, exclusive))
        if exclusive and self.writer == run_id:
            return None
        if self.writer and self.writer != run_id:
            if exclusive:
                self._wait_in_line(run_id)
            return self.writer
        if exclusive:
            ahead = self._wait_in_line(run_id)
            if ahead:
                return ahead
            if self.readers:
                return sorted(self.readers)[0]
            self.writer = run_id
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


def _stores() -> list[Any]:
    """The memory model always; the real Redis script when a Redis is given.

    The unit suite has no Redis, so ``HARNESS_LAB_TEST_REDIS_URL`` opts the Lua
    in (the stack's own Redis works: each store gets a throwaway key prefix).
    """
    stores: list[Any] = [pytest.param(MemoryLeases, id="memory")]
    url = os.environ.get("HARNESS_LAB_TEST_REDIS_URL")
    if url:

        def redis_store() -> Any:
            import redis

            from harness_lab_app.shared_tree import RedisTreeLeases

            prefix = f"harness-lab:test-shared-tree:{uuid.uuid4().hex}"
            return RedisTreeLeases(redis.Redis.from_url(url), prefix=prefix)

        stores.append(pytest.param(redis_store, id="redis"))
    return stores


@pytest.mark.parametrize("make_store", _stores())
def test_an_ablation_waiting_behind_another_ablation_keeps_shared_runs_out(
    make_store,
):
    """Writer B queued behind writer A must still block new readers.

    It used to be recorded as waiting only when readers blocked it, so while A
    ran, B was invisible, and after A released, model runs could keep taking the
    tree shared and starve B.
    """
    leases = make_store()
    assert leases.try_acquire("ablation-a", exclusive=True) is None
    assert leases.try_acquire("ablation-b", exclusive=True) == "ablation-a"
    leases.release("ablation-a", exclusive=True)
    assert leases.try_acquire("model-1", exclusive=False) == "ablation-b"
    assert leases.try_acquire("ablation-b", exclusive=True) is None
    leases.release("ablation-b", exclusive=True)
    assert leases.try_acquire("model-1", exclusive=False) is None


@pytest.mark.parametrize("make_store", _stores())
def test_the_first_waiting_ablation_keeps_its_place(make_store):
    """One waiting place, first come: a later writer queues behind it."""
    leases = make_store()
    leases.try_acquire("model-1", exclusive=False)
    assert leases.try_acquire("ablation-b", exclusive=True) == "model-1"
    assert leases.try_acquire("ablation-c", exclusive=True) == "ablation-b"
    leases.release("model-1", exclusive=False)
    # The tree is free, but B was first: C still waits.
    assert leases.try_acquire("ablation-c", exclusive=True) == "ablation-b"
    assert leases.try_acquire("ablation-b", exclusive=True) is None
    leases.release("ablation-b", exclusive=True)
    assert leases.try_acquire("ablation-c", exclusive=True) is None


@pytest.mark.parametrize("make_store", _stores())
def test_the_existing_rules_hold_on_every_store(make_store):
    leases = make_store()
    assert leases.try_acquire("opus", exclusive=False) is None
    assert leases.try_acquire("local", exclusive=False) is None
    assert leases.try_acquire("ablation", exclusive=True) in {"local", "opus"}
    assert leases.try_acquire("model-2", exclusive=False) == "ablation"
    assert leases.try_acquire("opus", exclusive=False) is None
    assert leases.refresh("opus", exclusive=False) is True
    assert leases.refresh("ablation", exclusive=True) is False
    leases.release("opus", exclusive=False)
    leases.release("local", exclusive=False)
    assert leases.try_acquire("ablation", exclusive=True) is None
    assert leases.refresh("ablation", exclusive=True) is True


@pytest.mark.parametrize("make_store", _stores())
def test_a_writer_that_holds_the_tree_gets_it_again(make_store):
    """A lost reply makes the worker ask again; it must not be refused.

    With another writer waiting, the holder used to be told to wait behind it,
    and both stalled until the holder's unrefreshed key lapsed.
    """
    leases = make_store()
    assert leases.try_acquire("ablation-a", exclusive=True) is None
    assert leases.try_acquire("ablation-b", exclusive=True) == "ablation-a"
    assert leases.try_acquire("ablation-a", exclusive=True) is None


def test_the_redis_client_never_blocks_forever(monkeypatch):
    """A half-open connection must time out, or a waiting run cannot be
    cancelled and a lost lease is never noticed."""
    import redis

    from harness_lab_app import shared_tree

    seen: dict[str, Any] = {}

    def from_url(url: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(register_script=lambda _src: None)

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(from_url))
    shared_tree.redis_leases("redis://redis:6379/0")
    assert 0 < seen["socket_timeout"] <= shared_tree.REFRESH_SECONDS
    assert 0 < seen["socket_connect_timeout"] <= shared_tree.REFRESH_SECONDS
