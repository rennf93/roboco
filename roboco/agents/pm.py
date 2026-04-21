"""
PM Agents (Cell PM and Main PM)

Implementation of PM workflows from the blueprint.
Cell PM:
    MONITOR → TRIAGE → ASSIGN → FACILITATE → ESCALATE → TRACK → REPORT
Main PM:
    OVERSEE → RECEIVE → PRIORITIZE → COORDINATE → DISTRIBUTE → REPORT UP → FACILITATE
"""

from typing import Any
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.agents.mixins import CyclicPhaseConfig, CyclicPhaseRunner
from roboco.models import NotificationType, TaskStatus
from roboco.models.agents import (
    CellPMPhase,
    CellStatus,
    Escalation,
    MainPMPhase,
    TaskAssignment,
)

logger = structlog.get_logger()


class CellPMAgent(Agent, CyclicPhaseRunner[CellPMPhase]):
    """
    Cell PM agent that manages a single cell (Backend, Frontend, or UX/UI).

    Workflow:
    1. MONITOR - Watch cell channel, track tasks, check health
    2. TRIAGE - Assess new tasks, prioritize
    3. ASSIGN - Match tasks to devs
    4. FACILITATE - Answer questions, remove blockers
    5. ESCALATE - Escalate beyond cell's control to Main PM
    6. TRACK - Monitor progress, update estimates
    7. REPORT - Status to Main PM
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize Cell PM agent."""
        super().__init__(config)
        self._current_phase = CellPMPhase.MONITOR
        self._cell_status = CellStatus(name=self.cell_name)
        self._pending_tasks: list[UUID] = []
        self._pending_escalations: list[Escalation] = []
        self._cell_channel_id: UUID | None = None

    async def _initialize(self) -> None:
        """Initialize Cell PM-specific resources."""
        self.log.debug("Cell PM agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup Cell PM-specific resources."""
        self._pending_tasks.clear()
        self._pending_escalations.clear()
        self.log.debug("Cell PM agent cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # CYCLIC PHASE RUNNER IMPLEMENTATION
    # =========================================================================

    def _get_cyclic_phase_configs(self) -> list[CyclicPhaseConfig[CellPMPhase]]:
        """Define the Cell PM workflow phases."""
        return [
            CyclicPhaseConfig(
                CellPMPhase.MONITOR,
                self._phase_monitor,
                CellPMPhase.TRIAGE,
            ),
            CyclicPhaseConfig(
                CellPMPhase.TRIAGE,
                self._phase_triage,
                CellPMPhase.ASSIGN,
            ),
            CyclicPhaseConfig(
                CellPMPhase.ASSIGN,
                self._phase_assign,
                CellPMPhase.FACILITATE,
            ),
            CyclicPhaseConfig(
                CellPMPhase.FACILITATE,
                self._phase_facilitate,
                CellPMPhase.ESCALATE,
            ),
            CyclicPhaseConfig(
                CellPMPhase.ESCALATE,
                self._phase_escalate,
                CellPMPhase.TRACK,
            ),
            CyclicPhaseConfig(
                CellPMPhase.TRACK,
                self._phase_track,
                CellPMPhase.REPORT,
            ),
            CyclicPhaseConfig(
                CellPMPhase.REPORT,
                self._phase_report,
                CellPMPhase.MONITOR,  # Cycle back
            ),
        ]

    # =========================================================================
    # LIFECYCLE IMPLEMENTATION
    # =========================================================================

    async def find_work(self) -> UUID | None:
        """
        Find work for the PM.

        Priority:
        1. Paused tasks with all subtasks complete (ready for closure)
        2. Assigned tasks in progress
        3. Fall back to cyclic management duties (self.id)
        """
        # Check for paused tasks ready for closure
        ready_task = await self._find_paused_task_ready_for_closure()
        if ready_task:
            self.log.info(
                "Found paused task ready for closure", task_id=str(ready_task)
            )
            return ready_task

        # Check for assigned in-progress tasks
        assigned_task = await self._find_assigned_task()
        if assigned_task:
            self.log.info("Found assigned task", task_id=str(assigned_task))
            return assigned_task

        # Fall back to cyclic management duties
        return self.id

    async def _find_paused_task_ready_for_closure(self) -> UUID | None:
        """
        Find own paused tasks where ALL descendants are complete.

        This is the key PM workflow: delegate subtasks, pause, get respawned
        when subtasks complete, then review and close.

        Important: Checks ALL descendants (children, grandchildren, etc.) not just
        direct children, to ensure the entire task tree is complete before closure.
        """
        try:
            # Get my paused tasks
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "paused", "assigned_to": str(self.id)},
            )
            paused_tasks = (
                result.get("items", result) if isinstance(result, dict) else result
            )

            for task in paused_tasks:
                task_id = task.get("id")
                if not task_id:
                    continue

                # Check ALL descendants (children, grandchildren, etc.)
                # Uses /tasks/{id}/descendants endpoint which does recursive BFS
                descendants = await self._api_call(
                    "GET",
                    f"/tasks/{task_id}/descendants",
                )
                # Descendants endpoint returns list directly
                if isinstance(descendants, dict):
                    descendants = descendants.get("items", [])

                if not descendants:
                    continue  # No descendants - not a delegation task

                # Check if ALL descendants are in terminal states
                all_complete = all(
                    d.get("status") in ("completed", "cancelled") for d in descendants
                )

                if all_complete:
                    return UUID(task_id) if isinstance(task_id, str) else task_id

            return None

        except Exception as e:
            self.log.warning(
                "Failed to find paused tasks ready for closure", error=str(e)
            )
            return None

    async def _find_assigned_task(self) -> UUID | None:
        """Find tasks assigned to this PM that need work.

        Checks for tasks in priority order:
        1. pending - newly assigned, needs claiming
        2. claimed - claimed but not started
        3. in_progress - active work
        4. awaiting_pm_review - tasks ready for PM approval
        """
        statuses_to_check = ["pending", "claimed", "in_progress", "awaiting_pm_review"]

        for status in statuses_to_check:
            try:
                result = await self._api_call(
                    "GET",
                    "/tasks",
                    params={"status": status, "assigned_to": str(self.id)},
                )
                tasks = (
                    result.get("items", result) if isinstance(result, dict) else result
                )
                if tasks:
                    task_id = tasks[0]["id"]
                    self.log.info(
                        "Found assigned task",
                        task_id=str(task_id),
                        status=status,
                    )
                    return UUID(task_id) if isinstance(task_id, str) else task_id
            except Exception as e:
                self.log.warning(
                    "Failed to find assigned task",
                    status=status,
                    error=str(e),
                )

        return None

    async def execute_task(self, task_id: UUID) -> bool:
        """
        Execute PM work.

        Two modes:
        1. task_id == self.id: Run cyclic management duties
        2. task_id is real task: Work on specific task (CLAIM → PLAN → START → ...)

        Returns True when task-specific work is complete, False for cyclic duties.
        """
        # Cyclic management duties (no specific task)
        if task_id == self.id:
            error = await self._run_phase_cycle()
            if error:
                self.log.error(
                    "Error in PM phase", phase=self._current_phase.value, error=error
                )
            return False  # Never complete - continuous duty

        # Task-specific work - delegate to task workflow
        return await self._execute_pm_task(task_id)

    async def _execute_pm_task(self, task_id: UUID) -> bool:
        """
        Execute PM workflow on a specific task.

        PM Workflow: CLAIM → PLAN → START → EXECUTE (delegate) → PAUSE → COMPLETE

        Returns True when PM work is done (delegated or completed).
        """
        try:
            task = await self._api_call("GET", f"/tasks/{task_id}")
            status = task.get("status")
            return await self._handle_pm_task_status(task_id, task, status)
        except Exception as e:
            self.log.error(
                "Error in PM task execution", task_id=str(task_id), error=str(e)
            )
            return False

    async def _handle_pm_task_status(
        self, task_id: UUID, task: dict[str, Any], status: str | None
    ) -> bool:
        """Handle PM task based on current status."""
        if status == "pending":
            await self._mark_claimed(task_id)
            self.log.info("PM claimed task", task_id=str(task_id))
            return False

        if status == "claimed":
            return await self._handle_claimed_task(task_id, task)

        if status == "in_progress":
            return await self._handle_in_progress_task(task_id, task)

        if status == "paused":
            # Paused tasks need to resume before completion
            # Lifecycle: paused → in_progress → completed
            await self._api_call("POST", f"/tasks/{task_id}/resume")
            self.log.info("PM resumed paused task", task_id=str(task_id))
            # Now complete via proper endpoint (validates PM role, checks subtasks)
            await self._api_call("POST", f"/tasks/{task_id}/complete")
            self.log.info("PM completed task", task_id=str(task_id))
            return True

        return False

    async def _handle_claimed_task(self, task_id: UUID, task: dict[str, Any]) -> bool:
        """Handle claimed task - plan then start."""
        if not task.get("plan"):
            plan = await self._create_pm_plan(task)
            await self._api_call("PATCH", f"/tasks/{task_id}", json={"plan": plan})
            self.log.info("PM planned task", task_id=str(task_id))
            return False

        await self._mark_in_progress(task_id)
        self.log.info("PM started task", task_id=str(task_id))
        return False

    async def _handle_in_progress_task(
        self, task_id: UUID, task: dict[str, Any]
    ) -> bool:
        """Handle in_progress task - delegate and pause."""
        delegated = await self._delegate_task(task_id, task)
        if delegated:
            remaining = ["Review subtask completions", "Close task"]
            await self._api_call(
                "POST",
                f"/tasks/{task_id}/pause",
                json={
                    "reason": "Awaiting subtask completion",
                    "checkpoint_summary": "Delegated to cell agents",
                    "remaining_work": remaining,
                },
            )
            self.log.info("PM delegated and paused", task_id=str(task_id))
        return True

    async def _create_pm_plan(self, task: dict[str, Any]) -> dict[str, Any]:
        """Create a PM triage plan for a task."""
        return {
            "approach": "Triage and delegate to cell developers",
            "steps": [
                "Analyze requirements",
                "Identify subtasks",
                "Assign to available developers",
                "Create work session",
                "Monitor progress",
            ],
            "risks": task.get("acceptance_criteria", [])[:2],
            "estimated_sessions": 1,
        }

    async def _delegate_task(self, task_id: UUID, task: dict[str, Any]) -> bool:
        """Delegate task by creating subtasks for developers.

        Follows blueprint workflow:
        CREATE (backlog) → SESSION → ACTIVATE (pending) → NOTIFY
        """
        # Find available developer
        best_dev = await self._find_best_dev(task_id)
        if not best_dev:
            self.log.warning("No available developer for task", task_id=str(task_id))
            return False

        team = self.team.value if self.team else "backend"

        # Step 1: Create subtask with status "backlog" (prevents premature pickup)
        subtask_resp = await self._api_call(
            "POST",
            "/tasks",
            json={
                "title": f"Implement: {task.get('title', 'Task')}",
                "description": task.get("description", ""),
                "team": team,
                "acceptance_criteria": task.get("acceptance_criteria", []),
                "parent_task_id": str(task_id),
                "assigned_to": str(best_dev.agent_id),
                "status": "backlog",  # Backlog until session is ready
            },
        )
        subtask_id = subtask_resp.get("id")
        if not subtask_id:
            self.log.error("Failed to create subtask", parent_task_id=str(task_id))
            return False

        self.log.info(
            "PM created subtask (backlog)",
            subtask_id=subtask_id,
            parent_task_id=str(task_id),
            assigned_to=str(best_dev.agent_id),
        )

        # Step 2: Create session for the subtask
        channel_slug = self._get_team_channel(team)
        try:
            await self._api_call(
                "POST",
                "/sessions/for-tasks",
                json={
                    "task_ids": [subtask_id],
                    "channel_slug": channel_slug,
                    "scope": f"Work session for {task.get('title', 'task')}",
                    "relationship_type": "implements",
                },
            )
            self.log.info("Session created for subtask", subtask_id=subtask_id)
        except Exception as e:
            self.log.warning(
                "Session creation failed, activating anyway",
                subtask_id=subtask_id,
                error=str(e),
            )

        # Step 3: Activate subtask (changes status to pending)
        try:
            await self._api_call("POST", f"/tasks/{subtask_id}/activate")
            self.log.info("Subtask activated", subtask_id=subtask_id)
        except Exception as e:
            self.log.error(
                "Failed to activate subtask",
                subtask_id=subtask_id,
                error=str(e),
            )
            return False

        # Step 4: Notify assigned developer
        try:
            await self._notify_developer(best_dev, subtask_id, task)
        except Exception as e:
            self.log.warning(
                "Failed to notify developer (task still assigned)",
                error=str(e),
            )

        return True

    def _get_team_channel(self, team: str) -> str:
        """Get the channel slug for a team."""
        channel_map = {
            "backend": "backend-cell",
            "frontend": "frontend-cell",
            "ux_ui": "uxui-cell",
        }
        return channel_map.get(team, "backend-cell")

    async def _notify_developer(
        self, dev: Any, subtask_id: str, task: dict[str, Any]
    ) -> None:
        """Notify developer of new task assignment."""
        await self._api_call(
            "POST",
            "/notifications",
            json={
                "type": "task_assigned",
                "priority": "normal",
                "to_agents": [str(dev.agent_id)],
                "subject": f"New task: {task.get('title', 'Task')}",
                "body": (
                    f"You've been assigned a new task.\n\n"
                    f"Task ID: {subtask_id}\n"
                    f"Title: {task.get('title', 'Task')}\n\n"
                    f"Use roboco_task_scan to find and claim it."
                ),
                "related_task_id": subtask_id,
                "requires_ack": False,
            },
        )

    # =========================================================================
    # CELL PM PHASES
    # =========================================================================

    async def _phase_monitor(self) -> None:
        """
        MONITOR phase: Watch cell health.

        - Watch cell channel
        - Track active tasks
        - Check for blockers
        """
        self.log.debug("MONITOR phase")

        # Update cell status
        self._cell_status.active_tasks = await self._count_active_tasks()
        self._cell_status.blocked_tasks = await self._count_blocked_tasks()
        self._cell_status.available_devs = await self._count_available_devs()

        # Check for concerning patterns
        if self._cell_status.blocked_tasks > 0:
            self._cell_status.concerns.append(
                f"{self._cell_status.blocked_tasks} blocked tasks"
            )

    async def _phase_triage(self) -> None:
        """
        TRIAGE phase: Assess and prioritize new tasks.
        """
        self.log.debug("TRIAGE phase")

        # Get unassigned tasks
        new_tasks = await self._get_unassigned_tasks()

        for task_id in new_tasks:
            # Use TOON for token-efficient context encoding
            triage_context = self.format_context_labeled(
                "Task Triage",
                {"task_id": str(task_id), "cell": self.cell_name},
            )

            prompt = f"""Assess this task for prioritization:

{triage_context}

Consider:
1. Complexity (low/medium/high)
2. Dependencies on other tasks
3. Priority (P0-P3)
4. Best dev fit based on skills

Format response as TOON:
{{complexity,dependencies,priority,dev_fit}}:
medium,TASK-abc123,P1,backend-dev-1
"""
            assessment = await self.think(prompt)
            self.log.info(
                "Task assessed", task_id=str(task_id), assessment=assessment[:100]
            )
            self._pending_tasks.append(task_id)

    async def _phase_assign(self) -> None:
        """
        ASSIGN phase: Match tasks to developers.
        """
        self.log.debug("ASSIGN phase")

        while self._pending_tasks and self._cell_status.available_devs > 0:
            task_id = self._pending_tasks.pop(0)

            # Find best dev
            assignment = await self._find_best_dev(task_id)
            if assignment:
                await self._assign_task(assignment)
                self._cell_status.available_devs -= 1

    async def _resolve_blocked_tasks(self) -> None:
        """Attempt to unblock any blocked tasks whose blocker has been resolved."""
        blocked_tasks = await self._get_blocked_tasks()
        for task_id in blocked_tasks:
            resolved = await self._check_blocker_resolved(task_id)
            if not resolved:
                continue
            success = await self._unblock_task(task_id)
            if not success:
                continue
            _, session_id = await self._get_task_info(task_id)
            await self.send_message(
                session_id,
                f"TASK-{str(task_id)[:8]} unblocked - blocker resolved",
                message_type="action",
                task_id=task_id,
            )

    def _build_question_prompt(self, question_content: str) -> str:
        """Build the think() prompt for a cell-member question."""
        question_context = self.format_context_labeled(
            "Cell Question",
            {"question": question_content, "cell": self.cell_name},
        )
        return f"""A cell member needs help:

{question_context}

As the Cell PM, provide:
1. Answer if you can
2. Or route to appropriate person
3. Or escalate if needed

Be helpful and unblock the team.
"""

    @staticmethod
    def _parse_task_id(task_id_raw: Any) -> UUID | None:
        """Parse a raw task_id (str or UUID or None) into a UUID or None."""
        if not task_id_raw:
            return None
        if isinstance(task_id_raw, str):
            return UUID(task_id_raw)
        return task_id_raw  # type: ignore[no-any-return]

    async def _answer_cell_question(self, question_data: dict[str, Any]) -> None:
        """Process a single pending cell-member question and respond."""
        question_content = question_data.get("content", "")
        task_id_raw = question_data.get("task_id")
        session_id = question_data.get("session_id")

        prompt = self._build_question_prompt(question_content)
        response = await self.think(prompt)

        if session_id:
            parsed_task_id = self._parse_task_id(task_id_raw)
            await self.send_message(
                UUID(session_id) if isinstance(session_id, str) else session_id,
                response,
                message_type="answer",
                task_id=parsed_task_id,
            )
            self.log.info(
                "PM responded to question",
                task_id=str(task_id_raw) if task_id_raw else None,
                response_preview=response[:100],
            )
        else:
            self.log.info(
                "PM response (no session - using channel)",
                task_id=str(task_id_raw) if task_id_raw else None,
                response_preview=response[:100],
            )

    async def _phase_facilitate(self) -> None:
        """
        FACILITATE phase: Help cell members.

        - Answer questions
        - Clarify requirements
        - Remove small blockers
        - Unblock blocked tasks when blocker is resolved
        """
        self.log.debug("FACILITATE phase")

        await self._resolve_blocked_tasks()

        questions = await self._get_pending_questions()
        for question_data in questions:
            await self._answer_cell_question(question_data)

    async def _phase_escalate(self) -> None:
        """
        ESCALATE phase: Handle issues beyond cell control.
        """
        self.log.debug("ESCALATE phase")

        for escalation in self._pending_escalations:
            # Notify Main PM
            await self._notify_main_pm(escalation)

        self._pending_escalations.clear()

    async def _phase_track(self) -> None:
        """
        TRACK phase: Monitor task progress.
        """
        self.log.debug("TRACK phase")

        active_tasks = await self._get_active_tasks()

        for task_id in active_tasks:
            progress = await self._check_task_progress(task_id)
            if progress.get("at_risk"):
                self._cell_status.concerns.append(f"Task {str(task_id)[:8]} at risk")

    async def _phase_report(self) -> None:
        """
        REPORT phase: Status to Main PM.
        """
        self.log.debug("REPORT phase")

        concerns = self._format_concerns()
        report = f"""
## {self.cell_name} Status Report

**Active Tasks**: {self._cell_status.active_tasks}
**Blocked Tasks**: {self._cell_status.blocked_tasks}
**Completed Today**: {self._cell_status.completed_today}
**Available Devs**: {self._cell_status.available_devs}

**Concerns**:
{concerns}
"""
        # Would send to #pm-all channel
        self.log.info("Report generated", report_length=len(report))
        self._cell_status.concerns.clear()

    def _format_concerns(self) -> str:
        """Format concerns for report."""
        if not self._cell_status.concerns:
            return "- None"
        return chr(10).join(f"- {c}" for c in self._cell_status.concerns)

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    async def _count_active_tasks(self) -> int:
        """Count active tasks in cell."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "in_progress", "team": team_param},
            )
            return len(result.get("items", []))
        except Exception as e:
            self.log.warning("Failed to count active tasks", error=str(e))
            return 0

    async def _count_blocked_tasks(self) -> int:
        """Count blocked tasks in cell."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "blocked", "team": team_param},
            )
            return len(result.get("items", []))
        except Exception as e:
            self.log.warning("Failed to count blocked tasks", error=str(e))
            return 0

    async def _count_available_devs(self) -> int:
        """Count available developers."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/agents",
                params={"role": "developer", "status": "idle", "team": team_param},
            )
            return len(result.get("items", []))
        except Exception as e:
            self.log.warning("Failed to count available devs", error=str(e))
            return 0

    async def _get_unassigned_tasks(self) -> list[UUID]:
        """Get tasks needing assignment."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "pending", "team": team_param, "assigned_to": None},
            )
            return [UUID(t["id"]) for t in result.get("items", [])]
        except Exception as e:
            self.log.warning("Failed to get unassigned tasks", error=str(e))
            return []

    async def _find_best_dev(self, task_id: UUID) -> TaskAssignment | None:
        """Find best developer for a task."""
        try:
            # Get available devs
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/agents",
                params={"role": "developer", "status": "idle", "team": team_param},
            )
            agents = result.get("items", [])
            if not agents:
                return None

            # For now, assign to first available
            agent = agents[0]
            return TaskAssignment(
                task_id=task_id,
                agent_id=UUID(agent["id"]),
                agent_name=agent.get("name", "Unknown"),
                reason="First available developer",
            )
        except Exception as e:
            self.log.warning("Failed to find best dev", error=str(e))
            return None

    async def _assign_task(self, assignment: TaskAssignment) -> None:
        """Assign a task to a developer."""
        try:
            await self._api_call(
                "PUT",
                f"/tasks/{assignment.task_id}",
                json={"assigned_to": str(assignment.agent_id)},
            )
            self.log.info(
                "Task assigned",
                task_id=str(assignment.task_id),
                agent=assignment.agent_name,
            )
        except Exception as e:
            self.log.error("Failed to assign task", error=str(e))

    async def _get_pending_questions(self) -> list[dict[str, Any]]:
        """Get unanswered questions from channel.

        Returns full message info including task_id for routing.
        """
        try:
            result = await self._api_call(
                "GET",
                "/messages",
                params={"message_type": "dialogue", "unanswered": True},
            )
            return [
                {
                    "content": m.get("content", ""),
                    "task_id": m.get("task_id"),
                    "session_id": m.get("session_id"),
                    "from_agent": m.get("from_agent"),
                }
                for m in result.get("items", [])
            ]
        except Exception as e:
            self.log.warning("Failed to get pending questions", error=str(e))
            return []

    async def _notify_main_pm(self, escalation: Escalation) -> None:
        """
        Notify Main PM of escalation.

        Uses NotificationType.BLOCKER_ESCALATION to formally escalate the issue.
        """
        # Build notification content
        notification_type = NotificationType.BLOCKER_ESCALATION
        task_ref = str(escalation.task_id)[:8] if escalation.task_id else "N/A"
        subject = f"Escalation from {self.cell_name}: {escalation.issue[:50]}"
        body = f"""
## Escalation from {self.cell_name}

**Issue:** {escalation.issue}
**Severity:** {escalation.severity}
**Task:** {task_ref}

**Proposed Solution:**
{escalation.proposed_solution or "No solution proposed"}

Please review and provide guidance.
"""

        self.log.info(
            "Escalation sent to Main PM",
            subject=subject,
            body_length=len(body),
            notification_type=notification_type.value,
            severity=escalation.severity,
        )

    async def _get_active_tasks(self) -> list[UUID]:
        """Get all active tasks in cell."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "in_progress", "team": team_param},
            )
            return [UUID(t["id"]) for t in result.get("items", [])]
        except Exception as e:
            self.log.warning("Failed to get active tasks", error=str(e))
            return []

    async def _get_blocked_tasks(self) -> list[UUID]:
        """Get all blocked tasks in cell."""
        try:
            team_param = self.team.value if self.team else None
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "blocked", "team": team_param},
            )
            return [UUID(t["id"]) for t in result.get("items", [])]
        except Exception as e:
            self.log.warning("Failed to get blocked tasks", error=str(e))
            return []

    async def _check_blocker_resolved(self, task_id: UUID) -> bool:
        """
        Check if a task's blocker has been resolved.

        This examines the blocker_reason and checks if conditions are met.
        For subtask blockers, checks if all subtasks are complete.
        """
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            blocker_reason = result.get("blocker_reason", "")

            # Check if subtasks are complete (common blocker)
            subtasks = result.get("subtasks", [])
            if subtasks:
                all_complete = all(s.get("status") == "completed" for s in subtasks)
                if all_complete:
                    return True

            # If no specific logic, return False (needs manual review)
            return not blocker_reason  # Resolved if reason was cleared

        except Exception as e:
            self.log.warning("Failed to check blocker", error=str(e))
            return False

    async def _check_task_progress(self, task_id: UUID) -> dict[str, Any]:
        """
        Check progress of a task.

        Returns risk assessment based on task status and time in state.
        """
        try:
            result = await self._api_call("GET", f"/tasks/{task_id}")
            status = result.get("status", "")
            risk_factors = []

            # Blocked tasks are always at risk
            if status == TaskStatus.BLOCKED.value:
                risk_factors.append("Task is blocked")

            return {
                "at_risk": len(risk_factors) > 0,
                "status": status,
                "risk_factors": risk_factors,
            }
        except Exception as e:
            self.log.warning("Failed to check task progress", error=str(e))
            return {
                "at_risk": False,
                "status": TaskStatus.IN_PROGRESS.value,
                "risk_factors": [],
            }


class MainPMAgent(Agent, CyclicPhaseRunner[MainPMPhase]):
    """
    Main PM agent that coordinates all cells.

    Workflow:
    1. OVERSEE - Monitor all cells
    2. RECEIVE - Get direction from Board, escalations from Cell PMs
    3. PRIORITIZE - Translate Board direction to cell priorities
    4. COORDINATE - Resolve cross-cell issues
    5. DISTRIBUTE - Push tasks/priorities to Cell PMs
    6. REPORT UP - Status to Board
    7. FACILITATE - All-hands coordination
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize Main PM agent."""
        super().__init__(config)
        self._current_phase = MainPMPhase.OVERSEE
        self._cell_statuses: dict[str, CellStatus] = {}
        self._board_directives: list[str] = []
        self._cross_cell_issues: list[dict[str, Any]] = []

    async def _initialize(self) -> None:
        """Initialize Main PM-specific resources."""
        self.log.debug("Main PM agent initialized", agent_id=str(self.id))

    async def _cleanup(self) -> None:
        """Cleanup Main PM-specific resources."""
        self._cell_statuses.clear()
        self._board_directives.clear()
        self._cross_cell_issues.clear()
        self.log.debug("Main PM agent cleanup complete", agent_id=str(self.id))

    # =========================================================================
    # CYCLIC PHASE RUNNER IMPLEMENTATION
    # =========================================================================

    def _get_cyclic_phase_configs(self) -> list[CyclicPhaseConfig[MainPMPhase]]:
        """Define the Main PM workflow phases."""
        return [
            CyclicPhaseConfig(
                MainPMPhase.OVERSEE,
                self._phase_oversee,
                MainPMPhase.RECEIVE,
            ),
            CyclicPhaseConfig(
                MainPMPhase.RECEIVE,
                self._phase_receive,
                MainPMPhase.PRIORITIZE,
            ),
            CyclicPhaseConfig(
                MainPMPhase.PRIORITIZE,
                self._phase_prioritize,
                MainPMPhase.COORDINATE,
            ),
            CyclicPhaseConfig(
                MainPMPhase.COORDINATE,
                self._phase_coordinate,
                MainPMPhase.DISTRIBUTE,
            ),
            CyclicPhaseConfig(
                MainPMPhase.DISTRIBUTE,
                self._phase_distribute,
                MainPMPhase.REPORT_UP,
            ),
            CyclicPhaseConfig(
                MainPMPhase.REPORT_UP,
                self._phase_report_up,
                MainPMPhase.FACILITATE,
            ),
            CyclicPhaseConfig(
                MainPMPhase.FACILITATE,
                self._phase_facilitate,
                MainPMPhase.OVERSEE,  # Cycle back
            ),
        ]

    # =========================================================================
    # LIFECYCLE IMPLEMENTATION
    # =========================================================================

    async def find_work(self) -> UUID | None:
        """
        Find work for the Main PM.

        Priority:
        1. Paused tasks with all subtasks complete (ready for closure)
        2. Assigned tasks in progress
        3. Fall back to cyclic management duties (self.id)
        """
        # Check for paused tasks ready for closure
        ready_task = await self._find_paused_task_ready_for_closure()
        if ready_task:
            self.log.info(
                "Found paused task ready for closure", task_id=str(ready_task)
            )
            return ready_task

        # Check for assigned in-progress tasks
        assigned_task = await self._find_assigned_task()
        if assigned_task:
            self.log.info("Found assigned task", task_id=str(assigned_task))
            return assigned_task

        # Fall back to cyclic management duties
        return self.id

    async def _find_paused_task_ready_for_closure(self) -> UUID | None:
        """
        Find own paused tasks where ALL descendants are complete.

        This is the key PM workflow: delegate subtasks, pause, get respawned
        when subtasks complete, then review and close.

        Important: Checks ALL descendants (children, grandchildren, etc.) not just
        direct children, to ensure the entire task tree is complete before closure.
        """
        try:
            # Get my paused tasks
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "paused", "assigned_to": str(self.id)},
            )
            paused_tasks = (
                result.get("items", result) if isinstance(result, dict) else result
            )

            for task in paused_tasks:
                task_id = task.get("id")
                if not task_id:
                    continue

                # Check ALL descendants (children, grandchildren, etc.)
                # Uses /tasks/{id}/descendants endpoint which does recursive BFS
                descendants = await self._api_call(
                    "GET",
                    f"/tasks/{task_id}/descendants",
                )
                # Descendants endpoint returns list directly
                if isinstance(descendants, dict):
                    descendants = descendants.get("items", [])

                if not descendants:
                    continue  # No descendants - not a delegation task

                # Check if ALL descendants are in terminal states
                all_complete = all(
                    d.get("status") in ("completed", "cancelled") for d in descendants
                )

                if all_complete:
                    return UUID(task_id) if isinstance(task_id, str) else task_id

            return None

        except Exception as e:
            self.log.warning(
                "Failed to find paused tasks ready for closure", error=str(e)
            )
            return None

    async def _find_assigned_task(self) -> UUID | None:
        """Find tasks assigned to this Main PM that need work.

        Checks for tasks in priority order:
        1. pending - newly assigned, needs claiming
        2. claimed - claimed but not started
        3. in_progress - active work
        4. awaiting_pm_review - tasks ready for PM approval
        """
        statuses_to_check = ["pending", "claimed", "in_progress", "awaiting_pm_review"]

        for status in statuses_to_check:
            try:
                result = await self._api_call(
                    "GET",
                    "/tasks",
                    params={"status": status, "assigned_to": str(self.id)},
                )
                tasks = (
                    result.get("items", result) if isinstance(result, dict) else result
                )
                if tasks:
                    task_id = tasks[0]["id"]
                    self.log.info(
                        "Found assigned task",
                        task_id=str(task_id),
                        status=status,
                    )
                    return UUID(task_id) if isinstance(task_id, str) else task_id
            except Exception as e:
                self.log.warning(
                    "Failed to find assigned task",
                    status=status,
                    error=str(e),
                )

        return None

    async def execute_task(self, task_id: UUID) -> bool:
        """
        Execute Main PM work.

        Two modes:
        1. task_id == self.id: Run cyclic coordination duties
        2. task_id is real task: Work on specific task (CLAIM → PLAN → START → ...)

        Returns True when task-specific work is complete, False for cyclic duties.
        """
        # Cyclic coordination duties (no specific task)
        if task_id == self.id:
            error = await self._run_phase_cycle()
            if error:
                self.log.error(
                    "Error in Main PM phase",
                    phase=self._current_phase.value,
                    error=error,
                )
            return False  # Never complete - continuous duty

        # Task-specific work - delegate to task workflow
        return await self._execute_main_pm_task(task_id)

    async def _execute_main_pm_task(self, task_id: UUID) -> bool:
        """
        Execute Main PM workflow on a specific task.

        Main PM Workflow: CLAIM → PLAN → START → DISTRIBUTE → PAUSE → COMPLETE

        Returns True when Main PM work is done (distributed or completed).
        """
        try:
            task = await self._api_call("GET", f"/tasks/{task_id}")
            status = task.get("status")
            return await self._handle_main_pm_task_status(task_id, task, status)
        except Exception as e:
            self.log.error(
                "Error in Main PM task execution",
                task_id=str(task_id),
                error=str(e),
            )
            return False

    async def _handle_main_pm_task_status(
        self, task_id: UUID, task: dict[str, Any], status: str | None
    ) -> bool:
        """Handle Main PM task based on current status."""
        if status == "pending":
            await self._mark_claimed(task_id)
            self.log.info("Main PM claimed task", task_id=str(task_id))
            return False

        if status == "claimed":
            return await self._handle_main_pm_claimed(task_id, task)

        if status == "in_progress":
            return await self._handle_main_pm_in_progress(task_id, task)

        if status == "paused":
            # Paused tasks need to resume before completion
            # Lifecycle: paused → in_progress → completed
            await self._api_call("POST", f"/tasks/{task_id}/resume")
            self.log.info("Main PM resumed paused task", task_id=str(task_id))
            # Now complete via proper endpoint (validates PM role, checks subtasks)
            await self._api_call("POST", f"/tasks/{task_id}/complete")
            self.log.info("Main PM completed task", task_id=str(task_id))
            return True

        return False

    def _get_team_channel(self, team: str) -> str:
        """Get the channel slug for a team."""
        channel_map = {
            "backend": "backend-cell",
            "frontend": "frontend-cell",
            "ux_ui": "uxui-cell",
        }
        return channel_map.get(team, "dev-all")

    async def _handle_main_pm_claimed(
        self, task_id: UUID, task: dict[str, Any]
    ) -> bool:
        """Handle claimed task - plan then start."""
        if not task.get("plan"):
            plan = await self._create_main_pm_plan(task)
            await self._api_call("PATCH", f"/tasks/{task_id}", json={"plan": plan})
            self.log.info("Main PM planned task", task_id=str(task_id))
            return False

        await self._mark_in_progress(task_id)
        self.log.info("Main PM started task", task_id=str(task_id))
        return False

    async def _handle_main_pm_in_progress(
        self, task_id: UUID, task: dict[str, Any]
    ) -> bool:
        """Handle in_progress task - distribute and pause."""
        distributed = await self._distribute_to_cells(task_id, task)
        if distributed:
            remaining = ["Monitor cell progress", "Close initiative"]
            await self._api_call(
                "POST",
                f"/tasks/{task_id}/pause",
                json={
                    "reason": "Awaiting cell completion",
                    "checkpoint_summary": "Distributed to Cell PMs",
                    "remaining_work": remaining,
                },
            )
            self.log.info("Main PM distributed and paused", task_id=str(task_id))
        return True

    async def _create_main_pm_plan(self, task: dict[str, Any]) -> dict[str, Any]:
        """Create a Main PM coordination plan for an initiative."""
        return {
            "approach": "Coordinate across cells to deliver initiative",
            "steps": [
                "Analyze initiative requirements",
                "Identify cell responsibilities",
                "Create tasks for Cell PMs",
                "Set up cross-cell sessions",
                "Monitor and coordinate",
            ],
            "risks": task.get("acceptance_criteria", [])[:2],
            "estimated_sessions": 2,
        }

    async def _distribute_to_cells(self, task_id: UUID, task: dict[str, Any]) -> bool:
        """Distribute initiative to appropriate Cell PMs."""
        title = task.get("title", "").lower()
        description = task.get("description", "").lower()
        content = title + description

        cells_needed = self._determine_cells_needed(content)

        for team, pm_slug in cells_needed:
            await self._create_cell_task(task_id, task, team, pm_slug)

        return len(cells_needed) > 0

    def _determine_cells_needed(self, content: str) -> list[tuple[str, str]]:
        """Determine which cells are needed based on content keywords."""
        cells = []
        backend_kw = ["api", "backend", "database", "server"]
        frontend_kw = ["ui", "frontend", "component", "page"]
        ux_kw = ["design", "ux", "figma", "mockup"]

        if any(kw in content for kw in backend_kw):
            cells.append(("backend", "be-pm"))
        if any(kw in content for kw in frontend_kw):
            cells.append(("frontend", "fe-pm"))
        if any(kw in content for kw in ux_kw):
            cells.append(("ux_ui", "ux-pm"))

        return cells if cells else [("backend", "be-pm")]

    async def _create_cell_task(
        self, parent_id: UUID, task: dict[str, Any], team: str, pm_slug: str
    ) -> None:
        """Create a task for a Cell PM.

        Follows blueprint workflow:
        CREATE (backlog) → GROUP → SESSION → ACTIVATE (pending) → NOTIFY
        """
        # Step 1: Create task with status "backlog"
        task_resp = await self._api_call(
            "POST",
            "/tasks",
            json={
                "title": f"[{team.upper()}] {task.get('title', 'Task')}",
                "description": task.get("description", ""),
                "team": team,
                "acceptance_criteria": task.get("acceptance_criteria", []),
                "parent_task_id": str(parent_id),
                "assigned_to": pm_slug,
                "status": "backlog",  # Backlog until session is ready
            },
        )
        cell_task_id = task_resp.get("id")
        if not cell_task_id:
            self.log.error("Failed to create cell task", parent_id=str(parent_id))
            return

        self.log.info(
            "Main PM created cell task (backlog)",
            cell_task_id=cell_task_id,
            parent_task_id=str(parent_id),
            team=team,
            assigned_to=pm_slug,
        )

        # Step 2: Create group if needed (cross-cell initiatives)
        channel_slug = self._get_team_channel(team)

        # Step 3: Create session for the cell task
        try:
            await self._api_call(
                "POST",
                "/sessions/for-tasks",
                json={
                    "task_ids": [cell_task_id],
                    "channel_slug": channel_slug,
                    "scope": f"Cell work for {task.get('title', 'initiative')}",
                    "relationship_type": "implements",
                },
            )
            self.log.info("Session created for cell task", cell_task_id=cell_task_id)
        except Exception as e:
            self.log.warning(
                "Session creation failed, activating anyway",
                cell_task_id=cell_task_id,
                error=str(e),
            )

        # Step 4: Activate task (changes status to pending)
        try:
            await self._api_call("POST", f"/tasks/{cell_task_id}/activate")
            self.log.info("Cell task activated", cell_task_id=cell_task_id)
        except Exception as e:
            self.log.error(
                "Failed to activate cell task",
                cell_task_id=cell_task_id,
                error=str(e),
            )
            return

        # Step 5: Notify Cell PM
        try:
            await self._api_call(
                "POST",
                "/notifications",
                json={
                    "type": "task_assigned",
                    "priority": "normal",
                    "to_agents": [pm_slug],
                    "subject": f"New initiative: {task.get('title', 'Task')}",
                    "body": (
                        f"A new initiative has been assigned to your cell.\n\n"
                        f"Task ID: {cell_task_id}\n"
                        f"Title: {task.get('title', 'Task')}\n\n"
                        f"Please triage and delegate to your team."
                    ),
                    "related_task_id": cell_task_id,
                    "requires_ack": True,
                },
            )
        except Exception as e:
            self.log.warning(
                "Failed to notify Cell PM (task still assigned)",
                error=str(e),
            )

    # =========================================================================
    # MAIN PM PHASES
    # =========================================================================

    async def _phase_oversee(self) -> None:
        """OVERSEE phase: Monitor all cells."""
        self.log.debug("OVERSEE phase")

        # Collect status from all cells
        for cell_name in ["backend-cell", "frontend-cell", "uxui-cell"]:
            status = await self._get_cell_status(cell_name)
            self._cell_statuses[cell_name] = status

        # Look for cross-cell issues
        self._cross_cell_issues = await self._detect_cross_cell_issues()

    async def _phase_receive(self) -> None:
        """RECEIVE phase: Get direction and escalations."""
        self.log.debug("RECEIVE phase")

        # Check for Board directives
        self._board_directives = await self._get_board_directives()

        # Check for Cell PM escalations
        escalations = await self._get_cell_pm_escalations()
        for esc in escalations:
            self.log.info("Received escalation", issue=esc.get("issue"))

    async def _phase_prioritize(self) -> None:
        """PRIORITIZE phase: Set cross-cell priorities."""
        self.log.debug("PRIORITIZE phase")

        if self._board_directives:
            # Build status data for TOON encoding
            cell_status_data = {
                name: {"active": s.active_tasks, "blocked": s.blocked_tasks}
                for name, s in self._cell_statuses.items()
            }

            # Use TOON for token-efficient context encoding
            priority_context = self.format_context_labeled(
                "Prioritization Context",
                {
                    "directives": self._board_directives,
                    "cell_status": cell_status_data,
                },
            )

            prompt = f"""Translate these Board directives into cell priorities:

