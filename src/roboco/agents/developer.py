"""
Developer Agent

Implementation of the Developer workflow from the blueprint.
Handles task lifecycle: SCAN → CLAIM → UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES → CLOSE
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.models import AgentRole, TaskStatus, Team

logger = structlog.get_logger()


class DevTaskPhase(str, Enum):
    """Phases of the developer task lifecycle."""

    SCAN = "scan"
    CLAIM = "claim"
    UNDERSTAND = "understand"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    NOTES = "notes"
    CLOSE = "close"
    BLOCKED = "blocked"


@dataclass
class TaskContext:
    """Context for the current task being worked on."""

    task_id: UUID
    title: str
    phase: DevTaskPhase = DevTaskPhase.CLAIM
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    current_subtask: int = 0
    blockers: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    journal_entries: list[str] = field(default_factory=list)


class DeveloperAgent(Agent):
    """
    Developer agent that follows the Dev Lifecycle.

    Workflow:
    1. SCAN - Check for assigned/paused tasks
    2. CLAIM - Lock and announce task
    3. UNDERSTAND - Read requirements, ask if unclear
    4. PLAN - Break into subtasks, create plan
    5. EXECUTE - Work through subtasks, commit frequently
    6. VERIFY - Self-test, run quality checks
    7. NOTES - Document journey, create handoff
    8. CLOSE - After QA approval, mark complete
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize developer agent."""
        super().__init__(config)
        self._task_context: TaskContext | None = None
        self._cell_channel_id: UUID | None = None

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

    # =========================================================================
    # LIFECYCLE IMPLEMENTATION
    # =========================================================================

    async def find_work(self) -> UUID | None:
        """
        SCAN phase: Find available work.

        Priority order:
        1. Own paused/interrupted tasks
        2. Assigned tasks
        3. If none, signal availability to PM
        """
        self.log.info("Scanning for work")

        # Check for paused tasks first (highest priority)
        paused_task = await self._find_paused_task()
        if paused_task:
            self.log.info("Found paused task", task_id=str(paused_task))
            return paused_task

        # Check for assigned tasks
        assigned_task = await self._find_assigned_task()
        if assigned_task:
            self.log.info("Found assigned task", task_id=str(assigned_task))
            return assigned_task

        # Signal availability to PM
        await self._signal_availability()
        return None

    async def execute_task(self, task_id: UUID) -> bool:
        """
        Execute task through the developer lifecycle phases.

        Returns True when task is completed (after QA + docs).
        """
        # Initialize or restore task context
        if self._task_context is None or self._task_context.task_id != task_id:
            self._task_context = TaskContext(
                task_id=task_id,
                title=await self._get_task_title(task_id),
            )

        ctx = self._task_context

        try:
            match ctx.phase:
                case DevTaskPhase.CLAIM:
                    await self._phase_claim(ctx)
                    ctx.phase = DevTaskPhase.UNDERSTAND

                case DevTaskPhase.UNDERSTAND:
                    understood = await self._phase_understand(ctx)
                    if understood:
                        ctx.phase = DevTaskPhase.PLAN
                    # If not understood, stay in UNDERSTAND (asking questions)

                case DevTaskPhase.PLAN:
                    await self._phase_plan(ctx)
                    ctx.phase = DevTaskPhase.EXECUTE

                case DevTaskPhase.EXECUTE:
                    completed = await self._phase_execute(ctx)
                    if completed:
                        ctx.phase = DevTaskPhase.VERIFY

                case DevTaskPhase.VERIFY:
                    verified = await self._phase_verify(ctx)
                    if verified:
                        ctx.phase = DevTaskPhase.NOTES
                    else:
                        ctx.phase = DevTaskPhase.EXECUTE  # Back to fix issues

                case DevTaskPhase.NOTES:
                    await self._phase_notes(ctx)
                    ctx.phase = DevTaskPhase.CLOSE

                case DevTaskPhase.CLOSE:
                    closed = await self._phase_close(ctx)
                    if closed:
                        self._task_context = None
                        return True

                case DevTaskPhase.BLOCKED:
                    resolved = await self._handle_blocked(ctx)
                    if resolved:
                        ctx.phase = DevTaskPhase.EXECUTE

            return False

        except Exception as e:
            self.log.error("Error in task phase", phase=ctx.phase.value, error=str(e))
            ctx.blockers.append(str(e))
            ctx.phase = DevTaskPhase.BLOCKED
            return False

    # =========================================================================
    # PHASE IMPLEMENTATIONS
    # =========================================================================

    async def _phase_claim(self, ctx: TaskContext) -> None:
        """
        CLAIM phase: Lock the task and announce.

        - Update task status to "claimed"
        - Announce in cell channel
        """
        self.log.info("CLAIM phase", task_id=str(ctx.task_id))

        # Update task status
        await self._update_task_status(ctx.task_id, TaskStatus.CLAIMED)

        # Announce in channel
        await self.send_message(
            self._cell_channel_id or ctx.task_id,  # Fallback if no channel
            f"Claiming TASK-{str(ctx.task_id)[:8]}: {ctx.title}",
            message_type="action",
        )

        # Journal entry
        ctx.journal_entries.append(
            f"[{datetime.utcnow().isoformat()}] Claimed task. Beginning work."
        )

    async def _phase_understand(self, ctx: TaskContext) -> bool:
        """
        UNDERSTAND phase: Read and comprehend requirements.

        - Read task record
        - Read related code/docs
        - Ask if unclear (GATE: must understand before proceeding)

        Returns True if understood, False if still clarifying.
        """
        self.log.info("UNDERSTAND phase", task_id=str(ctx.task_id))

        # Read task requirements
        requirements = await self._read_task_requirements(ctx.task_id)

        # Use LLM to understand and identify gaps
        prompt = f"""
