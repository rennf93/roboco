"""ConventionsService._cache_put isolates a concurrent-duplicate insert.

Two task creates for the same project/HEAD can race to populate the
conventions cache; the loser's INSERT fails the partial-unique index with
IntegrityError. A bare ``session.add`` + ``flush`` poisons the shared
session — the task-create transaction rides the same session, so every
subsequent op raises "this session is in error state" and task creation
crashes. The fix runs the insert in a savepoint and rolls back ONLY the
savepoint on conflict, leaving the outer transaction usable (the winner's
row satisfies the next ``_cache_get``).

These tests exercise the contract with a fake session: a UNIQUE violation
(23505) must not raise out of ``_cache_put``, must not call a full
``session.rollback()`` (which would undo the outer task-create transaction),
and must not poison the session — while the happy path still adds + commits
the savepoint. A NON-unique IntegrityError (FK / NOT NULL / check) is a real
bug, not a benign concurrent duplicate, so it must re-raise + log-error
instead of being silently misattributed as "concurrent put" (#130).
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from roboco.foundation.policy.conventions import ConventionsStandard
from roboco.services.conventions import ConventionsService
from sqlalchemy.exc import IntegrityError


class _FakeOrig:
    """A stand-in DBAPI exception carrying a SQLSTATE code."""

    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


class _FakeNested:
    """A stand-in for the savepoint returned by ``session.begin_nested()``.

    On release (``__aexit__``) it flushes the queued insert: a concurrent
    duplicate raises IntegrityError and the SAVEPOINT is rolled back — the
    outer session is NOT poisoned (only the savepoint failed). The happy path
    releases the savepoint cleanly. ``sqlstate`` selects unique (23505) vs a
    non-unique integrity error (e.g. 23503 FK violation).
    """

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> None:
        self._session.savepoint_started += 1

    async def __aexit__(self, exc_type: Any, exc: Any, _tb: Any) -> None:
        if self._session._sqlstate is not None:
            self._session.savepoint_rolled_back = True
            raise IntegrityError(
                "INSERT ... conventions_cache",
                {},
                _FakeOrig(self._session._sqlstate),
            )
        self._session.savepoint_committed = True


class _FakeSession:
    def __init__(self, *, sqlstate: str | None = None) -> None:
        # None = happy path; "23505" = unique duplicate; "23503" = FK violation.
        self._sqlstate = sqlstate
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
        if self._sqlstate is not None:
            self.poisoned = True
            raise IntegrityError(
                "INSERT ... conventions_cache", {}, _FakeOrig(self._sqlstate)
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
    # task-create session: the UNIQUE IntegrityError is contained to a
    # savepoint, the session is not poisoned, and no full rollback undoes the
    # outer task-create transaction.
    session = _FakeSession(sqlstate="23505")
    svc = ConventionsService(session=cast("Any", session))

    await svc._cache_put(uuid4(), "deadbeef", _mapping(), "ok")

    assert not session.poisoned
    assert not session.full_rollback_called
    assert session.savepoint_rolled_back is True


@pytest.mark.asyncio
async def test_cache_put_happy_path_adds_and_releases_savepoint() -> None:
    session = _FakeSession(sqlstate=None)
    svc = ConventionsService(session=cast("Any", session))

    await svc._cache_put(uuid4(), "deadbeef", _mapping(), "ok")

    assert len(session.added) == 1
    assert session.savepoint_committed is True
    assert not session.poisoned
    assert not session.full_rollback_called


@pytest.mark.asyncio
async def test_cache_put_reraises_non_unique_integrity_error() -> None:
    # A non-unique integrity error (FK / NOT NULL / check) is a real bug, not a
    # benign concurrent duplicate — it must surface, not be silently swallowed
    # as "concurrent put" (#130).
    session = _FakeSession(sqlstate="23503")
    svc = ConventionsService(session=cast("Any", session))

    with pytest.raises(IntegrityError):
        await svc._cache_put(uuid4(), "deadbeef", _mapping(), "ok")

    # The savepoint was rolled back (only the failed insert), never a full
    # session rollback — the outer task-create transaction stays usable.
    assert session.savepoint_rolled_back is True
    assert not session.full_rollback_called