{priority_context}

Format response as TOON tabular:
[N,]{{cell,priority,task_description}}:
backend-cell,P0,Implement critical auth fix
frontend-cell,P1,Update dashboard layout
"""
            priorities = await self.think(prompt)
            self.log.info("Priorities set", priorities=priorities[:200])

    async def _phase_coordinate(self) -> None:
        """COORDINATE phase: Resolve cross-cell issues."""
        self.log.debug("COORDINATE phase")

        for issue in self._cross_cell_issues:
            # Use TOON for token-efficient context encoding
            issue_context = self.format_context_labeled(
                "Cross-Cell Issue",
                {
                    "description": issue.get("description"),
                    "cells": issue.get("cells"),
                    "task_id": issue.get("task_id"),
                },
            )

            prompt = f"""Resolve this cross-cell issue:

{issue_context}

Propose a resolution that unblocks all parties.
"""
            resolution = await self.think(prompt)
            await self._apply_resolution(issue, resolution)

        self._cross_cell_issues.clear()

    async def _phase_distribute(self) -> None:
        """DISTRIBUTE phase: Push priorities to Cell PMs."""
        self.log.debug("DISTRIBUTE phase")

        for directive in self._board_directives:
            # Determine which cell(s) need this
            cell = self._route_directive(directive)
            if cell:
                await self._notify_cell_pm(cell, directive)

        self._board_directives.clear()

    async def _phase_report_up(self) -> None:
        """REPORT UP phase: Status to Board."""
        self.log.debug("REPORT UP phase")

        report = """
