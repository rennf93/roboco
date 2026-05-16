"""Task #153: i_will_plan must persist the panel-shaped rich plan dict.

Bug:
    Choreographer.i_will_plan builds ctx (_ClaimPlanStartContext) with the
    panel-shaped plan dict via _resolve_effective_plan, but spec_ctx
    (lifecycle.Context) holds the raw plan string. The verb runner uses
    spec_ctx — so the rich dict never reaches TaskService.set_plan.

    Symptom: the panel's Plan tab shows only `text` (the raw paragraph);
    `approach`, `sub_tasks`, `risks`, `open_questions` all stay empty.

Fix:
    Pass the resolved (possibly-dict) plan into spec_ctx so the runner's
    set_plan handler persists the rich shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


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
    task = base["task"]
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _pm_task_svc_with_set_plan_capture(
    pm_id: object, task_id: object, role: str = "cell_pm"
) -> AsyncMock:
    """TaskService mock that completes claim, set_plan, start for i_will_plan."""
    task_svc = AsyncMock()
    pending = MagicMock(
        id=task_id,
        status="pending",
        plan=None,
        assigned_to=None,
        task_type="planning",
        parent_task_id=None,
        sequence=0,
        team="backend",
        commits=[],
        pr_number=None,
        branch_name=None,
        quick_context=None,
        work_session_id=None,
    )
    claimed = MagicMock(
        id=task_id,
        status="claimed",
        plan=None,
        assigned_to=pm_id,
        task_type="planning",
        work_session_id=None,
    )
    started = MagicMock(
        id=task_id,
        status="in_progress",
        plan={"text": "x"},
        assigned_to=pm_id,
        task_type="planning",
        work_session_id=None,
    )
    task_svc.get.return_value = pending
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role=role, team="backend", slug=None
    )
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.get_subtasks.return_value = []
    task_svc.claim.return_value = claimed
    task_svc.set_plan.return_value = claimed
    task_svc.start.return_value = started
    task_svc.ensure_work_session.return_value = None
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return task_svc


@pytest.mark.asyncio
async def test_i_will_plan_persists_panel_shaped_plan() -> None:
    """When rich_plan has approach + sub_tasks, set_plan must receive the dict.

    Reproduces the panel "Plan tab is empty" bug: a PM submits a rich plan
    but only `{"text": "<raw paragraph>"}` reaches the DB, because the verb
    runner reads spec_ctx.plan (raw string) instead of the resolved dict.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = _pm_task_svc_with_set_plan_capture(pm_id, task_id, role="cell_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    sub_tasks_in = [
        {
            "title": "Backend slice",
            "description": (
                "be-dev-1 implements the API endpoint and the DB schema "
                "migration, with tests, behind the existing service layer."
            ),
        },
        {
            "title": "UX slice",
            "description": (
                "fe-dev-1 wires the panel view to the new endpoint and "
                "renders the result list with loading + error states."
            ),
        },
    ]
    rich = {
        "approach": (
            "Three-slice decomposition for the feature: backend builds the "
            "API + DB migration first, UX consumes it after the endpoint is "
            "merged, QA reviews each PR, docs follow. Strict sequencing; the "
            "UX slice depends on the backend slice landing first."
        ),
        "sub_tasks": sub_tasks_in,
        "risks": [{"risk": "schema migration may block", "mitigation": "rehearse"}],
    }

    env = await c.i_will_plan(
        pm_id, task_id, plan="decompose the feature", rich_plan=rich
    )
    body = env.as_dict()
    # Sanity: gate did not block.
    assert body.get("error") != "incomplete_input", body

    # The bug: set_plan was called with the raw string. We assert the rich
    # dict shape — this fails before the fix.
    task_svc.set_plan.assert_awaited_once()
    persisted = task_svc.set_plan.await_args.args[1]
    assert isinstance(persisted, dict), (
        f"set_plan was called with a non-dict ({type(persisted).__name__}) — "
        f"panel Plan tab needs the rich shape. Got: {persisted!r}"
    )
    assert persisted.get("approach"), (
        f"set_plan dict is missing 'approach' — got keys {list(persisted)}"
    )
    sub_tasks = persisted.get("sub_tasks") or []
    assert len(sub_tasks) == len(sub_tasks_in), (
        f"set_plan dict is missing sub_tasks (expected {len(sub_tasks_in)}) — "
        f"got {sub_tasks!r}"
    )
    assert persisted.get("text") == "decompose the feature"


@pytest.mark.asyncio
async def test_i_will_plan_with_thin_rich_plan_persists_string() -> None:
    """PM passing a valid rich_plan — set_plan receives the dict shape.

    Regression coverage for _resolve_effective_plan: with rich-plan fields
    populated the dict shape (not the raw string) must reach set_plan, and
    the raw paragraph is preserved under `text`.
    """
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = _pm_task_svc_with_set_plan_capture(pm_id, task_id, role="cell_pm")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    # A valid rich_plan satisfies the gate; we test the str-vs-dict branch
    # of _resolve_effective_plan by checking what shape reaches set_plan.
    env = await c.i_will_plan(
        pm_id,
        task_id,
        plan="bare plan paragraph",
        rich_plan={
            "approach": (
                "Single-slice decomposition: be-dev-1 owns the change end "
                "to end — branch, implement, test, open PR; QA reviews after "
                "the PR opens; docs follow; then be-pm completes and submits "
                "up. No cross-cell dependencies for this task."
            ),
            "sub_tasks": [
                {
                    "title": "Slice",
                    "description": (
                        "be-dev-1 implements the change with tests and opens "
                        "the leaf PR for QA review."
                    ),
                }
            ],
        },
    )
    body = env.as_dict()
    assert body.get("error") != "incomplete_input", body
    task_svc.set_plan.assert_awaited_once()
    persisted = task_svc.set_plan.await_args.args[1]
    # Rich plan supplied: dict shape persisted (regression coverage —
    # mirrors the rich test above but asserts the str-fallback branch
    # is not accidentally taken).
    assert isinstance(persisted, dict), persisted
    assert persisted.get("text") == "bare plan paragraph"
