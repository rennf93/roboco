"""Single chokepoint for persisting structured agent notes.

``apply_structured_note`` validates a payload against its content model, stores
the validated payload as the source of truth in
``task.notes_structured[content_type]``, and regenerates the derived TEXT mirror
column from ``render_markdown()``.

This is the ONLY place that writes a task's TEXT note columns after migration
041. Nothing else should hand-write ``dev_notes`` / ``qa_notes`` /
``auditor_notes`` / ``doc_notes`` / ``pr_reviewer_notes`` / ``quick_context`` —
they are derived, never authored directly.
"""

from __future__ import annotations

from typing import Any, Protocol

from roboco.foundation.policy.content import ContentModel, validate_content

# content-type -> the derived TEXT mirror column. A content type absent from
# this map is stored structured-only (no legacy TEXT reader to keep working).
_MIRROR_COLUMN: dict[str, str] = {
    "developer": "dev_notes",
    "qa": "qa_notes",
    "auditor": "auditor_notes",
    "doc": "doc_notes",
    "pr_review": "pr_reviewer_notes",
    "resumption": "quick_context",
}


class _NotesTask(Protocol):
    notes_structured: dict[str, Any] | None


def apply_structured_note(
    task: _NotesTask, content_type: str, payload: Any
) -> ContentModel:
    """Validate, persist as source of truth, and regenerate the TEXT mirror.

    Raises ``ContentValidationError`` BEFORE any mutation, so a rejected payload
    leaves the task untouched (no partial write).
    """
    model = validate_content(content_type, payload)

    structured = dict(task.notes_structured or {})
    structured[content_type] = model.model_dump(mode="json")
    task.notes_structured = structured  # reassign so the JSON column flags dirty

    column = _MIRROR_COLUMN.get(content_type)
    if column is not None:
        setattr(task, column, model.render_markdown())
    return model
