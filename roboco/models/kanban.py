"""
Kanban Board Models

Models for representing kanban board views with columns,
cards, and swimlanes for different roles.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from roboco.models.base import Complexity, RobocoBase, TaskStatus, Team


class KanbanBoardType(StrEnum):
    """Types of kanban boards available."""

    DEV = "dev"
    QA = "qa"
    DOCUMENTER = "documenter"
    PM = "pm"
    MAIN_PM = "main_pm"
    BOARD = "board"


# =============================================================================
# CARD MODELS
# =============================================================================


class KanbanCard(RobocoBase):
    """A card on the kanban board representing a task."""

    id: UUID
    title: str
    description: str | None = None
    priority: int = Field(ge=0, le=3)
    status: TaskStatus
    team: Team
    assigned_to: UUID | None = None
    assignee_name: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    target_date: datetime | None = None
    complexity: Complexity = Complexity.MEDIUM
    is_blocked: bool = False
    blocker_count: int = 0
    progress_percentage: int | None = None
    commit_count: int = 0
    has_subtasks: bool = False
    subtask_count: int = 0
    quick_context: str | None = None

    # For swimlane grouping
    swimlane_key: str | None = None


class KanbanColumn(RobocoBase):
    """A column on the kanban board."""

    id: str
    title: str
    status: TaskStatus | None = None  # The status this column represents
    cards: list[KanbanCard] = Field(default_factory=list)
    card_count: int = 0
    wip_limit: int | None = None  # Work in progress limit


class KanbanSwimlane(RobocoBase):
    """A swimlane for grouping cards."""

    id: str
    title: str
    columns: list[KanbanColumn] = Field(default_factory=list)


class KanbanBoard(RobocoBase):
    """A complete kanban board view."""

    id: str
    title: str
    board_type: KanbanBoardType
    team: Team | None = None

    # Board can have flat columns or swimlanes
    columns: list[KanbanColumn] = Field(default_factory=list)
    swimlanes: list[KanbanSwimlane] = Field(default_factory=list)

    # Metadata
    total_cards: int = 0
    blocked_count: int = 0
    last_updated: datetime | None = None


# =============================================================================
# COLUMN CONFIGURATIONS
# =============================================================================


DEV_COLUMNS = [
    ("backlog", "Backlog", TaskStatus.BACKLOG),
    ("pending", "Ready", TaskStatus.PENDING),
    ("assigned", "Assigned", TaskStatus.CLAIMED),
    ("in_progress", "In Progress", TaskStatus.IN_PROGRESS),
    ("verifying", "Verifying", TaskStatus.VERIFYING),
    ("blocked", "Blocked", TaskStatus.BLOCKED),
    ("paused", "Paused", TaskStatus.PAUSED),
    ("needs_revision", "Needs Revision", TaskStatus.NEEDS_REVISION),
    ("qa_review", "QA Review", TaskStatus.AWAITING_QA),
    ("documenting", "Documenting", TaskStatus.AWAITING_DOCUMENTATION),
    ("pr_review", "PR Review", TaskStatus.AWAITING_PR_REVIEW),
    ("pm_review", "PM Review", TaskStatus.AWAITING_PM_REVIEW),
    ("ceo_approval", "CEO Approval", TaskStatus.AWAITING_CEO_APPROVAL),
    ("done", "Done", TaskStatus.COMPLETED),
    ("cancelled", "Cancelled", TaskStatus.CANCELLED),
]

# VERIFYING is the developer's self-verification state — the task is still with
# the dev, not with QA. Mapping it to an 'In Review' column misrepresented dev
# mid-verification as active QA work; QA reviews tasks that reached AWAITING_QA.
QA_COLUMNS = [
    ("awaiting_review", "Awaiting Review", TaskStatus.AWAITING_QA),
    ("passed", "Passed", TaskStatus.AWAITING_DOCUMENTATION),
    ("failed", "Failed", TaskStatus.NEEDS_REVISION),
]

DOCUMENTER_COLUMNS = [
    ("awaiting_handoff", "Awaiting Handoff", TaskStatus.AWAITING_DOCUMENTATION),
    ("gathering", "Gathering", TaskStatus.IN_PROGRESS),  # Custom mapping
    ("writing", "Writing", TaskStatus.VERIFYING),  # Custom mapping
    ("published", "Published", TaskStatus.COMPLETED),
]

# The cell PM coordinates the QA -> docs -> PR-review -> PM-review -> CEO chain,
# so every in-flight gate/revision/paused/cancelled status must be visible, not
# dropped by a column set that only knew pending/claimed/in_progress/blocked.
PM_COLUMNS = [
    ("incoming", "Incoming", TaskStatus.PENDING),
    ("triaged", "Triaged", TaskStatus.CLAIMED),
    ("assigned", "Assigned", TaskStatus.IN_PROGRESS),
    ("verifying", "Verifying", TaskStatus.VERIFYING),
    ("blocked", "Blocked", TaskStatus.BLOCKED),
    ("paused", "Paused", TaskStatus.PAUSED),
    ("needs_revision", "Needs Revision", TaskStatus.NEEDS_REVISION),
    ("qa_review", "QA Review", TaskStatus.AWAITING_QA),
    ("documenting", "Documenting", TaskStatus.AWAITING_DOCUMENTATION),
    ("pr_review", "PR Review", TaskStatus.AWAITING_PR_REVIEW),
    ("pm_review", "PM Review", TaskStatus.AWAITING_PM_REVIEW),
    ("ceo_approval", "CEO Approval", TaskStatus.AWAITING_CEO_APPROVAL),
    ("done", "Done", TaskStatus.COMPLETED),
    ("cancelled", "Cancelled", TaskStatus.CANCELLED),
    ("backlog", "Backlog", TaskStatus.BACKLOG),
]

MAIN_PM_COLUMNS = [
    ("incoming", "Incoming", TaskStatus.PENDING),
    ("distributed", "Distributed", TaskStatus.CLAIMED),
    ("in_progress", "In Progress", TaskStatus.IN_PROGRESS),
    ("done", "Done", TaskStatus.COMPLETED),
]

BOARD_COLUMNS = [
    ("ideas", "Ideas", TaskStatus.PENDING),
    ("roadmap", "Roadmap", TaskStatus.CLAIMED),
    ("in_development", "In Development", TaskStatus.IN_PROGRESS),
    ("released", "Released", TaskStatus.COMPLETED),
]


def get_column_config(board_type: KanbanBoardType) -> list[tuple[str, str, TaskStatus]]:
    """Get column configuration for a board type."""
    configs = {
        KanbanBoardType.DEV: DEV_COLUMNS,
        KanbanBoardType.QA: QA_COLUMNS,
        KanbanBoardType.DOCUMENTER: DOCUMENTER_COLUMNS,
        KanbanBoardType.PM: PM_COLUMNS,
        KanbanBoardType.MAIN_PM: MAIN_PM_COLUMNS,
        KanbanBoardType.BOARD: BOARD_COLUMNS,
    }
    return configs.get(board_type, DEV_COLUMNS)
