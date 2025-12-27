"""
Mentor Service

Conversational RAG system that allows agents to have a dialogue with the
organizational knowledge base. Maintains conversation context for follow-ups
and synthesizes answers from multiple index types.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import structlog

from roboco.models.optimal import (
    IndexType,
    MentorConversation,
    MentorResponse,
    SearchResult,
)

logger = structlog.get_logger()

# Conversation TTL in seconds (1 hour)
CONVERSATION_TTL = 3600


class MentorService:
    """
    Conversational RAG service for agent mentoring.

    Enables agents to:
    - Ask questions and get synthesized answers
    - Have follow-up conversations with context
    - Get suggestions for related questions

    Searches across multiple knowledge indexes:
    - STANDARDS: Coding/security/workflow rules
    - DECISIONS: Past architectural decisions
    - JOURNALS: Agent learnings and reflections
    - CODE: Codebase patterns
    - LEARNINGS: Cross-agent knowledge
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        """
        Initialize MentorService.

        Args:
            redis_client: Optional Redis client for conversation persistence.
                         If None, conversations are stored in memory.
        """
        self._redis = redis_client
        self._memory_store: dict[str, MentorConversation] = {}
        self._optimal_service: Any = None

    async def initialize(self, optimal_service: Any) -> None:
        """
        Initialize with OptimalService reference.

        Args:
            optimal_service: The OptimalService instance for searching
        """
        self._optimal_service = optimal_service
        logger.info("MentorService initialized")

    def _get_conversation_key(self, conversation_id: str) -> str:
        """Build Redis key for conversation."""
        return f"mentor:conversation:{conversation_id}"

    async def _load_conversation(
        self, conversation_id: str
    ) -> MentorConversation | None:
        """Load conversation from storage."""
        if self._redis:
            key = self._get_conversation_key(conversation_id)
            data = await self._redis.get(key)
            if data:
                obj = json.loads(data)
                return MentorConversation(**obj)
            return None
        return self._memory_store.get(conversation_id)

    async def _save_conversation(self, conversation: MentorConversation) -> None:
        """Save conversation to storage."""
        if self._redis:
            key = self._get_conversation_key(conversation.conversation_id)
            data = json.dumps(
                {
                    "conversation_id": conversation.conversation_id,
                    "agent_id": str(conversation.agent_id),
                    "turns": conversation.turns,
                    "domain": conversation.domain,
                    "created_at": conversation.created_at,
                    "updated_at": conversation.updated_at,
                }
            )
            await self._redis.setex(key, CONVERSATION_TTL, data)
        else:
            self._memory_store[conversation.conversation_id] = conversation

    def _build_context_from_turns(
        self, turns: list[dict[str, Any]], max_turns: int = 5
    ) -> str:
        """Build context string from recent conversation turns."""
        recent_turns = turns[-max_turns:] if len(turns) > max_turns else turns
        context_parts = []
        for turn in recent_turns:
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            context_parts.append(f"{role.upper()}: {content}")
        return "\n".join(context_parts)

    def _generate_followups(
        self,
        _question: str,
        _answer: str,
        sources: list[SearchResult],
    ) -> list[str]:
        """Generate suggested follow-up questions."""
        followups = []
        max_followups = 3

        # Analyze sources to suggest follow-ups
        source_types = {s.index_type for s in sources}

        if IndexType.STANDARDS in source_types:
            followups.append("What are the exceptions to this rule?")
        if IndexType.DECISIONS in source_types:
            followups.append("What alternatives were considered?")
        if IndexType.CODE in source_types:
            followups.append("Can you show me an example implementation?")
        if IndexType.JOURNALS in source_types:
            followups.append("What problems did others encounter with this?")

        # Add generic follow-ups if we have room
        if len(followups) < max_followups:
            followups.append("Can you explain this in more detail?")
        if len(followups) < max_followups:
            followups.append("How does this relate to our current task?")

        return followups[:max_followups]

    async def ask(
        self,
        question: str,
        agent_id: str,
        conversation_id: str | None = None,
        domain: str | None = None,
        top_k: int = 5,
    ) -> MentorResponse:
        """
        Ask the mentor a question.

        Args:
            question: The question to ask
            agent_id: ID of the asking agent
            conversation_id: Optional ID for continuing a conversation
            domain: Optional domain filter (coding, security, workflow)
            top_k: Number of context chunks per index

        Returns:
            MentorResponse with answer, sources, and follow-up suggestions
        """
        if not self._optimal_service:
            raise RuntimeError("MentorService not initialized")

        # Load or create conversation
        conversation: MentorConversation | None = None
        if conversation_id:
            conversation = await self._load_conversation(conversation_id)

        if conversation is None:
            conversation = MentorConversation(
                conversation_id=str(uuid.uuid4()),
                agent_id=uuid.UUID(agent_id) if agent_id else uuid.uuid4(),
                turns=[],
                domain=domain,
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )

        # Build search query with conversation context
        context_str = self._build_context_from_turns(conversation.turns)
        enhanced_query = question
        if context_str:
            enhanced_query = (
                f"Previous context:\n{context_str}\n\nCurrent question: {question}"
            )

        # Determine which indexes to search based on domain
        index_types = self._get_indexes_for_domain(domain)

        # Search across relevant indexes
        all_results: list[SearchResult] = []
        for index_type in index_types:
            try:
                results = await self._search_index(index_type, enhanced_query, top_k)
                all_results.extend(results)
            except Exception as e:
                logger.warning(
                    "Failed to search index for mentor",
                    index_type=index_type.value,
                    error=str(e),
                )

        # Sort by relevance and take top results
        all_results.sort(key=lambda r: r.score, reverse=True)
        top_results = all_results[: top_k * 2]

        # Generate answer
        answer = await self._synthesize_answer(question, top_results, context_str)

        # Generate follow-up suggestions
        followups = self._generate_followups(question, answer, top_results)

        # Record this turn
        now = datetime.now(UTC).isoformat()
        conversation.turns.append(
            {
                "role": "user",
                "content": question,
                "timestamp": now,
            }
        )
        conversation.turns.append(
            {
                "role": "mentor",
                "content": answer,
                "timestamp": now,
                "sources": [s.source for s in top_results[:5]],
            }
        )
        conversation.updated_at = now

        # Save conversation
        await self._save_conversation(conversation)

        return MentorResponse(
            answer=answer,
            sources=top_results,
            conversation_id=conversation.conversation_id,
            suggested_followups=followups,
        )

    def _get_indexes_for_domain(self, domain: str | None) -> list[IndexType]:
        """Get relevant index types for a domain."""
        if domain == "coding":
            return [
                IndexType.STANDARDS,
                IndexType.CODE,
                IndexType.REVIEWS,
                IndexType.LEARNINGS,
            ]
        if domain == "security":
            return [
                IndexType.STANDARDS,
                IndexType.DECISIONS,
                IndexType.LEARNINGS,
            ]
        if domain == "workflow":
            return [
                IndexType.STANDARDS,
                IndexType.DECISIONS,
                IndexType.JOURNALS,
            ]
        # Default: search all relevant indexes
        return [
            IndexType.STANDARDS,
            IndexType.DECISIONS,
            IndexType.JOURNALS,
            IndexType.CODE,
            IndexType.LEARNINGS,
        ]

    async def _search_index(
        self,
        index_type: IndexType,
        query: str,
        top_k: int,
    ) -> list[SearchResult]:
        """Search a specific index."""
        from roboco.models.optimal import QueryContext

        context = QueryContext(index_types=[index_type])
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search(
                query=query,
                context=context,
                top_k=top_k,
            ),
        )

    async def _synthesize_answer(
        self,
        _question: str,
        sources: list[SearchResult],
        _conversation_context: str,
    ) -> str:
        """
        Synthesize an answer from search results.

        For now, this uses a simple template. In production, this would
        use an LLM to generate a coherent answer.
        """
        if not sources:
            return (
                "I couldn't find relevant information in the knowledge base. "
                "Try rephrasing your question or asking about a different topic."
            )

        # Build answer from top sources
        answer_parts = []

        # Group sources by type
        by_type: dict[IndexType, list[SearchResult]] = {}
        for source in sources[:5]:
            if source.index_type not in by_type:
                by_type[source.index_type] = []
            by_type[source.index_type].append(source)

        # Standards first
        if IndexType.STANDARDS in by_type:
            answer_parts.append("**Standards & Guidelines:**")
            for s in by_type[IndexType.STANDARDS][:2]:
                answer_parts.append(f"- {s.content[:200]}...")

        # Decisions
        if IndexType.DECISIONS in by_type:
            answer_parts.append("\n**Past Decisions:**")
            for s in by_type[IndexType.DECISIONS][:2]:
                answer_parts.append(f"- {s.content[:200]}...")

        # Learnings
        if IndexType.LEARNINGS in by_type or IndexType.JOURNALS in by_type:
            answer_parts.append("\n**Team Learnings:**")
            learnings = by_type.get(IndexType.LEARNINGS, []) + by_type.get(
                IndexType.JOURNALS, []
            )
            for s in learnings[:2]:
                answer_parts.append(f"- {s.content[:200]}...")

        # Code patterns
        if IndexType.CODE in by_type:
            answer_parts.append("\n**Code References:**")
            for s in by_type[IndexType.CODE][:2]:
                answer_parts.append(f"- See: {s.source}")

        if not answer_parts:
            first_content = sources[0].content[:500]
            return f"Based on my search, here's what I found:\n\n{first_content}"

        return "\n".join(answer_parts)

    async def get_conversation_history(
        self, conversation_id: str
    ) -> MentorConversation | None:
        """Get the full conversation history."""
        return await self._load_conversation(conversation_id)

    async def clear_conversation(self, conversation_id: str) -> bool:
        """Clear a conversation from storage."""
        if self._redis:
            key = self._get_conversation_key(conversation_id)
            await self._redis.delete(key)
            return True
        if conversation_id in self._memory_store:
            del self._memory_store[conversation_id]
            return True
        return False


class _MentorServiceHolder:
    """Holder for singleton MentorService instance."""

    instance: MentorService | None = None


async def get_mentor_service() -> MentorService:
    """Get or create the MentorService instance."""
    if _MentorServiceHolder.instance is None:
        _MentorServiceHolder.instance = MentorService()
    return _MentorServiceHolder.instance
