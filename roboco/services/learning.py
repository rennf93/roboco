"""
Learning Propagation Service

Handles cross-agent learning by:
- Immediately indexing new learnings for searchability
- Notifying similar-role agents of shareable learnings
- Tracking learning usage for relevance tuning
- Managing learning visibility and scope
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast
from uuid import UUID

import structlog

from roboco.foundation.identity import Role as _Role
from roboco.models.optimal import IndexType, SearchResult

# Human / human-driven roles are never agent learning recipients: the CEO is the
# human operator, and the prompter (intake) + secretary act only under direct CEO
# command, so a knowledge-share ping to them is inbox noise, not an agent signal.
# Resolved from the foundation enum at import time so a test that patches the
# models.base AgentRole alias can't break this constant.
_HUMAN_ONLY_ROLES = (_Role.CEO, _Role.PROMPTER, _Role.SECRETARY)

logger = structlog.get_logger()


class LearningScope(Enum):
    """Scope of a learning's visibility."""

    PERSONAL = "personal"  # Only visible to the author
    TEAM = "team"  # Visible to same-role agents
    CELL = "cell"  # Visible to entire cell
    ORG = "org"  # Visible to entire organization


class LearningType(Enum):
    """Type of learning."""

    SOLUTION = "solution"  # How to solve a problem
    PATTERN = "pattern"  # A useful code/workflow pattern
    GOTCHA = "gotcha"  # A pitfall to avoid
    INSIGHT = "insight"  # A useful understanding
    REVIEW_FEEDBACK = "review_feedback"  # From code review


@dataclass
class Learning:
    """A shareable learning from an agent."""

    learning_id: str
    agent_id: UUID
    agent_role: str
    content: str
    learning_type: LearningType
    scope: LearningScope
    tags: list[str] = field(default_factory=list)
    task_id: UUID | None = None
    source_file: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    usage_count: int = 0
    helpful_count: int = 0


@dataclass
class LearningNotification:
    """Notification about a new learning for an agent."""

    notification_id: str
    learning_id: str
    target_agent_id: UUID
    learning_summary: str
    reason: str  # Why this agent should see it
    created_at: str
    acknowledged: bool = False


@dataclass
class RecordLearningParams:
    """Parameters for recording a learning."""

    agent_id: UUID
    agent_role: str
    content: str
    learning_type: LearningType | str
    scope: LearningScope | str = LearningScope.TEAM
    tags: list[str] = field(default_factory=list)
    task_id: UUID | None = None
    source_file: str | None = None


