# RoboCo Data Models

This directory contains all Pydantic data models for the RoboCo AI Agents Company system.

## Model Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DATA MODEL HIERARCHY                                  │
└─────────────────────────────────────────────────────────────────────────────┘

Organization Layer:
├─► Agent          → Individual AI agents with roles, teams, permissions
├─► Channel        → Top-level communication containers (#backend-cell, etc.)
└─► Group          → Role-based groups within channels

Communication Layer:
├─► Session        → Bounded message groups (by time, count, or length)
├─► Message        → Extracted messages from agent streams
└─► Notification   → Formal signals requiring acknowledgment

Work Layer:
├─► Task           → Atomic unit of work with lifecycle states
├─► Project        → Git repository configuration
├─► WorkSession    → Git work context (branch, commits, PR)
├─► Journal        → Agent personal logs and reflections
└─► Handoff        → Dev → Documenter transition documents
```

## Files

| File | Description |
|------|-------------|
| `base.py` | Enums (TaskStatus, AgentRole, Team, etc.), base model class, common types |
| `task.py` | Task model with full lifecycle, commits, checkpoints |
| `agent.py` | Agent model with roles, teams, and state |
| `project.py` | Git repository configuration and commands |
| `work_session.py` | Git work session tracking (branch, commits, PR) |
| `session.py` | Communication session boundaries |
| `message.py` | Extracted messages and raw streams |
| `group.py` | Group model for role-based access |
| `channel.py` | Channel model for team structure |
| `notification.py` | Formal notification system |
| `journal.py` | Agent journaling and reflection |
| `handoff.py` | Documentation handoff system |

## Task Lifecycle States

From `roboco/enforcement/task_lifecycle.py`:

```
backlog ────────► pending ────────► claimed ────────► in_progress
    │                │                                     │
    ▼                ▼                                     ├──► blocked ──► in_progress
cancelled        cancelled                                 ├──► paused ───► in_progress
                                                          ├──► verifying
                                                          ├──► awaiting_pm_review ──► completed
                                                          │         │
                                                          │         ▼
                                                          │    awaiting_ceo_approval ──► completed
                                                          │                │
                                                          │                ▼
                                                          │          needs_revision
                                                          │
                                                          ▼
                                                     awaiting_qa
                                                          │
                                              ┌───────────┴───────────┐
                                              ▼                       ▼
                                   awaiting_documentation      needs_revision
                                              │
                                              ▼
                                     awaiting_pm_review
```

### Terminal States
- `completed` - Task successfully finished
- `cancelled` - Task cancelled by PM

### Waiting States (agent can work on other tasks)
- `blocked`, `paused`, `awaiting_qa`, `awaiting_documentation`, `awaiting_pm_review`, `awaiting_ceo_approval`

### Active States (agent is working)
- `claimed`, `in_progress`, `verifying`, `needs_revision`

## Enums Reference

### TaskStatus (from `base.py`)
```python
BACKLOG = "backlog"                           # PM setup phase
PENDING = "pending"                           # Ready for work
CLAIMED = "claimed"                           # Agent claimed, not started
IN_PROGRESS = "in_progress"                   # Active work
BLOCKED = "blocked"                           # External blocker
PAUSED = "paused"                             # Temporary pause
VERIFYING = "verifying"                       # Self-verification
NEEDS_REVISION = "needs_revision"             # QA/PM requested changes
AWAITING_QA = "awaiting_qa"                   # Ready for QA review
AWAITING_DOCUMENTATION = "awaiting_documentation"  # Ready for docs
AWAITING_PM_REVIEW = "awaiting_pm_review"     # Ready for PM review
AWAITING_CEO_APPROVAL = "awaiting_ceo_approval"    # Major task, CEO decides
COMPLETED = "completed"                       # Done
CANCELLED = "cancelled"                       # Cancelled
QUARANTINED = "quarantined"                   # Problem task, can return to pending
```

### AgentRole
```python
CEO = "ceo"                     # Executive (Human)
PRODUCT_OWNER = "product_owner" # Board
HEAD_MARKETING = "head_marketing" # Board
AUDITOR = "auditor"             # Board (Silent observer)
MAIN_PM = "main_pm"             # Management (coordinates all cells)
CELL_PM = "cell_pm"             # Cell management
DEVELOPER = "developer"         # Cell member
QA = "qa"                       # Cell member
DOCUMENTER = "documenter"       # Cell member
```

### Team
```python
BACKEND = "backend"     # Backend cell
FRONTEND = "frontend"   # Frontend cell
UX_UI = "ux_ui"         # UX/UI cell
BOARD = "board"         # Board level (no cell)
```

### WorkSessionStatus (from `work_session.py`)
```python
ACTIVE = "active"         # Work in progress
COMPLETED = "completed"   # PR merged
ABANDONED = "abandoned"   # Session cancelled
```

### TaskType (from `base.py`)
```python
CODE = "code"                     # Technical work - requires git workflow
DOCUMENTATION = "documentation"   # May or may not need git
RESEARCH = "research"             # Investigation/analysis - no git
PLANNING = "planning"             # Planning/design tasks - no git
DESIGN = "design"                 # UX/UI design tasks - no git
ADMINISTRATIVE = "administrative" # Administrative tasks - no git
```

### BranchReason (from `project.py`)
```python
FEATURE = "feature"   # New functionality
BUG = "bug"           # Bug fixes
CHORE = "chore"       # Maintenance
DOCS = "docs"         # Documentation
HOTFIX = "hotfix"     # Emergency fixes
```

## Key Models

### Task (`task.py`)
```python
class Task:
    id: UUID
    title: str
    description: str
    acceptance_criteria: list[str]
    status: TaskStatus
    team: Team
    created_by: UUID
    assigned_to: UUID | None

    # Task Type & Git Configuration
    task_type: TaskType              # code, documentation, research, planning, design, administrative
    requires_git: bool               # Whether git workflow applies

    # Project & Branch (branch auto-created on claim)
    project_id: UUID | None
    branch_name: str | None
    work_session_id: UUID | None

    # PR Tracking (set during AWAITING_DOCUMENTATION parallel phase)
    pr_number: int | None            # GitHub/GitLab PR number
    pr_url: str | None               # Full URL to PR

    # Parallel Execution Tracking (for AWAITING_DOCUMENTATION phase)
    docs_complete: bool              # Documenter has finished
    pr_created: bool                 # Developer has created PR

    # PM Approval Tracking
    pm_approvals: dict[str, bool]    # {'main_pm': True, 'cell_pm': True}

    # Planning
    plan: TaskPlan | None
    estimated_complexity: Complexity

    # Execution tracking
    commits: list[CommitRef]         # Linked git commits
    checkpoints: list[Checkpoint]    # Recovery points
    progress_updates: list[ProgressUpdate]

    # Documentation Notes
    dev_notes: str | None            # Journey notes from developer
    qa_notes: str | None             # QA feedback
    auditor_notes: str | None        # Auditor observations
    quick_context: str | None        # 2-3 sentences for quick context restoration

    # Proactive Knowledge Context (injected when task is claimed)
    proactive_context: dict | None   # RAG context: similar tasks, learnings, patterns
```

### Project (`project.py`)
```python
class Project:
    id: UUID
    name: str
    slug: str                      # URL-safe identifier (e.g., 'roboco', 'roboco-panel')
    git_url: str                   # Git repository URL
    default_branch: str            # e.g., "main"
    protected_branches: list[str]  # Cannot push directly

    # CI/CD commands
    test_command: str | None       # e.g., 'uv run pytest'
    lint_command: str | None       # e.g., 'uv run ruff check .'
    format_command: str | None     # e.g., 'uv run ruff format .'
    typecheck_command: str | None  # e.g., 'uv run mypy src/'
    build_command: str | None      # e.g., 'pnpm build'

    # Access control
    assigned_cell: Team
    allowed_agents: list[UUID] | None  # None = all agents in cell

    # Runtime State (managed by workspace service)
    workspace_path: str | None     # Legacy: now use WorkspaceService
    last_synced_at: datetime | None
    head_commit: str | None

    # Metadata
    created_by: UUID
    is_active: bool
```

### WorkSession (`work_session.py`)
```python
class WorkSession:
    id: UUID
    project_id: UUID
    task_id: UUID
    agent_id: UUID

    # Branch management
    branch_name: str
    base_branch: str
    target_branch: str

    # Audit trail
    commits: list[str]            # Commit SHAs
    files_modified: list[str]     # Changed files

    # PR tracking
    pr_number: int | None
    pr_url: str | None
    pr_status: str | None         # open, merged, closed
    pr_created_at: datetime | None
    pr_merged_at: datetime | None
    merged_by: UUID | None

    status: WorkSessionStatus
```

## Database Mapping

These Pydantic models are mirrored in SQLAlchemy tables at `roboco/db/tables.py`:

| Pydantic Model | SQLAlchemy Table |
|----------------|------------------|
| `Task` | `TaskTable` |
| `Agent` | `AgentTable` |
| `Project` | `ProjectTable` |
| `WorkSession` | `WorkSessionTable` |
| `Session` | `SessionTable` |
| `Message` | `MessageTable` |
| `Channel` | `ChannelTable` |
| `Group` | `GroupTable` |
| `Notification` | `NotificationTable` |
| `JournalEntry` | `JournalEntryTable` |

## Usage Examples

### Creating a Task
```python
from roboco.models.task import TaskCreate
from roboco.models.base import Team, Complexity

task_data = TaskCreate(
    title="Implement rate limiting",
    description="Add rate limiting to auth endpoints",
    acceptance_criteria=[
        "Rate limit of 5 attempts per minute",
        "Return 429 on limit exceeded",
    ],
    team=Team.BACKEND,
    priority=1,
    estimated_complexity=Complexity.MEDIUM,
)
```

### Creating a Project
```python
from roboco.models.project import ProjectCreate
from roboco.models.base import Team

project = ProjectCreate(
    name="RoboCo API",
    slug="roboco",
    git_url="git@github.com:org/roboco.git",
    default_branch="main",
    assigned_cell=Team.BACKEND,
    test_command="uv run pytest",
    lint_command="uv run ruff check .",
)
```

## Validation

All models use Pydantic v2 with strict validation:

- Type checking enforced
- Field constraints validated (min/max length, patterns)
- Extra fields forbidden by default
- Enum values used in serialization

## Related Documentation

- Task lifecycle: `docs/architecture/task_lifecycle.md`
- Data model: `docs/architecture/data_model.md`
- API overview: `docs/architecture/api_overview.md`
