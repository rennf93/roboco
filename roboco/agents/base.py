"""
Agent Base Class

The foundation for all AI agents in the RoboCo system.
Each agent follows the universal task lifecycle and communicates
through the Messaging API.
"""

import asyncio
import contextlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import structlog
from anthropic import AsyncAnthropic

if TYPE_CHECKING:
    from anthropic.types import MessageParam

from roboco.config import settings
from roboco.llm import ToonAdapter
from roboco.models import AgentRole, AgentStatus, TaskStatus, Team
from roboco.models.agents import AgentConfig, AgentState

# Type for reasoning stream callback (injected to avoid API layer coupling)
ReasoningStreamCallback = Callable[[UUID, str], Awaitable[None]]


class _ReasoningStreamHolder:
    """Holder for reasoning stream callback singleton."""

    callback: ReasoningStreamCallback | None = None


def set_reasoning_stream_callback(callback: ReasoningStreamCallback | None) -> None:
    """
    Set the callback for streaming agent reasoning.

    This decouples the agent layer from the API/WebSocket layer.
    Set to None to disable reasoning streaming.
    """
    _ReasoningStreamHolder.callback = callback


def get_reasoning_stream_callback() -> ReasoningStreamCallback | None:
    """Get the current reasoning stream callback."""
    return _ReasoningStreamHolder.callback


