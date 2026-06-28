"""F055: ``get_or_create_channel_by_slug`` must recover from a concurrent
auto-create race on the channel slug's UNIQUE constraint instead of crashing
the caller with an ``IntegrityError``.

Two concurrent callers (e.g. two Main-PM group-create requests hitting the
groups route) both miss the lookup, both auto-create the same seed channel,
and the loser's ``flush`` raises ``IntegrityError`` on ``channels.slug``
unique. With no handling that propagates as a 500 to whichever caller lost the
race, even though the channel they wanted now exists. The fix: isolate the
insert in a savepoint, and on a unique-conflict ``IntegrityError`` re-fetch
the now-existing channel (the winner's row) and return it. A conflict that
did NOT produce a row on re-fetch is re-raised (don't mask a real failure).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.messaging import MessagingService
from sqlalchemy.exc import IntegrityError

_SLUG = "backend-cell"
_LOOKUPS_BEFORE_AND_AFTER_RACE = 2


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
    svc.get_channel_by_slug = AsyncMock(side_effect=[None, existing])

    result = await svc.get_or_create_channel_by_slug(_SLUG)

    assert result is existing
    # Savepoint isolated the failed insert; re-fetch was the recovery.
    session.begin_nested.assert_called_once()
    assert svc.get_channel_by_slug.await_count == _LOOKUPS_BEFORE_AND_AFTER_RACE


@pytest.mark.asyncio
async def test_race_lost_but_re_fetch_empty_reraises() -> None:
    """If the conflict did NOT produce a row on re-fetch (a real failure, not a
    race), the IntegrityError is re-raised — never masked as a silent None."""
    svc, _session = _svc(flush_side_effect=_integrity_error())
    svc.get_channel_by_slug = AsyncMock(side_effect=[None, None])

    with pytest.raises(IntegrityError):
        await svc.get_or_create_channel_by_slug(_SLUG)


@pytest.mark.asyncio
async def test_normal_auto_create_unaffected_by_savepoint() -> None:
    """No race: the insert flushes cleanly inside the savepoint and the
    newly-created channel is returned (regression guard — the savepoint must
    not break the happy path)."""
    svc, session = _svc()  # flush succeeds
    svc.get_channel_by_slug = AsyncMock(
        side_effect=[None]
    )  # not present, then never re-called

    result = await svc.get_or_create_channel_by_slug(_SLUG)

    assert result is not None
    assert result.slug == _SLUG
    session.begin_nested.assert_called_once()
    session.add.assert_called_once()
    assert session.flush.await_count == 1
    assert svc.get_channel_by_slug.await_count == 1  # no recovery re-fetch
