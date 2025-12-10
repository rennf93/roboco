"""
Journal Model

Personal agent journals for reflection, growth tracking, and debugging.
Each agent maintains their own journal with entries tied to tasks and sessions.
"""

from datetime import datetime
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
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")

    # Embedding for RAG search
    embedding: list[float] | None = Field(
        default=None, description="Vector embedding for search"
    )

    # Sentiment/mood tracking (for growth analysis)
    sentiment: str | None = Field(
        default=None,
        description="Sentiment indicator (positive, neutral, negative, frustrated, confident, etc.)",
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
        self.last_entry_at = datetime.utcnow()

        # Update type counts
        type_key = entry_type.value
        self.entries_by_type[type_key] = self.entries_by_type.get(type_key, 0) + 1


# =============================================================================
# JOURNAL ENTRY FACTORIES
# =============================================================================


def create_task_reflection(
    journal_id: UUID,
    task_id: UUID,
    title: str,
    what_done: str,
    what_learned: str,
    what_struggled: str,
    next_steps: list[str],
    tags: list[str] | None = None,
) -> JournalEntry:
    """Create a task reflection entry."""
    content = f"""## What I Did
{what_done}

## What I Learned
{what_learned}

## What I Struggled With
{what_struggled}

## Next Steps
{chr(10).join(f"- [ ] {step}" for step in next_steps)}
"""
    return JournalEntry(
        journal_id=journal_id,
        type=JournalEntryType.TASK_REFLECTION,
        title=title,
        content=content,
        task_id=task_id,
        tags=tags or [],
    )


def create_decision_log(
    journal_id: UUID,
    title: str,
    context: str,
    options: list[dict[str, str]],
    chosen: str,
    rationale: str,
    consequences: list[str],
    task_id: UUID | None = None,
    tags: list[str] | None = None,
) -> JournalEntry:
    """Create a decision log entry."""
    options_text = ""
    for i, opt in enumerate(options, 1):
        options_text += f"\n**Option {i}: {opt.get('name', f'Option {i}')}**\n"
        options_text += f"- Pros: {opt.get('pros', 'N/A')}\n"
        options_text += f"- Cons: {opt.get('cons', 'N/A')}\n"

    content = f"""## Context
{context}

## Options Considered
{options_text}

## Decision
Chose **{chosen}** because {rationale}

## Consequences
{chr(10).join(f"- {c}" for c in consequences)}
"""
    return JournalEntry(
        journal_id=journal_id,
        type=JournalEntryType.DECISION_LOG,
        title=title,
        content=content,
        task_id=task_id,
        tags=tags or [],
    )


def create_learning_entry(
    journal_id: UUID,
    title: str,
    what_learned: str,
    how_applied: str | None = None,
    source: str | None = None,
    task_id: UUID | None = None,
    tags: list[str] | None = None,
) -> JournalEntry:
    """Create a learning entry."""
    content = f"""## What I Learned
{what_learned}
"""
    if how_applied:
        content += f"""
## How I Applied It
{how_applied}
"""
    if source:
        content += f"""
## Source
{source}
"""
    return JournalEntry(
        journal_id=journal_id,
        type=JournalEntryType.LEARNING,
        title=title,
        content=content,
        task_id=task_id,
        tags=tags or [],
        sentiment="positive",
    )


def create_struggle_entry(
    journal_id: UUID,
    title: str,
    what_struggled: str,
    attempted_solutions: list[str],
    resolution: str | None = None,
    help_needed: str | None = None,
    task_id: UUID | None = None,
    tags: list[str] | None = None,
) -> JournalEntry:
    """Create a struggle/difficulty entry."""
    content = f"""## What I Struggled With
{what_struggled}

## What I Tried
{chr(10).join(f"- {s}" for s in attempted_solutions)}
"""
    if resolution:
        content += f"""
## Resolution
{resolution}
"""
    if help_needed:
        content += f"""
## Help Needed
{help_needed}
"""
    return JournalEntry(
        journal_id=journal_id,
        type=JournalEntryType.STRUGGLE,
        title=title,
        content=content,
        task_id=task_id,
        tags=tags or [],
        sentiment="frustrated",
    )


def create_general_entry(
    journal_id: UUID,
    title: str,
    content: str,
    task_id: UUID | None = None,
    session_id: UUID | None = None,
    tags: list[str] | None = None,
    is_private: bool = False,
) -> JournalEntry:
    """Create a general journal entry."""
    return JournalEntry(
        journal_id=journal_id,
        type=JournalEntryType.GENERAL,
        title=title,
        content=content,
        task_id=task_id,
        session_id=session_id,
        tags=tags or [],
        is_private=is_private,
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
