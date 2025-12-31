"""
Task MCP Server

Exposes task management tools to Claude Code agents with built-in
enforcement of task lifecycle rules.

Tools (Core - all agents):
- roboco_task_scan: List available tasks (paused, assigned, available)
- roboco_task_get: Get task details
- roboco_task_claim: Claim a task
- roboco_task_plan: Submit implementation plan
- roboco_task_start: Start working on task
- roboco_task_progress: Update progress
- roboco_task_escalate: Escalate task up hierarchy
- roboco_task_substitute: Release task gracefully
- roboco_task_submit_pm_review: Submit non-dev task directly to PM
- roboco_agent_idle: Signal no work available (triggers shutdown)

Tools (Blocking - Developer/PM):
- roboco_task_block: Mark task as blocked
- roboco_task_unblock: Unblock task
- roboco_task_pause: Pause task

Tools (Developer):
- roboco_task_submit_verification: Self-verify before QA
- roboco_task_submit_qa: Submit for QA review

Tools (QA):
- roboco_task_qa_pass: Pass QA
- roboco_task_qa_fail: Fail QA with issues

Tools (Documenter):
- roboco_task_docs_complete: Mark documentation complete

Tools (PM/Board):
- roboco_task_create: Create new task
- roboco_task_assign: Assign task to agent
- roboco_task_activate: Move task from backlog to pending
- roboco_task_complete: Mark task complete (after full workflow)
- roboco_task_cancel: Cancel a task
- roboco_task_escalate_to_ceo: Escalate task to CEO for approval (sends notification)

Tools (Sessions - PM/Board):
- roboco_session_create_for_tasks: Create work session for tasks
- roboco_session_link_task: Link session to task
- roboco_session_unlink_task: Unlink session from task
- roboco_session_get_for_task: Get sessions for a task (all agents)
- roboco_group_create: Create agent groups (Main PM only)
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.mcp.schemas import (
    GroupCreateInput,
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
    handle_escalate_to_ceo,
    handle_group_create,
    handle_session_create_for_tasks,
    handle_session_get_for_task,
    handle_session_link_task,
    handle_session_unlink_task,
    handle_submit_pm_review,
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
    handle_task_substitute,
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
            team: Optional team filter (backend, frontend, ux_ui)

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
    async def roboco_task_substitute(
        task_id: str,
        reason: str,
        details: str,
        suggested_role: str | None = None,
        suggested_team: str | None = None,
    ) -> dict[str, Any]:
        """
        Request to be substituted out of a task.

        Use this to gracefully release a task when you cannot or should not
        continue working on it. This BYPASSES the normal "can't claim while
        in_progress" rule - that's the whole point.

        REASONS (SubstituteReason enum values):
        - low_context: Insufficient context to continue safely
        - out_of_scope_team: Task belongs to different team
        - out_of_scope_role: Task requires different role (e.g., QA, not dev)
        - task_complete: Finished work, releasing for next stage
        - max_retries: Exceeded retry limit, need fresh perspective
        - blocked_external: Need skills outside your capabilities

        EFFECT:
        - Task is released and reassigned (or moved to QA/docs/blocked)
        - You are FREE to claim new work with roboco_task_scan()

        Args:
            task_id: Task UUID to release
            reason: One of: low_context, out_of_scope_team, out_of_scope_role,
                    task_complete, max_retries, blocked_external
            details: Human-readable explanation
            suggested_role: Hint for reassignment (developer, qa, pm, documenter)
            suggested_team: Hint for reassignment (backend, frontend, ux_ui)

        Returns:
            Confirmation with next steps
        """
        return await handle_task_substitute(
            client,
            task_id,
            agent_id,
            reason,
            details,
            suggested_role=suggested_role,
            suggested_team=suggested_team,
        )

    @mcp.tool()
    async def roboco_task_submit_pm_review(
        task_id: str, notes: str | None = None
    ) -> dict[str, Any]:
        """
        Submit task directly for PM review.

        Use this for tasks that don't follow the standard dev→QA→docs workflow:
        - PM validation tasks assigned directly to you
        - QA audit tasks (not reviewing dev work)
        - Any directly-assigned non-dev work

        The task will transition to 'awaiting_pm_review' and the PM
        will verify and complete it.

        WHEN TO USE:
        - You completed a task assigned directly to you (not dev work)
        - The task doesn't need QA/docs review
        - You want the PM to verify and close it

        Args:
            task_id: The task UUID to submit
            notes: Optional completion notes

        Returns:
            Task awaiting PM review
        """
        return await handle_submit_pm_review(client, task_id, agent_id, notes)


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


def _register_developer_submit_tools(
    mcp: FastMCP, client: ApiClient, agent_id: str
) -> None:
    """Register developer-only submission tools (submit_verification, submit_qa)."""

    @mcp.tool()
    async def roboco_task_submit_verification(task_id: str) -> dict[str, Any]:
        """
        Submit task for self-verification (developer only).

        ENFORCEMENT:
        - Only developers can use this tool
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
        Submit task for QA review (developer only).

        ENFORCEMENT:
        - Only developers can use this tool
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


