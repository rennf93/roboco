"""Structural type for the composed JournalService, TYPE_CHECKING-only.

``JournalService`` is composed from mixins (``roboco/services/journal.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
JournalService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``JournalServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``JournalSurface`` what ``JournalService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_journal_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.journal import Journal, JournalEntry, JournalEntryCreate


class JournalEntriesSurface(Protocol):
    """``JournalEntries``'s members that other classes reach."""

    async def create_entry(
        self, entry_create: JournalEntryCreate
    ) -> JournalEntry | None: ...
    async def get_entry(self, entry_id: UUID) -> JournalEntry | None: ...


class JournalSurface(Protocol):
    """``JournalService``'s members that other classes reach."""

    async def _commit_unless_inside_caller_savepoint(self) -> None: ...
    async def _get_optimal_service(self) -> Any: ...
    async def get_agent_slug(self, agent_id: UUID) -> str | None: ...
    async def get_journal_by_agent(self, agent_id: UUID) -> Journal | None: ...
    async def get_or_create_journal(self, agent_id: UUID) -> Journal: ...


class JournalServiceSelf(JournalEntriesSurface, JournalSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and JournalService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _optimal_service: Any
