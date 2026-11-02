"""Structural type for the composed TelegramInboundEngine, TYPE_CHECKING-only.

``TelegramInboundEngine`` is composed from mixins
(``roboco/services/telegram_inbound.py``) whose methods were moved verbatim out of the
one class. mypy checks each mixin against its own bases only, so a call into a sibling
mixin, or into TelegramInboundEngine's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``TelegramInboundEngineSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the
other classes (``TelegramInboundSurface`` what ``TelegramInboundEngine`` itself does),
with each method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_telegram_inbound_engine_mixins.py`` fails on a stub its
surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.telegram_client import TelegramClient
    from roboco.services.telegram_credentials import TelegramCredentialsData
    from roboco.services.telegram_inbound import ParsedCallback


class TelegramApprovalsSurface(Protocol):
    """``TelegramApprovals``'s members that other classes reach."""

    async def _dispatch_approve(
        self, kind: str, id8: str, extra: str, *, notes: str | None
    ) -> tuple[bool, str]: ...


class TelegramBridgeSurface(Protocol):
    """``TelegramBridge``'s members that other classes reach."""

    async def _cmd_newtask(
        self,
        chat_id: str,
        text: str,
        creds: TelegramCredentialsData,
        client: TelegramClient,
    ) -> None: ...
    async def _dispatch_bridge_command(
        self,
        cmd: str,
        args: str,
        client: TelegramClient,
        chat_id: str,
        creds: TelegramCredentialsData,
    ) -> bool: ...
    async def _handle_bridge_callback(
        self,
        parsed: ParsedCallback,
        chat_id: str,
        origin_message_id: int | None,
        creds: TelegramCredentialsData,
        client: TelegramClient,
    ) -> None: ...


class TelegramCallbacksSurface(Protocol):
    """``TelegramCallbacks``'s members that other classes reach."""

    async def _finish_action(
        self, client: TelegramClient, origin_message_id: int | None, ok: bool, text: str
    ) -> None: ...
    def _mark_audit(
        self, kind: str, task_id: UUID, action: str, *, item_id: str = ...
    ) -> None: ...


class TelegramQueueViewsSurface(Protocol):
    """``TelegramQueueViews``'s members that other classes reach."""

    async def _render_blocked(self) -> str: ...
    async def _render_task(self, args: str) -> str: ...
    async def _resolve_task(self, id8: str) -> TaskTable | None: ...
    async def _send_queue(self, client: TelegramClient) -> None: ...


class TelegramRejectionsSurface(Protocol):
    """``TelegramRejections``'s members that other classes reach."""

    async def _dispatch_reject(
        self, kind: str, id8: str, extra: str, reason: str
    ) -> tuple[bool, str]: ...


class TelegramInboundEngineSelf(
    TelegramApprovalsSurface,
    TelegramBridgeSurface,
    TelegramCallbacksSurface,
    TelegramQueueViewsSurface,
    TelegramRejectionsSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and TelegramInboundEngine's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _injected_client: Any
