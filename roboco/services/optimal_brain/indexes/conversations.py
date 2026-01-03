"""
Conversations Index Plugin

Handles indexing and searching extracted conversation messages.
"""

from typing import Any
from uuid import UUID

from roboco.models.optimal import IndexConversationParams, IndexType
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin


class ConversationsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching conversation messages.

    Handles:
    - Extracted messages from agent streams
    - Session discussions
    - Channel conversations
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.CONVERSATIONS

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for conversation message."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "conversation",
            "channel_id": str(kwargs.get("channel_id", "")),
            "session_id": str(kwargs.get("session_id", "")),
            "agent_id": str(kwargs.get("agent_id", "")),
            "task_id": str(kwargs.get("task_id")) if kwargs.get("task_id") else "none",
            "message_type": kwargs.get("message_type", "unknown"),
        }

    def build_source_uri(
        self,
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Build source URI for conversation."""
        del doc_id  # Unused - URI built from session/agent
        session_id = kwargs.get("session_id", "unknown")
        agent_id = kwargs.get("agent_id", "unknown")
        return f"roboco://conversations/{session_id}-{agent_id}"

    async def index_message(self, params: IndexConversationParams) -> None:
        """
        Index a conversation message.

        Args:
            params: IndexConversationParams containing message details
        """
        await self.ingest(
            content=params.content,
            doc_id=f"{params.session_id}-{params.agent_id}"[:50],
            channel_id=params.channel_id,
            session_id=params.session_id,
            agent_id=params.agent_id,
            task_id=params.task_id,
            message_type=params.message_type,
        )

    async def search_by_channel(
        self,
        query: str,
        channel_id: UUID,
        top_k: int = 5,
    ) -> list:
        """Search conversations in a specific channel."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"channel_id": str(channel_id)},
        )
        return outcome.results

    async def search_by_session(
        self,
        query: str,
        session_id: UUID,
        top_k: int = 5,
    ) -> list:
        """Search conversations in a specific session."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"session_id": str(session_id)},
        )
        return outcome.results