You are analyzing a task before beginning work.

Task: {ctx.title}
Requirements:
{requirements}

Analyze:
1. What exactly needs to be done?
2. What are the acceptance criteria?
3. Is anything unclear that requires clarification?

If everything is clear, respond with: "UNDERSTOOD: [your understanding summary]"
If clarification needed, respond with: "QUESTION: [your question]"
"""
        response = await self.think(prompt)

        if response.startswith("UNDERSTOOD:"):
            # Add understanding to journal
            ctx.journal_entries.append(
                f"[{datetime.utcnow().isoformat()}] Understanding: {response}"
            )
            return True
        else:
            # Ask question in channel
            question = response.replace("QUESTION:", "").strip()
            await self.send_message(
                self._cell_channel_id or ctx.task_id,
                f"Question about TASK-{str(ctx.task_id)[:8]}: {question}",
                message_type="dialogue",
            )
            return False

    async def _phase_plan(self, ctx: TaskContext) -> None:
        """
        PLAN phase: Break task into subtasks.

        - Create implementation plan
        - Identify dependencies and risks
        - Journal the approach
        """
        self.log.info("PLAN phase", task_id=str(ctx.task_id))

        # Use LLM to create plan
        prompt = f"""
Create an implementation plan for this task:

Task: {ctx.title}
Understanding: {ctx.journal_entries[-1] if ctx.journal_entries else "No previous context"}

Break this into ordered subtasks. For each subtask:
- Clear description
- Files to modify
- Estimated complexity (small/medium/large)

Format as JSON array:
[
  {{"description": "...", "files": ["..."], "complexity": "small|medium|large"}},
  ...
]
"""
        response = await self.think(prompt)

        # Parse subtasks (simplified - would use proper JSON parsing)
        ctx.subtasks = [{"description": response, "files": [], "complexity": "medium"}]

        # Journal entry
        ctx.journal_entries.append(
            f"[{datetime.utcnow().isoformat()}] Plan: {len(ctx.subtasks)} subtasks created"
        )

        # Announce plan
        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"TASK-{str(ctx.task_id)[:8]} plan ready: {len(ctx.subtasks)} subtasks",
            message_type="action",
        )

    async def _phase_execute(self, ctx: TaskContext) -> bool:
        """
        EXECUTE phase: Work through subtasks.

        - Execute current subtask
        - Commit with meaningful messages
        - Update progress

        Returns True when all subtasks complete.
        """
        self.log.info(
            "EXECUTE phase",
            task_id=str(ctx.task_id),
            subtask=ctx.current_subtask,
            total=len(ctx.subtasks),
        )

        if ctx.current_subtask >= len(ctx.subtasks):
            return True

        subtask = ctx.subtasks[ctx.current_subtask]

        # Use LLM to work on subtask
        prompt = f"""
