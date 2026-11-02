"""Structural type for the composed A2AService, TYPE_CHECKING-only.

``A2AService`` is composed from mixins (``roboco/services/a2a.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
A2AService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``A2AServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``A2ASurface`` what ``A2AService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_a2_a_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import A2AConversationTable, A2AMessageTable
    from roboco.models.a2a import A2AChatMessage, A2AConversation


class A2AAgentCardsSurface(Protocol):
    """``A2AAgentCards``'s members that other classes reach."""

    @staticmethod
    def resolve_target_agent(metadata: dict[str, Any]) -> str | None: ...


class A2AChatDeliverySurface(Protocol):
    """``A2AChatDelivery``'s members that other classes reach."""

    async def send_chat_message(
        self,
        conversation_id: UUID,
        from_agent: str,
        content: str,
        options: dict[str, Any] | None = ...,
    ) -> A2AChatMessage: ...


class A2AConversationsSurface(Protocol):
    """``A2AConversations``'s members that other classes reach."""

    @staticmethod
    def _canonical_pair(agent_a: str, agent_b: str) -> tuple[str, str]: ...
    def _conv_to_model(self, conv: A2AConversationTable) -> A2AConversation: ...
    def _msg_to_model(self, msg: A2AMessageTable) -> A2AChatMessage: ...
    async def _resolve_slug_from_id(self, agent_id: UUID) -> str: ...
    async def get_or_create_conversation(
        self,
        agent_a: str,
        agent_b: str,
        topic: str | None = ...,
        task_id: UUID | None = ...,
    ) -> A2AConversation: ...


class A2ASendSurface(Protocol):
    """``A2ASend``'s members that other classes reach."""

    async def _ack_pending_wake_notifications(self, agent_id: UUID) -> None: ...
    async def _maybe_wake_ceo_recipient(
        self, from_slug: str, to_slug: str, task_id: str | None
    ) -> None: ...
    @staticmethod
    async def _publish_a2a_message_sent(
        msg: A2AChatMessage,
        task_id: str | None,
        from_slug: str,
        to_slug: str,
        skill: str | None,
    ) -> None: ...


class A2AServiceSelf(
    A2AAgentCardsSurface,
    A2AChatDeliverySurface,
    A2AConversationsSurface,
    A2ASendSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    A2AService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