## Organization Status Report

### Cell Summary
"""
        for cell_name, status in self._cell_statuses.items():
            report += f"""
**{cell_name}**:
- Active: {status.active_tasks}
- Blocked: {status.blocked_tasks}
- Available: {status.available_devs}
"""

        # Would send to #main-pm-board
        self.log.info("Board report generated", report_length=len(report))

    async def _phase_facilitate(self) -> None:
        """FACILITATE phase: All-hands coordination."""
        self.log.debug("FACILITATE phase")

        # Check for all-hands items
        # Process improvements, announcements, etc.

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    async def _get_cell_status(self, cell_name: str) -> CellStatus:
        """Get status of a cell."""
        try:
            # Get task counts per status for this cell
            team = cell_name.replace("-cell", "")
            active_result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "in_progress", "team": team},
            )
            blocked_result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "blocked", "team": team},
            )
            devs_result = await self._api_call(
                "GET",
                "/agents",
                params={"role": "developer", "status": "idle", "team": team},
            )
            return CellStatus(
                name=cell_name,
                active_tasks=len(active_result.get("items", [])),
                blocked_tasks=len(blocked_result.get("items", [])),
                available_devs=len(devs_result.get("items", [])),
            )
        except Exception as e:
            self.log.warning("Failed to get cell status", cell=cell_name, error=str(e))
            return CellStatus(name=cell_name)

    async def _detect_cross_cell_issues(self) -> list[dict[str, Any]]:
        """Detect cross-cell dependencies and issues."""
        try:
            # Look for tasks blocked by other cells
            result = await self._api_call(
                "GET",
                "/tasks",
                params={"status": "blocked"},
            )
            issues = []
            for task in result.get("items", []):
                blocker = task.get("blocker_reason", "")
                if "frontend" in blocker.lower() or "backend" in blocker.lower():
                    issues.append(
                        {
                            "description": f"Cross-cell blocker: {blocker}",
                            "cells": [task.get("team", "unknown")],
                            "task_id": task.get("id"),
                        }
                    )
            return issues
        except Exception as e:
            self.log.warning("Failed to detect cross-cell issues", error=str(e))
            return []

    async def _get_board_directives(self) -> list[str]:
        """Get directives from Board."""
        try:
            result = await self._api_call(
                "GET",
                "/messages",
                params={"channel": "main-pm-board", "message_type": "action"},
            )
            return [m.get("content", "") for m in result.get("items", [])]
        except Exception as e:
            self.log.warning("Failed to get board directives", error=str(e))
            return []

    async def _get_cell_pm_escalations(self) -> list[dict[str, Any]]:
        """Get escalations from Cell PMs."""
        try:
            result = await self._api_call(
                "GET",
                "/notifications",
                params={"type": "escalation", "status": "pending"},
            )
            items: list[dict[str, Any]] = result.get("items", [])
            return items
        except Exception as e:
            self.log.warning("Failed to get escalations", error=str(e))
            return []

    async def _apply_resolution(
        self,
        issue: dict[str, Any],
        resolution: str,
    ) -> None:
        """Apply a cross-cell resolution."""
        self.log.info(
            "Resolution applied",
            issue=issue.get("description"),
            resolution_length=len(resolution),
        )

    def _route_directive(self, directive: str) -> str | None:
        """Route a directive to appropriate cell."""
        directive_lower = directive.lower()
        if "backend" in directive_lower or "api" in directive_lower:
            return "backend-cell"
        elif "frontend" in directive_lower or "ui" in directive_lower:
            return "frontend-cell"
        elif "ux" in directive_lower or "design" in directive_lower:
            return "uxui-cell"
        return None

    async def _notify_cell_pm(self, cell: str, directive: str) -> None:
        """Notify a Cell PM of a directive."""
        self.log.info("Directive sent", cell=cell, directive=directive[:50])
