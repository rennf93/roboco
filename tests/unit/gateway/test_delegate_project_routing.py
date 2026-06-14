from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
    DelegateInputs,
)


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
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


def _parent(pm_id: Any, product_id: Any = None, project_id: Any = None) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        project_id=project_id or uuid4(),
        product_id=product_id,
        status="in_progress",
        assigned_to=pm_id,
    )


def _inputs(**kw: Any) -> DelegateInputs:
    base: dict[str, Any] = {
        "title": "Implement endpoint",
        "description": "Add /v1/foo endpoint with tests",
        "assigned_to": "be-dev-1",
        "team": "backend",
        "task_type": "code",
        "nature": "technical",
        "acceptance_criteria": ["GET /v1/foo returns 200 with body"],
    }
    base.update(kw)
    return DelegateInputs(**base)


async def _run(parent: Any, inputs: DelegateInputs, product: Any = None) -> Any:
    pm_id = parent.assigned_to
    task_svc = AsyncMock()
    task_svc.get.return_value = parent
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.get_subtasks.return_value = []
    task_svc.create_subtask.return_value = MagicMock(id=uuid4())
    deps = _make_deps(task=task_svc, **({"product": product} if product else {}))
    c = Choreographer(deps)
    env = await c.delegate(pm_id, parent.id, inputs)
    return env, task_svc


@pytest.mark.asyncio
async def test_explicit_project_id_overrides_everything() -> None:
    override = uuid4()
    parent = _parent(uuid4(), product_id=uuid4())
    env, task_svc = await _run(parent, _inputs(project_id=override))
    assert env.error is None, env.as_dict()
    req = task_svc.create_subtask.call_args.args[0]
    assert req.project_id == override


@pytest.mark.asyncio
async def test_product_map_resolves_project_when_no_override() -> None:
    mapped = uuid4()
    product_id = uuid4()
    parent = _parent(uuid4(), product_id=product_id)
    product = AsyncMock()
    product.project_for.return_value = mapped
    env, task_svc = await _run(parent, _inputs(), product=product)
    assert env.error is None, env.as_dict()
    req = task_svc.create_subtask.call_args.args[0]
    assert req.project_id == mapped
    assert req.product_id == product_id  # inherited onto the subtask
    product.project_for.assert_awaited_once()


@pytest.mark.asyncio
async def test_falls_back_to_parent_project_when_no_product() -> None:
    parent = _parent(uuid4(), product_id=None)
    env, task_svc = await _run(parent, _inputs())
    assert env.error is None, env.as_dict()
    req = task_svc.create_subtask.call_args.args[0]
    assert req.project_id == parent.project_id


@pytest.mark.asyncio
async def test_partial_product_map_degrades_to_parent_project() -> None:
    product_id = uuid4()
    parent = _parent(uuid4(), product_id=product_id)
    product = AsyncMock()
    product.project_for.return_value = None  # no mapping for this cell
    env, task_svc = await _run(parent, _inputs(), product=product)
    assert env.error is None, env.as_dict()
    req = task_svc.create_subtask.call_args.args[0]
    assert req.project_id == parent.project_id
    assert req.product_id == product_id
