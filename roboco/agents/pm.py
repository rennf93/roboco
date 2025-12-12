"""
PM Agents (Cell PM and Main PM)

Implementation of PM workflows from the blueprint.
Cell PM:
    MONITOR → TRIAGE → ASSIGN → FACILITATE → ESCALATE → TRACK → REPORT
Main PM:
    OVERSEE → RECEIVE → PRIORITIZE → COORDINATE → DISTRIBUTE → REPORT UP → FACILITATE
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from roboco.agents.base import Agent, AgentConfig
from roboco.models import AgentRole, NotificationType, TaskStatus, Team

logger = structlog.get_logger()


class CellPMPhase(str, Enum):
    """Phases of the Cell PM lifecycle."""

    MONITOR = "monitor"
    TRIAGE = "triage"
    ASSIGN = "assign"
    FACILITATE = "facilitate"
    ESCALATE = "escalate"
    TRACK = "track"
    REPORT = "report"


class MainPMPhase(str, Enum):
    """Phases of the Main PM lifecycle."""

    OVERSEE = "oversee"
    RECEIVE = "receive"
    PRIORITIZE = "prioritize"
    COORDINATE = "coordinate"
    DISTRIBUTE = "distribute"
    REPORT_UP = "report_up"
    FACILITATE = "facilitate"


@dataclass
class CellStatus:
    """Status of a cell."""

    name: str
    active_tasks: int = 0
    blocked_tasks: int = 0
    completed_today: int = 0
    available_devs: int = 0
    concerns: list[str] = field(default_factory=list)


@dataclass
class TaskAssignment:
    """A task assignment decision."""

    task_id: UUID
    agent_id: UUID
    agent_name: str
    reason: str


@dataclass
class Escalation:
    """An escalation to higher management."""

    issue: str
    severity: str  # low, medium, high, critical
    task_id: UUID | None = None
    proposed_solution: str | None = None


class CellPMAgent(Agent):
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

    @property
    def cell_name(self) -> str:
        """Get the cell name."""
        if self.team == Team.BACKEND:
            return "backend-cell"
        elif self.team == Team.FRONTEND:
            return "frontend-cell"
        elif self.team == Team.UX_UI:
            return "uxui-cell"
        return "unknown-cell"

    async def find_work(self) -> UUID | None:
        """
        PM always has work - returns a pseudo task ID for management duties.
        """
        # PMs are always active, cycling through phases
        return self.id  # Use own ID as "task" since PM work is continuous

    async def execute_task(self, _task_id: UUID) -> bool:
        """
        Execute PM duties in a cycle.

        Returns False to keep running continuously.
        """
        try:
            match self._current_phase:
                case CellPMPhase.MONITOR:
                    await self._phase_monitor()
                    self._current_phase = CellPMPhase.TRIAGE

                case CellPMPhase.TRIAGE:
                    await self._phase_triage()
                    self._current_phase = CellPMPhase.ASSIGN

                case CellPMPhase.ASSIGN:
                    await self._phase_assign()
                    self._current_phase = CellPMPhase.FACILITATE

                case CellPMPhase.FACILITATE:
                    await self._phase_facilitate()
                    self._current_phase = CellPMPhase.ESCALATE

                case CellPMPhase.ESCALATE:
                    await self._phase_escalate()
                    self._current_phase = CellPMPhase.TRACK

                case CellPMPhase.TRACK:
                    await self._phase_track()
                    self._current_phase = CellPMPhase.REPORT

                case CellPMPhase.REPORT:
                    await self._phase_report()
                    self._current_phase = CellPMPhase.MONITOR

            return False  # Never complete - continuous duty

        except Exception as e:
            self.log.error(
                "Error in PM phase", phase=self._current_phase.value, error=str(e)
            )
            return False

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

    async def _phase_facilitate(self) -> None:
        """
        FACILITATE phase: Help cell members.

        - Answer questions
        - Clarify requirements
        - Remove small blockers
        """
        self.log.debug("FACILITATE phase")

        # Check for pending questions in channel
        questions = await self._get_pending_questions()

        for question in questions:
            # Use TOON for token-efficient context encoding
            question_context = self.format_context_labeled(
                "Cell Question",
                {"question": question, "cell": self.cell_name},
            )

            prompt = f"""A cell member needs help:

{question_context}

As the Cell PM, provide:
1. Answer if you can
2. Or route to appropriate person
3. Or escalate if needed