Execute this subtask:

Task: {ctx.title}
Subtask {ctx.current_subtask + 1}/{len(ctx.subtasks)}: {subtask.get("description", str(subtask))}

Provide:
1. Code changes needed
2. Commands to run
3. Commit message in format: type(scope): description

Respond with the implementation.
"""
        response = await self.think_and_stream(prompt)

        # Record work done
        ctx.journal_entries.append(
            f"[{datetime.utcnow().isoformat()}] Subtask {ctx.current_subtask + 1}: {response[:100]}..."
        )

        # Simulate commit (in real implementation would execute git)
        commit_hash = f"commit_{ctx.current_subtask}"
        ctx.commits.append(commit_hash)

        # Progress update
        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"TASK-{str(ctx.task_id)[:8]} progress: subtask {ctx.current_subtask + 1}/{len(ctx.subtasks)} complete",
            message_type="action",
        )

        ctx.current_subtask += 1
        return ctx.current_subtask >= len(ctx.subtasks)

    async def _phase_verify(self, ctx: TaskContext) -> bool:
        """
        VERIFY phase: Self-test against acceptance criteria.

        - Run quality checks (ruff, mypy, pytest)
        - Self-review against acceptance criteria
        - Flag for QA if passing

        Returns True if verified, False if issues found.
        """
        self.log.info("VERIFY phase", task_id=str(ctx.task_id))

        # Run quality checks (simulated)
        checks = [
            ("ruff format", True),
            ("ruff check", True),
            ("mypy", True),
            ("pytest", True),
        ]

        all_passed = True
        for check_name, passed in checks:
            if not passed:
                all_passed = False
                ctx.journal_entries.append(
                    f"[{datetime.utcnow().isoformat()}] VERIFY FAILED: {check_name}"
                )

        if all_passed:
            # Flag for QA
            await self.send_message(
                self._cell_channel_id or ctx.task_id,
                f"TASK-{str(ctx.task_id)[:8]} ready for QA review. "
                f"Commits: {', '.join(ctx.commits)}",
                message_type="action",
            )
            ctx.journal_entries.append(
                f"[{datetime.utcnow().isoformat()}] VERIFY PASSED. Flagged for QA."
            )

        return all_passed

    async def _phase_notes(self, ctx: TaskContext) -> None:
        """
        NOTES phase: Document journey and create handoff.

        - Complete journey notes
        - Link commits
        - Create documenter handoff
        """
        self.log.info("NOTES phase", task_id=str(ctx.task_id))

        # Generate handoff using LLM
        prompt = f"""
Create a documentation handoff for this completed task:

Task: {ctx.title}
Commits: {", ".join(ctx.commits)}
Journal:
{chr(10).join(ctx.journal_entries)}

