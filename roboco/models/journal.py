"""
Journal Model

Personal agent journals for reflection, growth tracking, and debugging.
Each agent maintains their own journal with entries tied to tasks and sessions.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    JournalEntryType,
    RobocoBase,
    TimestampMixin,
)

# =============================================================================
# JOURNAL ENTRY MODEL
# =============================================================================


class JournalEntry(TimestampMixin):
    """
    A single journal entry by an agent.

    Entries capture reflections, learnings, struggles, and decisions
    made during work.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Entry ID")
    journal_id: UUID = Field(..., description="Parent journal ID")

    # Content
    type: JournalEntryType = Field(..., description="Type of journal entry")
    title: str = Field(..., min_length=1, max_length=200, description="Entry title")
    content: str = Field(..., description="Entry content (markdown)")

    # Context
    task_id: UUID | None = Field(default=None, description="Related task if applicable")
    session_id: UUID | None = Field(
        default=None, description="Related session if applicable"
    )

    # Metadata
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    # Embedding for RAG search
    embedding: list[float] | None = Field(
        default=None, description="Vector embedding for search"
    )

    # Sentiment/mood tracking (for growth analysis)
    sentiment: str | None = Field(
        default=None,
        description="Sentiment indicator (positive, neutral, negative, frustrated, confident, etc.)",  # noqa: E501
    )

    # Visibility
    is_private: bool = Field(
        default=False,
        description="If True, only visible to agent and CEO/Auditor",
    )


# =============================================================================
# JOURNAL MODEL
# =============================================================================


class Journal(TimestampMixin):
    """
    An agent's personal journal.

    Contains all entries for an agent, with methods for
    reflection and growth tracking.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Journal ID")
    agent_id: UUID = Field(..., description="Owning agent ID")

    # Metadata
    total_entries: int = Field(default=0, ge=0)
    last_entry_at: datetime | None = None

    # Summary (auto-generated periodically)
    latest_summary: str | None = Field(
        default=None, description="AI-generated summary of recent entries"
    )
    summary_updated_at: datetime | None = None

    # Growth metrics
    entries_by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Count of entries by type",
    )

    def record_entry(self, entry_type: JournalEntryType) -> None:
        """Record that a new entry was added."""
        self.total_entries += 1
        self.last_entry_at = datetime.now(UTC)

        # Update type counts
        type_key = entry_type.value
        self.entries_by_type[type_key] = self.entries_by_type.get(type_key, 0) + 1


# =============================================================================
# JOURNAL ENTRY FACTORIES
# =============================================================================


@dataclass
class TaskReflectionParams:
    """Parameters for creating a task reflection entry."""

    task_id: UUID
    title: str
    what_done: str
    what_learned: str
    what_struggled: str
    next_steps: list[str]
    tags: list[str] = field(default_factory=list)
    journal_id: UUID | None = None


@dataclass
class DecisionLogParams:
    """Parameters for creating a decision log entry."""

    title: str
    context: str
    options: list[dict[str, str]]
    chosen: str
    rationale: str
    consequences: list[str]
    task_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    journal_id: UUID | None = None


@dataclass
class LearningEntryParams:
    """Parameters for creating a learning entry."""

    title: str
    what_learned: str
    how_applied: str | None = None
    source: str | None = None
    task_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    journal_id: UUID | None = None


@dataclass
class StruggleEntryParams:
    """Parameters for creating a struggle entry."""

    title: str
    what_struggled: str
    attempted_solutions: list[str]
    resolution: str | None = None
    help_needed: str | None = None
    task_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    journal_id: UUID | None = None


@dataclass
class GeneralEntryParams:
    """Parameters for creating a general journal entry."""

    title: str
    content: str
    task_id: UUID | None = None
    session_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    is_private: bool = False
    journal_id: UUID | None = None


def create_task_reflection(params: TaskReflectionParams) -> JournalEntry:
    """Create a task reflection entry."""
    if params.journal_id is None:
        msg = "journal_id is required for task reflection"
        raise ValueError(msg)
    content = f"""## What I Did
{params.what_done}

