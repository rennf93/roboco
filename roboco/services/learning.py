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

from roboco.models.optimal import IndexType, SearchResult

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
        from sqlalchemy import select

        from roboco.db.base import get_db_context
        from roboco.db.tables import AgentTable
        from roboco.models import NotificationPriority, NotificationType
        from roboco.models.notification import CreateNotificationParams
        from roboco.services.notification import NotificationService

        try:
            async with get_db_context() as db:
                # Build query based on scope
                query = select(AgentTable).where(AgentTable.id != learning.agent_id)

                if learning.scope == LearningScope.TEAM:
                    # Only notify agents with the same role
                    from roboco.models.base import AgentRole

                    try:
                        role_enum = AgentRole(learning.agent_role.upper())
                        query = query.where(AgentTable.role == role_enum)
                    except ValueError:
                        # Invalid role, skip role filtering
                        pass
                elif learning.scope == LearningScope.CELL:
                    # TODO: Filter by cell (team field)
                    pass
                # For ORG scope, notify all agents

                result = await db.execute(query)
                agents = result.scalars().all()

                if not agents:
                    logger.debug(
                        "No agents to notify for learning",
                        learning_id=learning.learning_id,
                    )
                    return

                # Create notifications using the notification service pattern
                notification_svc = NotificationService()

                # Get a short summary of the learning
                max_summary_len = 200
                summary = (
                    learning.content[:max_summary_len] + "..."
                    if len(learning.content) > max_summary_len
                    else learning.content
                )

                for agent in agents:
                    # Create in-memory notification for our queue
                    from datetime import UTC, datetime
                    from uuid import uuid4

                    reason = (
                        f"New {learning.learning_type.value} from {learning.agent_role}"
                    )
                    # Convert SQLAlchemy UUID to Python UUID
                    agent_uuid = UUID(str(agent.id))
                    notification = LearningNotification(
                        notification_id=f"lrn-notif-{uuid4().hex[:8]}",
                        learning_id=learning.learning_id,
                        target_agent_id=agent_uuid,
                        learning_summary=summary,
                        reason=reason,
                        created_at=datetime.now(UTC).isoformat(),
                    )
                    self._notification_queue.append(notification)

                    # Also create a formal notification in the database
                    await notification_svc._create_notification(
                        CreateNotificationParams(
                            notification_type=NotificationType.KNOWLEDGE_SHARE,
                            priority=NotificationPriority.NORMAL,
                            from_agent=str(learning.agent_id),
                            to_agents=[agent.slug],
                            subject=f"New Learning: {learning.learning_type.value}",
                            body=summary,
                        )
                    )

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
