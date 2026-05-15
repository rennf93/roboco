"""Task #163: delegate() rejects PM-created documentation subtasks.

Smoke-12: be-pm delegated TWO subtasks under one cell parent — a
`code` subtask (be-dev-1, spawned) and a `documentation` subtask
(be-dev-2). The orchestrator's dev-dispatch refuses to spawn a
developer for a documentation task_type, so the doc subtask became a
permanent orphan that would deadlock submit_up (all subtasks must be
terminal). The spine-cap is per-type so code+documentation both passed
the sibling-dedup. Fix: _delegate_static_guards rejects
task_type='documentation' with a remediate explaining the lifecycle
auto-handles docs after the code subtask passes QA.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import DelegateInputs


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
        "messaging": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, m).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _parent(parent_id: object) -> MagicMock:
    return MagicMock(
        id=parent_id,
        project_id=uuid4(),
        team="backend",
        status="in_progress",
        task_type="planning",
        sequence=0,
        assigned_to=uuid4(),
    )


def _inputs(task_type: str) -> DelegateInputs:
    return DelegateInputs(
        title="Doc the change",
        description="Write documentation for the README change",
        acceptance_criteria=["doc created", "doc linked"],
        assigned_to="be-dev-2",
        team="backend",
        task_type=task_type,
        nature="technical",
        estimated_complexity="medium",
    )


@pytest.mark.asyncio
async def test_delegate_rejects_documentation_task_type() -> None:
    """A documentation-typed subtask is rejected with a lifecycle hint."""
    pm_id = uuid4()
    parent_id = uuid4()
    deps = _make_deps()
    c = Choreographer(deps)

    env = await c._delegate_static_guards(
        pm_id, parent_id, _parent(parent_id), _inputs("documentation")
    )
    assert env is not None, "documentation subtask must be rejected"
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    remediate = (body["remediate"] or "").lower()
    assert "task_type='code'" in remediate or "task_type=code" in remediate
    assert "awaiting_documentation" in remediate or "automatically" in remediate
    # Must NOT tell the PM to just retry documentation.
    assert "documenter" in remediate


@pytest.mark.asyncio
async def test_delegate_allows_code_task_type() -> None:
    """The code subtask (the only thing the PM should delegate) passes
    the static guard."""
    pm_id = uuid4()
    parent_id = uuid4()
    deps = _make_deps()
    c = Choreographer(deps)

    env = await c._delegate_static_guards(
        pm_id, parent_id, _parent(parent_id), _inputs("code")
    )
    assert env is None, f"code subtask must pass static guards, got {env}"


@pytest.mark.asyncio
async def test_delegate_still_allows_planning_for_cell_pm() -> None:
    """Main PM delegating planning to a cell PM is unaffected."""
    pm_id = uuid4()
    parent_id = uuid4()
    deps = _make_deps()
    c = Choreographer(deps)

    inputs = DelegateInputs(
        title="Backend slice",
        description="Own the backend slice end to end",
        acceptance_criteria=["slice done"],
        assigned_to="be-pm",
        team="backend",
        task_type="planning",
        nature="technical",
        estimated_complexity="medium",
    )
    env = await c._delegate_static_guards(pm_id, parent_id, _parent(parent_id), inputs)
    assert env is None, f"planning→cell-PM must pass, got {env}"
