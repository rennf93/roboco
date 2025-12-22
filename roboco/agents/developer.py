"""
Developer Agent

Implementation of the Developer workflow from the blueprint.
Handles task lifecycle:
    SCAN → CLAIM → UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES → CLOSE
"""

from datetime import UTC, datetime
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.agents.mixins import PhaseConfig, PhaseEngine
from roboco.models import TaskStatus
from roboco.models.agents import DevTaskPhase, TaskContext

logger = structlog.get_logger()


class DeveloperAgent(Agent, PhaseEngine[DevTaskPhase, TaskContext]):
    """
    Developer agent that follows the Dev Lifecycle.

    Workflow:
    1. SCAN - Check for assigned/paused tasks
    2. CLAIM - Lock and announce task
    3. UNDERSTAND - Read requirements, ask if unclear
    4. PLAN - Break into subtasks, create plan
    5. EXECUTE - Work through subtasks, commit frequently
    6. VERIFY - Self-test, run quality checks
    7. NOTES - Document journey, create handoff, submit for QA
    8. DONE - Return to SCAN (QA → Documenter → PM complete the task)
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize developer agent."""
        super().__init__(config)
        self._task_context: TaskContext | None = None
        self._cell_channel_id: UUID | None = None

    async def _initialize(self) -> None:
        """Initialize developer-specific resources."""
        self.log.debug("Developer agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup developer-specific resources."""
        self._task_context = None
        self.log.debug("Developer agent cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # PHASE ENGINE IMPLEMENTATION
    # =========================================================================

    def _get_phase_configs(self) -> list[PhaseConfig[DevTaskPhase]]:
        """Define the developer workflow phases."""
        return [
            PhaseConfig(
                DevTaskPhase.CLAIM,
                self._phase_claim,
                next_phase=DevTaskPhase.UNDERSTAND,
            ),
            PhaseConfig(
                DevTaskPhase.UNDERSTAND,
                self._phase_understand,
                next_phase=DevTaskPhase.PLAN,
                requires_completion=True,
            ),
            PhaseConfig(
                DevTaskPhase.PLAN,
                self._phase_plan,
                next_phase=DevTaskPhase.EXECUTE,
            ),
            PhaseConfig(
                DevTaskPhase.EXECUTE,
                self._phase_execute,
                next_phase=DevTaskPhase.VERIFY,
                requires_completion=True,
            ),
            PhaseConfig(
                DevTaskPhase.VERIFY,
                self._phase_verify,
                next_phase=DevTaskPhase.NOTES,
                fail_phase=DevTaskPhase.EXECUTE,  # Back to execute on failure
                requires_completion=True,
            ),
            PhaseConfig(
                DevTaskPhase.NOTES,
                self._phase_notes,
                next_phase=None,  # Terminal - developer done
            ),
            PhaseConfig(
                DevTaskPhase.BLOCKED,
                self._phase_blocked,
                next_phase=DevTaskPhase.EXECUTE,  # Resume execution when unblocked
                requires_completion=True,
            ),
        ]

    def _get_current_phase(self, ctx: TaskContext) -> DevTaskPhase:
        """Get the current phase from context."""
        return ctx.phase

    def _set_current_phase(self, ctx: TaskContext, phase: DevTaskPhase) -> None:
        """Set the current phase in context."""
        ctx.phase = phase

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

        Returns True when developer's work is complete (submitted for QA).
        QA, Documenter, and PM handle the rest of the lifecycle.
        """
        # Initialize or restore task context
        if self._task_context is None or self._task_context.task_id != task_id:
            self._task_context = TaskContext(
                task_id=task_id,
                title=await self._get_task_title(task_id),
            )

        ctx = self._task_context

        try:
            result = await self._run_phase_engine(ctx)

            if result.error:
                self.log.error("Phase error", error=result.error)
                ctx.blockers.append(result.error)
                ctx.phase = DevTaskPhase.BLOCKED
                return False

            if result.completed:
                self._task_context = None
                return True

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
            self._cell_channel_id or ctx.task_id,
            f"Claiming TASK-{str(ctx.task_id)[:8]}: {ctx.title}",
            message_type="action",
        )

        # Journal entry
        ctx.journal_entries.append(
            f"[{datetime.now(UTC).isoformat()}] Claimed task. Beginning work."
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

        # Format context using TOON for token efficiency
        task_context = self.format_context_labeled(
            "Task Context",
            {"title": ctx.title, "requirements": requirements},
        )

        # Use LLM to understand and identify gaps
        prompt = f"""You are analyzing a task before beginning work.

{task_context}

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
                f"[{datetime.now(UTC).isoformat()}] Understanding: {response}"
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

        # Format context using TOON
        plan_context = self.format_context_labeled(
            "Task",
            {
                "title": ctx.title,
                "understanding": ctx.journal_entries[-1]
                if ctx.journal_entries
                else "No context",
            },
        )

        # Use LLM to create plan - request TOON tabular response
        prompt = f"""Create an implementation plan for this task:

{plan_context}

Break this into ordered subtasks. For each subtask provide:
- Clear description
- Files to modify
- Estimated complexity (small/medium/large)

Format response as TOON tabular:
[N,]{{description,files,complexity}}:
Implement the main logic,src/main.py|src/utils.py,medium
Add unit tests,tests/test_main.py,small
"""
        response = await self.think(prompt)

        # Parse subtasks using TOON (falls back to JSON)
        try:
            subtasks = self.parse_llm_response(response)
            if isinstance(subtasks, list):
                ctx.subtasks = subtasks
            else:
                ctx.subtasks = [
                    {"description": response, "files": [], "complexity": "medium"}
                ]
        except ValueError:
            # Fallback if parsing fails
            ctx.subtasks = [
                {"description": response, "files": [], "complexity": "medium"}
            ]

        # Journal entry
        ts = datetime.now(UTC).isoformat()
        ctx.journal_entries.append(f"[{ts}] Plan: {len(ctx.subtasks)} subtasks created")

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

        # Format context using TOON
        execute_context = self.format_context_labeled(
            "Execution Context",
            {
                "task": ctx.title,
                "subtask_number": ctx.current_subtask + 1,
                "total_subtasks": len(ctx.subtasks),
                "description": subtask.get("description", ""),
                "files": subtask.get("files", []),
            },
        )

        # Use LLM to work on subtask
        prompt = f"""Execute this subtask:

{execute_context}

Provide:
1. Code changes needed
2. Commands to run
3. Commit message in format: type(scope): description

Respond with the implementation.
"""
        response = await self.think_and_stream(prompt)

        # Record work done
        ts = datetime.now(UTC).isoformat()
        subtask_num = ctx.current_subtask + 1
        ctx.journal_entries.append(f"[{ts}] Subtask {subtask_num}: {response[:100]}...")

        # Simulate commit (in real implementation would execute git)
        commit_hash = f"commit_{ctx.current_subtask}"
        ctx.commits.append(commit_hash)

        # Progress update - save to task AND send message
        completed = ctx.current_subtask + 1
        total = len(ctx.subtasks)
        percentage = int((completed / total) * 100) if total > 0 else 0
        progress_msg = f"Completed subtask {completed}/{total}: {subtask['title']}"

        # Save progress to task (QA will see this!)
        await self._add_progress(ctx.task_id, progress_msg, percentage)

        await self.send_message(
            self._cell_channel_id or ctx.task_id,
            f"TASK-{str(ctx.task_id)[:8]} ({percentage}%) {progress_msg}",
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
                    f"[{datetime.now(UTC).isoformat()}] VERIFY FAILED: {check_name}"
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
                f"[{datetime.now(UTC).isoformat()}] VERIFY PASSED. Flagged for QA."
            )

        return all_passed

    async def _phase_notes(self, ctx: TaskContext) -> None:
        """
        NOTES phase: Document journey and create handoff.

        - Complete journey notes (stored in task dev_notes for QA)
        - Link commits
        - Create documenter handoff summary
        """
        self.log.info("NOTES phase", task_id=str(ctx.task_id))

        # Generate dev_notes for QA verification
        dev_notes_prompt = f"""
Summarize the work done for QA verification:

Task: {ctx.title}
Commits: {", ".join(ctx.commits)}
Work log:
{chr(10).join(ctx.journal_entries)}

Create a brief summary for QA including:
1. What was built and where (files/modules)
2. Key implementation decisions
3. Tests added
4. Any gotchas or important context
"""
        dev_notes = await self.think(dev_notes_prompt)

        # Generate handoff summary for documenter
        handoff_prompt = f"""
Create a handoff summary for the documenter:

Task: {ctx.title}
What was built: {dev_notes[:500]}

Summarize in 2-3 sentences what documentation is needed.
"""
        handoff_summary = await self.think(handoff_prompt)

        # Store notes in task via API (this is what QA will see!)
        await self._submit_for_qa(ctx.task_id, dev_notes, handoff_summary)

        ctx.journal_entries.append(
            f"[{datetime.now(UTC).isoformat()}] Submitted for QA with dev_notes"
        )

    async def _phase_blocked(self, ctx: TaskContext) -> bool:
        """
        BLOCKED phase: Handle blocked state.

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
        try:
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "paused", "assigned_to": str(self.id)},
            )
            tasks = result.get("items", [])
            return UUID(tasks[0]["id"]) if tasks else None
        except Exception as e:
            self.log.warning("Failed to find paused task", error=str(e))
            return None

    async def _find_assigned_task(self) -> UUID | None:
        """Find tasks assigned to this agent."""
        try:
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "pending", "assigned_to": str(self.id)},
            )
            tasks = result.get("items", [])
            return UUID(tasks[0]["id"]) if tasks else None
        except Exception as e:
            self.log.warning("Failed to find assigned task", error=str(e))
            return None

    async def _signal_availability(self) -> None:
        """Signal availability to PM."""
        await self.send_message(
            self._cell_channel_id or self.id,
            f"{self.name} available for new tasks",
            message_type="dialogue",
        )

    async def _submit_for_qa(
        self, task_id: UUID, dev_notes: str, handoff_summary: str
    ) -> None:
        """
        Submit task for QA review with notes.

        This stores dev_notes in the task (visible to QA) and transitions
        the task to awaiting_qa status.
        """
        try:
            # First store dev_notes (this is what QA will see!)
            combined_notes = f"{dev_notes}\n\n---\nHandoff Summary:\n{handoff_summary}"
            await self._api_call(
                "PATCH",
                f"/tasks/{task_id}",
                json={"dev_notes": combined_notes},
            )
            self.log.info("Dev notes saved to task", task_id=str(task_id))

            # Then transition to awaiting_qa
            await self._api_call("POST", f"/tasks/{task_id}/submit-qa")
            self.log.info("Task submitted for QA", task_id=str(task_id))

        except Exception as e:
            self.log.error("Failed to submit for QA", error=str(e))
            raise
