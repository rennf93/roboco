"""Structural type for the composed NotificationService, TYPE_CHECKING-only.

``NotificationService`` is composed from mixins (``roboco/services/notification.py``)
whose methods were moved verbatim out of the one class. mypy checks each mixin against
its own bases only, so a call into a sibling mixin, or into NotificationService's own
members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``NotificationServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the
other classes (``NotificationSurface`` what ``NotificationService`` itself does), with
each method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_notification_service_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.notification import CreateNotificationParams


class NotificationSurface(Protocol):
    """``NotificationService``'s members that other classes reach."""

    async def _create_notification(
        self, params: CreateNotificationParams, db_session: AsyncSession | None = ...
    ) -> None: ...


class NotificationServiceSelf(NotificationSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``:
    NotificationService's instance attributes here, each class's
    members on its surface."""
