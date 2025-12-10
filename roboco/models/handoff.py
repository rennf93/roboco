"""
Handoff Model

Documenter handoffs contain all the information needed for
a Documenter to create production documentation from developer work.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    HandoffStatus,
    RobocoBase,
    TimestampMixin,
)

# =============================================================================
# SUPPORTING MODELS
# =============================================================================


class CodeSample(RobocoBase):
    """A code sample to include in documentation."""

    title: str = Field(..., description="Title/description of the sample")
    language: str = Field(..., description="Programming language")
    code: str = Field(..., description="The code sample")
    explanation: str | None = Field(
        default=None, description="Explanation of what the code does"
    )


class DocumentationItem(RobocoBase):
    """A specific documentation deliverable."""

    doc_type: str = Field(
        ..., description="Type: api, readme, architecture, changelog, guide"
    )
    description: str = Field(..., description="What needs to be documented")
    priority: int = Field(default=2, ge=0, le=3, description="0=required, 3=optional")
    completed: bool = Field(default=False)
    output_path: str | None = Field(
        default=None, description="Where the documentation should go"
    )


class ConversationRef(RobocoBase):
    """Reference to an important conversation."""

    message_id: UUID = Field(..., description="Message ID")
    topic: str = Field(..., description="What the conversation was about")
    key_insight: str = Field(..., description="The important takeaway")


# =============================================================================
# MAIN HANDOFF MODEL
# =============================================================================


class DocumenterHandoff(TimestampMixin):
    """
    Handoff document from Developer to Documenter.

    Contains all the information needed to create production
    documentation for a completed task.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Handoff ID")
    task_id: UUID = Field(..., description="Related task ID")

    # Summary
    summary: str = Field(
        ..., description="Plain language description of what was built (2-3 sentences)"
    )

    # What Changed
    new_functionality: list[str] = Field(
        default_factory=list, description="New features/capabilities"
    )
    modified_behavior: list[str] = Field(
        default_factory=list, description="Changed behaviors"
    )
    breaking_changes: list[str] = Field(
        default_factory=list, description="Breaking changes (if any)"
    )

    # Documentation Needed
    required_docs: list[DocumentationItem] = Field(
        default_factory=list, description="Required documentation items"
    )
    optional_docs: list[DocumentationItem] = Field(
        default_factory=list, description="Optional/nice-to-have docs"
    )

    # Key Commits
    commits: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {hash, description, key_files} dicts",
    )

    # Code Locations
    new_files: list[dict[str, str]] = Field(
        default_factory=list, description="List of {path, purpose} dicts"
    )
    modified_files: list[dict[str, str]] = Field(
        default_factory=list, description="List of {path, changes} dicts"
    )

    # Important Conversations
    key_conversations: list[ConversationRef] = Field(
        default_factory=list, description="References to important discussions"
    )

    # Code Samples
    code_samples: list[CodeSample] = Field(
        default_factory=list, description="Code examples to include in docs"
    )

    # Gotchas & Warnings
    gotchas: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {title, explanation} dicts",
    )

    # Related Documentation
    related_docs: list[str] = Field(
        default_factory=list, description="Existing docs that may need updates"
    )

    # Suggested Changelog Entry
    changelog_entry: str | None = Field(
        default=None, description="Suggested changelog entry in markdown"
    )

    # Dev's Key Learnings
    key_learnings: list[str] = Field(
        default_factory=list, description="Learnings worth documenting"
    )
    key_decisions: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {decision, rationale} dicts",
    )

    # Questions for Documenter
    questions: list[str] = Field(
        default_factory=list, description="Clarifying questions from dev"
    )

    # Dev Notes Location
    dev_notes_location: str | None = Field(
        default=None, description="Path to developer's journey notes"
    )

    # Status
    status: HandoffStatus = Field(default=HandoffStatus.PENDING)
    assigned_to: UUID | None = Field(default=None, description="Assigned documenter")

    # Timestamps
    claimed_at: datetime | None = None
    completed_at: datetime | None = None

    # Documenter feedback
    documenter_notes: str | None = None

    def claim(self, documenter_id: UUID) -> None:
        """Claim the handoff."""
        self.assigned_to = documenter_id
        self.claimed_at = datetime.utcnow()
        self.status = HandoffStatus.CLAIMED

    def start(self) -> None:
        """Start working on documentation."""
        self.status = HandoffStatus.IN_PROGRESS

    def complete(self, notes: str | None = None) -> None:
        """Mark handoff as complete."""
        self.completed_at = datetime.utcnow()
        self.status = HandoffStatus.COMPLETED
        if notes:
            self.documenter_notes = notes

    def add_required_doc(
        self,
        doc_type: str,
        description: str,
        output_path: str | None = None,
    ) -> None:
        """Add a required documentation item."""
        self.required_docs.append(
            DocumentationItem(
                doc_type=doc_type,
                description=description,
                priority=1,
                output_path=output_path,
            )
        )

    def add_code_sample(
        self,
        title: str,
        language: str,
        code: str,
        explanation: str | None = None,
    ) -> None:
        """Add a code sample for documentation."""
        self.code_samples.append(
            CodeSample(
                title=title,
                language=language,
                code=code,
                explanation=explanation,
            )
        )

    def add_gotcha(self, title: str, explanation: str) -> None:
        """Add a gotcha/warning."""
        self.gotchas.append({"title": title, "explanation": explanation})


# =============================================================================
# HANDOFF FACTORY
# =============================================================================


def create_handoff(
    task_id: UUID,
    summary: str,
    commits: list[dict[str, str]],
    dev_notes_location: str,
    new_functionality: list[str] | None = None,
    modified_behavior: list[str] | None = None,
    breaking_changes: list[str] | None = None,
) -> DocumenterHandoff:
    """Create a basic handoff document."""
    handoff = DocumenterHandoff(
        task_id=task_id,
        summary=summary,
        commits=commits,
        dev_notes_location=dev_notes_location,
        new_functionality=new_functionality or [],
        modified_behavior=modified_behavior or [],
        breaking_changes=breaking_changes or [],
    )

    # Always add changelog as required
    handoff.add_required_doc(
        doc_type="changelog",
        description="Changelog entry for this task",
    )

    return handoff


# =============================================================================
# CREATE SCHEMA
# =============================================================================


class HandoffCreate(RobocoBase):
    """Schema for creating a new handoff."""

    task_id: UUID
    summary: str
    new_functionality: list[str] = Field(default_factory=list)
    modified_behavior: list[str] = Field(default_factory=list)
    breaking_changes: list[str] = Field(default_factory=list)
    commits: list[dict[str, str]] = Field(default_factory=list)
    new_files: list[dict[str, str]] = Field(default_factory=list)
    modified_files: list[dict[str, str]] = Field(default_factory=list)
    code_samples: list[CodeSample] = Field(default_factory=list)
    gotchas: list[dict[str, str]] = Field(default_factory=list)
    related_docs: list[str] = Field(default_factory=list)
    changelog_entry: str | None = None
    key_learnings: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    dev_notes_location: str | None = None
