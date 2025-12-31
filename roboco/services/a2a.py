"""
A2A (Agent-to-Agent) Protocol Service

Provides business logic for A2A protocol operations including:
- Agent discovery and card generation
- Task lifecycle management via A2A semantics
- Message handling and routing
"""

from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import ALL_AGENTS, get_agent_skills, get_agent_team
from roboco.config import settings
from roboco.db.tables import AgentTable, TaskTable
from roboco.events import Event, EventType, get_event_bus
from roboco.models.a2a import (
    A2AArtifact,
    A2AMessage,
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
        """Build service endpoint URL from settings."""
        connect_host = "127.0.0.1" if settings.host == "0.0.0.0" else settings.host
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
            service_endpoint=f"{A2AService.get_service_endpoint()}/api/v1/a2a",
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
            service_endpoint=f"{self.get_service_endpoint()}/api/v1/a2a",
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

    async def route_to_agent(
        self,
        target_agent_slug: str,
        task: TaskTable,
        skill: str | None = None,
        message: str | None = None,
    ) -> None:
        """
        Route an A2A task to a specific agent.

        This publishes an event that:
        1. Notifies the agent if they're online (via WebSocket)
        2. Triggers the orchestrator to spawn them if needed
        """
        target_uuid = AGENT_UUIDS.get(target_agent_slug)
        if not target_uuid:
            return

        # Assign task to target agent
        task.assigned_to = cast("Any", UUID(target_uuid))
        await self.session.flush()

        # Publish A2A request event for routing
        try:
            bus = get_event_bus()
            if bus.is_connected():
                await bus.publish(
                    Event(
                        type=EventType.TASK_ASSIGNED,
                        data={
                            "task_id": str(task.id),
                            "assigned_to": target_uuid,
                            "agent_slug": target_agent_slug,
                            "skill": skill or "general",
                            "message": message or "",
                            "source": "a2a",
                        },
                    )
                )
        except Exception:
            pass  # Don't fail if event bus unavailable

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

    async def create_task_from_a2a_message(
        self,
        request: SendMessageRequest,
    ) -> TaskTable:
        """
        Create a new task from an A2A message request.

        Handles the full flow: extract message, resolve target, create task, route.
        """
        message = request.message
        title, description, message_text = self.extract_message_text(message)

        metadata = request.metadata or {}
        target_agent = self.resolve_target_agent(metadata)
        skill = metadata.get("skill")
        team = self.get_team_from_agent(target_agent) if target_agent else Team.BACKEND

        creator_agent = await self.resolve_creator_agent(metadata.get("from_agent"))
        if creator_agent is None:
            raise ValueError("No agent available to create tasks")

        task = TaskTable(
            title=f"[A2A] {title}" if target_agent else title,
            description=description,
            acceptance_criteria=["Task completed as specified"],
            status=TaskStatus.PENDING,
            priority=5,
            team=team,
            created_by=creator_agent.id,
            dev_notes=f"A2A Request | Skill: {skill or 'general'}" if skill else None,
        )
        self.session.add(task)
        await self.session.flush()

        if target_agent:
            await self.route_to_agent(target_agent, task, skill, message_text)

        return task

    async def update_task_from_message(
        self, task_id: str, message: A2AMessage
    ) -> TaskTable:
        """
        Update an existing task with a new message.

        Args:
            task_id: Task UUID string
            message: A2A message to append

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
        return task