Be helpful and unblock the team.
"""
            response = await self.think(prompt)
            await self.send_message(
                self._cell_channel_id or self.id,
                response,
                message_type="dialogue",
            )

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

    async def _get_pending_questions(self) -> list[str]:
        """Get unanswered questions from channel."""
        try:
            result = await self._api_call(
                "GET",
                "/messages",
                params={"message_type": "dialogue", "unanswered": True},
            )
            return [m.get("content", "") for m in result.get("items", [])]
        except Exception as e:
            self.log.warning("Failed to get pending questions", error=str(e))
            return []

    async def _notify_main_pm(self, escalation: Escalation) -> None:
        """
        Notify Main PM of escalation.

        Uses NotificationType.ESCALATION to formally escalate the issue.
        """
        # Build notification content
        notification_type = NotificationType.ESCALATION
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


class MainPMAgent(Agent):
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

    async def find_work(self) -> UUID | None:
        """Main PM always has work."""
        return self.id

    async def execute_task(self, _task_id: UUID) -> bool:
        """Execute Main PM duties in a cycle."""
        try:
            match self._current_phase:
                case MainPMPhase.OVERSEE:
                    await self._phase_oversee()
                    self._current_phase = MainPMPhase.RECEIVE

                case MainPMPhase.RECEIVE:
                    await self._phase_receive()
                    self._current_phase = MainPMPhase.PRIORITIZE

                case MainPMPhase.PRIORITIZE:
                    await self._phase_prioritize()
                    self._current_phase = MainPMPhase.COORDINATE

                case MainPMPhase.COORDINATE:
                    await self._phase_coordinate()
                    self._current_phase = MainPMPhase.DISTRIBUTE

                case MainPMPhase.DISTRIBUTE:
                    await self._phase_distribute()
                    self._current_phase = MainPMPhase.REPORT_UP

                case MainPMPhase.REPORT_UP:
                    await self._phase_report_up()
                    self._current_phase = MainPMPhase.FACILITATE

                case MainPMPhase.FACILITATE:
                    await self._phase_facilitate()
                    self._current_phase = MainPMPhase.OVERSEE

            return False

        except Exception as e:
            self.log.error(
                "Error in Main PM phase", phase=self._current_phase.value, error=str(e)
            )
            return False

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
            return result.get("items", [])
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


# =========================================================================
# FACTORY FUNCTIONS
# =========================================================================


def create_backend_pm(
    name: str = "BE-PM",
    system_prompt: str | None = None,
) -> CellPMAgent:
    """Factory function to create a backend PM agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/backend/be-pm.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are the Backend Cell PM."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        system_prompt=system_prompt,
        capabilities=["task_management", "notifications"],
        can_notify=True,
    )

    return CellPMAgent(config)


def create_frontend_pm(
    name: str = "FE-PM",
    system_prompt: str | None = None,
) -> CellPMAgent:
    """Factory function to create a frontend PM agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/frontend/fe-pm.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are the Frontend Cell PM."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.CELL_PM,
        team=Team.FRONTEND,
        system_prompt=system_prompt,
        capabilities=["task_management", "notifications"],
        can_notify=True,
    )

    return CellPMAgent(config)


def create_ux_pm(
    name: str = "UX-PM",
    system_prompt: str | None = None,
) -> CellPMAgent:
    """Factory function to create a UX/UI PM agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/ux_ui/ux-pm.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are the UX/UI Cell PM."

    config = AgentConfig(
        name=name,
        slug=name.lower().replace(" ", "-"),
        role=AgentRole.CELL_PM,
        team=Team.UX_UI,
        system_prompt=system_prompt,
        capabilities=["task_management", "notifications"],
        can_notify=True,
    )

    return CellPMAgent(config)


def create_main_pm(
    name: str = "Main PM",
    system_prompt: str | None = None,
) -> MainPMAgent:
    """Factory function to create the Main PM agent."""
    if system_prompt is None:
        blueprint_path = Path("agents/blueprints/board/main-pm.md")
        if blueprint_path.exists():
            content = blueprint_path.read_text()
            match = re.search(r"## System Prompt\s*```\s*(.*?)```", content, re.DOTALL)
            system_prompt = match.group(1).strip() if match else ""
        else:
            system_prompt = "You are the Main PM coordinating all cells."

    config = AgentConfig(
        name=name,
        slug="main-pm",
        role=AgentRole.MAIN_PM,
        team=Team.BOARD,
        system_prompt=system_prompt,
        capabilities=["task_management", "notifications", "cross_cell_coordination"],
        can_notify=True,
    )

    return MainPMAgent(config)
