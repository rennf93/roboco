"""Structural type for the composed WorkSessionService, TYPE_CHECKING-only.

``WorkSessionService`` is composed from mixins (``roboco/services/work_session.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so a call into a sibling mixin, or into WorkSessionService's own
members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``WorkSessionServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the
other classes (``WorkSessionSurface`` what ``WorkSessionService`` itself does), with
each method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_work_session_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import WorkSessionTable


class WorkSessionSurface(Protocol):
    """``WorkSessionService``'s members that other classes reach."""

    async def get(self, session_id: UUID) -> WorkSessionTable | None: ...


class WorkSessionServiceSelf(WorkSessionSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and WorkSessionService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
