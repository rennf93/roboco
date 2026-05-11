"""TaskUpdate (PATCH /tasks/{id}) must reject blanking acceptance_criteria.

The Golden Rule "no task without acceptance criteria" applies to every
mutation, not just creation. Setting acceptance_criteria to [] or None
in a PATCH is a Golden Rule violation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from roboco.api.schemas.tasks import TaskUpdate


def test_task_update_accepts_omitting_acceptance_criteria() -> None:
    """Omitting the field is fine — caller isn't touching it."""
    upd = TaskUpdate(title="new title")
    assert upd.acceptance_criteria is None


def test_task_update_accepts_replacing_acceptance_criteria_with_non_empty_list() -> (
    None
):
    upd = TaskUpdate(acceptance_criteria=["new criterion"])
    assert upd.acceptance_criteria == ["new criterion"]


def test_task_update_rejects_empty_acceptance_criteria() -> None:
    """Empty list = "blank the criteria" = Golden Rule violation."""
    with pytest.raises(ValidationError):
        TaskUpdate(acceptance_criteria=[])


def test_task_update_rejects_explicit_none_when_present() -> None:
    """Pydantic differentiates omitted (default) from explicitly None."""
    with pytest.raises(ValidationError):
        TaskUpdate(**{"acceptance_criteria": None})


def test_task_update_rejects_short_description() -> None:
    """min_length applies on PATCH too."""
    with pytest.raises(ValidationError):
        TaskUpdate(description="x")
