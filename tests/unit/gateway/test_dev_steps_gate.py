"""#172: a developer's i_will_work_on must carry a substantive step checklist.

The dev plan was a free string with only a presence gate. Plan-driven
progress (#173) needs a checklist on the executing dev's task too, so
i_will_work_on now takes structured `steps` (same SubTask shape as a
PM's sub_tasks), gated for depth like the PM plan, persisted into
task.plan.sub_tasks via the panel-shaped path. Re-entry/recovery
short-circuit before the gate so a respawned dev is never re-blocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

_GOOD_STEP_DESC = (
    "be-dev-1 prepends the smoke-test HTML comment above the README H1, "
    "leaving the rest of the file untouched, then stages the change."
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


def _dev_task_svc(task_id: object, *, status: str = "pending") -> AsyncMock:
    svc = AsyncMock()
    svc.get.return_value = MagicMock(
        id=task_id,
        status=status,
        plan=None,
        assigned_to=None,
        task_type="code",
        parent_task_id=uuid4(),
        sequence=0,
        team="backend",
        commits=[],
        pr_number=None,
        branch_name=None,
        quick_context=None,
    )
    svc.agent_for.return_value = MagicMock(
        id=uuid4(), role="developer", team="backend", slug="be-dev-1"
    )
    svc.list_in_progress_for_agent.return_value = []
    svc.list_paused_for_agent.return_value = []
    svc.get_subtasks.return_value = []
    svc.session = MagicMock()
    svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return svc


@pytest.mark.asyncio
async def test_dev_fresh_claim_without_steps_is_rejected() -> None:
    dev_id = uuid4()
    task_id = uuid4()
    c = Choreographer(_make_deps(task=_dev_task_svc(task_id)))

    env = await c.i_will_work_on(dev_id, task_id, plan="do the thing")
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "steps" in (body.get("missing") or []), body


@pytest.mark.asyncio
async def test_dev_thin_step_description_is_rejected() -> None:
    dev_id = uuid4()
    task_id = uuid4()
    c = Choreographer(_make_deps(task=_dev_task_svc(task_id)))

    env = await c.i_will_work_on(
        dev_id,
        task_id,
        plan="do the thing",
        steps=[{"title": "Edit README", "description": "edit it"}],
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input", body
    assert "steps" in (body.get("missing") or []), body


@pytest.mark.asyncio
async def test_dev_with_substantive_steps_passes_gate_and_persists_checklist() -> None:
    dev_id = uuid4()
    task_id = uuid4()
    svc = _dev_task_svc(task_id)
    claimed = MagicMock(
        id=task_id, status="claimed", plan=None, assigned_to=dev_id, task_type="code"
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=dev_id,
        task_type="code",
    )
    svc.claim.return_value = claimed
    svc.set_plan.return_value = claimed
    svc.start.return_value = started
    c = Choreographer(_make_deps(task=svc))

    steps_in = [
        {"title": "Edit README", "description": _GOOD_STEP_DESC},
        {"title": "Commit + open PR", "description": _GOOD_STEP_DESC},
    ]
    env = await c.i_will_work_on(
        dev_id,
        task_id,
        plan="implement the README change end to end",
        steps=steps_in,
    )
    body = env.as_dict()
    assert body.get("error") != "incomplete_input", body
    # Steps were layered into the panel-shaped plan dict and persisted.
    svc.set_plan.assert_awaited_once()
    persisted = svc.set_plan.await_args.args[1]
    assert isinstance(persisted, dict), persisted
    sub_tasks = persisted.get("sub_tasks") or []
    assert len(sub_tasks) == len(steps_in), persisted
    assert all(st.get("title") for st in sub_tasks), sub_tasks
    assert persisted.get("text") == "implement the README change end to end"


@pytest.mark.asyncio
async def test_dev_reentry_in_progress_short_circuits_before_steps_gate() -> None:
    """A respawned dev re-calling on a task it owns in_progress with NO
    steps must short-circuit to OK, not be re-blocked for steps."""
    dev_id = uuid4()
    task_id = uuid4()
    svc = _dev_task_svc(task_id, status="in_progress")
    svc.get.return_value.assigned_to = dev_id
    c = Choreographer(_make_deps(task=svc))

    env = await c.i_will_work_on(dev_id, task_id, plan="resume: keep going")
    body = env.as_dict()
    assert body.get("error") is None, body
    assert body.get("status") == "in_progress", body
