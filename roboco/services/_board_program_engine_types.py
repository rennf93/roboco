"""Structural type for the composed BoardProgramEngine, TYPE_CHECKING-only.

``BoardProgramEngine`` is composed from mixins (``roboco/services/board_programs.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so a call into a sibling mixin, or into BoardProgramEngine's own
members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``BoardProgramEngineSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the
other classes (``BoardProgramEngineSurface`` what ``BoardProgramEngine`` itself does),
with each method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_board_program_engine_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import BoardProgramCycleTable


class BoardProgramCyclesSurface(Protocol):
    """``BoardProgramCycles``'s members that other classes reach."""

    async def _cycle_for_exploration(
        self, key: str, exploration_task_id: UUID
    ) -> BoardProgramCycleTable | None: ...
    async def _latest_cycle(self, key: str) -> BoardProgramCycleTable | None: ...


class BoardProgramDecisionsSurface(Protocol):
    """``BoardProgramDecisions``'s members that other classes reach."""

    async def _dedup_state(self, key: str) -> tuple[bool, datetime | None]: ...


class BoardProgramEngineSelf(
    BoardProgramCyclesSurface, BoardProgramDecisionsSurface, Protocol
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and BoardProgramEngine's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
