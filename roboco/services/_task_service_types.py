"""Structural type for the composed TaskService, TYPE_CHECKING-only.

``TaskService`` is composed from mixins (``roboco/services/task.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
TaskService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``TaskServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``TaskSurface`` what ``TaskService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_task_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.models.base import TaskStatus, Team
    from roboco.services.task import _ClaimFieldSnapshot
    from roboco.services.uptime import UptimeLedger


class TaskListQueriesSurface(Protocol):
    """``TaskListQueries``'s members that other classes reach."""

    def fifo_order(self, query: Any) -> Any: ...
    async def list_by_status(
        self, status: TaskStatus, team: Team | None = ...
    ) -> list[TaskTable]: ...


class TaskSurface(Protocol):
    """``TaskService``'s members that other classes reach."""

    _AGENT_NON_TERMINAL_STATUSES: ClassVar[set[TaskStatus]]
    _DEV_ACTIVE_STATUSES: ClassVar[set[TaskStatus]]
    _JOURNAL_CONTEXT_STATUSES: ClassVar[set[TaskStatus]]

    async def _claim_blocked_by_sequence(self, task: TaskTable) -> str | None: ...
    async def _load_uptime_ledger(self, now: datetime) -> UptimeLedger | None: ...
    async def unmet_dependency_ids(self, dependency_ids: list[UUID]) -> list[UUID]: ...


class TaskServiceSelf(TaskListQueriesSurface, TaskSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and TaskService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _pending_claim_snapshots: dict[UUID, _ClaimFieldSnapshot]
