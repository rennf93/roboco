"""
Task MCP Server

Exposes task management tools to Claude Code agents with built-in
enforcement of task lifecycle rules.

Tools:
- roboco_task_scan: List available tasks (paused, assigned, available)
- roboco_task_get: Get task details
- roboco_task_claim: Claim a task
- roboco_task_plan: Submit implementation plan
- roboco_task_start: Start working on task
- roboco_task_progress: Update progress
- roboco_task_block: Mark task as blocked
- roboco_task_unblock: Unblock task
- roboco_task_pause: Pause task
- roboco_task_submit_qa: Submit for QA review
- roboco_task_qa_pass: Pass QA (QA role only)
- roboco_task_qa_fail: Fail QA (QA role only)
- roboco_task_docs_complete: Mark docs complete (Documenter only)
- roboco_task_complete: Mark task complete (PM only, after docs)
- roboco_task_create: Create new task (PM only)
- roboco_task_assign: Assign task to agent (PM only)
- roboco_task_cancel: Cancel a task (PM/Board only)
- roboco_task_escalate: Escalate task up hierarchy (all agents)
- roboco_session_create_for_tasks: Create work session for tasks (PM only)
- roboco_session_link_task: Link session to task (PM only)
- roboco_session_unlink_task: Unlink session from task (PM only)
- roboco_session_get_for_task: Get sessions for a task (all agents)
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.mcp.schemas import (
    SessionCreateForTasksInput,
    SessionLinkTaskInput,
    TaskAssignInput,
    TaskBlockInput,
    TaskCreateInput,
    TaskEscalateInput,
    TaskPauseInput,
)
from roboco.mcp.tasks.handlers import (
    handle_agent_idle,
    handle_docs_complete,
    handle_session_create_for_tasks,
    handle_session_get_for_task,
    handle_session_link_task,
    handle_session_unlink_task,
    handle_task_activate,
    handle_task_assign,
    handle_task_block,
    handle_task_cancel,
    handle_task_claim,
    handle_task_complete,
    handle_task_create,
    handle_task_escalate,
    handle_task_get,
    handle_task_pause,
    handle_task_plan,
    handle_task_progress,
    handle_task_qa_fail,
    handle_task_qa_pass,
    handle_task_scan,
    handle_task_start,
    handle_task_submit_qa,
    handle_task_submit_verification,
    handle_task_unblock,
)
from roboco.mcp.utils import ApiClient


def _register_core_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register core task lifecycle tools."""

    @mcp.tool()
    async def roboco_task_scan(team: str | None = None) -> dict[str, Any]:
        """
        Scan for available tasks.

        Returns tasks in priority order:
        1. PAUSED tasks (yours) - must resume these first
        2. ASSIGNED tasks (explicitly given to you)
        3. AVAILABLE tasks (team pool, can claim)

        Args:
            team: Optional team filter (backend, frontend, uxui)

        Returns:
            Dict with paused/assigned/available tasks and guidance
        """
        return await handle_task_scan(client, team, agent_id)

    @mcp.tool()
    async def roboco_task_get(task_id: str) -> dict[str, Any]:
        """
        Get detailed information about a task.

        Args:
            task_id: The task UUID

        Returns:
            Task details with current status and guidance
        """
        return await handle_task_get(client, task_id)

    @mcp.tool()
    async def roboco_task_claim(task_id: str) -> dict[str, Any]:
        """
        Claim a task to work on it.

        ENFORCEMENT:
        - Task must be in 'pending' status
        - You cannot claim if you have an active (non-waiting) task
        - Paused tasks must be resumed first

        Args:
            task_id: The task UUID to claim

        Returns:
            Claimed task with project context and next step guidance
        """
        return await handle_task_claim(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_plan(
        task_id: str,
        approach: str,
        steps: list[dict[str, str]],
        risks: list[str] | None = None,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Submit implementation plan for a task.

        NOTE: The 'steps' parameter creates a CHECKLIST within this task's plan.
        These are NOT real database subtasks. To create actual subtasks that
        other agents can claim and work on, use roboco_task_create() with
        parent_task_id instead.

        ENFORCEMENT:
        - Task must be in 'claimed' status
        - You must be the assigned agent

        Args:
            task_id: The task UUID
            approach: High-level approach description
            steps: List of plan steps (checklist) with 'title' and 'description'
            risks: Optional list of identified risks
            open_questions: Optional questions (BLOCKS start if present)

        Returns:
            Updated task with guidance
        """
        plan_params = {
            "approach": approach,
            "sub_tasks": steps,
            "risks": risks,
            "open_questions": open_questions,
        }
        return await handle_task_plan(client, task_id, plan_params, agent_id)

    @mcp.tool()
    async def roboco_task_start(task_id: str) -> dict[str, Any]:
        """
        Start working on a task.

        ENFORCEMENT:
        - Task must be in 'claimed' or 'paused' status
        - Plan must be submitted first (for claimed tasks)
        - You must be the assigned agent

        Args:
            task_id: The task UUID

        Returns:
            Updated task with execution guidance
        """
        return await handle_task_start(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_progress(
        task_id: str, message: str, percentage: int
    ) -> dict[str, Any]:
        """
        Update task progress.

        ENFORCEMENT:
        - Percentage is REQUIRED (0-100) to show real progress
        - Message must describe what was accomplished

        Args:
            task_id: The task UUID
            message: Progress update message describing work done
            percentage: Completion percentage (0-100), required

        Returns:
            Updated task
        """
        return await handle_task_progress(
            client, task_id, message, percentage, agent_id
        )

    @mcp.tool()
    async def roboco_agent_idle() -> dict[str, Any]:
        """
        Signal that you have no work and should go idle.

        Call this when roboco_task_scan returns no tasks.
        Your container will be terminated to save resources.
        You will be automatically respawned when new work is available.

        Returns:
            Confirmation of idle state
        """
        return await handle_agent_idle(client, agent_id)


def _register_blocking_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register blocking/unblocking/pause tools."""

    @mcp.tool()
    async def roboco_task_block(
        task_id: str, reason: str, blocker_type: str, what_needed: str
    ) -> dict[str, Any]:
        """
        Mark task as blocked.

        ENFORCEMENT:
        - Task must be in 'in_progress' status
        - Reason and what_needed are required

        Args:
            task_id: The task UUID
            reason: Why the task is blocked
            blocker_type: Type (external/internal/question/dependency)
            what_needed: What is needed to unblock

        Returns:
            Updated task with options
        """
        data = TaskBlockInput(
            task_id=task_id,
            reason=reason,
            blocker_type=blocker_type,
            what_needed=what_needed,
        )
        return await handle_task_block(client, data, agent_id)

    @mcp.tool()
    async def roboco_task_unblock(task_id: str) -> dict[str, Any]:
        """
        Unblock a task and resume work.

        ENFORCEMENT:
        - Task must be in 'blocked' status
        - You must be the assigned agent

        Args:
            task_id: The task UUID

        Returns:
            Updated task ready for work
        """
        return await handle_task_unblock(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_pause(
        task_id: str, reason: str, checkpoint_summary: str, remaining_work: list[str]
    ) -> dict[str, Any]:
        """
        Pause a task (e.g., for higher priority work).

        ENFORCEMENT:
        - Task must be in 'in_progress' status
        - Checkpoint is required for context restoration

        Args:
            task_id: The task UUID
            reason: Why pausing
            checkpoint_summary: Summary of current state
            remaining_work: List of remaining sub-tasks

        Returns:
            Paused task with resume instructions
        """
        data = TaskPauseInput(
            task_id=task_id,
            reason=reason,
            checkpoint_summary=checkpoint_summary,
            remaining_work=remaining_work,
        )
        return await handle_task_pause(client, data, agent_id)


def _register_qa_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register QA and verification tools."""

    @mcp.tool()
    async def roboco_task_submit_verification(task_id: str) -> dict[str, Any]:
        """
        Submit task for self-verification.

        ENFORCEMENT:
        - Task must be in 'in_progress' status
        - At least one commit should exist

        Args:
            task_id: The task UUID

        Returns:
            Task in verifying status with checklist
        """
        return await handle_task_submit_verification(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_submit_qa(
        task_id: str, dev_notes: str, handoff_summary: str
    ) -> dict[str, Any]:
        """
        Submit task for QA review.

        ENFORCEMENT:
        - Task must be in 'verifying' status
        - Dev notes and handoff summary required

        Args:
            task_id: The task UUID
            dev_notes: Journey notes from development
            handoff_summary: Summary for QA reviewer

        Returns:
            Task submitted for QA
        """
        return await handle_task_submit_qa(
            client, task_id, dev_notes, handoff_summary, agent_id
        )

    @mcp.tool()
    async def roboco_task_qa_pass(task_id: str, qa_notes: str) -> dict[str, Any]:
        """
        Pass a task through QA (QA role only).

        ENFORCEMENT:
        - Caller must have QA role
        - Task must be in 'awaiting_qa' status
        - QA notes required

        Args:
            task_id: The task UUID
            qa_notes: QA review notes

        Returns:
            Task ready for documentation
        """
        return await handle_task_qa_pass(client, task_id, qa_notes, agent_id)

    @mcp.tool()
    async def roboco_task_qa_fail(
        task_id: str, qa_notes: str, issues: list[str]
    ) -> dict[str, Any]:
        """
        Fail a task in QA review (QA role only).

        ENFORCEMENT:
        - Caller must have QA role
        - Task must be in 'awaiting_qa' status
        - Issues list required

        Args:
            task_id: The task UUID
            qa_notes: QA review notes
            issues: List of specific issues found

        Returns:
            Task returned for revision
        """
        return await handle_task_qa_fail(client, task_id, qa_notes, issues, agent_id)

    @mcp.tool()
    async def roboco_task_docs_complete(
        task_id: str, doc_notes: str | None = None
    ) -> dict[str, Any]:
        """
        Mark documentation as complete (documenter only).

        Transitions task from awaiting_documentation to awaiting_pm_review.
        The Cell PM will then review and complete the task.

        ENFORCEMENT:
        - Only documenters can use this tool
        - Task must be in 'awaiting_documentation' status

        Args:
            task_id: The task UUID
            doc_notes: Optional notes about the documentation completed

        Returns:
            Task now awaiting PM review
        """
        return await handle_docs_complete(client, task_id, agent_id, doc_notes)

    @mcp.tool()
    async def roboco_task_complete(task_id: str) -> dict[str, Any]:
        """
        Mark task as completed (PM only).

        Only PMs can complete tasks, after documenter marks docs complete.
        This is the final step in the workflow: Dev → QA → Documenter → PM.

        ENFORCEMENT:
        - Only PMs can use this tool
        - Task must be in 'awaiting_pm_review' status

        Args:
            task_id: The task UUID

        Returns:
            Completed task
        """
        return await handle_task_complete(client, task_id, agent_id)


def _register_pm_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register PM delegation and management tools."""

    @mcp.tool()
    async def roboco_task_create(data: TaskCreateInput) -> dict[str, Any]:
        """
        Create a new task (PM and management only).

        Use this to:
        - Create subtasks when breaking down complex work
        - Create new tasks for your team (Cell PM)
        - Create tasks for any team (Main PM, Board)

        ENFORCEMENT:
        - Only PMs and management can create tasks
        - Cell PMs can only create tasks for their own team

        Args:
            data: TaskCreateInput with title, description, acceptance_criteria,
                  team, and optional parent_task_id, assigned_to, priority, status.
                  Use status="backlog" for subtasks needing session setup.

        Returns:
            Created task with next step guidance
        """
        return await handle_task_create(client, data, agent_id)

    @mcp.tool()
    async def roboco_task_assign(task_id: str, assignee: str) -> dict[str, Any]:
        """
        Assign a task to an agent (PM and management only).

        Use this to:
        - Delegate work to team members
        - Reassign tasks to different agents
        - Hand off tasks to other PMs for their teams

        ENFORCEMENT:
        - Only PMs and management can assign tasks
        - Cell PMs can only assign within their own team

        Args:
            task_id: The task UUID to assign
            assignee: Agent slug to assign (e.g., "be-dev-1", "fe-pm")

        Returns:
            Updated task with assignment confirmation
        """
        input_data = TaskAssignInput(task_id=task_id, assignee=assignee)
        return await handle_task_assign(client, input_data, agent_id)

    @mcp.tool()
    async def roboco_task_cancel(
        task_id: str, reason: str | None = None
    ) -> dict[str, Any]:
        """
        Cancel a task (PM and board only).

        Use this to:
        - Cancel obsolete or duplicate tasks
        - Cancel tasks that are no longer needed
        - Cancel blocked tasks that cannot be resolved

        ENFORCEMENT:
        - Only PMs and board members can cancel tasks
        - Cannot cancel completed or already-cancelled tasks

        Args:
            task_id: The task UUID to cancel
            reason: Optional reason for cancellation

        Returns:
            Cancelled task confirmation
        """
        return await handle_task_cancel(client, task_id, agent_id, reason)

    @mcp.tool()
    async def roboco_task_escalate(
        task_id: str, reason: str, escalate_to: str | None = None
    ) -> dict[str, Any]:
        """
        Escalate a task up the management hierarchy.

        Use this when:
        - Task is blocked by something outside your control
        - You need PM guidance or decision
        - Task scope has grown beyond your authority
        - Cross-team coordination is needed

        Escalation chain:
        - Developer/QA/Doc -> Cell PM
        - Cell PM -> Main PM
        - Main PM -> Product Owner

        Args:
            task_id: The task UUID to escalate
            reason: Why this task needs escalation (be specific)
            escalate_to: Optional specific target (overrides default chain)

        Returns:
            Task with escalation confirmation
        """
        input_data = TaskEscalateInput(
            task_id=task_id, reason=reason, escalate_to=escalate_to
        )
        return await handle_task_escalate(client, input_data, agent_id)

    @mcp.tool()
    async def roboco_task_activate(task_id: str) -> dict[str, Any]:
        """
        Activate a task from BACKLOG to PENDING status (PM only).

        This is the FINAL STEP in task setup. After creating and assigning
        a task, you MUST:
        1. Create a session: roboco_session_create_for_tasks()
        2. Activate the task: roboco_task_activate()

        Only after activation will the orchestrator spawn agents to work on it.

        ENFORCEMENT:
        - Only PMs and management can activate tasks
        - Task must be in BACKLOG status
        - Task MUST have at least one linked session

        Args:
            task_id: The task UUID to activate

        Returns:
            Activated task with PENDING status
        """
        return await handle_task_activate(client, task_id, agent_id)


def _register_session_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register session management tools."""

    @mcp.tool()
    async def roboco_session_create_for_tasks(
        data: SessionCreateForTasksInput,
    ) -> dict[str, Any]:
        """
        Create a work session linked to one or more tasks (PM only).

        Use this to:
        - Create a discussion context for a task or set of related tasks
        - Enable assigned agents to communicate about the work
        - Set up planning/review sessions for complex tasks

        SCOPE LEVELS:
        - "initiative": Cross-cell sessions in #dev-all (Main PM only)
        - "cell": Cell-specific sessions in team channel (Cell PM default)
        - "task": Individual task execution (Developer level)

        ENFORCEMENT:
        - Only PMs and management can create task-linked sessions
        - Cell PMs can only create sessions in their team's channel

        Args:
            data: SessionCreateForTasksInput with task_ids, channel_slug,
                  scope (initiative/cell/task), and relationship_type

        Returns:
            Created session with task links
        """
        return await handle_session_create_for_tasks(client, data, agent_id)

    @mcp.tool()
    async def roboco_session_link_task(
        data: SessionLinkTaskInput,
    ) -> dict[str, Any]:
        """
        Link an existing session to a task (PM only).

        Use this to:
        - Add additional tasks to an existing session
        - Link related tasks to the same discussion context
        - Mark a session as primary for a specific task

        ENFORCEMENT:
        - Only PMs and management can link sessions to tasks
        - One primary session per task (use is_primary carefully)

        Args:
            data: SessionLinkTaskInput with session_id, task_id,
                  optional is_primary and relationship_type

        Returns:
            Created link confirmation
        """
        return await handle_session_link_task(client, data, agent_id)

    @mcp.tool()
    async def roboco_session_unlink_task(
        session_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        """
        Remove a task from a session (PM only).

        Use this to:
        - Remove tasks that are no longer relevant to the session
        - Clean up session-task links after task completion

        ENFORCEMENT:
        - Only PMs and management can unlink sessions from tasks

        Args:
            session_id: Session ID to unlink from
            task_id: Task ID to unlink

        Returns:
            Unlink confirmation
        """
        return await handle_session_unlink_task(client, session_id, task_id, agent_id)

    @mcp.tool()
    async def roboco_session_get_for_task(task_id: str) -> dict[str, Any]:
        """
        Get all sessions linked to a task.

        Use this to:
        - Find the discussion context for a task you're working on
        - Check if a task has a primary session
        - See all related sessions (planning, review, etc.)

        Args:
            task_id: Task ID to query sessions for

        Returns:
            List of sessions with their relationship types
        """
        return await handle_session_get_for_task(client, task_id, agent_id)


def create_task_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Task MCP server for a specific agent.

    The agent_id is embedded in the server to enforce ownership rules.

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-task-{agent_id}", json_response=True)
    client = ApiClient(agent_id)

    # Register all tools via helper functions
    _register_core_tools(mcp, client, agent_id)
    _register_blocking_tools(mcp, client, agent_id)
    _register_qa_tools(mcp, client, agent_id)
    _register_pm_tools(mcp, client, agent_id)
    _register_session_tools(mcp, client, agent_id)

    return mcp


if __name__ == "__main__":
    import sys

    _MIN_ARGS = 2
    if len(sys.argv) < _MIN_ARGS:
        print("Usage: python task_server.py <agent_id>")
        sys.exit(1)

    agent_id_cli = sys.argv[1]
    server = create_task_mcp_server(agent_id_cli)
    server.run()