def _register_qa_verdict_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register QA-only verdict tools (qa_pass, qa_fail)."""

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


def _register_documenter_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register documenter-only tools (docs_complete)."""

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


def _register_pm_completion_tools(
    mcp: FastMCP, client: ApiClient, agent_id: str
) -> None:
    """Register PM-only task completion tools."""

    @mcp.tool()
    async def roboco_task_complete(
        task_id: str,
        force_with_cancelled: bool = False,
        justification: str | None = None,
    ) -> dict[str, Any]:
        """
        Mark task as completed (PM only).

        Only PMs can complete tasks, after documenter marks docs complete.
        This is the final step in the workflow: Dev → QA → Documenter → PM.

        ENFORCEMENT:
        - Only PMs can use this tool
        - Task must be in 'awaiting_pm_review' status
        - All subtasks must be completed (or use force_with_cancelled)

        PM Override for cancelled subtasks:
        If some subtasks were cancelled but PM judges work is done anyway,
        use force_with_cancelled=True with justification explaining why.
        Only works if ALL non-completed subtasks are cancelled.

        Args:
            task_id: The task UUID
            force_with_cancelled: Override cancelled subtask check
            justification: Required when force_with_cancelled=True

        Returns:
            Completed task
        """
        return await handle_task_complete(
            client, task_id, agent_id, force_with_cancelled, justification
        )

    @mcp.tool()
    async def roboco_task_escalate_to_ceo(
        task_id: str, notes: str | None = None
    ) -> dict[str, Any]:
        """
        Escalate a task to CEO for final approval (PM only).

        Use this for major tasks that require CEO sign-off before completion:
        - Parent tasks with multiple subtasks
        - High-priority or high-risk features
        - Breaking changes or architectural decisions
        - Tasks that need final executive approval

        ENFORCEMENT:
        - Only PMs and management can escalate to CEO
        - Task must be in 'awaiting_pm_review' status

        After escalation, the CEO will:
        - roboco_task_ceo_approve: Complete the task
        - roboco_task_ceo_reject: Send back for revision

        Args:
            task_id: The task UUID to escalate
            notes: Optional notes for the CEO explaining the escalation

        Returns:
            Task in awaiting_ceo_approval status
        """
        return await handle_escalate_to_ceo(client, task_id, agent_id, notes)


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

        ORDERING SUBTASKS:
        When creating multiple subtasks, use sequence and dependency_ids:
        - sequence: Lower numbers execute first (1, 2, 3...)
        - dependency_ids: Task IDs that must complete first

        Example for 3 ordered subtasks:
        1. "Fix bug" (sequence=1, no deps)
        2. "Add feature" (sequence=2, depends on #1)
        3. "Write tests" (sequence=3, depends on #1 and #2)

        Args:
            data: TaskCreateInput with:
                - title, description, acceptance_criteria, team (required)
                - parent_task_id, assigned_to, priority, status (optional)
                - sequence: Order within siblings (0 = default)
                - dependency_ids: Task IDs that must complete first

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
    async def roboco_task_cancel(task_id: str, reason: str) -> dict[str, Any]:
        """
        Cancel a task (PM and board only).

        IMPORTANT: Reason is REQUIRED. Must start with a valid category:
        - duplicate: Task duplicates existing work
        - obsolete: Requirements changed, task no longer needed
        - blocked_permanently: External dependency won't be resolved
        - reassigned: Work moved to different task/approach
        - scope_change: Project scope changed, task out of scope
        - stakeholder_request: CEO/Board requested cancellation

        Example: "obsolete: requirements changed per TASK-123 discussion"

        ENFORCEMENT:
        - Only PMs and board members can cancel tasks
        - Cannot cancel completed or already-cancelled tasks
        - Cannot cancel in_progress tasks assigned to others (ask to pause first)

        Args:
            task_id: The task UUID to cancel
            reason: REQUIRED - Category + details (e.g., "duplicate: same as TASK-456")

        Returns:
            Cancelled task confirmation
        """
        return await handle_task_cancel(client, task_id, agent_id, reason)

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


def _register_group_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register group management tools (Main PM only)."""

    @mcp.tool()
    async def roboco_group_create(data: GroupCreateInput) -> dict[str, Any]:
        """
        Create a group in a channel (Main PM only).

        Groups organize work into feature/initiative scopes within channels.
        The typical workflow is:
        1. Main PM creates a Group for a feature/initiative
        2. Cell PM creates Sessions within the Group for work items
        3. Developers communicate within Sessions

        ENFORCEMENT:
        - Only Main PM, CEO, or Auditor can create groups
        - Cell PMs should escalate if they need a group created

        Args:
            data: GroupCreateInput with channel_slug, name, hierarchy_level

        Returns:
            Created group with guidance
        """
        return await handle_group_create(client, data, agent_id)


def create_task_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Task MCP server for a specific agent.

    The agent_id is embedded in the server to enforce ownership rules.
    Tools are registered based on role - agents only see tools they can use.

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server with role-appropriate tools
    """
    from roboco.agents_config import get_agent_role

    mcp = FastMCP(f"roboco-task-{agent_id}", json_response=True)
    client = ApiClient(agent_id)
    role = get_agent_role(agent_id)

    # Core tools available to ALL agents
    _register_core_tools(mcp, client, agent_id)

    # Role-specific tool registration
    if role == "developer":
        # Developers: submit workflow + blocking
        _register_developer_submit_tools(mcp, client, agent_id)
        _register_blocking_tools(mcp, client, agent_id)

    elif role == "qa":
        # QA: verdict tools only
        _register_qa_verdict_tools(mcp, client, agent_id)

    elif role == "documenter":
        # Documenters: docs completion only
        _register_documenter_tools(mcp, client, agent_id)

    elif role == "cell_pm":
        # Cell PMs: task management + sessions (no group creation)
        _register_pm_completion_tools(mcp, client, agent_id)
        _register_pm_tools(mcp, client, agent_id)
        _register_session_tools(mcp, client, agent_id)
        _register_blocking_tools(mcp, client, agent_id)

    elif role == "main_pm":
        # Main PM: full management including group creation
        _register_pm_completion_tools(mcp, client, agent_id)
        _register_pm_tools(mcp, client, agent_id)
        _register_session_tools(mcp, client, agent_id)
        _register_group_tools(mcp, client, agent_id)
        _register_blocking_tools(mcp, client, agent_id)

    elif role in ("product_owner", "head_marketing", "auditor", "ceo"):
        # Board/CEO: PM tools + completion + groups
        # Note: CEO is human (HiTL), uses API directly for approvals
        _register_pm_completion_tools(mcp, client, agent_id)
        _register_pm_tools(mcp, client, agent_id)
        _register_session_tools(mcp, client, agent_id)
        _register_group_tools(mcp, client, agent_id)

    # Unknown role: only core tools (scan, get, claim, etc.)

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