class LearningPropagationService:
    """
    Service for propagating learnings across agents.

    Responsibilities:
    1. Index learnings immediately for searchability
    2. Determine which agents should be notified
    3. Track learning usage and helpfulness
    4. Manage learning lifecycle
    """

    def __init__(self) -> None:
        """Initialize LearningPropagationService."""
        self._optimal_service: Any = None
        self._notification_queue: list[LearningNotification] = []

    async def initialize(self, optimal_service: Any) -> None:
        """
        Initialize with OptimalService reference.

        Args:
            optimal_service: The OptimalService instance
        """
        self._optimal_service = optimal_service
        logger.info("LearningPropagationService initialized")

    async def record_learning(self, params: RecordLearningParams) -> Learning:
        """
        Record a new learning and propagate it.

        Args:
            params: RecordLearningParams containing:
                - agent_id: ID of the agent recording the learning
                - agent_role: Role of the agent (developer, qa, pm)
                - content: The learning content
                - learning_type: Type of learning
                - scope: Visibility scope
                - tags: Optional tags for categorization
                - task_id: Optional associated task
                - source_file: Optional source file reference

        Returns:
            The created Learning object
        """
        if not self._optimal_service:
            raise RuntimeError("LearningPropagationService not initialized")

        # Normalize enum values
        learning_type = params.learning_type
        scope = params.scope
        if isinstance(learning_type, str):
            learning_type = LearningType(learning_type)
        if isinstance(scope, str):
            scope = LearningScope(scope)

        # Generate learning ID
        import hashlib

        content_hash = hashlib.md5(
            params.content.encode(), usedforsecurity=False
        ).hexdigest()[:12]
        learning_id = f"lrn-{content_hash}"

        # Create learning object
        learning = Learning(
            learning_id=learning_id,
            agent_id=params.agent_id,
            agent_role=params.agent_role,
            content=params.content,
            learning_type=learning_type,
            scope=scope,
            tags=params.tags,
            task_id=params.task_id,
            source_file=params.source_file,
        )

        # 1. Index immediately for searchability
        await self._index_learning(learning)

        # 2. Determine who should be notified
        if scope != LearningScope.PERSONAL:
            await self._create_notifications(learning)

        logger.info(
            "Learning recorded",
            learning_id=learning_id,
            agent_id=str(params.agent_id),
            scope=scope.value,
            type=learning_type.value,
        )

        return learning

    async def _index_learning(self, learning: Learning) -> None:
        """Index a learning for searchability."""
        from roboco.services.optimal_brain.indexes.learnings import (
            RecordLearningParams as LearningParams,
        )

        params = LearningParams(
            content=learning.content,
            category=learning.learning_type.value,
            agent_role=learning.agent_role,
            team=None,  # Could be derived from agent role
            shareable=learning.scope != LearningScope.PERSONAL,
            tags=learning.tags,
        )
        await self._optimal_service.record_learning(params)

    async def _create_notifications(self, learning: Learning) -> None:
        """Create notifications for agents who should see this learning."""
        from roboco.db.base import get_db_context
        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        try:
            async with get_db_context() as db:
                agents = await self._fetch_notify_agents(db, learning)
                if not agents:
                    logger.debug(
                        "No agents to notify for learning",
                        learning_id=learning.learning_id,
                    )
                    return

                summary, subject, reason = self._notification_text(learning)
                self._enqueue_in_memory(learning, agents, summary, reason)
                notification_ids = await self._persist_notifications(
                    db, learning, agents, summary, subject
                )

                delivery_service = get_notification_delivery_service(db)
                for nid in notification_ids:
                    await delivery_service.deliver(nid)
                await db.commit()

                logger.info(
                    "Created learning notifications",
                    learning_id=learning.learning_id,
                    agents_notified=len(agents),
                )
        except Exception as e:
            logger.warning(
                "Failed to create learning notifications",
                learning_id=learning.learning_id,
                error=str(e),
            )

    async def _fetch_notify_agents(self, db: Any, learning: Learning) -> Any:
        """Query agents to notify based on scope; never author, never human role."""
        from sqlalchemy import select

        from roboco.db.tables import AgentTable
        from roboco.models.base import AgentRole

        query = (
            select(AgentTable)
            .where(AgentTable.id != learning.agent_id)
            .where(AgentTable.role.notin_(_HUMAN_ONLY_ROLES))
        )
        if learning.scope == LearningScope.TEAM:
            try:
                role_enum = AgentRole(learning.agent_role.upper())
            except ValueError:
                role_enum = None
            if role_enum is not None:
                query = query.where(AgentTable.role == role_enum)
        # CELL scope: TODO filter by team field; ORG scope: notify all agents.
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    def _notification_text(
        learning: Learning,
    ) -> tuple[str, str, str]:
        """Build (summary, subject, reason) for a learning notification."""
        max_summary_len = 200
        summary = (
            learning.content[:max_summary_len] + "..."
            if len(learning.content) > max_summary_len
            else learning.content
        )
        subject = f"New Learning: {learning.learning_type.value}"
        reason = f"New {learning.learning_type.value} from {learning.agent_role}"
        return summary, subject, reason

    def _enqueue_in_memory(
        self,
        learning: Learning,
        agents: Any,
        summary: str,
        reason: str,
    ) -> None:
        """Append in-memory LearningNotification rows (read by get_pending)."""
        from datetime import UTC, datetime
        from uuid import uuid4

        for agent in agents:
            self._notification_queue.append(
                LearningNotification(
                    notification_id=f"lrn-notif-{uuid4().hex[:8]}",
                    learning_id=learning.learning_id,
                    target_agent_id=UUID(str(agent.id)),
                    learning_summary=summary,
                    reason=reason,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )

    async def _persist_notifications(
        self,
        db: Any,
        learning: Learning,
        agents: Any,
        summary: str,
        subject: str,
    ) -> list[UUID]:
        """Bulk-insert NotificationTable rows; returns their ids for delivery."""
        from uuid import uuid4

        from sqlalchemy import insert

        from roboco.db.tables import NotificationTable
        from roboco.foundation.policy.communications import ACK_REQUIRED_BY_TYPE
        from roboco.models import NotificationPriority, NotificationType

        # One bulk INSERT — replaces N sequential _create_notification calls
        # each opening their own session/transaction (pool pressure).
        # id set explicitly so delivery can address each row by UUID.
        # This bypasses NotificationService's dedup guards (_duplicate_unacked_exists
        # + the delivery-side coalesce) entirely — safe only because KNOWLEDGE_SHARE
        # is exempt from both (ACK_REQUIRED_BY_TYPE[KNOWLEDGE_SHARE]=False: one-shot,
        # not ack-required). Reclassifying KNOWLEDGE_SHARE as ack-required must
        # revisit this path or dedup silently stops applying to it.
        notification_ids = [uuid4() for _ in agents]
        rows = [
            {
                "id": nid,
                "type": NotificationType.KNOWLEDGE_SHARE,
                "priority": NotificationPriority.NORMAL,
                "from_agent": learning.agent_id,
                "to_agents": [agent.id],
                "subject": subject,
                "body": summary,
                "requires_ack": ACK_REQUIRED_BY_TYPE[NotificationType.KNOWLEDGE_SHARE],
            }
            for nid, agent in zip(notification_ids, agents, strict=True)
        ]
        await db.execute(insert(NotificationTable), rows)
        return notification_ids

    async def get_learnings_for_agent(
        self,
        agent_id: UUID,
        agent_role: str,
        _scope: str | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        """
        Get learnings visible to an agent.

        Args:
            agent_id: ID of the requesting agent
            agent_role: Role of the agent
            scope: Optional scope filter
            limit: Maximum results

        Returns:
            List of matching learnings
        """
        if not self._optimal_service:
            raise RuntimeError("LearningPropagationService not initialized")

        # Build query based on role
        query = f"{agent_role} learnings"

        results = await self._optimal_service.search_learnings(
            query=query,
            top_k=limit,
        )

        # Filter by scope visibility
        filtered = []
        for result in results:
            result_scope = result.metadata.get("scope", "org")
            result_role = result.metadata.get("agent_role", "")
            result_agent = result.metadata.get("agent_id", "")

            # Check visibility
            if result_scope == "personal" and result_agent != str(agent_id):
                continue
            if result_scope == "team" and result_role != agent_role:
                continue
            # cell and org scope are visible to all

            filtered.append(result)

        return filtered[:limit]

    async def mark_learning_helpful(
        self,
        learning_id: str,
        agent_id: UUID,
        helpful: bool = True,
    ) -> None:
        """
        Record that a learning was helpful (or not).

        Used for relevance tuning.

        Args:
            learning_id: ID of the learning
            agent_id: ID of the agent providing feedback
            helpful: Whether the learning was helpful
        """
        # In a full implementation, this would:
        # 1. Update the learning's helpfulness score
        # 2. Adjust ranking weight
        # 3. Possibly trigger re-indexing

        logger.info(
            "Learning feedback recorded",
            learning_id=learning_id,
            agent_id=str(agent_id),
            helpful=helpful,
        )

    async def mark_learning_used(
        self,
        learning_id: str,
        agent_id: UUID,
        context: str | None = None,
    ) -> None:
        """
        Record that a learning was used.

        Args:
            learning_id: ID of the learning
            agent_id: ID of the agent that used it
            context: Optional context of how it was used
        """
        # In a full implementation, this would:
        # 1. Increment usage count
        # 2. Record usage context for analysis

        logger.info(
            "Learning used",
            learning_id=learning_id,
            agent_id=str(agent_id),
            has_context=context is not None,
        )

    async def get_pending_notifications(
        self,
        agent_id: UUID,
    ) -> list[LearningNotification]:
        """
        Get unacknowledged learning notifications for an agent.

        Args:
            agent_id: ID of the agent

        Returns:
            List of pending notifications
        """
        # In a full implementation, this would query the DB
        # For now, return from memory queue
        return [
            n
            for n in self._notification_queue
            if n.target_agent_id == agent_id and not n.acknowledged
        ]

    async def acknowledge_notification(
        self,
        notification_id: str,
        agent_id: UUID,
    ) -> bool:
        """
        Mark a notification as acknowledged.

        Args:
            notification_id: ID of the notification
            agent_id: ID of the acknowledging agent

        Returns:
            True if notification was found and acknowledged
        """
        for notification in self._notification_queue:
            if (
                notification.notification_id == notification_id
                and notification.target_agent_id == agent_id
            ):
                notification.acknowledged = True
                return True
        return False

    async def get_learning_stats(
        self,
        _agent_id: UUID | None = None,
        _scope: LearningScope | None = None,
    ) -> dict[str, Any]:
        """
        Get statistics about learnings.

        Args:
            agent_id: Optional filter by agent
            scope: Optional filter by scope

        Returns:
            Statistics dictionary
        """
        # In a full implementation, this would aggregate from DB
        return {
            "total_learnings": 0,
            "by_type": {
                "solution": 0,
                "pattern": 0,
                "gotcha": 0,
                "insight": 0,
                "review_feedback": 0,
            },
            "by_scope": {
                "personal": 0,
                "team": 0,
                "cell": 0,
                "org": 0,
            },
            "most_helpful": [],
            "most_used": [],
        }

    async def search_similar_learnings(
        self,
        content: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """
        Search for similar existing learnings.

        Useful for checking if a learning already exists
        before recording a duplicate.

        Args:
            content: Content to search for
            top_k: Maximum results

        Returns:
            List of similar learnings
        """
        if not self._optimal_service:
            raise RuntimeError("LearningPropagationService not initialized")

        return cast(
            "list[SearchResult]",
            await self._optimal_service.search(
                query=content,
                index_types=[IndexType.LEARNINGS],
                top_k=top_k,
            ),
        )


class _LearningServiceHolder:
    """Holder for singleton LearningPropagationService instance."""

    instance: LearningPropagationService | None = None


async def get_learning_service() -> LearningPropagationService:
    """Get or create the LearningPropagationService instance."""
    if _LearningServiceHolder.instance is None:
        _LearningServiceHolder.instance = LearningPropagationService()
    return _LearningServiceHolder.instance
