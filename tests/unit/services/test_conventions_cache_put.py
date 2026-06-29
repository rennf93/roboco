"""ConventionsService._cache_put isolates a concurrent-duplicate insert.

Two task creates for the same project/HEAD can race to populate the
conventions cache; the loser's INSERT fails the partial-unique index with
IntegrityError. A bare ``session.add`` + ``flush`` poisons the shared
session — the task-create transaction rides the same session, so every
subsequent op raises "this session is in error state" and task creation
crashes. The fix runs the insert in a savepoint and rolls back ONLY the
savepoint on conflict, leaving the outer transaction usable (the winner's
row satisfies the next ``_cache_get``).

These tests exercise the contract with a fake session: a duplicate must not
raise out of ``_cache_put``, must not call a full ``session.rollback()``
(which would undo the outer task-create transaction), and must not poison
the session — while the happy path still adds + commits the savepoint.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from roboco.foundation.policy.conventions import ConventionsStandard
from roboco.services.conventions import ConventionsService
from sqlalchemy.exc import IntegrityError


class _FakeNested:
    """A stand-in for the savepoint returned by ``session.begin_nested()``.

    On release (``__aexit__``) it flushes the queued insert: a concurrent
    duplicate raises IntegrityError and the SAVEPOINT is rolled back — the
    outer session is NOT poisoned (only the savepoint failed). The happy path
    releases the savepoint cleanly.
    """

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> None:
        self._session.savepoint_started += 1

    async def __aexit__(self, exc_type: Any, exc: Any, _tb: Any) -> None:
        if self._session._duplicate:
            self._session.savepoint_rolled_back = True
            raise IntegrityError(
                "INSERT ... conventions_cache", {}, Exception("unique")
            )
        self._session.savepoint_committed = True


class _FakeSession:
    def __init__(self, *, duplicate: bool) -> None:
        self._duplicate = duplicate
        self.added: list[Any] = []
        self.poisoned = False
        self.full_rollback_called = False
        self.savepoint_started = 0
        self.savepoint_committed = False
        self.savepoint_rolled_back = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # Old-code path: a bare flush on a duplicate poisons the shared session.
        if self._duplicate:
            self.poisoned = True
            raise IntegrityError(
                "INSERT ... conventions_cache", {}, Exception("unique")
            )

    def begin_nested(self) -> _FakeNested:
        return _FakeNested(self)

    async def rollback(self) -> None:
        self.full_rollback_called = True


def _mapping() -> ConventionsStandard:
    return ConventionsStandard(modules=[], rules={}, waivers=[])


@pytest.mark.asyncio
async def test_cache_put_tolerates_concurrent_duplicate_without_poisoning() -> None:
    # The loser of a concurrent cache-populate race must not crash the shared
    # task-create session: the duplicate IntegrityError is contained to a
    # savepoint, the session is not poisoned, and no full rollback undoes the
    # outer task-create transaction.
    session = _FakeSession(duplicate=True)
    svc = ConventionsService(session=cast("Any", session))

    await svc._cache_put(uuid4(), "deadbeef", _mapping(), "ok")

    assert not session.poisoned
    assert not session.full_rollback_called
    assert session.savepoint_rolled_back is True


@pytest.mark.asyncio
async def test_cache_put_happy_path_adds_and_releases_savepoint() -> None:
    session = _FakeSession(duplicate=False)
    svc = ConventionsService(session=cast("Any", session))

    await svc._cache_put(uuid4(), "deadbeef", _mapping(), "ok")

    assert len(session.added) == 1
    assert session.savepoint_committed is True
    assert not session.poisoned
    assert not session.full_rollback_called
