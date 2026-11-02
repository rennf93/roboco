"""Structural type for the composed NotificationDeliveryService, TYPE_CHECKING-only.

``NotificationDeliveryService`` is composed from mixins
(``roboco/services/notification_delivery.py``) whose methods were moved verbatim out of
the one class. mypy checks each mixin against its own bases only, so a call into a
sibling mixin, or into NotificationDeliveryService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of
``NotificationDeliveryServiceSelf``, which declares that cross-class surface:
``FooSurface`` holds what ``Foo`` offers the other classes
(``NotificationDeliverySurface`` what ``NotificationDeliveryService`` itself does), with
each method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_notification_delivery_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import AgentTable, NotificationTable
    from roboco.services.uptime import UptimeLedger


class NotificationDeliverySurface(Protocol):
    """``NotificationDeliveryService``'s members that other classes reach."""

    async def _get_agent_by_id(self, agent_id: UUID) -> AgentTable | None: ...
    async def _get_agent_by_slug(self, slug: str) -> AgentTable | None: ...
    async def _persist_and_deliver(
        self, notification: NotificationTable, *, bypass_purpose_dedup: bool = ...
    ) -> bool: ...
    async def get_notification(
        self, notification_id: UUID
    ) -> NotificationTable | None: ...


class NotificationHandoffsSurface(Protocol):
    """``NotificationHandoffs``'s members that other classes reach."""

    async def _get_ceo_agent(self) -> AgentTable | None: ...


class NotificationReescalationSurface(Protocol):
    """``NotificationReescalation``'s members that other classes reach."""

    async def _maybe_reescalate(
        self, n: NotificationTable, now: datetime, ledger: UptimeLedger | None = ...
    ) -> None: ...


class NotificationSweepsSurface(Protocol):
    """``NotificationSweeps``'s members that other classes reach."""

    def _log_expired_notification(self, n: NotificationTable) -> None: ...
    def _log_permanently_unacked(self, n: NotificationTable) -> None: ...


class NotificationDeliveryServiceSelf(
    NotificationDeliverySurface,
    NotificationHandoffsSurface,
    NotificationReescalationSurface,
    NotificationSweepsSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``: BaseService's
    and NotificationDeliveryService's instance attributes here, each class's members on
    its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
