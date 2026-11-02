"""Structural type for the composed DashboardService, TYPE_CHECKING-only.

``DashboardService`` is composed from mixins (``roboco/services/dashboard.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
DashboardService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``DashboardServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``DashboardSurface`` what ``DashboardService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_dashboard_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.dashboard import DashboardStorage
    from roboco.services.metrics import MetricsService


class DashboardFlagsSurface(Protocol):
    """``DashboardFlags``'s members that other classes reach."""

    def count_unresolved_flags(self, severity: str) -> int: ...


class DashboardReportsSurface(Protocol):
    """``DashboardReports``'s members that other classes reach."""

    def get_last_report_time(self) -> datetime | None: ...


class DashboardSurface(Protocol):
    """``DashboardService``'s members that other classes reach."""

    @property
    def metrics(self) -> MetricsService: ...


class DashboardServiceSelf(
    DashboardFlagsSurface, DashboardReportsSurface, DashboardSurface, Protocol
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and DashboardService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _metrics: MetricsService | None
    _storage: DashboardStorage