logger = structlog.get_logger()


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
        self._llm_client: AsyncAnthropic | None = None
        self._toon = ToonAdapter()

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

    @property
    def llm_client(self) -> "AsyncAnthropic":
        """Get or create the LLM client."""
        if self._llm_client is None:
            self._llm_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._llm_client

    # =========================================================================
    # TOON SERIALIZATION (for token-efficient LLM communication)
    # =========================================================================

    def format_context(self, data: dict[str, Any]) -> str:
        """
        Format context data for LLM using TOON.

        TOON (Token-Oriented Object Notation) reduces token consumption
        by 30-60% compared to JSON while maintaining semantic clarity.

        Args:
            data: Dictionary to encode for LLM prompt.

        Returns:
            TOON-formatted string.
        """
        return self._toon.encode(data)

    def format_context_labeled(self, label: str, data: dict[str, Any]) -> str:
        """
        Format labeled context data for embedding in prompts.

        Args:
            label: Section label (e.g., "Task Context").
            data: Dictionary to encode.

        Returns:
            Labeled TOON-formatted string.
        """
        return self._toon.format_for_prompt(label, data)

    def parse_llm_response(self, response: str) -> dict[str, Any] | list[Any]:
        """
        Parse structured data from LLM response.

        Attempts TOON parsing first, falls back to JSON.

        Args:
            response: Raw LLM response text.

        Returns:
            Parsed Python dict or list.
        """
        return self._toon.decode(response)

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
        self.state.last_activity = datetime.now(UTC)

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

    @abstractmethod
    async def _initialize(self) -> None:
        """Initialize agent resources. Override in subclasses."""

    @abstractmethod
    async def _cleanup(self) -> None:
        """Cleanup agent resources. Override in subclasses."""

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
        self.state.last_activity = datetime.now(UTC)

        try:
            # Execute the task (implemented by subclasses)
            if self.state.current_task_id is None:
                self.log.warning("No current task to execute")
                return
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
        self.state.messages_sent += 1
        self.state.last_activity = datetime.now(UTC)

        url = f"http://{settings.host}:{settings.port}/api/v1/messages"
        async with httpx.AsyncClient() as client:
            await client.post(
                url,
                json={
                    "channel_id": str(channel_id),
                    "agent_id": str(self.id),
                    "content": content,
                    "message_type": message_type,
                },
            )

        self.log.debug(
            "Message sent",
            channel_id=str(channel_id),
            message_type=message_type,
            content_length=len(content),
        )

    async def stream_reasoning(self, content: str) -> None:
        """
        Stream reasoning to observers.

        This is the agent's internal thought process,
        visible to the Auditor and monitoring systems.

        The actual streaming mechanism (WebSocket, SSE, etc.) is injected
        via set_reasoning_stream_callback() during application initialization.
        """
        callback = get_reasoning_stream_callback()
        if callback:
            await callback(self.id, content)
        self.log.debug("Streamed reasoning", content_length=len(content))

    # =========================================================================
    # LLM INTERACTION
    # =========================================================================

    async def think(
        self,
        prompt: str,
        _context: dict[str, Any] | None = None,
    ) -> str:
        """
        Send a prompt to the LLM and get a response.

        Args:
            prompt: The prompt to send
            context: Additional context to include (reserved for future use)

        Returns:
            The LLM's response
        """
        self.log.debug("Thinking", prompt_length=len(prompt))

        messages: list[MessageParam] = [{"role": "user", "content": prompt}]

        response = await self.llm_client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=self.config.system_prompt,
            messages=messages,
        )

        # Extract text from first content block
        if response.content and hasattr(response.content[0], "text"):
            return response.content[0].text
        return ""

    async def think_and_stream(
        self,
        prompt: str,
        _context: dict[str, Any] | None = None,
    ) -> str:
        """
        Send a prompt and stream the response.

        Args:
            prompt: The prompt to send
            context: Additional context to include (reserved for future use)

        Returns:
            The complete response after streaming
        """
        self.log.debug("Thinking (streaming)", prompt_length=len(prompt))

        messages: list[MessageParam] = [{"role": "user", "content": prompt}]
        full_response = ""

        async with self.llm_client.messages.stream(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=self.config.system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                full_response += text
                await self.stream_reasoning(text)

        return full_response

    # =========================================================================
    # API HELPER
    # =========================================================================

    async def _api_call(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Make API call to RoboCo services.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., "/tasks" or "/tasks/{id}")
            **kwargs: Additional arguments passed to httpx

        Returns:
            JSON response as dictionary
        """
        url = f"http://{settings.host}:{settings.port}/api/v1{path}"
        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    # =========================================================================
    # COMMON TASK HELPERS
    # =========================================================================

    @property
    def cell_name(self) -> str:
        """Get the cell name based on team."""
        if self.team == Team.BACKEND:
            return "backend-cell"
        elif self.team == Team.FRONTEND:
            return "frontend-cell"
        elif self.team == Team.UX_UI:
            return "uxui-cell"
        return "unknown-cell"

    @property
    def cell_channel_id(self) -> UUID | None:
        """
        Get the cell channel ID.

        Override in subclass or set via _set_cell_channel_id.
        """
        return getattr(self, "_cell_channel_id", None)

    def _set_cell_channel_id(self, channel_id: UUID | None) -> None:
        """Set the cell channel ID."""
        self._cell_channel_id = channel_id

    async def _get_task_title(self, task_id: UUID) -> str:
        """Get task title from API."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            title: str = result.get("title", f"Task {str(task_id)[:8]}")
            return title
        except Exception as e:
            self.log.warning("Failed to get task title", error=str(e))
            return f"Task {str(task_id)[:8]}"

    async def _read_task_requirements(self, task_id: UUID) -> str:
        """Read task requirements from task record."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            description = result.get("description", "")
            acceptance_criteria = result.get("acceptance_criteria", [])
            criteria_text = "\n".join(f"- {c}" for c in acceptance_criteria)
            return f"{description}\n\nAcceptance Criteria:\n{criteria_text}"
        except Exception as e:
            self.log.warning("Failed to read task requirements", error=str(e))
            return "Requirements unavailable"

    async def _read_dev_notes(self, task_id: UUID) -> str:
        """Read developer's journey notes (dev_notes + progress_updates)."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            notes: str = result.get("dev_notes") or ""

            # Also include progress updates as they contain developer's work log
            progress_updates = result.get("progress_updates", [])
            if progress_updates:
                progress_text = "\n".join(
                    f"[{u.get('timestamp', 'N/A')}] ({u.get('percentage', 0)}%) "
                    f"{u.get('message', '')}"
                    for u in progress_updates
                )
                if notes:
                    notes = f"{notes}\n\nProgress Updates:\n{progress_text}"
                else:
                    notes = f"Progress Updates:\n{progress_text}"

            return notes if notes else "No developer notes available"
        except Exception as e:
            self.log.warning("Failed to read dev notes", error=str(e))
            return "Dev notes unavailable"

    async def _read_team_journal_for_task(self, task_id: UUID) -> str:
        """
        Read team member journal entries for a specific task.

        Cell members can read each other's journals. This queries for
        journal entries linked to the given task.

        Args:
            task_id: Task to get journal entries for

        Returns:
            Formatted journal entries or empty string if none/error
        """
        try:
            # Get task to find assigned developer
            task = await self._api_call("GET", f"/tasks/{task_id}")
            assigned_to = task.get("assigned_to")
            if not assigned_to:
                return ""

            # Query journal entries for this task from the assigned agent
            result = await self._api_call(
                "GET",
                f"/journals/{assigned_to}/entries",
                params={"task_id": str(task_id), "limit": 10},
            )
            entries = result.get("items", [])
            if not entries:
                return ""

            # Format entries
            formatted = []
            for entry in entries:
                entry_type = entry.get("entry_type", "entry")
                title = entry.get("title", "Untitled")
                content = entry.get("content", "")
                timestamp = entry.get("created_at", "")
                formatted.append(f"[{timestamp}] {entry_type}: {title}\n{content}")

            return "\n\n".join(formatted)
        except Exception as e:
            self.log.warning("Failed to read team journal", error=str(e))
            return ""

    async def _get_task_commits(self, task_id: UUID) -> list[str]:
        """Get commits for the task."""
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            commits: list[str] = result.get("commits", [])
            return commits
        except Exception as e:
            self.log.warning("Failed to get task commits", error=str(e))
            return []

    async def _update_task_status(self, task_id: UUID, status: TaskStatus) -> None:
        """Update task status via API."""
        try:
            await self._api_call(
                "PUT",
                f"/tasks/{task_id}",
                json={"status": status.value},
            )
            self.log.info(
                "Task status updated", task_id=str(task_id), status=status.value
            )
        except Exception as e:
            self.log.error("Failed to update task status", error=str(e))

    # =========================================================================
    # SEMANTIC STATUS HELPERS
    # =========================================================================

    async def _mark_claimed(self, task_id: UUID) -> None:
        """Mark task as claimed."""
        await self._update_task_status(task_id, TaskStatus.CLAIMED)

    async def _mark_in_progress(self, task_id: UUID) -> None:
        """Mark task as in progress."""
        await self._update_task_status(task_id, TaskStatus.IN_PROGRESS)

    async def _mark_blocked(self, task_id: UUID) -> None:
        """Mark task as blocked."""
        await self._update_task_status(task_id, TaskStatus.BLOCKED)

    async def _unblock_task(self, task_id: UUID) -> bool:
        """
        Unblock a blocked task.

        Only PMs can unblock tasks in their cell.

        Args:
            task_id: Task to unblock

        Returns:
            True if unblocked successfully, False otherwise
        """
        try:
            await self._api_call("POST", f"/tasks/{task_id}/unblock")
            self.log.info("Task unblocked", task_id=str(task_id))
            return True
        except Exception as e:
            self.log.error("Failed to unblock task", task_id=str(task_id), error=str(e))
            return False

    async def _mark_awaiting_qa(self, task_id: UUID) -> None:
        """Mark task as awaiting QA review."""
        await self._update_task_status(task_id, TaskStatus.AWAITING_QA)

    async def _mark_needs_revision(self, task_id: UUID) -> None:
        """Mark task as needing revision (QA failed)."""
        await self._update_task_status(task_id, TaskStatus.NEEDS_REVISION)

    async def _mark_awaiting_documentation(self, task_id: UUID) -> None:
        """Mark task as awaiting documentation."""
        await self._update_task_status(task_id, TaskStatus.AWAITING_DOCUMENTATION)

    async def _mark_awaiting_pm_review(self, task_id: UUID) -> None:
        """Mark task as awaiting PM review."""
        await self._update_task_status(task_id, TaskStatus.AWAITING_PM_REVIEW)

    async def _mark_completed(self, task_id: UUID) -> None:
        """Mark task as completed."""
        await self._update_task_status(task_id, TaskStatus.COMPLETED)

    # =========================================================================
    # API QUERY HELPERS
    # =========================================================================

    async def _find_tasks(
        self,
        status: str | TaskStatus | None = None,
        team: Team | None = None,
        assigned_to: UUID | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Find tasks matching criteria.

        Args:
            status: Task status to filter by
            team: Team to filter by
            assigned_to: Agent ID to filter by assignment
            limit: Maximum results to return

        Returns:
            List of task dictionaries
        """
        params: dict[str, Any] = {"limit": limit}

        if status:
            status_val = status.value if isinstance(status, TaskStatus) else status
            params["status"] = status_val
        if team:
            params["team"] = team.value
        if assigned_to:
            params["assigned_to"] = str(assigned_to)

        try:
            result = await self._api_call("GET", "/tasks", params=params)
            items: list[dict[str, Any]] = result.get("items", [])
            return items
        except Exception as e:
            self.log.warning("Failed to find tasks", error=str(e))
            return []

    async def _find_first_task(
        self,
        status: str | TaskStatus | None = None,
        team: Team | None = None,
        assigned_to: UUID | None = None,
    ) -> UUID | None:
        """
        Find first task matching criteria.

        Returns:
            Task ID if found, None otherwise
        """
        tasks = await self._find_tasks(status, team, assigned_to, limit=1)
        return UUID(tasks[0]["id"]) if tasks else None

    async def _count_tasks(
        self,
        status: str | TaskStatus | None = None,
        team: Team | None = None,
    ) -> int:
        """Count tasks matching criteria."""
        tasks = await self._find_tasks(status, team, limit=100)
        return len(tasks)

    # =========================================================================
    # PROGRESS HELPERS
    # =========================================================================

    async def _add_progress(
        self,
        task_id: UUID,
        message: str,
        percentage: int,
    ) -> None:
        """
        Add progress update to task.

        This is saved to task.progress_updates and visible to QA/PM.

        Args:
            task_id: Task to update
            message: Progress message
            percentage: Completion percentage (0-100)
        """
        try:
            await self._api_call(
                "POST",
                f"/tasks/{task_id}/progress",
                json={"message": message, "percentage": percentage},
            )
            self.log.info("Progress saved", task_id=str(task_id), percentage=percentage)
        except Exception as e:
            self.log.warning("Failed to save progress", error=str(e))

    async def _report_progress(
        self,
        task_id: UUID,
        message: str,
        percentage: int,
        channel_id: UUID | None = None,
    ) -> None:
        """
        Report progress: save to task AND send channel message.

        Args:
            task_id: Task to update
            message: Progress message
            percentage: Completion percentage (0-100)
            channel_id: Channel to notify (uses cell_channel_id or task_id)
        """
        await self._add_progress(task_id, message, percentage)

        target_channel = channel_id or self.cell_channel_id or task_id
        task_ref = str(task_id)[:8]
        await self.send_message(
            target_channel,
            f"TASK-{task_ref} ({percentage}%) {message}",
            message_type="action",
        )

    # =========================================================================
    # TOON FORMATTER HELPERS
    # =========================================================================

    def _format_task_context(
        self,
        task_id: UUID,
        title: str,
        requirements: str | None = None,
        dev_notes: str | None = None,
    ) -> str:
        """Format task context for LLM prompts."""
        data: dict[str, Any] = {
            "task_id": str(task_id)[:8],
            "title": title,
        }
        if requirements:
            data["requirements"] = requirements
        if dev_notes:
            data["dev_notes"] = dev_notes
        return self.format_context_labeled("Task Context", data)

    def _format_execution_context(
        self,
        task_title: str,
        subtask_num: int,
        total_subtasks: int,
        description: str,
        files: list[str] | None = None,
    ) -> str:
        """Format execution context for LLM prompts."""
        data: dict[str, Any] = {
            "task": task_title,
            "subtask": f"{subtask_num}/{total_subtasks}",
            "description": description,
        }
        if files:
            data["files"] = files
        return self.format_context_labeled("Execution Context", data)

    def _format_review_context(
        self,
        title: str,
        requirements: str,
        dev_notes: str,
        commits: str,
    ) -> str:
        """Format review context for QA/PM prompts."""
        return self.format_context_labeled(
            "Review Context",
            {
                "title": title,
                "requirements": requirements,
                "dev_notes": dev_notes,
                "commits": commits,
            },
        )

    def _format_test_context(
        self,
        name: str,
        description: str,
        steps: list[str],
        expected: str,
    ) -> str:
        """Format test case context for QA prompts."""
        return self.format_context_labeled(
            "Test Case",
            {
                "name": name,
                "description": description,
                "steps": steps,
                "expected": expected,
            },
        )

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
