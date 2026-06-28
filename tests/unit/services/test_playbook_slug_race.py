"""F110 — ``PlaybookService.draft`` slug TOCTOU must not 500.

``draft`` pre-checks the slug with ``_get_by_slug`` then INSERTs. Two concurrent
same-title drafts both miss the pre-check (neither sees the other's uncommitted
row), so the loser's flush hits the ``playbooks.slug`` UNIQUE constraint and
raises ``IntegrityError``. The pre-check alone cannot close the race — the DB
constraint is the authoritative guard. The fix wraps the insert in a savepoint
and converts the ``IntegrityError`` into a clean ``ConflictError`` (the same
error the pre-check raises), so the loser gets a 409, not an unhandled 500.

Playbooks are distinct curated content (unlike shared-infrastructure channels,
where the loser reuses the winner's row): two same-title drafts are two
different procedures that collided on the derived slug, so the loser must be
told to retry with a distinct title — it must NOT silently reuse the winner's
row (that would drop the loser's content).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.models.playbook import PlaybookCreate
from roboco.services.base import ConflictError
from roboco.services.playbook import PlaybookService
from sqlalchemy.exc import IntegrityError


def _integrity_error() -> IntegrityError:
    return IntegrityError(
        "INSERT INTO playbooks ...",
        {},
        Exception("duplicate key value violates unique constraint playbooks_slug_key"),
    )


def _svc(*, flush_side_effect: Any = None) -> tuple[PlaybookService, AsyncMock]:
    """Build a PlaybookService on a mock session that simulates the race.

    ``begin_nested`` returns an async context manager (the savepoint); the
    default AsyncMock magic-method config makes ``async with`` work and
    ``__aexit__`` returns falsy so an exception raised in the body propagates
    (mirroring a real savepoint, which rolls back and re-raises).
    """
    session = AsyncMock()
    session.add = MagicMock()
    if flush_side_effect is not None:
        session.flush = AsyncMock(side_effect=flush_side_effect)
    else:
        session.flush = AsyncMock()
    session.begin_nested = MagicMock(return_value=AsyncMock())
    svc = PlaybookService(session)
    return svc, session


def _create(title: str = "Retry flaky pg") -> PlaybookCreate:
    return PlaybookCreate(
        title=title,
        problem="connection resets intermittently",
        procedure="1. retry with backoff",
        tags=["backend"],
        scope="org",
    )


@pytest.mark.asyncio
async def test_draft_slug_race_raises_conflict_not_integrity_error() -> None:
    """Concurrent same-title loser: pre-check misses (None), flush raises
    IntegrityError on the UNIQUE slug — draft must convert it to a clean
    ConflictError, not let it propagate as an unhandled 500 (F110)."""
    svc, session = _svc(flush_side_effect=_integrity_error())
    # Pre-check misses the row (the race window: the other draft is uncommitted).
    object.__setattr__(svc, "_get_by_slug", AsyncMock(return_value=None))

    with pytest.raises(ConflictError):
        await svc.draft(_create(title="Colliding Title"), created_by=MagicMock())

    # The insert was isolated in a savepoint so the loser's failed insert does
    # not poison the caller's pending transaction.
    session.begin_nested.assert_called_once()


@pytest.mark.asyncio
async def test_draft_happy_path_still_inserts() -> None:
    """No race: pre-check misses, flush succeeds — the savepoint path is used
    and the row is added (regression guard for the F110 wrap)."""
    svc, session = _svc()
    object.__setattr__(svc, "_get_by_slug", AsyncMock(return_value=None))

    await svc.draft(_create(title="Clean Title"), created_by=MagicMock())

    session.add.assert_called_once()
    session.begin_nested.assert_called_once()