## What I Learned
{params.what_learned}

## What I Struggled With
{params.what_struggled}

## Next Steps
{chr(10).join(f"- [ ] {step}" for step in params.next_steps)}
"""
    return JournalEntry(
        journal_id=params.journal_id,
        type=JournalEntryType.TASK_REFLECTION,
        title=params.title,
        content=content,
        task_id=params.task_id,
        tags=params.tags,
    )


def create_decision_log(params: DecisionLogParams) -> JournalEntry:
    """Create a decision log entry."""
    if params.journal_id is None:
        msg = "journal_id is required for decision log"
        raise ValueError(msg)
    options_text = ""
    for i, opt in enumerate(params.options, 1):
        options_text += f"\n**Option {i}: {opt.get('name', f'Option {i}')}**\n"
        options_text += f"- Pros: {opt.get('pros', 'N/A')}\n"
        options_text += f"- Cons: {opt.get('cons', 'N/A')}\n"

    content = f"""## Context
{params.context}

## Options Considered
{options_text}

## Decision
Chose **{params.chosen}** because {params.rationale}

## Consequences
{chr(10).join(f"- {c}" for c in params.consequences)}
"""
    return JournalEntry(
        journal_id=params.journal_id,
        type=JournalEntryType.DECISION_LOG,
        title=params.title,
        content=content,
        task_id=params.task_id,
        tags=params.tags,
    )


def create_learning_entry(params: LearningEntryParams) -> JournalEntry:
    """Create a learning entry."""
    if params.journal_id is None:
        msg = "journal_id is required for learning entry"
        raise ValueError(msg)
    content = f"""## What I Learned
{params.what_learned}
"""
    if params.how_applied:
        content += f"""
## How I Applied It
{params.how_applied}
"""
    if params.source:
        content += f"""
## Source
{params.source}
"""
    return JournalEntry(
        journal_id=params.journal_id,
        type=JournalEntryType.LEARNING,
        title=params.title,
        content=content,
        task_id=params.task_id,
        tags=params.tags,
        sentiment="positive",
    )


def create_struggle_entry(params: StruggleEntryParams) -> JournalEntry:
    """Create a struggle/difficulty entry."""
    if params.journal_id is None:
        msg = "journal_id is required for struggle entry"
        raise ValueError(msg)
    content = f"""## What I Struggled With
{params.what_struggled}

## What I Tried
{chr(10).join(f"- {s}" for s in params.attempted_solutions)}
"""
    if params.resolution:
        content += f"""
## Resolution
{params.resolution}
"""
    if params.help_needed:
        content += f"""
## Help Needed
{params.help_needed}
"""
    return JournalEntry(
        journal_id=params.journal_id,
        type=JournalEntryType.STRUGGLE,
        title=params.title,
        content=content,
        task_id=params.task_id,
        tags=params.tags,
        sentiment="frustrated",
    )


def create_general_entry(params: GeneralEntryParams) -> JournalEntry:
    """Create a general journal entry."""
    if params.journal_id is None:
        msg = "journal_id is required for general entry"
        raise ValueError(msg)
    return JournalEntry(
        journal_id=params.journal_id,
        type=JournalEntryType.GENERAL,
        title=params.title,
        content=params.content,
        task_id=params.task_id,
        session_id=params.session_id,
        tags=params.tags,
        is_private=params.is_private,
    )


# =============================================================================
# CREATE SCHEMA
# =============================================================================


class JournalEntryCreate(RobocoBase):
    """Schema for creating a new journal entry."""

    journal_id: UUID
    type: JournalEntryType
    title: str = Field(..., min_length=1, max_length=200)
    content: str
    task_id: UUID | None = None
    session_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    is_private: bool = False
