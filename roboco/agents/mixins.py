"""
Agent Mixins and Abstractions

Reusable components for agent implementations:
- PhaseEngine: Unified phase dispatch and transitions
- ContextManager: Context lifecycle management
"""

import contextlib
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

# =============================================================================
# PHASE ENGINE
# =============================================================================


@dataclass
class PhaseConfig[PhaseT: Enum]:
    """
    Configuration for a phase in the workflow.

    Attributes:
        phase: The phase enum value
        handler: Async function to execute for this phase
        next_phase: Phase to transition to after completion (None = terminal)
        fail_phase: Phase to transition to if handler returns False (optional)
        requires_completion: If True, handler must return True to advance
    """

    phase: PhaseT
    handler: Callable[..., Awaitable[bool | None]]
    next_phase: PhaseT | None = None
    fail_phase: PhaseT | None = None
    requires_completion: bool = False


@dataclass
class PhaseResult:
    """Result of running a phase."""

    completed: bool = False  # True if workflow complete
    advanced: bool = False  # True if phase advanced
    error: str | None = None


class PhaseEngine[PhaseT: Enum, ContextT]:
    """
    Mixin for phase-based workflow execution.

    Provides unified phase dispatch and transition logic that can be
    configured per agent type. Replaces duplicate _dispatch_phase and
    _run_phase implementations across agents.

    Usage:
        class MyAgent(Agent, PhaseEngine[MyPhase, MyContext]):
            def _get_phase_configs(self) -> list[PhaseConfig[MyPhase]]:
                return [
                    PhaseConfig(MyPhase.START, self._phase_start, MyPhase.WORK),
                    PhaseConfig(MyPhase.WORK, self._phase_work, MyPhase.END,
                               requires_completion=True),
                    PhaseConfig(MyPhase.END, self._phase_end, None),
                ]
    """

    @abstractmethod
    def _get_phase_configs(self) -> list[PhaseConfig[PhaseT]]:
        """
        Define the phase workflow configuration.

        Returns:
            List of PhaseConfig defining handlers and transitions.
        """
        ...

    @abstractmethod
    def _get_current_phase(self, ctx: ContextT) -> PhaseT:
        """Get the current phase from context."""
        ...

    @abstractmethod
    def _set_current_phase(self, ctx: ContextT, phase: PhaseT) -> None:
        """Set the current phase in context."""
        ...

    async def _run_phase_engine(self, ctx: ContextT) -> PhaseResult:
        """
        Execute the current phase and handle transitions.

        Args:
            ctx: The workflow context

        Returns:
            PhaseResult indicating completion status
        """
        configs = {cfg.phase: cfg for cfg in self._get_phase_configs()}
        current_phase = self._get_current_phase(ctx)

        config = configs.get(current_phase)
        if not config:
            return PhaseResult(error=f"No config for phase: {current_phase}")

        try:
            # Execute handler
            result = await config.handler(ctx)

            # Terminal phase check
            if config.next_phase is None:
                return PhaseResult(completed=True)

            # Check if we should advance
            should_advance = True
            if config.requires_completion:
                should_advance = result is True

            if should_advance:
                self._set_current_phase(ctx, config.next_phase)
                return PhaseResult(advanced=True)

            # Handle failure transition (e.g., VERIFY fails → back to EXECUTE)
            if config.fail_phase is not None and result is False:
                self._set_current_phase(ctx, config.fail_phase)
                return PhaseResult(advanced=True)

            return PhaseResult()

        except Exception as e:
            return PhaseResult(error=str(e))


# =============================================================================
# CYCLIC PHASE RUNNER (for continuous-duty agents like PM/Board)
# =============================================================================


@dataclass
class CyclicPhaseConfig[PhaseT: Enum]:
    """
    Configuration for a phase in a continuous cycle.

    Unlike PhaseConfig, this is for agents that cycle forever
    (PM, Board agents) rather than completing tasks.
    """

    phase: PhaseT
    handler: Callable[..., Awaitable[None]]
    next_phase: PhaseT


class CyclicPhaseRunner[PhaseT: Enum]:
    """
    Mixin for continuous-duty agents that cycle through phases.

    Unlike PhaseEngine which handles task completion, this is for
    agents like PM and Board that run continuously.

    Usage:
        class CellPMAgent(Agent, CyclicPhaseRunner[CellPMPhase]):
            _current_phase: CellPMPhase = CellPMPhase.MONITOR

            def _get_cyclic_phase_configs(self) -> list[CyclicPhaseConfig]:
                return [
                    CyclicPhaseConfig(CellPMPhase.MONITOR, self._phase_monitor,
                                     CellPMPhase.TRIAGE),
                    # ... etc
                ]
    """

    _current_phase: PhaseT

    @abstractmethod
    def _get_cyclic_phase_configs(self) -> list[CyclicPhaseConfig[PhaseT]]:
        """Define the cyclic phase workflow."""
        ...

    async def _run_phase_cycle(self) -> str | None:
        """
        Execute the current phase and advance to next.

        Returns error message if any, None on success.
        """
        configs = {cfg.phase: cfg for cfg in self._get_cyclic_phase_configs()}

        config = configs.get(self._current_phase)
        if not config:
            return f"No config for phase: {self._current_phase}"

        try:
            await config.handler()
            self._current_phase = config.next_phase
            return None
        except Exception as e:
            return str(e)


