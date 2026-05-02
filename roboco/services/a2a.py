"""
A2A (Agent-to-Agent) Protocol Service

Provides business logic for A2A protocol operations including:
- Agent discovery and card generation
- Task lifecycle management via A2A semantics
- Message handling and routing
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import ALL_AGENTS, get_agent_skills, get_agent_team
from roboco.config import settings
from roboco.db.tables import (
    A2AConversationTable,
    A2AMessageTable,
    AgentTable,
    TaskTable,
)
from roboco.enforcement import validate_a2a_access
from roboco.events import Event, EventType, get_event_bus
from roboco.models.a2a import (
    A2AArtifact,
    A2AChatMessage,
    A2AConversation,
    A2AConversationStatus,
    A2AConversationSummary,
    A2AInboxSummary,
    A2AMessage,
    A2AMessageKind,
    A2APair,
    A2ATask,
    A2ATaskStatus,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    SecurityScheme,
    SendMessageRequest,
    TextPart,
    task_status_to_a2a_state,
)
from roboco.models.base import TaskStatus, Team
from roboco.seeds.initial_data import AGENT_UUIDS

logger = structlog.get_logger()


class A2AService:
    """
    Service layer for A2A protocol operations.

    Provides methods for:
    - Building Agent Cards for discovery
    - Converting between RoboCo tasks and A2A tasks
    - Processing A2A messages
    """

    def __init__(self, session: AsyncSession):
        """Initialize with database session."""
        self.session = session

    @staticmethod
    def get_service_endpoint() -> str:
        """Build service endpoint URL from settings.

        When the API binds to the all-interfaces address (dev default),
        we can't use it for outbound callbacks — dial loopback instead.
        `is_unspecified` covers both 0.0.0.0 and ::, and avoids a bare
        literal that trips bandit B104.
        """
        import ipaddress

        try:
            is_any_iface = ipaddress.ip_address(settings.host).is_unspecified
        except ValueError:
            is_any_iface = False
        connect_host = "127.0.0.1" if is_any_iface else settings.host
        return f"http://{connect_host}:{settings.port}"

    @staticmethod
    def build_system_agent_card() -> AgentCard:
        """
        Build the system-level Agent Card for RoboCo.

        This card represents the entire RoboCo system and is served
        at /.well-known/agent.json
        """
        return AgentCard(
            id="roboco-system",
            name="RoboCo System",
            description=(
                "RoboCo is an AI Agentic Company - a virtual organization of "
                "AI agents designed to operate as a complete software "
                "development workforce."
            ),
            provider=AgentProvider(
                organization="RoboCo",
                url="https://github.com/roboco",
            ),
            protocol_version="1.0",
            service_endpoint=f"{A2AService.get_service_endpoint()}/api/a2a",
            version=settings.app_version,
            capabilities=AgentCapabilities(
                streaming=True,
                push_notifications=False,
                state_transition_history=True,
            ),
            default_input_modes=["text/plain", "application/json"],
            default_output_modes=["text/plain", "application/json"],
            skills=[
                AgentSkill(
                    id="software-development",
                    name="Software Development",
                    description="Full-stack software development with AI agents",
                    tags=["development", "coding", "qa", "documentation"],
                ),
                AgentSkill(
                    id="task-management",
                    name="Task Management",
                    description="Create and manage development tasks",
                    tags=["tasks", "kanban", "planning"],
                ),
                AgentSkill(
                    id="code-review",
                    name="Code Review",
                    description="Review and quality assurance of code",
                    tags=["qa", "review", "testing"],
                ),
            ],
            documentation_url="https://github.com/roboco/docs",
            security_schemes={
                "bearerAuth": SecurityScheme(type="http", scheme="bearer"),
            },
            security=[{"bearerAuth": []}],
        )

    async def build_agent_card(self, agent_id: str) -> AgentCard | None:
        """
        Build an Agent Card for a specific agent.

        Args:
            agent_id: Either a UUID string or agent slug

        Returns:
            AgentCard for the agent, or None if not found
        """
        # Try to parse as UUID first
        try:
            uuid = UUID(agent_id)
            result = await self.session.execute(
                select(AgentTable).where(AgentTable.id == uuid)
            )
        except ValueError:
            # Not a UUID, try slug lookup
            result = await self.session.execute(
                select(AgentTable).where(AgentTable.slug == agent_id)
            )

        agent = result.scalar_one_or_none()
        if agent is None:
            return None

        return self._agent_to_card(agent)

    def _agent_to_card(self, agent: AgentTable) -> AgentCard:
        """Convert an AgentTable row to an AgentCard."""
        agent_id = str(agent.id)
        agent_slug = agent.slug

        # Map role to skills
        role_skills: dict[str, list[AgentSkill]] = {
            "developer": [
                AgentSkill(
                    id="coding",
                    name="Code Development",
                    description="Write and implement code",
                    tags=["development", "coding"],
                ),
                AgentSkill(
                    id="debugging",
                    name="Debugging",
                    description="Debug and fix code issues",
                    tags=["debugging", "troubleshooting"],
                ),
            ],
            "qa": [
                AgentSkill(
                    id="testing",
                    name="Testing",
                    description="Test code and verify quality",
                    tags=["qa", "testing"],
                ),
                AgentSkill(
                    id="review",
                    name="Code Review",
                    description="Review code for quality and issues",
                    tags=["qa", "review"],
                ),
            ],
            "documenter": [
                AgentSkill(
                    id="documentation",
                    name="Documentation",
                    description="Write technical documentation",
                    tags=["documentation", "writing"],
                ),
            ],
            "cell_pm": [
                AgentSkill(
                    id="coordination",
                    name="Task Coordination",
                    description="Coordinate tasks within the cell",
                    tags=["management", "coordination"],
                ),
            ],
            "main_pm": [
                AgentSkill(
                    id="planning",
                    name="Project Planning",
                    description="Plan and coordinate across cells",
                    tags=["management", "planning"],
                ),
            ],
        }

        skills = role_skills.get(agent.role, [])

        return AgentCard(
            id=agent_id,
            name=agent.name,
            description=f"{agent.name} - {agent.role} agent in RoboCo",
            provider=AgentProvider(
                organization="RoboCo",
                url="https://github.com/roboco",
            ),
            protocol_version="1.0",
            service_endpoint=f"{self.get_service_endpoint()}/api/a2a",
            version=settings.app_version,
            capabilities=AgentCapabilities(
                streaming=True,
                push_notifications=False,
                state_transition_history=True,
            ),
            default_input_modes=["text/plain", "application/json"],
            default_output_modes=["text/plain", "application/json"],
            skills=skills,
            metadata={
                "slug": agent_slug,
                "role": agent.role,
                "team": agent.team,
            },
            security_schemes={
                "bearerAuth": SecurityScheme(type="http", scheme="bearer"),
            },
            security=[{"bearerAuth": []}],
        )

    def task_to_a2a(self, task: TaskTable) -> A2ATask:
        """
        Convert a RoboCo TaskTable to A2A Task.

        This is the canonical conversion that maintains semantic
        mapping between RoboCo's internal task model and A2A.
        """
        task_id = str(task.id)

        # Get status value as string
        if hasattr(task.status, "value"):
            status_value = task.status.value
        else:
            status_value = str(task.status)

        a2a_state = task_status_to_a2a_state(status_value)

        # Build status message from dev_notes if present
        status_message = None
        if task.dev_notes:
            status_message = A2AMessage(
                role="agent",
                parts=[TextPart(text=task.dev_notes)],
                task_id=task_id,
            )

        a2a_status = A2ATaskStatus(
            state=a2a_state,
            message=status_message,
            timestamp=task.updated_at or task.created_at,
        )

        # Build artifacts from task outputs (future: populate from outputs)
        artifacts: list[A2AArtifact] = []

        # Build metadata
        metadata: dict[str, str | int] = {
            "roboco_status": status_value,
            "priority": task.priority,
            "team": str(task.team),
        }
        if task.assigned_to:
            metadata["assigned_to"] = str(task.assigned_to)
        if task.parent_task_id:
            metadata["parent_task_id"] = str(task.parent_task_id)

        return A2ATask(
            id=task_id,
            context_id=task_id,
            status=a2a_status,
            artifacts=artifacts,
            history=[],
            metadata=metadata,
        )

    async def get_task(self, task_id: str) -> A2ATask | None:
        """
        Get a task by ID and return as A2A Task.

        Args:
            task_id: Task UUID string

        Returns:
            A2ATask or None if not found
        """
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            return None

        result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == task_uuid)
        )
        task = result.scalar_one_or_none()

        if task is None:
            return None

        return self.task_to_a2a(task)

    async def list_tasks(
        self,
        page_size: int = 20,
        offset: int = 0,
        order_by: str | None = None,
    ) -> tuple[list[A2ATask], bool]:
        """
        List tasks with pagination.

        Args:
            page_size: Number of results to return
            offset: Starting offset
            order_by: Sort order ("created_at desc" or "created_at asc")

        Returns:
            Tuple of (tasks, has_more)
        """
        query = select(TaskTable)

        # Apply ordering
        if order_by == "created_at asc":
            query = query.order_by(TaskTable.created_at.asc())
        else:
            query = query.order_by(TaskTable.created_at.desc())

        # Apply pagination (fetch one extra to detect more)
        query = query.offset(offset).limit(page_size + 1)

        result = await self.session.execute(query)
        tasks = list(result.scalars().all())

        has_more = len(tasks) > page_size
        if has_more:
            tasks = tasks[:page_size]

        return [self.task_to_a2a(t) for t in tasks], has_more

    async def create_task_from_message(
        self,
        title: str,
        description: str,
        created_by: UUID,
        team: Team = Team.BACKEND,
    ) -> A2ATask:
        """
        Create a new task from an A2A message.

        Args:
            title: Task title
            description: Task description
            created_by: Agent ID creating the task
            team: Team assignment

        Returns:
            Created A2ATask
        """
        task = TaskTable(
            title=title,
            description=description,
            acceptance_criteria=["Task completed as specified"],
            status=TaskStatus.PENDING,
            priority=5,
            team=team,
            created_by=created_by,
        )
        self.session.add(task)
        await self.session.flush()
        await self.session.refresh(task)

        logger.info(
            "Created task from A2A message",
            task_id=str(task.id),
            title=title,
        )

        return self.task_to_a2a(task)

    async def cancel_task(self, task_id: str, reason: str | None = None) -> A2ATask:
        """
        Cancel a task and all non-terminal descendants.

        Args:
            task_id: Task UUID string
            reason: Optional cancellation reason

        Returns:
            Updated A2ATask

        Raises:
            ValueError: If task not found or already in terminal state
        """
        # Import here to avoid circular imports
        from roboco.services.task import TaskService

        try:
            task_uuid = UUID(task_id)
        except ValueError as e:
            raise ValueError(f"Invalid task ID: {task_id}") from e

        # Check task exists and is cancellable before using service
        result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == task_uuid)
        )
        task = result.scalar_one_or_none()

        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        # Check if cancellable
        if hasattr(task.status, "value"):
            status_value = task.status.value
        else:
            status_value = str(task.status)

        if status_value in ["completed", "cancelled"]:
            raise ValueError(f"Task already in terminal state: {status_value}")

        # Add reason to notes before cancel
        if reason:
            reason_text = f"Cancellation reason: {reason}"
            if task.dev_notes:
                task.dev_notes = f"{task.dev_notes}\n\n{reason_text}"
            else:
                task.dev_notes = reason_text
            await self.session.flush()

        # Use TaskService for consistent cancel behavior (cascades to descendants)
        task_service = TaskService(self.session)
        task = await task_service.cancel(task_uuid)

        if task is None:
            raise ValueError(f"Failed to cancel task: {task_id}")

        logger.info("Cancelled task via A2A", task_id=task_id, reason=reason)

        return self.task_to_a2a(task)

    async def discover_agents(
        self,
        role: str | None = None,
        team: str | None = None,
        skill_tag: str | None = None,
    ) -> list[AgentCard]:
        """
        Discover agents matching criteria.

        Args:
            role: Filter by agent role
            team: Filter by team
            skill_tag: Filter by skill tag (future)

        Returns:
            List of matching AgentCards
        """
        query = select(AgentTable)

        if role:
            query = query.where(AgentTable.role == role)
        if team:
            query = query.where(AgentTable.team == team)

        result = await self.session.execute(query)
        agents = result.scalars().all()

        cards = [self._agent_to_card(agent) for agent in agents]

        # Filter by skill tag if specified
        if skill_tag:
            cards = [
                card
                for card in cards
                if any(skill_tag in skill.tags for skill in card.skills)
            ]

        return cards

    # =========================================================================
    # MESSAGE ROUTING
    # =========================================================================

    @staticmethod
    def get_team_from_agent(agent_slug: str) -> Team:
        """Get Team enum from agent slug."""
        team_str = get_agent_team(agent_slug)
        team_map = {
            "backend": Team.BACKEND,
            "frontend": Team.FRONTEND,
            "ux_ui": Team.UX_UI,
        }
        return team_map.get(team_str or "", Team.BACKEND)

    @staticmethod
    def resolve_target_agent(metadata: dict[str, Any]) -> str | None:
        """
        Resolve target agent from A2A request metadata.

        Returns agent slug or None if not specified.
        """
        # Check for explicit target
        target = metadata.get("target_agent")
        if target and target in ALL_AGENTS:
            return cast("str", target)

        # Check for skill-based routing
        skill = metadata.get("skill")
        if skill:
            for agent_slug in ALL_AGENTS:
                agent_skills = get_agent_skills(agent_slug)
                skill_ids = [s.get("id", "") for s in agent_skills]
                if skill in skill_ids:
                    return agent_slug

        return None

    # =========================================================================
    # MESSAGE HANDLING
    # =========================================================================

    @staticmethod
    def extract_message_text(message: A2AMessage) -> tuple[str, str, str]:
        """Extract title, description, and full text from message parts."""
        text_parts = [p for p in message.parts if p.type == "text"]
        if not text_parts:
            return "A2A Task", "", ""

        text_part = text_parts[0]
        if not hasattr(text_part, "text"):
            return "A2A Task", "", ""

        message_text = text_part.text
        lines = message_text.split("\n", 1)
        title = lines[0][:200]
        description = lines[1] if len(lines) > 1 else message_text
        return title, description, message_text

    @staticmethod
    def update_task_with_message(task: TaskTable, message: A2AMessage) -> None:
        """Update an existing task's dev_notes with new message content."""
        text_parts = [p for p in message.parts if p.type == "text"]
        if not text_parts:
            return

        text_part = text_parts[0]
        if not hasattr(text_part, "text"):
            return

        new_text = text_part.text
        task.dev_notes = (
            f"{task.dev_notes}\n\n{new_text}" if task.dev_notes else new_text
        )

    async def resolve_creator_agent(
        self, from_agent_id: str | None
    ) -> AgentTable | None:
        """Resolve the creator agent from ID or fall back to main PM."""
        if from_agent_id and from_agent_id in ALL_AGENTS:
            from_uuid = AGENT_UUIDS.get(from_agent_id)
            if from_uuid:
                result = await self.session.execute(
                    select(AgentTable).where(AgentTable.id == UUID(from_uuid))
                )
                return result.scalar_one_or_none()

        # Fall back to main PM
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.role == "main_pm").limit(1)
        )
        return result.scalar_one_or_none()

    async def create_a2a_notification(
        self,
        request: SendMessageRequest,
    ) -> dict[str, Any]:
        """
        Create an A2A notification for peer-to-peer communication.

        Does NOT create tasks - A2A is messaging only.
        task_id is REQUIRED - A2A is communication about existing tasks.

        Returns dict with notification_id, status, and target_agent.
        """
        from roboco.services.notification import NotificationService

        message = request.message
        metadata = request.metadata or {}
        config = request.configuration

        # task_id is REQUIRED for A2A
        task_id = message.task_id
        if not task_id:
            raise ValueError("A2A requests must reference a task_id")

        from_agent = metadata.get("from_agent")
        target_agent = self.resolve_target_agent(metadata)
        skill = metadata.get("skill", "general")

        # Enforce A2A hierarchy permissions
        if from_agent and target_agent:
            from roboco.agents_config import can_a2a_direct, get_a2a_route_hint

            allowed, error_msg = can_a2a_direct(from_agent, target_agent)
            if not allowed:
                hint = get_a2a_route_hint(from_agent, target_agent)
                raise ValueError(f"{error_msg} Hint: {hint}")
        urgent_from_config = config.urgent if config else False
        urgent = urgent_from_config or metadata.get("urgent", False)

        # Extract message content
        _, _, message_text = self.extract_message_text(message)

        logger.info(
            "Creating A2A notification (fallback)",
            task_id=task_id,
            from_agent=from_agent,
            target_agent=target_agent,
            skill=skill,
            urgent=urgent,
        )

        # Create notification - orchestrator dispatcher will handle spawning
        notification_service = NotificationService()
        await notification_service.send_a2a_notification(
            task_id=task_id,
            a2a_context={
                "from_agent": from_agent or "unknown",
                "to_agent": target_agent or "",
                "skill": skill,
                "message": message_text,
                "urgent": urgent,
            },
        )

        return {
            "status": "sent",
            "target_agent": target_agent,
            "task_id": task_id,
        }

    async def update_task_from_message(
        self,
        task_id: str,
        message: A2AMessage,
        responder_agent: str | None = None,
    ) -> TaskTable:
        """
        Update an existing task with a new message (response).

        When a response is received, notifies the original requester
        and spawns them if offline (bidirectional A2A).

        Args:
            task_id: Task UUID string
            message: A2A message to append
            responder_agent: Agent sending the response (for routing back)

        Returns:
            Updated TaskTable

        Raises:
            ValueError: If task not found or invalid ID
        """
        try:
            task_uuid = UUID(task_id)
        except ValueError as e:
            raise ValueError(f"Invalid task ID: {task_id}") from e

        result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == task_uuid)
        )
        task = result.scalar_one_or_none()

        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        self.update_task_with_message(task, message)

        # Notify original requester of the response (bidirectional A2A)
        await self._notify_original_requester(task, responder_agent)

        return task

    @staticmethod
    def _lookup_requester_slug(created_by: Any) -> str | None:
        """Find the agent slug for a task creator UUID."""
        from roboco.seeds.initial_data import AGENT_UUIDS

        created_by_str = str(created_by)
        for slug, uuid_str in AGENT_UUIDS.items():
            if uuid_str == created_by_str:
                return slug
        return None

    @staticmethod
    async def _publish_a2a_response_event(
        task: TaskTable,
        created_by: Any,
        requester_slug: str,
        responder_agent: str | None,
    ) -> None:
        """Publish a TASK_ASSIGNED event to notify/spawn the requester."""
        try:
            bus = get_event_bus()
            if not bus.is_connected():
                return
            await bus.publish(
                Event(
                    type=EventType.TASK_ASSIGNED,
                    data={
                        "task_id": str(task.id),
                        "assigned_to": str(created_by),
                        "agent_slug": requester_slug,
                        "skill": "a2a_response",
                        "message": f"Response received for A2A task {task.id}",
                        "source": "a2a_response",
                        "urgent": False,
                        "from_agent": responder_agent or "agent",
                    },
                )
            )
        except Exception:
            pass  # Don't fail if event bus unavailable

    async def _notify_original_requester(
        self,
        task: TaskTable,
        responder_agent: str | None = None,
    ) -> None:
        """
        Notify the original A2A requester of a response.

        If the requester is offline, triggers spawn via event.
        This enables bidirectional A2A where both parties can be
        spawned as needed until they're both online.
        """
        dev_notes = task.dev_notes or ""
        if "A2A Request" not in dev_notes:
            return  # Not an A2A task

        created_by = task.created_by
        if not created_by:
            return

        requester_slug = self._lookup_requester_slug(created_by)
        if not requester_slug:
            return

        # Don't notify if responder is the same as requester
        if responder_agent and responder_agent == requester_slug:
            return

        await self._publish_a2a_response_event(
            task, created_by, requester_slug, responder_agent
        )

    # =========================================================================
    # PERSISTENT CONVERSATION MANAGEMENT
    # =========================================================================
    # These methods handle persistent A2A conversations stored in the database.
    # They complement the existing A2A protocol methods above.

    @staticmethod
    def _canonical_pair(agent_a: str, agent_b: str) -> tuple[str, str]:
        """Return agents in canonical order (lexically smaller first)."""
        return (agent_a, agent_b) if agent_a < agent_b else (agent_b, agent_a)

    async def get_or_create_conversation(
        self,
        agent_a: str,
        agent_b: str,
        topic: str | None = None,
        task_id: UUID | None = None,
    ) -> A2AConversation:
        """
        Get existing conversation or create new one.

        Args:
            agent_a: First agent slug
            agent_b: Second agent slug
            topic: Optional conversation topic
            task_id: Optional task to link

        Returns:
            A2AConversation model

        Raises:
            A2AAccessDeniedError: If A2A not permitted between agents
        """
        # Validate permissions
        validate_a2a_access(agent_a, agent_b)

        # Canonical ordering
        a, b = self._canonical_pair(agent_a, agent_b)

        # Try to find existing
        query = select(A2AConversationTable).where(
            A2AConversationTable.agent_a == a,
            A2AConversationTable.agent_b == b,
        )
        if topic:
            query = query.where(A2AConversationTable.topic == topic)
        else:
            query = query.where(A2AConversationTable.topic.is_(None))

        result = await self.session.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            return self._conv_to_model(existing)

        # Create new conversation
        conv = A2AConversationTable(
            agent_a=a,
            agent_b=b,
            topic=topic,
            task_id=task_id,
            status=A2AConversationStatus.ACTIVE,
        )
        self.session.add(conv)
        await self.session.flush()
        await self.session.refresh(conv)

        logger.info(
            "Created A2A conversation",
            conversation_id=str(conv.id),
            agent_a=a,
            agent_b=b,
            topic=topic,
        )

        return self._conv_to_model(conv)

    async def get_conversation(
        self,
        conversation_id: UUID,
        agent_slug: str,
    ) -> A2AConversation | None:
        """
        Get conversation by ID if agent is a participant.

        Args:
            conversation_id: Conversation UUID
            agent_slug: Agent requesting (must be participant)

        Returns:
            A2AConversation or None if not found/not authorized
        """
        result = await self.session.execute(
            select(A2AConversationTable).where(
                A2AConversationTable.id == conversation_id
            )
        )
        conv = result.scalar_one_or_none()

        if conv is None:
            return None

        # Verify agent is participant
        if agent_slug not in (conv.agent_a, conv.agent_b):
            return None

        return self._conv_to_model(conv)

    async def list_conversations(
        self,
        agent_slug: str,
        status: A2AConversationStatus | None = None,
        with_agent: str | None = None,
        task_id: UUID | None = None,
        limit: int = 50,
    ) -> list[A2AConversationSummary]:
        """
        List conversations for an agent.

        Args:
            agent_slug: Agent to list for
            status: Filter by status
            with_agent: Filter by other participant
            task_id: Filter by linked task
            limit: Max results

        Returns:
            List of conversation summaries
        """
        from sqlalchemy import or_

        query = select(A2AConversationTable).where(
            or_(
                A2AConversationTable.agent_a == agent_slug,
                A2AConversationTable.agent_b == agent_slug,
            )
        )

        if status:
            query = query.where(A2AConversationTable.status == status)

        if with_agent:
            a, b = self._canonical_pair(agent_slug, with_agent)
            query = query.where(
                A2AConversationTable.agent_a == a,
                A2AConversationTable.agent_b == b,
            )

        if task_id:
            query = query.where(A2AConversationTable.task_id == task_id)

        query = query.order_by(A2AConversationTable.updated_at.desc()).limit(limit)

        result = await self.session.execute(query)
        conversations = result.scalars().all()

        summaries = []
        for conv in conversations:
            # Get last message preview
            msg_query = (
                select(A2AMessageTable)
                .where(A2AMessageTable.conversation_id == conv.id)
                .order_by(A2AMessageTable.created_at.desc())
                .limit(1)
            )
            msg_result = await self.session.execute(msg_query)
            last_msg = msg_result.scalar_one_or_none()

            other = conv.agent_b if agent_slug == conv.agent_a else conv.agent_a
            unread = (
                conv.unread_by_a if agent_slug == conv.agent_a else conv.unread_by_b
            )

            summaries.append(
                A2AConversationSummary(
                    id=str(conv.id),
                    other_agent=other,
                    topic=conv.topic,
                    task_id=str(conv.task_id) if conv.task_id else None,
                    status=conv.status,
                    message_count=conv.message_count,
                    unread_count=unread,
                    last_message_at=conv.last_message_at,
                    last_message_preview=(last_msg.content[:100] if last_msg else None),
                )
            )

        return summaries

    async def close_conversation(
        self,
        conversation_id: UUID,
        agent_slug: str,
        resolution: str | None = None,
    ) -> None:
        """Close a conversation."""
        result = await self.session.execute(
            select(A2AConversationTable).where(
                A2AConversationTable.id == conversation_id
            )
        )
        conv = result.scalar_one_or_none()

        if conv is None:
            raise ValueError(f"Conversation not found: {conversation_id}")

        if agent_slug not in (conv.agent_a, conv.agent_b):
            raise ValueError("Not a participant in this conversation")

        conv.status = A2AConversationStatus.CLOSED
        conv.resolution = resolution
        await self.session.flush()

        logger.info(
            "Closed A2A conversation",
            conversation_id=str(conversation_id),
            by_agent=agent_slug,
        )

    async def send_chat_message(
        self,
        conversation_id: UUID,
        from_agent: str,
        content: str,
        options: dict[str, Any] | None = None,
    ) -> A2AChatMessage:
        """
        Send message in conversation.

        Args:
            conversation_id: Target conversation
            from_agent: Sender slug
            content: Message content
            options: Optional dict with message_kind, response_to_id, requires_response

        Returns:
            Created A2AChatMessage

        Raises:
            ValueError: If conversation_id is nil, conversation not found,
                or sender not participant
        """
        if conversation_id.int == 0:
            raise ValueError(
                "conversation_id must not be the nil UUID; "
                "call get_or_create_conversation() first"
            )

        from datetime import UTC, datetime

        opts = options or {}
        message_kind = opts.get("message_kind", A2AMessageKind.MESSAGE)
        response_to_id = opts.get("response_to_id")
        requires_response = opts.get("requires_response", False)

        result = await self.session.execute(
            select(A2AConversationTable).where(
                A2AConversationTable.id == conversation_id
            )
        )
        conv = result.scalar_one_or_none()

        if conv is None:
            raise ValueError(f"Conversation not found: {conversation_id}")

        if from_agent not in (conv.agent_a, conv.agent_b):
            raise ValueError("Not a participant in this conversation")

        # Create message
        msg = A2AMessageTable(
            conversation_id=conversation_id,
            from_agent=from_agent,
            content=content,
            message_kind=message_kind,
            response_to_id=response_to_id,
            requires_response=requires_response,
        )
        self.session.add(msg)

        # Update conversation stats
        conv.message_count += 1
        conv.last_message_at = datetime.now(UTC)

        # Update unread count for the OTHER agent
        if from_agent == conv.agent_a:
            conv.unread_by_b += 1
        else:
            conv.unread_by_a += 1

        await self.session.flush()
        await self.session.refresh(msg)

        logger.info(
            "Sent A2A chat message",
            conversation_id=str(conversation_id),
            message_id=str(msg.id),
            from_agent=from_agent,
        )

        return self._msg_to_model(msg)

    async def get_messages(
        self,
        conversation_id: UUID,
        agent_slug: str,
        limit: int = 100,
        before: datetime | None = None,
    ) -> list[A2AChatMessage]:
        """Get messages in conversation."""
        # Verify access
        conv_result = await self.session.execute(
            select(A2AConversationTable).where(
                A2AConversationTable.id == conversation_id
            )
        )
        conv = conv_result.scalar_one_or_none()

        if conv is None:
            return []

        if agent_slug not in (conv.agent_a, conv.agent_b):
            return []

        query = (
            select(A2AMessageTable)
            .where(A2AMessageTable.conversation_id == conversation_id)
            .order_by(A2AMessageTable.created_at.desc())
            .limit(limit)
        )

        if before:
            query = query.where(A2AMessageTable.created_at < before)

        result = await self.session.execute(query)
        messages = result.scalars().all()

        # Return in chronological order
        return [self._msg_to_model(m) for m in reversed(list(messages))]

    async def mark_read(
        self,
        conversation_id: UUID,
        agent_slug: str,
    ) -> None:
        """Mark all messages in conversation as read by agent."""
        from datetime import UTC, datetime

        result = await self.session.execute(
            select(A2AConversationTable).where(
                A2AConversationTable.id == conversation_id
            )
        )
        conv = result.scalar_one_or_none()

        if conv is None:
            return

        if agent_slug not in (conv.agent_a, conv.agent_b):
            return

        # Reset unread count
        if agent_slug == conv.agent_a:
            conv.unread_by_a = 0
        else:
            conv.unread_by_b = 0

        # Mark messages as read using SQLAlchemy update statement
        from sqlalchemy import update

        stmt = (
            update(A2AMessageTable)
            .where(A2AMessageTable.conversation_id == conversation_id)
            .where(A2AMessageTable.from_agent != agent_slug)
            .where(A2AMessageTable.read_at.is_(None))
            .values(read_at=datetime.now(UTC))
        )
        await self.session.execute(stmt)

        await self.session.flush()

    async def get_inbox_summary(self, agent_slug: str) -> A2AInboxSummary:
        """Get summary of pending A2A for agent."""
        from sqlalchemy import func, or_

        # Get conversations with unread
        conv_query = select(A2AConversationTable).where(
            or_(
                A2AConversationTable.agent_a == agent_slug,
                A2AConversationTable.agent_b == agent_slug,
            )
        )
        conv_result = await self.session.execute(conv_query)
        conversations = conv_result.scalars().all()

        total_unread = 0
        conversations_with_unread = 0

        for conv in conversations:
            unread = (
                conv.unread_by_a if agent_slug == conv.agent_a else conv.unread_by_b
            )
            if unread > 0:
                conversations_with_unread += 1
                total_unread += unread

        # Count pending responses (messages I sent that require response)
        pending_query = (
            select(func.count())
            .select_from(A2AMessageTable)
            .where(
                A2AMessageTable.from_agent == agent_slug,
                A2AMessageTable.requires_response.is_(True),
            )
        )
        pending_result = await self.session.execute(pending_query)
        pending_responses = pending_result.scalar() or 0

        # Count unanswered requests (messages to me that require response)
        unanswered_query = (
            select(func.count())
            .select_from(A2AMessageTable)
            .join(A2AConversationTable)
            .where(
                or_(
                    A2AConversationTable.agent_a == agent_slug,
                    A2AConversationTable.agent_b == agent_slug,
                ),
                A2AMessageTable.from_agent != agent_slug,
                A2AMessageTable.requires_response.is_(True),
            )
        )
        unanswered_result = await self.session.execute(unanswered_query)
        unanswered_requests = unanswered_result.scalar() or 0

        return A2AInboxSummary(
            total_unread=total_unread,
            conversations_with_unread=conversations_with_unread,
            pending_responses=pending_responses,
            unanswered_requests=unanswered_requests,
        )

    async def list_pairs(self, agent_slug: str) -> list[A2APair]:
        """List unique agent pairs for frontend display."""
        from sqlalchemy import or_

        query = (
            select(A2AConversationTable)
            .where(
                or_(
                    A2AConversationTable.agent_a == agent_slug,
                    A2AConversationTable.agent_b == agent_slug,
                )
            )
            .order_by(A2AConversationTable.updated_at.desc())
        )

        result = await self.session.execute(query)
        conversations = result.scalars().all()

        # Group by pair
        pairs: dict[tuple[str, str], A2APair] = {}
        for conv in conversations:
            pair_key = (conv.agent_a, conv.agent_b)
            if pair_key not in pairs:
                pairs[pair_key] = A2APair(
                    agent_a=conv.agent_a,
                    agent_b=conv.agent_b,
                    conversation_count=0,
                    total_unread=0,
                    last_activity=None,
                )

            pairs[pair_key].conversation_count += 1

            unread = (
                conv.unread_by_a if agent_slug == conv.agent_a else conv.unread_by_b
            )
            pairs[pair_key].total_unread += unread

            current_activity = pairs[pair_key].last_activity
            if current_activity is None or (
                conv.updated_at is not None and conv.updated_at > current_activity
            ):
                pairs[pair_key].last_activity = conv.updated_at

        return list(pairs.values())

    # =========================================================================
    # MODEL CONVERSIONS
    # =========================================================================

    def _conv_to_model(self, conv: A2AConversationTable) -> A2AConversation:
        """Convert table row to Pydantic model."""
        return A2AConversation(
            id=str(conv.id),
            agent_a=conv.agent_a,
            agent_b=conv.agent_b,
            topic=conv.topic,
            task_id=str(conv.task_id) if conv.task_id else None,
            status=conv.status,
            resolution=conv.resolution,
            message_count=conv.message_count,
            unread_by_a=conv.unread_by_a,
            unread_by_b=conv.unread_by_b,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            last_message_at=conv.last_message_at,
        )

    def _msg_to_model(self, msg: A2AMessageTable) -> A2AChatMessage:
        """Convert table row to Pydantic model."""
        return A2AChatMessage(
            id=str(msg.id),
            conversation_id=str(msg.conversation_id),
            from_agent=msg.from_agent,
            content=msg.content,
            message_kind=msg.message_kind,
            response_to_id=str(msg.response_to_id) if msg.response_to_id else None,
            requires_response=msg.requires_response,
            read_at=msg.read_at,
            created_at=msg.created_at,
            edited_at=msg.edited_at,
            edit_history=msg.edit_history or [],
        )

    # =========================================================================
    # GATEWAY (CHOREOGRAPHER + CONTENT_ACTIONS) BACKFILL
    # =========================================================================

    async def _resolve_slug_from_id(self, agent_id: UUID) -> str:
        """Look up an agent's slug from its UUID; raise ValueError if missing."""
        result = await self.session.execute(
            select(AgentTable.slug).where(AgentTable.id == agent_id)
        )
        slug = result.scalar_one_or_none()
        if not slug:
            raise ValueError(f"Agent not found for id {agent_id}")
        return str(slug)

    async def send(
        self,
        *,
        from_agent: UUID,
        to_agent: UUID | str,
        task_id: UUID,
        body: str,
        skill: str | None = None,
    ) -> A2AChatMessage:
        """Gateway adapter — send a directed A2A message between two agents.

        Recipient may be either a UUID (choreographer call shape) or a
        slug string (content_actions call shape). The sender is always a
        UUID; both ends are resolved to slugs because the
        conversation/message tables key on slug.

        Resolves to:
        1. `get_or_create_conversation(sender_slug, recipient_slug, task_id=...)`
        2. `send_chat_message(conversation.id, sender_slug, content=body, ...)`

        `skill` is recorded in message metadata so the receiver knows which
        capability is being requested.
        """
        from_slug = await self._resolve_slug_from_id(from_agent)
        to_slug = (
            await self._resolve_slug_from_id(to_agent)
            if isinstance(to_agent, UUID)
            else to_agent
        )

        conv = await self.get_or_create_conversation(
            agent_a=from_slug,
            agent_b=to_slug,
            task_id=task_id,
        )
        options: dict[str, Any] = {}
        if skill is not None:
            options["skill"] = skill
        return await self.send_chat_message(
            conversation_id=UUID(conv.id),
            from_agent=from_slug,
            content=body,
            options=options or None,
        )
