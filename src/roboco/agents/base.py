"""
Agent Base Class

The foundation for all AI agents in the RoboCo system.
Each agent follows the universal task lifecycle and communicates
through the Messaging API.
"""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from roboco.models import AgentRole, AgentStatus, Team

logger = structlog.get_logger()


# =============================================================================
# CONFIGURATION
# =============================================================================


class ModelProvider(str, Enum):
    """LLM provider options."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    LOCAL = "local"


class AgentConfig(BaseModel):
    """Configuration for an agent instance."""

    # Identity
    id: UUID = Field(default_factory=uuid4)
    name: str
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$")
    role: AgentRole
    team: Team | None = None

    # Model configuration
    provider: ModelProvider = ModelProvider.ANTHROPIC
    model: str = "claude-sonnet-4-20250514"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)

    # System prompt (loaded from blueprints)
    system_prompt: str

    # Capabilities
    capabilities: list[str] = Field(default_factory=list)

    # Permissions
    can_notify: bool = False
    channel_ids: list[UUID] = Field(default_factory=list)


# =============================================================================
# AGENT LIFECYCLE STATE
# =============================================================================


class AgentState(BaseModel):
    """Current state of an agent."""

    status: AgentStatus = AgentStatus.OFFLINE
    current_task_id: UUID | None = None
    current_session_id: UUID | None = None
    last_activity: datetime | None = None
    error: str | None = None

    # Metrics
    messages_sent: int = 0
    tasks_completed: int = 0


# =============================================================================
# BASE AGENT CLASS
# =============================================================================


class Agent(ABC):
    """
    Base class for all RoboCo agents.

    Agents are autonomous AI workers that:
    - Follow the universal task lifecycle
    - Communicate through channels
    - Stream their reasoning to observers
    - Maintain journals for reflection
    """

    def __init__(self, config: AgentConfig) -> None:
        """
        Initialize an agent.

        Args:
            config: Agent configuration
        """
        self.config = config
        self.state = AgentState()
        self._running = False
        self._task: asyncio.Task | None = None

        self.log = logger.bind(
            agent_id=str(config.id),
            agent_name=config.name,
            agent_role=config.role.value,
        )

    @property
    def id(self) -> UUID:
        """Agent's unique identifier."""
        return self.config.id

    @property
    def name(self) -> str:
        """Agent's display name."""
        return self.config.name

    @property
    def role(self) -> AgentRole:
        """Agent's role in the organization."""
        return self.config.role

    @property
    def team(self) -> Team | None:
        """Agent's team affiliation."""
        return self.config.team

    @property
    def is_running(self) -> bool:
        """Check if agent is currently running."""
        return self._running

    @property
    def is_idle(self) -> bool:
        """Check if agent is idle (running but no task)."""
        return self._running and self.state.current_task_id is None

    # =========================================================================
    # LIFECYCLE METHODS
    # =========================================================================

    async def start(self) -> None:
        """
        Start the agent.

        Initializes connections and begins the main loop.
        """
        if self._running:
            self.log.warning("Agent already running")
            return

        self.log.info("Starting agent")
        self._running = True
        self.state.status = AgentStatus.IDLE
        self.state.last_activity = datetime.utcnow()

        # Initialize connections
        await self._initialize()

        # Start main loop
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """
        Stop the agent gracefully.

        Saves state and closes connections.
        """
        if not self._running:
            return

        self.log.info("Stopping agent")
        self._running = False

        # Cancel main loop
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        # Save state and cleanup
        await self._cleanup()

        self.state.status = AgentStatus.OFFLINE
        self.log.info("Agent stopped")

    async def _initialize(self) -> None:
        """Initialize agent resources. Override in subclasses."""
        pass

    async def _cleanup(self) -> None:
        """Cleanup agent resources. Override in subclasses."""
        pass

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    async def _run_loop(self) -> None:
        """
        Main agent loop.

        Continuously scans for work and processes tasks.
        """
        while self._running:
            try:
                if self.state.current_task_id:
                    # Continue working on current task
                    await self._work_on_task()
                else:
                    # Scan for new work
                    await self._scan_for_work()

                # Brief pause to prevent tight loop
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error("Error in agent loop", error=str(e))
                self.state.error = str(e)
                await asyncio.sleep(5)  # Back off on error

    async def _scan_for_work(self) -> None:
        """
        Scan for available work.

        Checks for:
        1. Own interrupted/paused tasks (priority)
        2. Assigned tasks
        3. Available tasks in queue
        """
        self.state.status = AgentStatus.IDLE

        # Look for work (implemented by subclasses)
        task_id = await self.find_work()

        if task_id:
            self.state.current_task_id = task_id
            self.state.status = AgentStatus.ACTIVE
            self.log.info("Found work", task_id=str(task_id))

    async def _work_on_task(self) -> None:
        """
        Work on the current task.

        Follows the task lifecycle:
        CLAIM → UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES → CLOSE
        """
        self.state.status = AgentStatus.ACTIVE
        self.state.last_activity = datetime.utcnow()

        try:
            # Execute the task (implemented by subclasses)
            completed = await self.execute_task(self.state.current_task_id)

            if completed:
                self.state.tasks_completed += 1
                self.state.current_task_id = None
                self.log.info("Task completed")

        except Exception as e:
            self.log.error("Error executing task", error=str(e))
            self.state.error = str(e)
            # Don't clear task - allow retry or manual intervention

    # =========================================================================
    # ABSTRACT METHODS (Implement in subclasses)
    # =========================================================================

    @abstractmethod
    async def find_work(self) -> UUID | None:
        """
        Find available work for this agent.

        Returns:
            Task ID if work found, None otherwise.
        """
        pass

    @abstractmethod
    async def execute_task(self, task_id: UUID) -> bool:
        """
        Execute a task.

        Args:
            task_id: ID of the task to execute

        Returns:
            True if task completed successfully, False otherwise.
        """
        pass

    # =========================================================================
    # COMMUNICATION METHODS
    # =========================================================================

    async def send_message(
        self,
        channel_id: UUID,
        content: str,
        message_type: str = "dialogue",
    ) -> None:
        """
        Send a message to a channel.

        Args:
            channel_id: Target channel
            content: Message content
            message_type: Type of message (reasoning, dialogue, action, etc.)
        """
        # TODO: Integrate with Messaging API
        self.state.messages_sent += 1
        self.state.last_activity = datetime.utcnow()
        self.log.debug(
            "Sending message",
            channel_id=str(channel_id),
            message_type=message_type,
            content_length=len(content),
        )

    async def stream_reasoning(self, content: str) -> None:
        """
        Stream reasoning to observers.

        This is the agent's internal thought process,
        visible to the Auditor and monitoring systems.
        """
        # TODO: Integrate with WebSocket streaming
        self.log.debug("Streaming reasoning", content_length=len(content))

    # =========================================================================
    # LLM INTERACTION
    # =========================================================================

    async def think(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """
        Send a prompt to the LLM and get a response.

        Args:
            prompt: The prompt to send
            context: Additional context to include

        Returns:
            The LLM's response
        """
        # TODO: Integrate with actual LLM provider
        self.log.debug("Thinking", prompt_length=len(prompt))

        # Placeholder - will integrate with Anthropic/OpenAI
        return f"[Placeholder response for: {prompt[:50]}...]"

    async def think_and_stream(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        Send a prompt and stream the response.

        Args:
            prompt: The prompt to send
            context: Additional context to include

        Returns:
            The complete response after streaming
        """
        # TODO: Integrate with actual LLM provider with streaming
        self.log.debug("Thinking (streaming)", prompt_length=len(prompt))

        # Placeholder
        response = f"[Placeholder streaming response for: {prompt[:50]}...]"
        await self.stream_reasoning(response)
        return response

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def to_dict(self) -> dict[str, Any]:
        """Convert agent to dictionary representation."""
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.config.slug,
            "role": self.role.value,
            "team": self.team.value if self.team else None,
            "status": self.state.status.value,
            "current_task_id": str(self.state.current_task_id)
            if self.state.current_task_id
            else None,
            "last_activity": self.state.last_activity.isoformat()
            if self.state.last_activity
            else None,
            "messages_sent": self.state.messages_sent,
            "tasks_completed": self.state.tasks_completed,
        }
