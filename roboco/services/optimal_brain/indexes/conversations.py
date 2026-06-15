"""
Conversations Index Plugin

Handles indexing and searching extracted conversation messages.
"""

from typing import Any
from uuid import UUID

from roboco.models.optimal import IndexConversationParams, IndexType
from roboco.services.optimal_brain.indexes.base import (
    BaseIndexPlugin,
    IngestResult,
    build_doc_source,
)


class ConversationsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching conversation messages.

    Handles:
    - Extracted messages from agent streams
    - Session discussions
    - Channel conversations
    """

    # Many messages share one source URI per session+agent, so deleting by
    # source on re-ingest would wipe earlier messages. Keep append semantics
    # for this index (the only one whose source is not 1:1 with a record).
    replace_on_reingest = False

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
    ) -> str | None:
        """Build source URI for conversation; returns None if session_id is missing."""
        del doc_id  # Unused - URI built from session/agent
        raw_session = kwargs.get("session_id")
        if raw_session is None:
            return None
        agent_id = kwargs.get("agent_id") or "unknown"
        combined = f"{raw_session}-{agent_id}"
        return build_doc_source(kind="conversations", id_=combined)

    async def index_message(self, params: IndexConversationParams) -> IngestResult:
        """
        Index a conversation message.

        Args:
            params: IndexConversationParams containing message details

        Returns:
            IngestResult so the caller can tell whether the message persisted.
        """
        return await self.ingest(
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
