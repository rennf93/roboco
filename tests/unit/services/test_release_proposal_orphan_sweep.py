"""A restart mid-execute kills ``_run_approve_background`` but the Redis release
mutex (TTL 3000s) persists with no heartbeat, so a CEO retry gets
``already_in_progress`` for up to 50 min. ``sweep_orphan_release_locks`` scans
the ``roboco:release_proposal:*`` keyspace at orchestrator start and deletes any
whose task_id isn't in the in-flight registry — after a restart that registry is
empty, so every surviving key is an orphan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import patch
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    import asyncio
from roboco.services import release_proposal as rp
from roboco.services.release_proposal import sweep_orphan_release_locks


class _KeysRedis:
    """Minimal fake backing ``keys`` / ``delete`` / ``set`` / ``aclose`` for the
    sweep (the existing ``_FakeRedis`` in the concurrency module has no
    ``keys``). Keys are stored as bytes to mirror redis-py's default
    ``decode_responses=False``."""

    def __init__(self) -> None:
        self._store: dict[bytes, bytes] = {}

    async def set(self, name: str, value: str) -> bool:
        self._store[name.encode()] = value.encode()
        return True

    async def keys(self, pattern: str) -> list[bytes]:
        prefix = pattern.rstrip("*").encode()
        return [k for k in self._store if k.startswith(prefix)]

    async def delete(self, *keys: bytes | str) -> int:
        n = 0
        for k in keys:
            kb = k.encode() if isinstance(k, str) else k
            if self._store.pop(kb, None) is not None:
                n += 1
        return n

    async def get(self, name: str) -> bytes | None:
        return self._store.get(name.encode())

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_sweep_deletes_orphan_release_locks() -> None:
    """A stale lock from a pre-restart approve (no longer in the in-flight
    registry) is deleted; a key whose task_id IS in flight is preserved."""

    fake = _KeysRedis()
    orphan_key = f"{rp._RELEASE_LOCK_PREFIX}{uuid4()}"
    await fake.set(orphan_key, "deadtoken")

    # An in-flight approve — its lock must survive the sweep.
    in_flight_id = uuid4()
    in_flight_key = f"{rp._RELEASE_LOCK_PREFIX}{in_flight_id}"
    await fake.set(in_flight_key, "livetoken")

    rp._INFLIGHT_APPROVES.clear()
    rp._INFLIGHT_APPROVES[in_flight_id] = cast("asyncio.Task[None]", object())

    with patch("roboco.services.release_proposal.redis.from_url", return_value=fake):
        await rp.sweep_orphan_release_locks()

    assert await fake.get(orphan_key) is None  # orphan deleted
    assert await fake.get(in_flight_key) is not None  # in-flight preserved
    rp._INFLIGHT_APPROVES.clear()


@pytest.mark.asyncio
async def test_sweep_ignores_non_uuid_keys() -> None:
    """A key under the prefix whose tail isn't a UUID is left alone (defensive
    against an unrelated key colliding with the prefix)."""

    fake = _KeysRedis()
    junk_key = f"{rp._RELEASE_LOCK_PREFIX}not-a-uuid"
    await fake.set(junk_key, "x")

    rp._INFLIGHT_APPROVES.clear()
    with patch("roboco.services.release_proposal.redis.from_url", return_value=fake):
        await rp.sweep_orphan_release_locks()

    assert await fake.get(junk_key) is not None


@pytest.mark.asyncio
async def test_sweep_redis_failure_does_not_raise() -> None:
    """A Redis outage at startup must not crash the orchestrator — the sweep is
    best-effort and logs a warning instead of raising."""

    class _BoomRedis:
        async def keys(self, _pattern: str) -> list[bytes]:
            raise ConnectionError("redis down")

        async def aclose(self) -> None:
            return None

    with patch(
        "roboco.services.release_proposal.redis.from_url", return_value=_BoomRedis()
    ):
        # Must not raise.
        await rp.sweep_orphan_release_locks()


@pytest.mark.asyncio
async def test_sweep_no_keys_is_noop() -> None:
    """Empty keyspace — sweep returns without touching anything."""

    fake = _KeysRedis()
    rp._INFLIGHT_APPROVES.clear()
    with patch("roboco.services.release_proposal.redis.from_url", return_value=fake):
        await rp.sweep_orphan_release_locks()
    assert await fake.keys(f"{rp._RELEASE_LOCK_PREFIX}*") == []


def test_sweep_is_module_level_callable() -> None:
    """``sweep_orphan_release_locks`` is a module-level function the
    orchestrator can import at startup (not bound to a service instance)."""

    assert callable(sweep_orphan_release_locks)


# ponytail: the in-flight registry values are asyncio.Task; for the sweep test
# only membership matters, so a plain object() stand-in avoids spinning a task.
