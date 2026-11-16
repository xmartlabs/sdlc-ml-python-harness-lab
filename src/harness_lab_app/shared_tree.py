"""Who may use the lab's shared tree right now, across every run lane (ADR-0082).

Every run reads the lab's native skill views (``.claude/skills`` and the other
tools' copies), and a run with a skill ablation rewrites them for its whole
lifetime (ADR-0011). With one lane that was harmless. With two (``runs`` and
``runs-parallel``), an ablation on one lane would change the catalog under a
model comparison running on the other — and a plain run starting there would
restore the full catalog in the middle of the ablation.

So the tree is a readers-writer lease, held by the worker for the whole run:

- a run that rewrites the tree takes it **exclusively** and runs alone;
- every other run takes it **shared**, so the two lanes still run side by side;
- a waiting exclusive run stops new shared ones from starting, or a steady
  stream of model runs on the second lane would hold it off forever. There is
  one waiting place, first come: an exclusive run blocked by anyone — readers
  or another writer — takes it if it is free, and a later exclusive run queues
  behind it. It lapses unless re-claimed, so an abandoned wait frees itself.

The lease lives in Redis, the broker every worker already shares, and expires
unless it is refreshed, so a worker killed mid-run frees it on its own. Not a
file lock: on Docker Desktop's bind mount ``flock`` and ``lockf`` exclude
nothing — not between containers, not even between two processes of one
container (probed 2026-11-15).
"""

from __future__ import annotations

from typing import Any, Protocol

KEY_PREFIX = "harness-lab:shared-tree"
# A lease outlives two missed refreshes; a killed worker frees it in minutes.
LEASE_SECONDS = 120
REFRESH_SECONDS = 30
# How long a waiting exclusive run keeps new shared runs out without polling.
WAITING_SECONDS = 60
# The longest any one lease call may block, well inside one refresh period.
REDIS_TIMEOUT_SECONDS = 5

# KEYS: writer, readers (sorted set: run id -> expiry), waiting writer.
# ARGV: run id, "1" exclusive / "0" shared, lease seconds, waiting seconds.
# Returns nil when the lease is taken, else the run id standing in the way.
_ACQUIRE = """
local now = tonumber(redis.call('TIME')[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
local function wait_in_line()
  local waiting = redis.call('GET', KEYS[3])
  if not waiting or waiting == ARGV[1] then
    redis.call('SET', KEYS[3], ARGV[1], 'EX', tonumber(ARGV[4]))
    return nil
  end
  return waiting
end
local writer = redis.call('GET', KEYS[1])
if writer == ARGV[1] and ARGV[2] == '1' then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[3]))
  return false
end
if writer and writer ~= ARGV[1] then
  if ARGV[2] == '1' then wait_in_line() end
  return writer
end
if ARGV[2] == '1' then
  local ahead = wait_in_line()
  if ahead then return ahead end
  local readers = redis.call('ZRANGE', KEYS[2], 0, 0)
  if #readers > 0 then return readers[1] end
  redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[3]))
  redis.call('DEL', KEYS[3])
  return false
end
local waiting = redis.call('GET', KEYS[3])
local holding = redis.call('ZSCORE', KEYS[2], ARGV[1])
if waiting and waiting ~= ARGV[1] and not holding then return waiting end
redis.call('ZADD', KEYS[2], now + tonumber(ARGV[3]), ARGV[1])
redis.call('EXPIRE', KEYS[2], 2 * tonumber(ARGV[3]))
return false
"""

_REFRESH = """
local now = tonumber(redis.call('TIME')[1])
if ARGV[2] == '1' then
  if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
    return 1
  end
  return 0
end
if redis.call('ZSCORE', KEYS[2], ARGV[1]) then
  redis.call('ZADD', KEYS[2], now + tonumber(ARGV[3]), ARGV[1])
  redis.call('EXPIRE', KEYS[2], 2 * tonumber(ARGV[3]))
  return 1
end
return 0
"""

_RELEASE = """
if ARGV[2] == '1' then
  if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
  if redis.call('GET', KEYS[3]) == ARGV[1] then redis.call('DEL', KEYS[3]) end
else
  redis.call('ZREM', KEYS[2], ARGV[1])
end
return 1
"""


class TreeLeases(Protocol):
    """The lease store; ``try_acquire`` returns who blocks, or None when held."""

    def try_acquire(self, run_id: str, exclusive: bool) -> str | None: ...

    def refresh(self, run_id: str, exclusive: bool) -> bool: ...

    def release(self, run_id: str, exclusive: bool) -> None: ...


def rewrites_shared_tree(config: Any) -> bool:
    """Whether a run changes the skill catalog every other run reads.

    Only a skill ablation of a ``lab_root`` run does: ``bench_run.py`` applies
    the ablated views to the lab's own tree for the run's lifetime. A run in a
    target workspace projects its skills there, not into the lab.
    """
    ablates = bool(
        getattr(config, "skills_disabled", None)
        or getattr(config, "skills_extra", None)
    )
    return ablates and str(getattr(config, "harness_source", "")) == "lab_root"


class RedisTreeLeases:
    """The lease in Redis, each step one atomic script."""

    def __init__(self, client: Any, prefix: str = KEY_PREFIX) -> None:
        self._keys = [f"{prefix}:writer", f"{prefix}:readers", f"{prefix}:waiting"]
        self._acquire = client.register_script(_ACQUIRE)
        self._refresh = client.register_script(_REFRESH)
        self._release = client.register_script(_RELEASE)

    def _args(self, run_id: str, exclusive: bool) -> list[Any]:
        return [run_id, "1" if exclusive else "0", LEASE_SECONDS, WAITING_SECONDS]

    def try_acquire(self, run_id: str, exclusive: bool) -> str | None:
        holder = self._acquire(keys=self._keys, args=self._args(run_id, exclusive))
        if holder is None:
            return None
        return holder.decode() if isinstance(holder, bytes) else str(holder)

    def refresh(self, run_id: str, exclusive: bool) -> bool:
        return bool(self._refresh(keys=self._keys, args=self._args(run_id, exclusive)))

    def release(self, run_id: str, exclusive: bool) -> None:
        self._release(keys=self._keys, args=self._args(run_id, exclusive))


def redis_leases(redis_url: str) -> RedisTreeLeases:
    """The lease store on the broker's Redis."""
    import redis  # the broker's client; only the worker needs it

    # Bounded, or a half-open connection blocks forever: a waiting run could not
    # be cancelled and a lost lease would never be noticed.
    return RedisTreeLeases(
        redis.Redis.from_url(
            redis_url,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        )
    )
