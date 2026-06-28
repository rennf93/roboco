"""``create_session`` must not orphan an ACTIVE session under concurrent posts.

Lock the group row (``SELECT ... FOR UPDATE``) and re-read
``active_session_id`` under the lock before creating, so concurrent callers
serialize per group and the loser reuses the winner's session.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.db.tables import SessionTable
from roboco.models.base import SessionStatus
from roboco.models.messaging import SessionCreateRequest
from roboco.services.messaging import MessagingService
from sqlalchemy.dialects import postgresql

_GROUP_ID = MagicMock(name="group-id")
_WINNER_SESSION_ID = MagicMock(name="winner-session-id")


def _bind(svc: object, name: str, value: object) -> Any:
    """Stub `name` on `svc` without tripping mypy's method-assign check.
    Returns the value (typed ``Any``) so the caller can keep a reference for
    assertions — ``object.__setattr__`` does not narrow the attribute type, so
    assert on the returned local, not ``svc.<name>``."""
    object.__setattr__(svc, name, value)
    return value


@pytest.mark.asyncio
async def test_lock_group_emits_for_update() -> None:
    """``_lock_group`` must issue ``SELECT ... FOR UPDATE`` (the row lock that
    serializes concurrent session creation per group)."""
    session = AsyncMock()
    captured: list[Any] = []
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = MagicMock(active_session_id=None)

    async def _exec(stmt: Any) -> Any:
        captured.append(stmt)
        return result_mock

    session.execute = AsyncMock(side_effect=_exec)
    svc = MessagingService(session)

    await svc._lock_group(_GROUP_ID)

    sql = str(
        captured[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_create_session_race_loser_reuses_winner_under_lock() -> None:
    """Concurrent posts: caller A wins the race and links its session while
    caller B is between the check and the create. Caller B locks the group,
    re-reads ``active_session_id`` (now A's session), and reuses it — no second
    ACTIVE session is created (no orphan)."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = MessagingService(session)

    winner = MagicMock(name="winner-session", status=SessionStatus.ACTIVE)
    _bind(svc, "get_group", AsyncMock(return_value=MagicMock(active_session_id=None)))
    # Under the lock, the group now reflects the winner's link.
    lock_group = _bind(
        svc,
        "_lock_group",
        AsyncMock(return_value=MagicMock(active_session_id=_WINNER_SESSION_ID)),
    )
    get_session = _bind(svc, "get_session", AsyncMock(return_value=winner))

    result = await svc.create_session(SessionCreateRequest(group_id=_GROUP_ID))

    assert result is winner
    lock_group.assert_awaited_once()
    get_session.assert_awaited_once()
    session.add.assert_not_called()  # no orphaning INSERT
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_session_creates_when_no_active_under_lock() -> None:
    """No race: under the lock there is still no active session, so create a
    new ACTIVE session and link it on the group (regression guard — the lock
    must not break the happy path)."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = MessagingService(session)

    locked_group = MagicMock(active_session_id=None)
    _bind(svc, "get_group", AsyncMock(return_value=MagicMock(active_session_id=None)))
    lock_group = _bind(svc, "_lock_group", AsyncMock(return_value=locked_group))
    get_session = _bind(svc, "get_session", AsyncMock())  # NOT called (no active id)

    result = await svc.create_session(SessionCreateRequest(group_id=_GROUP_ID))

    assert isinstance(result, SessionTable)
    assert result.status == SessionStatus.ACTIVE
    lock_group.assert_awaited_once()
    get_session.assert_not_awaited()
    session.add.assert_called_once()
    assert session.flush.await_count >= 1