Create a handoff summary including:
1. What was built
2. Key changes
3. Documentation needed
4. Code samples to include
"""
        handoff = await self.think(prompt)

        ctx.journal_entries.append(
            f"[{datetime.utcnow().isoformat()}] Handoff created for documenter"
        )

        # Update task status
        await self._update_task_status(ctx.task_id, TaskStatus.AWAITING_QA)

    async def _phase_close(self, ctx: TaskContext) -> bool:
        """
        CLOSE phase: After QA + documentation approval.

        - Verify QA approved
        - Verify documentation complete
        - Mark task completed

        Returns True if closed, False if waiting.
        """
        self.log.info("CLOSE phase", task_id=str(ctx.task_id))

        # Check if QA approved (simulated - would check actual status)
        qa_approved = await self._check_qa_approved(ctx.task_id)
        doc_complete = await self._check_docs_complete(ctx.task_id)

        if qa_approved and doc_complete:
            await self._update_task_status(ctx.task_id, TaskStatus.COMPLETED)
            await self.send_message(
                self._cell_channel_id or ctx.task_id,
                f"TASK-{str(ctx.task_id)[:8]} completed!",
                message_type="action",
            )
            return True

        return False

    async def _handle_blocked(self, ctx: TaskContext) -> bool:
        """
        Handle blocked state.

        - Document blocker
        - Notify PM
        - Wait for resolution

        Returns True if resolved.
        """
        self.log.info("BLOCKED", task_id=str(ctx.task_id), blockers=ctx.blockers)

        if ctx.blockers:
            blocker = ctx.blockers[-1]
            await self.send_message(
                self._cell_channel_id or ctx.task_id,
                f"BLOCKED on TASK-{str(ctx.task_id)[:8]}: {blocker}",
                message_type="blocker",
            )
            await self._update_task_status(ctx.task_id, TaskStatus.BLOCKED)

        # Check if blocker resolved (simulated)
        resolved = False
        if resolved:
            ctx.blockers.clear()

        return resolved

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    async def _find_paused_task(self) -> UUID | None:
        """Find own paused/interrupted tasks."""
        # TODO: Query task API for paused tasks assigned to this agent
        return None

    async def _find_assigned_task(self) -> UUID | None:
        """Find tasks assigned to this agent."""
        # TODO: Query task API for assigned tasks
        return None

    async def _signal_availability(self) -> None:
        """Signal availability to PM."""
        await self.send_message(
            self._cell_channel_id or self.id,
            f"{self.name} available for new tasks",
            message_type="dialogue",
        )

    async def _get_task_title(self, task_id: UUID) -> str:
        """Get task title from API."""
        # TODO: Query task API
        return f"Task {str(task_id)[:8]}"

    async def _read_task_requirements(self, task_id: UUID) -> str:
        """Read task requirements from task record."""
        # TODO: Read from .tasks/active/TASK-XXX/
        return "Requirements placeholder"

    async def _update_task_status(self, task_id: UUID, status: TaskStatus) -> None:
        """Update task status via API."""
        # TODO: Update via task API
        self.log.info("Task status updated", task_id=str(task_id), status=status.value)

    async def _check_qa_approved(self, task_id: UUID) -> bool:
        """Check if QA has approved the task."""
        # TODO: Check via task API
        return True  # Simulated

    async def _check_docs_complete(self, task_id: UUID) -> bool:
        """Check if documentation is complete."""
        # TODO: Check via task API
        return True  # Simulated


def create_backend_developer(
    name: str = "BE-Dev-1",
    system_prompt: str | None = None,
) -> DeveloperAgent:
    """Factory function to create a backend developer agent."""
    if system_prompt is None:
        # Load from blueprint file
        blueprint_path = Path("agents/blueprints/backend/be-dev.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            # Extract system prompt section (between ```blocks after ## System Prompt)
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a backend developer."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        system_prompt=system_prompt,
        capabilities=["code_execution", "git_operations", "file_management"],
    )

    return DeveloperAgent(config)


def create_frontend_developer(
    name: str = "FE-Dev-1",
    system_prompt: str | None = None,
) -> DeveloperAgent:
    """Factory function to create a frontend developer agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/frontend/fe-dev.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a frontend developer."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.DEVELOPER,
        team=Team.FRONTEND,
        system_prompt=system_prompt,
        capabilities=["code_execution", "git_operations", "file_management"],
    )

    return DeveloperAgent(config)


def create_ux_developer(
    name: str = "UX-Dev-1",
    system_prompt: str | None = None,
) -> DeveloperAgent:
    """Factory function to create a UX/UI developer agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/ux_ui/ux-dev.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            import re

            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are a UX/UI developer."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.DEVELOPER,
        team=Team.UX_UI,
        system_prompt=system_prompt,
        capabilities=["design_tools", "file_management"],
    )

    return DeveloperAgent(config)
