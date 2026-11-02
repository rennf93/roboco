"""Structural type for the composed MetricsService, TYPE_CHECKING-only.

``MetricsService`` is composed from mixins (``roboco/services/metrics.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
MetricsService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``MetricsServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``MetricsSurface`` what ``MetricsService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_metrics_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.base import Team
    from roboco.models.metrics import BlockerMetrics


class MetricsFlowSurface(Protocol):
    """``MetricsFlow``'s members that other classes reach."""

    async def _completed_reworked_counts(
        self, since: datetime, team: Team | None
    ) -> tuple[int, int]: ...


class MetricsProgressSignalsSurface(Protocol):
    """``MetricsProgressSignals``'s members that other classes reach."""

    async def _task_progress_signal_times(
        self, task_ids: set[str]
    ) -> dict[str, list[datetime]]: ...


class MetricsSurface(Protocol):
    """``MetricsService``'s members that other classes reach."""

    async def get_blocker_metrics(self) -> BlockerMetrics: ...


class MetricsTaskDetailSurface(Protocol):
    """``MetricsTaskDetail``'s members that other classes reach."""

    async def _tokens_cost_for(
        self, *, agent_slug: str | None, team: Team | None, since: datetime
    ) -> tuple[int, float]: ...


class MetricsServiceSelf(
    MetricsFlowSurface,
    MetricsProgressSignalsSurface,
    MetricsSurface,
    MetricsTaskDetailSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and MetricsService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
