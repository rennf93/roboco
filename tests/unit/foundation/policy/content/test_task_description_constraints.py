"""TaskDescription.constraints: renders as a section when present, else absent."""

from __future__ import annotations

from typing import Any

from roboco.foundation.identity import Team
from roboco.foundation.policy.content.models import TaskDescription, WorkUnit


def _desc(**overrides: Any) -> TaskDescription:
    fields: dict[str, Any] = {
        "objective": "Build the thing properly",
        "the_work": [WorkUnit(team=Team.BACKEND, summary="do the work", items=["a"])],
        "acceptance_criteria": ["it works"],
    }
    fields.update(overrides)
    return TaskDescription(**fields)


def test_constraints_render_as_section() -> None:
    md = _desc(constraints=["no models in routers"]).render_markdown()
    assert "## Constraints" in md
    assert "no models in routers" in md


def test_no_constraints_section_when_empty() -> None:
    assert "## Constraints" not in _desc().render_markdown()
