"""
MCP Input Schemas

Pydantic models for MCP tool input validation.
"""

from pydantic import BaseModel, Field

# =============================================================================
# JOURNAL SCHEMAS
# =============================================================================


class JournalEntryInput(BaseModel):
    """Input for creating a general journal entry."""

    title: str = Field(..., description="Entry title (short description)")
    content: str = Field(..., description="Entry content (detailed text)")
    entry_type: str = Field(
        default="general",
        description="Type: general, task_reflection, decision_log, learning, struggle",
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    session_id: str | None = Field(default=None, description="Optional related session")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")
    is_private: bool = Field(
        default=False, description="If true, only you and CEO/Auditor can see"
    )


class TaskReflectionInput(BaseModel):
    """Input for creating a task reflection entry."""

    task_id: str = Field(..., description="The task UUID you're reflecting on")
    session_id: str | None = Field(default=None, description="Optional session context")
    title: str = Field(..., description="Reflection title")
    what_done: str = Field(..., description="What was accomplished")
    what_learned: str = Field(..., description="Key learnings from this task")
    what_struggled: str = Field(..., description="What was difficult or challenging")
    next_steps: list[str] = Field(
        default_factory=list, description="Optional follow-up items"
    )
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


class DecisionOption(BaseModel):
    """A decision option with pros/cons."""

    name: str = Field(..., description="Option name/title")
    pros: str = Field(default="", description="Pros/advantages of this option")
    cons: str = Field(default="", description="Cons/disadvantages of this option")


class DecisionLogInput(BaseModel):
    """Input for logging a decision."""

    title: str = Field(..., description="Decision title")
    context: str = Field(..., description="What situation led to this decision")
    options: list[DecisionOption] = Field(
        ..., min_length=2, description="Options considered (at least 2)"
    )
    chosen: str = Field(..., description="Which option was chosen")
    rationale: str = Field(..., description="Why this option was chosen")
    consequences: list[str] = Field(
        default_factory=list, description="Expected consequences"
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


class LearningInput(BaseModel):
    """Input for logging a learning."""

    title: str = Field(..., description="Learning title")
    what_learned: str = Field(..., description="The actual learning/insight")
    how_applied: str | None = Field(
        default=None, description="How you applied or plan to apply this"
    )
    source: str | None = Field(
        default=None, description="Where you learned this (docs, experiment, etc.)"
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


class StruggleInput(BaseModel):
    """Input for logging a struggle."""

    title: str = Field(..., description="Struggle title")
    what_struggled: str = Field(..., description="What the challenge was")
    attempted_solutions: list[str] = Field(
        default_factory=list, description="What you tried (even if it didn't work)"
    )
    resolution: str | None = Field(
        default=None, description="How it was resolved (if resolved)"
    )
    help_needed: str | None = Field(
        default=None, description="What help you need (if unresolved)"
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


# =============================================================================
# MESSAGE SCHEMAS
# =============================================================================


class SendMessageInput(BaseModel):
    """Input for sending a message."""

    channel_slug: str = Field(..., description="Channel slug (e.g., 'backend-cell')")
    content: str = Field(..., description="Message content")
    task_id: str = Field(..., description="Task ID (routes to task's session)")
    message_type: str = Field(
        default="dialogue",
        description="Type: reasoning, dialogue, decision, action, blocker, technical",
    )
    reply_to: str | None = Field(default=None, description="Message ID to reply to")
    mentions: list[str] = Field(default_factory=list, description="Agents to mention")


class AskQuestionInput(BaseModel):
    """Input for asking a question."""

    channel_slug: str
    question: str
    task_id: str  # Required - routes to task's session
    context: str | None = None


class ReportBlockerInput(BaseModel):
    """Input for reporting a blocker."""

    channel_slug: str
    blocker_description: str
    what_needed: str
    task_id: str  # Required - routes to task's session


# =============================================================================
# NOTIFICATION SCHEMAS
# =============================================================================


class SendNotificationInput(BaseModel):
    """Input for sending a notification."""

    recipients: list[str] = Field(..., description="Agent IDs to notify")
    subject: str = Field(..., description="Notification subject")
    body: str = Field(..., description="Notification body")
    notification_type: str = Field(
        default="info", description="Type: info, alert, task, escalation, approval"
    )
    priority: str = Field(default="normal", description="low, normal, high, urgent")
    requires_ack: bool = Field(default=True, description="Require acknowledgment")
    related_task_id: str | None = Field(default=None, description="Related task")


# =============================================================================
# TASK MANAGEMENT SCHEMAS (PM Tools)
# =============================================================================


class TaskCreateInput(BaseModel):
    """Input for creating a task (PM only).

    ORDERING: Use sequence and dependency_ids to control task execution order.
    - sequence: Lower numbers execute first (1, 2, 3...)
    - dependency_ids: Tasks that must complete before this one can be claimed

    PROJECT: project_slug is required (all tasks follow git workflow).
    - Use 'roboco' for internal RoboCo codebase work
    - Use roboco_project_list() to see available projects
    """

    title: str = Field(..., min_length=1, max_length=200, description="Task title")
    description: str = Field(
        ..., min_length=10, description="Task description (min 10 chars)"
    )
    acceptance_criteria: list[str] = Field(
        ..., min_length=1, description="At least one acceptance criterion"
    )
    team: str = Field(..., description="Team: backend, frontend, ux_ui")
    # Project selection - required for all tasks (git workflow is mandatory)
    project_slug: str = Field(
        ...,
        description=(
            "Project slug (e.g., 'roboco', 'roboco-panel'). "
            "Required for all tasks. Use 'roboco' for internal codebase."
        ),
    )
    task_type: str = Field(
        default="code",
        description=(
            "Task type: code, documentation, research, planning, "
            "design, administrative. All types follow git workflow."
        ),
    )
    parent_task_id: str | None = Field(
        default=None, description="Parent task for subtasks"
    )
    assigned_to: str | None = Field(default=None, description="Agent slug to assign to")
    priority: int = Field(default=2, ge=0, le=3, description="Priority 0-3 (0=lowest)")
    complexity: str = Field(
        default="medium", description="Complexity: low, medium, high, critical"
    )
    nature: str = Field(
        default="technical", description="Task nature: technical, non_technical"
    )
    status: str = Field(
        default="backlog",
        description="Status: 'backlog' (default) or 'pending' (ready for work)",
    )
    # Task ordering - IMPORTANT for subtask sequencing
    sequence: int = Field(
        default=0,
        description="Execution order within siblings (lower = first). E.g., 1, 2, 3",
    )
    dependency_ids: list[str] = Field(
        default_factory=list,
        description="Task IDs that must complete before this task can be claimed",
    )


class TaskAssignInput(BaseModel):
    """Input for assigning a task (PM only)."""

    task_id: str = Field(..., description="Task ID to assign")
    assignee: str = Field(..., description="Agent slug to assign to (e.g., 'be-dev-1')")


class TaskEscalateInput(BaseModel):
    """Input for escalating a task."""

    task_id: str = Field(..., description="Task ID to escalate")
    reason: str = Field(..., description="Reason for escalation")
    escalate_to: str | None = Field(
        default=None, description="Override default escalation target"
    )


class TaskBlockInput(BaseModel):
    """Input for blocking a task."""

    task_id: str = Field(..., description="Task ID to block")
    reason: str = Field(..., description="Why the task is blocked")
    blocker_type: str = Field(
        ..., description="Type: external, internal, question, dependency"
    )
    what_needed: str = Field(..., description="What is needed to unblock")


class TaskPauseInput(BaseModel):
    """Input for pausing a task."""

    task_id: str = Field(..., description="Task ID to pause")
    reason: str = Field(..., description="Why pausing")
    checkpoint_summary: str = Field(..., description="Summary of current state")
    remaining_work: list[str] = Field(
        default_factory=list, description="List of remaining sub-tasks"
    )


# =============================================================================
# SESSION-TASK SCHEMAS (PM Tools)
# =============================================================================


class SessionCreateForTasksInput(BaseModel):
    """Input for creating a session linked to tasks (PM only)."""

    task_ids: list[str] = Field(
        ..., min_length=1, description="Task IDs to link to the session"
    )
    channel_slug: str = Field(..., description="Channel where session is created")
    group_id: str | None = Field(
        default=None,
        description="Group ID to place session under (from roboco_group_create)",
    )
    scope: str = Field(
        default="cell",
        description="Scope level: initiative (Main PM), cell (Cell PM), task (dev)",
    )
    relationship_type: str = Field(
        default="discussion",
        description="Type: discussion, planning, review, retrospective",
    )


class SessionLinkTaskInput(BaseModel):
    """Input for linking a session to a task (PM only)."""

    session_id: str = Field(..., description="Session ID to link")
    task_id: str = Field(..., description="Task ID to link")
    is_primary: bool = Field(
        default=False, description="Mark as primary session for this task"
    )
    relationship_type: str = Field(
        default="discussion",
        description="Type: discussion, planning, review, retrospective",
    )


# =============================================================================
# GROUP SCHEMAS (Main PM Only)
# =============================================================================


class GroupCreateInput(BaseModel):
    """Input for creating a group in a channel (Main PM only).

    Groups organize work into feature/initiative scopes within channels.
    - Main PM creates Groups for features/initiatives
    - Cell PMs create Sessions within Groups for work items
    - Developers communicate within Sessions
    """

    channel_slug: str = Field(
        ...,
        description="Channel slug where group will be created (e.g., 'backend-cell')",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Group name (e.g., 'User Preferences Feature')",
    )
    hierarchy_level: int = Field(
        default=4,
        ge=0,
        le=4,
        description="Access level: 0=CEO, 1=Board, 2=Main PM, 3=Cell PM, 4=Members",
    )


# =============================================================================
# DOCUMENTATION SCHEMAS
# =============================================================================


class WriteDocInput(BaseModel):
    """Input for writing a documentation file."""

    task_id: str = Field(..., description="Task UUID this documentation belongs to")
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Filename (e.g., 'endpoints.md') - no path separators",
    )
    doc_type: str = Field(
        ...,
        description="Type: api, qa, guide, readme, changelog, architecture, design",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Human-readable title",
    )
    content: str = Field(..., min_length=1, description="Full markdown content")


class UpdateDocInput(BaseModel):
    """Input for updating a documentation file."""

    path: str = Field(
        ...,
        description="Normalized path to update (e.g., 'backend/api/endpoints.md')",
    )
    title: str | None = Field(default=None, description="New title (optional)")
    content: str | None = Field(default=None, description="New content (optional)")


# =============================================================================
# PROJECT SCHEMAS
# =============================================================================


class ProjectCreateInput(BaseModel):
    """Input for creating/registering a new project (Main PM+ only).

    Projects are git repositories that agents work on.
    Workspaces are created automatically when agents need them.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable project name",
    )
    slug: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe identifier (lowercase, hyphens only)",
    )
    git_url: str = Field(
        ...,
        description="Git repository URL (SSH: git@github.com:... or HTTPS)",
    )
    assigned_cell: str = Field(
        ...,
        description="Cell that owns this project: backend, frontend, ux_ui",
    )
    default_branch: str = Field(
        default="main",
        description="Default branch name",
    )
    protected_branches: list[str] = Field(
        default_factory=lambda: ["main", "master"],
        description="Branches that cannot be pushed directly",
    )
    test_command: str | None = Field(
        default=None,
        description="Command to run tests (e.g., 'uv run pytest')",
    )
    lint_command: str | None = Field(
        default=None,
        description="Command to run linter (e.g., 'uv run ruff check .')",
    )
    format_command: str | None = Field(
        default=None,
        description="Command to format code (e.g., 'uv run ruff format .')",
    )
    typecheck_command: str | None = Field(
        default=None,
        description="Command to run type checker (e.g., 'uv run mypy src/')",
    )
    build_command: str | None = Field(
        default=None,
        description="Command to build (e.g., 'pnpm build')",
    )


class ProjectUpdateInput(BaseModel):
    """Input for updating a project (PM only).

    Cell PMs can only update projects in their cell.
    Main PM and above can update any project.
    """

    name: str | None = Field(default=None, description="New project name")
    git_url: str | None = Field(default=None, description="New git URL")
    default_branch: str | None = Field(default=None, description="New default branch")
    protected_branches: list[str] | None = Field(
        default=None, description="New protected branches list"
    )
    test_command: str | None = Field(default=None, description="New test command")
    lint_command: str | None = Field(default=None, description="New lint command")
    format_command: str | None = Field(default=None, description="New format command")
    typecheck_command: str | None = Field(
        default=None, description="New typecheck command"
    )
    build_command: str | None = Field(default=None, description="New build command")
    is_active: bool | None = Field(default=None, description="Set active/inactive")
