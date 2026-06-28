"""Recover from a concurrent auto-create race on the channel slug's UNIQUE
constraint instead of crashing the caller with an ``IntegrityError``.

Isolate the insert in a savepoint; on a unique-conflict ``IntegrityError``
re-fetch the winner's row. A conflict that did NOT produce a row is re-raised.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.messaging import MessagingService
from sqlalchemy.exc import IntegrityError

_SLUG = "backend-cell"
_LOOKUPS_BEFORE_AND_AFTER_RACE = 2


def _bind(svc: object, name: str, value: object) -> Any:
    """Stub `name` on `svc` without tripping mypy's method-assign check.
    Returns the value (typed ``Any``) so the caller can keep a reference for
    assertions — ``object.__setattr__`` does not narrow the attribute type, so
    assert on the returned local, not ``svc.<name>``."""
    object.__setattr__(svc, name, value)
    return value


def _integrity_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO channels ...",
        {},
        Exception("duplicate key value violates unique constraint channels_slug_key"),
    )


def _svc(*, flush_side_effect: Any = None) -> tuple[MessagingService, AsyncMock]:
    session = AsyncMock()
    session.add = MagicMock()
    if flush_side_effect is not None:
        session.flush = AsyncMock(side_effect=flush_side_effect)
    else:
        session.flush = AsyncMock()
    # ``begin_nested`` returns an async context manager (savepoint). The default
    # AsyncMock magic-method config makes ``async with`` work; ``__aexit__``
    # returns falsy so an exception raised in the body propagates (mirroring
    # the real savepoint, which rolls back and re-raises).
    session.begin_nested = MagicMock(return_value=AsyncMock())
    svc = MessagingService(session)
    return svc, session


@pytest.mark.asyncio
async def test_race_lost_refetches_existing_channel() -> None:
    """Concurrent auto-create loser: flush raises IntegrityError, the method
    re-fetches the winner's channel and returns it (no crash)."""
    existing = MagicMock(name="existing-channel", slug=_SLUG)
    svc, session = _svc(flush_side_effect=_integrity_error())
    get_channel_by_slug = _bind(
        svc, "get_channel_by_slug", AsyncMock(side_effect=[None, existing])
    )

    result = await svc.get_or_create_channel_by_slug(_SLUG)

    assert result is existing
    # Savepoint isolated the failed insert; re-fetch was the recovery.
    session.begin_nested.assert_called_once()
    assert get_channel_by_slug.await_count == _LOOKUPS_BEFORE_AND_AFTER_RACE


@pytest.mark.asyncio
async def test_race_lost_but_re_fetch_empty_reraises() -> None:
    """If the conflict did NOT produce a row on re-fetch (a real failure, not a
    race), the IntegrityError is re-raised — never masked as a silent None."""
    svc, _session = _svc(flush_side_effect=_integrity_error())
    _bind(svc, "get_channel_by_slug", AsyncMock(side_effect=[None, None]))

    with pytest.raises(IntegrityError):
        await svc.get_or_create_channel_by_slug(_SLUG)


@pytest.mark.asyncio
async def test_normal_auto_create_unaffected_by_savepoint() -> None:
    """No race: the insert flushes cleanly inside the savepoint and the
    newly-created channel is returned (regression guard — the savepoint must
    not break the happy path)."""
    svc, session = _svc()  # flush succeeds
    get_channel_by_slug = _bind(
        svc, "get_channel_by_slug", AsyncMock(side_effect=[None])
    )  # not present, then never re-called

    result = await svc.get_or_create_channel_by_slug(_SLUG)

    assert result is not None
    assert result.slug == _SLUG
    session.begin_nested.assert_called_once()
    session.add.assert_called_once()
    assert session.flush.await_count == 1
    assert get_channel_by_slug.await_count == 1  # no recovery re-fetch
