"""The trails bridge's state for the panel, and the CEO's retry of a stuck
queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select, update

from roboco.config import settings
from roboco.db.tables import EntireTrailOutboxTable, EntireTrailTable
from roboco.services.entire_credentials import get_entire_credentials_service
from roboco.services.entire_trails.cli import cli_available

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class EntireTrailsStatus:
    """Whether the bridge can run, and what its outbox holds."""

    enabled: bool
    has_token: bool
    cli_available: bool
    open_trails: int
    pending: int
    dead: int
    last_error: str | None


async def trails_status(session: AsyncSession) -> EntireTrailsStatus:
    counts = await session.execute(
        select(EntireTrailOutboxTable.dead, func.count()).group_by(
            EntireTrailOutboxTable.dead
        )
    )
    by_dead = {bool(dead): int(count) for dead, count in counts.all()}
    last_error = await session.execute(
        select(EntireTrailOutboxTable.last_error)
        .where(EntireTrailOutboxTable.last_error.is_not(None))
        .order_by(EntireTrailOutboxTable.dead.desc(), EntireTrailOutboxTable.id)
        .limit(1)
    )
    open_trails = await session.execute(
        select(func.count()).where(EntireTrailTable.closed_at.is_(None))
    )
    return EntireTrailsStatus(
        enabled=settings.entire_trails_enabled,
        has_token=await get_entire_credentials_service(session).has_token(),
        cli_available=cli_available(),
        open_trails=int(open_trails.scalar_one()),
        pending=by_dead.get(False, 0),
        dead=by_dead.get(True, 0),
        last_error=last_error.scalar_one_or_none(),
    )


async def retry_dead(session: AsyncSession) -> int:
    """Put every dead row back in its queue with a fresh attempt budget;
    returns how many."""
    result = await session.execute(
        update(EntireTrailOutboxTable)
        .where(EntireTrailOutboxTable.dead.is_(True))
        .values(dead=False, attempts=0, next_attempt_at=datetime.now(UTC))
    )
    return int(cast("CursorResult[Any]", result).rowcount or 0)
