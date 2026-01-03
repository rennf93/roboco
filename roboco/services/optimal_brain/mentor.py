"""
Mentor Service

Personalized AI mentor for agents. Unlike the generic RAG query endpoint,
the mentor is agent-aware: it knows the agent's role, team, and past
experiences from their journal. It adapts explanations and suggestions
to the agent's specific context.

Key differentiators from /rag/query:
- Agent-aware: Fetches agent profile (role, team, capabilities)
- Personal context: Searches agent's own journals for related experiences
- Role-specific advice: Developer gets code-focused answers, QA gets testing focus
- Adaptive follow-ups: Suggestions based on agent role and topic
- Learning tracking: Remembers what agent has asked across conversations
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from roboco.config import settings
from roboco.models.base import AgentRole
from roboco.models.optimal import (
    IndexType,
    MentorConversation,
    MentorResponse,
    SearchResult,
)

logger = structlog.get_logger()

# Conversation TTL in seconds (1 hour)
CONVERSATION_TTL = 3600

# Max chars per citation content
MAX_CONTENT_CHARS = 800

# Max sources to use in synthesis
MAX_SOURCES = 8

# Max journal entries to include as personal context
MAX_JOURNAL_CONTEXT = 3


@dataclass
class AgentProfile:
    """Agent profile for personalized mentoring."""

    agent_id: str
    slug: str
    role: AgentRole
    team: str | None
    capabilities: list[str]


# Base instruction that all role prompts include
_BASE_INSTRUCTION = (
    "You are a senior technical mentor helping an AI agent complete their work. "
    "Based on the knowledge base context, provide thorough, actionable guidance.\n\n"
    "Your response MUST:\n"
    "- Give specific, actionable advice (not vague suggestions)\n"
    "- Include code examples or step-by-step instructions when relevant\n"
    "- Reference specific standards, decisions, or past learnings from context\n"
    "- Warn about common pitfalls or mistakes others have made\n"
    "- Be comprehensive - the agent depends on your guidance to work correctly\n\n"
    "Do NOT give generic advice. Do NOT use <think> tags."
)

# Role-specific system prompts
ROLE_PROMPTS: dict[AgentRole, str] = {
    AgentRole.DEVELOPER: (
        f"{_BASE_INSTRUCTION}\n\n"
        "You are advising a SOFTWARE DEVELOPER. Your guidance must include:\n"
        "- Exact code patterns and examples they should use\n"
        "- Implementation steps in order of execution\n"
        "- Error handling approaches for this specific case\n"
        "- Testing considerations for the code they'll write\n"
        "- References to coding standards that apply\n"
        "- Warnings about bugs or issues others encountered with similar code"
    ),
    AgentRole.QA: (
        f"{_BASE_INSTRUCTION}\n\n"
        "You are advising a QA ENGINEER. Your guidance must include:\n"
        "- Specific test cases they should write (happy path + edge cases)\n"
        "- What to verify and expected outcomes\n"
        "- Common bugs to look for in this area\n"
        "- Acceptance criteria checklist\n"
        "- Integration points that need testing\n"
        "- Past issues found in similar features"
    ),
    AgentRole.CELL_PM: (
        f"{_BASE_INSTRUCTION}\n\n"
        "You are advising a CELL PM. Your guidance must include:\n"
        "- How to break down this work into specific tasks\n"
        "- Dependencies between tasks and blocking risks\n"
        "- Which team members should handle which parts\n"
        "- Acceptance criteria for each task\n"
        "- Past decisions that affect this work\n"
        "- Coordination points with other cells"
    ),
    AgentRole.MAIN_PM: (
        f"{_BASE_INSTRUCTION}\n\n"
        "You are advising the MAIN PM. Your guidance must include:\n"
        "- Cross-team coordination requirements\n"
        "- Resource allocation considerations\n"
        "- Risk assessment and mitigation strategies\n"
        "- Timeline dependencies across cells\n"
        "- Escalation points and decision gates\n"
        "- Strategic alignment with project goals"
    ),
    AgentRole.PRODUCT_OWNER: (
        f"{_BASE_INSTRUCTION}\n\n"
        "You are advising the PRODUCT OWNER. Your guidance must include:\n"
        "- How this affects user experience and value\n"
        "- Requirements clarity and acceptance criteria\n"
        "- Technical trade-offs in plain language\n"
        "- Priority considerations vs other features\n"
        "- Stakeholder communication points\n"
        "- Past decisions that set precedent"
    ),
    AgentRole.AUDITOR: (
        f"{_BASE_INSTRUCTION}\n\n"
        "You are advising the AUDITOR. Your guidance must include:\n"
        "- Compliance requirements that apply\n"
        "- Quality metrics to measure\n"
        "- Process adherence checkpoints\n"
        "- Documentation requirements\n"
        "- Past violations or issues to watch for\n"
        "- Standards that must be verified"
    ),
}

# Role-specific follow-up suggestions
ROLE_FOLLOWUPS: dict[AgentRole, list[str]] = {
    AgentRole.DEVELOPER: [
        "What are common pitfalls to avoid here?",
        "Can you show me a code example?",
        "How should I test this?",
        "What's the performance impact?",
    ],
    AgentRole.QA: [
        "What edge cases should I test?",
        "What's the acceptance criteria?",
        "How do I verify this works correctly?",
        "What are the risk areas?",
    ],
    AgentRole.CELL_PM: [
        "How should I break this down into tasks?",
        "What are the dependencies?",
        "How long should this take?",
        "Who should I involve?",
    ],
    AgentRole.MAIN_PM: [
        "How does this affect other teams?",
        "What are the resource implications?",
        "What's the priority relative to other work?",
        "What risks should I communicate?",
    ],
}

DEFAULT_FOLLOWUPS = [
    "Can you explain this in more detail?",
    "What are the alternatives?",
    "How does this fit with our standards?",
]


class MentorService:
    """
    Personalized AI mentor for agents.

    Unlike the generic RAG query, the mentor:
    - Knows the agent's role, team, and capabilities
    - Searches the agent's own journals for related past experiences
    - Adapts explanations to the agent's level and focus area
    - Generates role-specific follow-up suggestions
    - Tracks learning across conversations
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        """Initialize MentorService."""
        self._redis = redis_client
        self._memory_store: dict[str, MentorConversation] = {}
        self._optimal_service: Any = None

    async def initialize(self, optimal_service: Any) -> None:
        """Initialize with OptimalService reference."""
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

    async def _get_agent_profile(self, agent_id: str) -> AgentProfile | None:
        """
        Fetch agent profile from database.

        Returns role, team, and capabilities for personalized mentoring.
        """
        try:
            from roboco.db import get_db_context
            from roboco.services.repositories.query_helpers import get_agent_by_slug

            async with get_db_context() as db:
                # Try by slug first (most common case from API headers)
                agent_row = await get_agent_by_slug(db, agent_id)

                if not agent_row:
                    # Try by UUID
                    from roboco.services.repositories import resolve_agent_uuid

                    resolved_id = await resolve_agent_uuid(db, agent_id)
                    if resolved_id:
                        from sqlalchemy import select

                        from roboco.db.tables import AgentTable

                        result = await db.execute(
                            select(AgentTable).where(AgentTable.id == resolved_id)
                        )
                        agent_row = result.scalar_one_or_none()

                if agent_row:
                    return AgentProfile(
                        agent_id=str(agent_row.id),
                        slug=agent_row.slug,
                        role=AgentRole(agent_row.role),
                        team=agent_row.team,
                        capabilities=agent_row.capabilities or [],
                    )

        except Exception as e:
            logger.warning("Failed to fetch agent profile", agent_id=agent_id, error=e)

        return None

    async def _get_agent_journal_context(
        self, agent_id: str, question: str
    ) -> list[dict[str, Any]]:
        """
        Search agent's own journals for related past experiences.

        This gives the mentor personal context about what the agent
        has struggled with or learned before.
        """
        try:
            from roboco.db import get_db_context
            from roboco.services.journal import JournalService
            from roboco.services.repositories import resolve_agent_uuid

            async with get_db_context() as db:
                resolved_id = await resolve_agent_uuid(db, agent_id)
                if not resolved_id:
                    return []

                journal_service = JournalService(db)
                entries = await journal_service.search_entries(
                    agent_id=resolved_id,
                    query=question,
                    top_k=MAX_JOURNAL_CONTEXT,
                )

                return [
                    {
                        "type": entry.type.value,
                        "title": entry.title,
                        "content": entry.content[:500],  # Truncate for context
                        "created_at": (
                            entry.created_at.isoformat() if entry.created_at else None
                        ),
                    }
                    for entry in entries
                ]

        except Exception as e:
            logger.warning(
                "Failed to search agent journals",
                agent_id=agent_id,
                error=str(e),
            )
            return []

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
        agent_role: AgentRole | None,
    ) -> list[str]:
        """Generate role-specific follow-up suggestions."""
        followups: list[str] = []
        max_followups = 3

        # Get role-specific suggestions first
        if agent_role and agent_role in ROLE_FOLLOWUPS:
            role_suggestions = ROLE_FOLLOWUPS[agent_role]
            # Pick suggestions that seem relevant to the topic
            for suggestion in role_suggestions:
                if len(followups) >= max_followups:
                    break
                followups.append(suggestion)

        # Add source-type based suggestions
        source_types = {s.index_type for s in sources}
        if len(followups) < max_followups and IndexType.STANDARDS in source_types:
            followups.append("What are the exceptions to this rule?")
        if len(followups) < max_followups and IndexType.DECISIONS in source_types:
            followups.append("What alternatives were considered?")
        if len(followups) < max_followups and IndexType.JOURNALS in source_types:
            followups.append("What problems did others encounter?")

        # Fill with defaults if needed
        for default in DEFAULT_FOLLOWUPS:
            if len(followups) >= max_followups:
                break
            if default not in followups:
                followups.append(default)

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
        Ask the mentor a question with personalized, agent-aware response.

        Unlike /rag/query, this:
        1. Fetches the agent's profile (role, team)
        2. Searches the agent's own journals for related context
        3. Builds a role-specific prompt
        4. Generates personalized follow-ups
        """
        if not self._optimal_service:
            raise RuntimeError("MentorService not initialized")

        # Fetch agent profile for personalization
        agent_profile = await self._get_agent_profile(agent_id)
        agent_role = agent_profile.role if agent_profile else None

        logger.info(
            "Mentor ask starting",
            question=question[:50],
            agent_id=agent_id,
            agent_role=agent_role.value if agent_role else "unknown",
        )

        # Search agent's own journals for related past experiences
        journal_context = await self._get_agent_journal_context(agent_id, question)
        if journal_context:
            logger.info(
                "Found agent journal context",
                entries=len(journal_context),
            )

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
        search_stats: dict[str, int] = {}
        search_errors: dict[str, str] = {}
        for index_type in index_types:
            try:
                results = await self._search_index(index_type, enhanced_query, top_k)
                search_stats[index_type.value] = len(results)
                all_results.extend(results)
            except Exception as e:
                search_stats[index_type.value] = -1
                search_errors[index_type.value] = str(e)
                logger.warning(
                    "Failed to search index for mentor",
                    index_type=index_type.value,
                    error=str(e),
                )

        logger.info(
            "Mentor search complete",
            total_results=len(all_results),
            by_index=search_stats,
        )

        # Sort by relevance and deduplicate
        all_results.sort(key=lambda r: r.score, reverse=True)
        seen_sources: set[str] = set()
        deduplicated: list[SearchResult] = []
        for r in all_results:
            if r.source not in seen_sources:
                seen_sources.add(r.source)
                deduplicated.append(r)
        top_results = deduplicated[: top_k * 2]

        # Generate personalized answer
        answer = await self._synthesize_answer(
            question=question,
            sources=top_results,
            conversation_context=context_str,
            agent_profile=agent_profile,
            journal_context=journal_context,
        )

        # Generate role-specific follow-ups
        followups = self._generate_followups(question, answer, top_results, agent_role)

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
            search_stats=search_stats,
            search_errors=search_errors,
            # Personalization context
            agent_role=agent_profile.role.value if agent_profile else None,
            agent_team=agent_profile.team if agent_profile else None,
            journal_entries_used=len(journal_context),
        )

    def _get_indexes_for_domain(self, domain: str | None) -> list[IndexType]:
        """Get relevant index types for a domain."""
        if domain == "coding":
            return [
                IndexType.STANDARDS,
                IndexType.DOCUMENTATION,
                IndexType.REVIEWS,
                IndexType.LEARNINGS,
            ]
        if domain == "security":
            return [
                IndexType.STANDARDS,
                IndexType.DOCUMENTATION,
                IndexType.DECISIONS,
                IndexType.LEARNINGS,
            ]
        if domain == "workflow":
            return [
                IndexType.STANDARDS,
                IndexType.DOCUMENTATION,
                IndexType.DECISIONS,
                IndexType.JOURNALS,
            ]
        # Default: search all relevant indexes
        return [
            IndexType.STANDARDS,
            IndexType.DOCUMENTATION,
            IndexType.DECISIONS,
            IndexType.JOURNALS,
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
        from roboco.services.optimal import get_optimal_service

        # Get fresh service reference to avoid stale state issues
        service = await get_optimal_service()
        context = QueryContext(index_types=[index_type])
        results = await service.search(
            query=query,
            context=context,
            top_k=top_k,
        )
        return list(results)

    def _build_kb_context(self, sources: list[SearchResult]) -> str:
        """Build knowledge base context string from sources."""
        if not sources:
            return ""

        type_labels = {
            IndexType.STANDARDS: "Standards & Guidelines",
            IndexType.DECISIONS: "Past Decisions",
            IndexType.LEARNINGS: "Team Learnings",
            IndexType.JOURNALS: "Agent Journals",
            IndexType.REVIEWS: "Code Reviews",
            IndexType.DOCUMENTATION: "Documentation",
            IndexType.ERRORS: "Error Patterns",
            IndexType.CONVERSATIONS: "Conversations",
        }

        by_type: dict[IndexType, list[SearchResult]] = {}
        for source in sources[:MAX_SOURCES]:
            if source.index_type not in by_type:
                by_type[source.index_type] = []
            by_type[source.index_type].append(source)

        context_parts = []
        for index_type, results in by_type.items():
            label = type_labels.get(index_type, index_type.value)
            context_parts.append(f"## {label}")
            for r in results[:3]:
                context_parts.append(f"[Source: {r.source}]")
                content = (
                    r.content[:MAX_CONTENT_CHARS] + "..."
                    if len(r.content) > MAX_CONTENT_CHARS
                    else r.content
                )
                context_parts.append(content)
                context_parts.append("")

        return "\n".join(context_parts)

    def _build_user_prompt(
        self,
        question: str,
        sources: list[SearchResult],
        conversation_context: str,
        agent_profile: AgentProfile | None,
        journal_context: list[dict[str, Any]],
    ) -> str:
        """Build the user prompt with all context."""
        parts: list[str] = []

        # Add agent context
        if agent_profile:
            parts.append(f"Agent: {agent_profile.slug} ({agent_profile.role.value})")
            if agent_profile.team:
                parts.append(f"Team: {agent_profile.team}")
            parts.append("")

        # Add agent's personal journal context
        if journal_context:
            parts.append("## Your Past Related Experiences")
            for entry in journal_context:
                entry_type = entry.get("type", "unknown")
                title = entry.get("title", "Untitled")
                content = entry.get("content", "")
                parts.append(f"[{entry_type}] {title}")
                parts.append(content[:300])
                parts.append("")

        # Add conversation context
        if conversation_context:
            parts.append(f"## Previous Conversation\n{conversation_context}\n")

        # Add knowledge base context
        kb_context = self._build_kb_context(sources)
        if kb_context:
            parts.append(f"## Knowledge Base\n{kb_context}")

        parts.append(f"\nQuestion: {question}")
        return "\n".join(parts)

    async def _synthesize_answer(
        self,
        question: str,
        sources: list[SearchResult],
        conversation_context: str,
        agent_profile: AgentProfile | None,
        journal_context: list[dict[str, Any]],
    ) -> str:
        """Synthesize a personalized answer using LLM."""
        import asyncio

        if not sources and not journal_context:
            return (
                "I couldn't find relevant information in the knowledge base. "
                "Try rephrasing your question or asking about a different topic."
            )

        # Get role-specific system prompt
        base_prompt = (
            "Answer the question based on the knowledge base context provided below. "
            "Synthesize a clear, thorough answer. Do NOT just copy text - explain in "
            "your own words. If the context doesn't fully answer the question, say "
            "what you can based on what's available."
        )
        if agent_profile and agent_profile.role in ROLE_PROMPTS:
            system_prompt = ROLE_PROMPTS[agent_profile.role]
        else:
            system_prompt = base_prompt

        # Build user prompt
        user_prompt = self._build_user_prompt(
            question, sources, conversation_context, agent_profile, journal_context
        )

        # Call LLM
        try:
            async with asyncio.timeout(120.0):
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{settings.local_llm_base_url}/chat/completions",
                        json={
                            "model": settings.local_llm_model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "max_tokens": 4096,
                            "temperature": 0.5,
                            "options": {"num_ctx": 8192},
                        },
                    )

                    if response.is_success:
                        data = response.json()
                        raw_answer: str = data["choices"][0]["message"]["content"]
                        answer = self._extract_answer(raw_answer)

                        if not answer:
                            logger.warning(
                                "LLM response empty after extraction",
                                model=settings.local_llm_model,
                                original_length=len(raw_answer),
                            )
                            return self._fallback_answer(sources, agent_profile)

                        return answer
                    else:
                        logger.warning(
                            "LLM call failed in mentor",
                            status=response.status_code,
                            error=response.text[:200],
                        )
                        return self._fallback_answer(sources, agent_profile)

        except (TimeoutError, httpx.TimeoutException):
            logger.warning("LLM call timed out in mentor (60s)")
            return self._fallback_answer(sources, agent_profile)
        except Exception as e:
            logger.warning("LLM call failed in mentor", error=str(e))
            return self._fallback_answer(sources, agent_profile)

    def _extract_answer(self, text: str) -> str:
        """Extract answer from LLM response, handling think tags."""
        # First try: get content outside think tags
        outside = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        outside = re.sub(r"</think>", "", outside).strip()
        if outside:
            return outside

        # If empty, extract content FROM inside think tags
        inside_match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        if inside_match:
            return inside_match.group(1).strip()

        return text.strip()

    def _fallback_answer(
        self,
        sources: list[SearchResult],
        agent_profile: AgentProfile | None = None,
    ) -> str:
        """Generate a fallback answer when LLM is unavailable."""
        if not sources:
            return (
                "I couldn't find relevant information in the knowledge base. "
                "Try rephrasing your question or check if the topic is documented."
            )

        # Role-specific intro
        role_intros = {
            AgentRole.DEVELOPER: (
                "Here's what I found in the knowledge base for your implementation. "
                "Review these sources for guidance:"
            ),
            AgentRole.QA: (
                "Here's what I found in the knowledge base for testing. "
                "Review these sources for test cases and verification approaches:"
            ),
            AgentRole.CELL_PM: (
                "Here's what I found in the knowledge base for task planning. "
                "Review these sources for breakdown and coordination:"
            ),
            AgentRole.MAIN_PM: (
                "Here's what I found in the knowledge base for coordination. "
                "Review these sources for cross-team implications:"
            ),
            AgentRole.PRODUCT_OWNER: (
                "Here's what I found in the knowledge base for requirements. "
                "Review these sources for product decisions:"
            ),
            AgentRole.AUDITOR: (
                "Here's what I found in the knowledge base for compliance. "
                "Review these sources for standards and metrics:"
            ),
        }

        intro = (
            "Here's what I found in the knowledge base. "
            "Review these sources for detailed guidance:"
        )
        if agent_profile and agent_profile.role:
            intro = role_intros.get(agent_profile.role, intro)

        parts = [f"{intro}\n"]
        for source in sources[:5]:
            source_type = source.index_type.value if source.index_type else "unknown"
            parts.append(f"**[{source_type}] {source.source}**")
            parts.append(source.content[:800])
            parts.append("")

        parts.append(
            "\n*Note: LLM synthesis unavailable. "
            "The above are direct extracts from the knowledge base.*"
        )
        return "\n".join(parts)

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