# =============================================================================
# CONTEXT MANAGER
# =============================================================================


@dataclass
class BaseContext:
    """Base context with common fields."""

    task_id: UUID
    title: str = ""
    notes: list[str] = field(default_factory=list)


class ContextManager[ContextT]:
    """
    Mixin for managing workflow context lifecycle.

    Handles:
    - Context initialization/restoration
    - Context cleanup on completion
    - Type-safe context access

    Usage:
        class MyAgent(Agent, ContextManager[MyContext]):
            _context: MyContext | None = None

            def _create_context(self, task_id: UUID, title: str) -> MyContext:
                return MyContext(task_id=task_id, title=title)

            async def execute_task(self, task_id: UUID) -> bool:
                ctx = await self._ensure_context(task_id)
                # ... work with ctx
    """

    _context: ContextT | None

    @abstractmethod
    def _create_context(self, task_id: UUID, title: str) -> ContextT:
        """Create a new context instance."""
        ...

    @abstractmethod
    def _get_context_task_id(self, ctx: ContextT) -> UUID:
        """Get the task ID from a context."""
        ...

    @abstractmethod
    async def _get_task_title(self, task_id: UUID) -> str:
        """Get task title from API (implemented in base Agent)."""
        ...

    async def _ensure_context(self, task_id: UUID) -> ContextT:
        """
        Ensure context exists for the given task.

        Creates new context if none exists or if task ID changed.
        """
        if self._context is None or self._get_context_task_id(self._context) != task_id:
            title = await self._get_task_title(task_id)
            self._context = self._create_context(task_id, title)
        return self._context

    def _clear_context(self) -> None:
        """Clear the current context."""
        self._context = None

    @property
    def context(self) -> ContextT | None:
        """Get the current context (may be None)."""
        return self._context

    def require_context(self) -> ContextT:
        """Get context, raising if None."""
        if self._context is None:
            raise RuntimeError("No active context")
        return self._context


# =============================================================================
# WORK FINDER
# =============================================================================


@dataclass
class WorkSearchStrategy:
    """
    A strategy for finding work.

    Attributes:
        name: Descriptive name for logging
        finder: Async function that returns task ID or None
        priority: Lower = higher priority
    """

    name: str
    finder: Callable[[], Awaitable[UUID | None]]
    priority: int = 0


class WorkFinder:
    """
    Mixin for finding work with prioritized strategies.

    Replaces duplicate find_work implementations with a configurable
    search strategy pattern.

    Usage:
        class MyAgent(Agent, WorkFinder):
            def _get_work_strategies(self) -> list[WorkSearchStrategy]:
                return [
                    WorkSearchStrategy("paused", self._find_paused, priority=0),
                    WorkSearchStrategy("assigned", self._find_assigned, priority=1),
                ]
    """

    _pending_work: list[UUID]

    @abstractmethod
    def _get_work_strategies(self) -> list[WorkSearchStrategy]:
        """Define work search strategies in priority order."""
        ...

    async def _find_work_prioritized(self) -> UUID | None:
        """
        Find work using prioritized strategies.

        First checks pending work queue, then tries each strategy
        in priority order.
        """
        # Check pending queue first
        if hasattr(self, "_pending_work") and self._pending_work:
            return self._pending_work.pop(0)

        # Try strategies in priority order
        strategies = sorted(self._get_work_strategies(), key=lambda s: s.priority)
        for strategy in strategies:
            task_id = await strategy.finder()
            if task_id:
                return task_id

        return None


# =============================================================================
# PROGRESS TRACKER
# =============================================================================


@dataclass
class ProgressUpdate:
    """A progress update for a task."""

    message: str
    percentage: int
    details: dict[str, Any] = field(default_factory=dict)


class ProgressTracker:
    """
    Mixin for tracking and reporting progress.

    Provides unified progress reporting that saves to task AND
    sends channel messages.
    """

    @abstractmethod
    async def _api_call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Make API call (implemented in base Agent)."""
        ...

    @abstractmethod
    async def send_message(
        self, channel_id: UUID, content: str, message_type: str
    ) -> None:
        """Send message to channel (implemented in base Agent)."""
        ...

    async def _report_progress(
        self,
        task_id: UUID,
        channel_id: UUID | None,
        message: str,
        percentage: int,
    ) -> None:
        """
        Report progress for a task.

        Saves to task record AND sends channel message.

        Args:
            task_id: Task to update
            channel_id: Channel to notify (uses task_id if None)
            message: Progress message
            percentage: Completion percentage (0-100)
        """
        # Save to task (suppress errors - logged by _api_call)
        with contextlib.suppress(Exception):
            await self._api_call(
                "POST",
                f"/tasks/{task_id}/progress",
                json={"message": message, "percentage": percentage},
            )

        # Send channel message
        task_ref = str(task_id)[:8]
        await self.send_message(
            channel_id or task_id,
            f"TASK-{task_ref} ({percentage}%) {message}",
            message_type="action",
        )
