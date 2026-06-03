"""
Prompter Service

Human-facing conversational AI service backed by Anthropic Claude.
Persists conversation threads and message history in the database so
that context is maintained across requests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import PromptConversationTable, PromptMessageTable
from roboco.services.base import BaseService, NotFoundError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# System prompt used for every Prompter conversation
# ---------------------------------------------------------------------------

PROMPTER_SYSTEM_PROMPT = (
    "You are a helpful assistant embedded in the RoboCo platform. "
    "You help users understand the status of projects, answer questions "
    "about the AI agents and their work, and assist with general queries. "
    "Be concise, accurate, and professional."
)

# ---------------------------------------------------------------------------
# Available models (returned by list_models)
# ---------------------------------------------------------------------------

_AVAILABLE_MODELS: list[str] = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]


class PromptService(BaseService):
    """
    Service for the human-facing Prompter feature.

    Manages conversation threads and message history, and streams
    responses from the Anthropic API using the full conversation context.
    """

    service_name: ClassVar[str] = "prompter"

    # ------------------------------------------------------------------
    # Model catalogue
    # ------------------------------------------------------------------

    async def list_models(self) -> list[str]:
        """Return the list of available Anthropic model identifiers."""
        return list(_AVAILABLE_MODELS)

    # ------------------------------------------------------------------
    # Conversation CRUD
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        agent_id: str,
        title: str | None = None,
    ) -> PromptConversationTable:
        """Create a new conversation thread for *agent_id*."""
        conversation = PromptConversationTable(agent_id=agent_id, title=title)
        self.session.add(conversation)
        await self.session.flush()
        await self.session.refresh(conversation)
        self.log.info(
            "Created prompter conversation",
            conversation_id=str(conversation.id),
            agent_id=agent_id,
        )
        return conversation

    async def get_conversation(self, conversation_id: UUID) -> PromptConversationTable:
        """
        Return the conversation with *conversation_id*.

        Raises :class:`~roboco.services.base.NotFoundError` when no row exists.
        """
        result = await self.session.execute(
            select(PromptConversationTable).where(
                PromptConversationTable.id == conversation_id
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise NotFoundError("PromptConversation", resource_id=str(conversation_id))
        return conversation

    async def list_conversations(self, agent_id: str) -> list[PromptConversationTable]:
        """Return all conversations for *agent_id*, newest first."""
        result = await self.session.execute(
            select(PromptConversationTable)
            .where(PromptConversationTable.agent_id == agent_id)
            .order_by(PromptConversationTable.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete_conversation(self, conversation_id: UUID) -> None:
        """
        Delete the conversation and all its messages (cascade on FK).

        Raises :class:`~roboco.services.base.NotFoundError` when no row exists.
        """
        conversation = await self.get_conversation(conversation_id)
        await self.session.delete(conversation)
        await self.session.flush()
        self.log.info(
            "Deleted prompter conversation",
            conversation_id=str(conversation_id),
        )

    # ------------------------------------------------------------------
    # Streaming message
    # ------------------------------------------------------------------

    async def stream_message(
        self,
        conversation_id: UUID,
        user_content: str,
        model: str = "claude-3-5-sonnet-20241022",
    ) -> AsyncIterator[str]:
        """
        Stream an assistant reply for *user_content* in *conversation_id*.

        This method is an async generator.  Iterate it with ``async for``::

            async for delta in service.stream_message(conv_id, "Hello"):
                print(delta, end="", flush=True)

        Steps performed on first iteration:

        1. Validate the conversation exists (raises ``NotFoundError`` if not).
        2. Persist the user ``PromptMessageTable`` row.
        3. Reload full message history and call the Anthropic streaming API
           with :data:`PROMPTER_SYSTEM_PROMPT` as the system prompt.
        4. Yield each text delta as it arrives.
        5. After the stream ends, persist the complete assistant response.
        """
        from anthropic import AsyncAnthropic

        # 1. Validate conversation
        await self.get_conversation(conversation_id)

        # 2. Persist user message
        user_msg = PromptMessageTable(
            conversation_id=conversation_id,
            role="user",
            content=user_content,
        )
        self.session.add(user_msg)
        await self.session.flush()

        # 3. Load full message history (ordered by created_at asc)
        history_result = await self.session.execute(
            select(PromptMessageTable)
            .where(PromptMessageTable.conversation_id == conversation_id)
            .order_by(PromptMessageTable.created_at)
        )
        history = list(history_result.scalars().all())
        messages = [{"role": msg.role, "content": msg.content} for msg in history]

        # 4. Stream from Anthropic, yielding text deltas
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        full_response: list[str] = []

        async with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=PROMPTER_SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            async for text_delta in stream.text_stream:
                full_response.append(text_delta)
                yield text_delta

        # 5. Persist complete assistant response
        assistant_content = "".join(full_response)
        assistant_msg = PromptMessageTable(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_content,
        )
        self.session.add(assistant_msg)
        await self.session.flush()

        self.log.info(
            "Streamed prompter message",
            conversation_id=str(conversation_id),
            model=model,
            response_chars=len(assistant_content),
        )


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def get_prompt_service(db: AsyncSession) -> PromptService:
    """Factory function — returns a :class:`PromptService` bound to *db*."""
    return PromptService(db)
