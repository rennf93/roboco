"""Tests for Board (and Main PM) escalate_to_ceo Choreographer verb.

Covers Phase 4 Task 1: role allow-list (main_pm, product_owner, head_marketing),
state gate (awaiting_pm_review only), and journal:decision tracing gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    # VerbRunner wraps composed atomic actions in
    # ``task.session.begin_nested()``. AsyncMock auto-attribute access
    # would return an unawaitable coroutine, breaking the
    # ``async with`` protocol. Overwrite session with a MagicMock that
    # implements the async-context-manager protocol explicitly.
    task_dep = base["task"]
    task_dep.session = MagicMock()
    task_dep.session.begin_nested = MagicMock(
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_succeeds_for_product_owner() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(id=agent_id, role="product_owner")
    task_svc.escalate_to_ceo.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(agent_id, task_id, reason="ready for CEO sign-off")
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"
    task_svc.escalate_to_ceo.assert_awaited_once_with(
        task_id=task_id,
        agent_role="product_owner",
        notes="ready for CEO sign-off",
        actor_agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_succeeds_for_head_marketing() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(id=agent_id, role="head_marketing")
    task_svc.escalate_to_ceo.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(agent_id, task_id, reason="brand-affecting change")
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"
    task_svc.escalate_to_ceo.assert_awaited_once_with(
        task_id=task_id,
        agent_role="head_marketing",
        notes="brand-affecting change",
        actor_agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_blocks_wrong_state() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress", team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(
        agent_id, task_id, reason="escalating to the CEO for sign-off"
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "awaiting_pm_review" in body["message"]
    task_svc.escalate_to_ceo.assert_not_awaited()


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_rejects_when_runner_declines() -> None:
    """Runner returns None (service declined — e.g. the task left
    awaiting_pm_review mid-flight) → a clean invalid_state, NOT an unhandled
    ``None.status`` 500 (the board's escalate-from-blocked crash loop)."""
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    task_svc.escalate_to_ceo.return_value = None
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(agent_id, task_id, reason="ready for CEO sign-off")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "awaiting_pm_review" in body["message"]


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_blocks_disallowed_role() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="qa")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(
        agent_id, task_id, reason="escalating to the CEO for sign-off"
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "qa" in body["message"]
    task_svc.escalate_to_ceo.assert_not_awaited()


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_requires_journal_decision() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.latest_decision_at.return_value = None
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(
        agent_id, task_id, reason="escalating to the CEO for sign-off"
    )
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]
    task_svc.escalate_to_ceo.assert_not_awaited()


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_returns_not_found_when_task_missing() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(
        agent_id, task_id, reason="escalating to the CEO for sign-off"
    )
    body = env.as_dict()
    assert body["error"] == "not_found"
    task_svc.escalate_to_ceo.assert_not_awaited()


@pytest.mark.asyncio
async def test_board_escalate_to_ceo_succeeds_for_main_pm() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(id=agent_id, role="main_pm")
    task_svc.escalate_to_ceo.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_to_ceo(agent_id, task_id, reason="root task done")
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"
    task_svc.escalate_to_ceo.assert_awaited_once_with(
        task_id=task_id,
        agent_role="main_pm",
        notes="root task done",
        actor_agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_board_triage_returns_strategic_first() -> None:
    po_id = uuid4()
    strategic = MagicMock(
        id=uuid4(),
        status="awaiting_pm_review",
        title="strategic root",
        team="backend",
        parent_task_id=None,
    )
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="product_owner", team="board")
    task_svc.list_strategic_for_board.return_value = [strategic]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.board_triage(po_id)
    body = env.as_dict()
    assert body["task_id"] == str(strategic.id)
    assert "escalate_to_ceo" in body["next"]


@pytest.mark.asyncio
async def test_board_triage_returns_idle_when_nothing_strategic() -> None:
    po_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="product_owner", team="board")
    task_svc.list_strategic_for_board.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.board_triage(po_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    assert body["task_id"] is None


@pytest.mark.asyncio
async def test_board_triage_works_for_head_marketing() -> None:
    """Verb is role-agnostic among board members — head_marketing also gets results."""
    hm_id = uuid4()
    strategic = MagicMock(
        id=uuid4(),
        status="awaiting_pm_review",
        title="marketing strategic",
        team="frontend",
        parent_task_id=None,
    )
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="head_marketing", team="board")
    task_svc.list_strategic_for_board.return_value = [strategic]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.board_triage(hm_id)
    body = env.as_dict()
    assert body["task_id"] == str(strategic.id)


@pytest.mark.asyncio
async def test_board_triage_idle_branch_carries_company_goals_for_product_owner() -> (
    None
):
    """The idle branch is what the Product Owner's roadmap-exploration one-shot
    spawn hits first (its directly-assigned exploration task is never itself a
    "strategic root awaiting PM review"), so the CEO's brand_voice/north_star
    charter must still reach it there."""
    po_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="product_owner", team="board")
    task_svc.list_strategic_for_board.return_value = []
    deps = _make_deps(task=task_svc)
    deps.evidence_repo.company_goals.return_value = {
        "north_star": "Win the market",
        "brand_voice": "Confident, dry wit.",
    }
    c = Choreographer(deps)

    env = await c.board_triage(po_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    company_goals = body["context_briefing"]["company_goals"]
    assert company_goals["brand_voice"] == "Confident, dry wit."
    assert company_goals["north_star"] == "Win the market"
    deps.evidence_repo.company_goals.assert_awaited_once()


@pytest.mark.asyncio
async def test_board_triage_idle_branch_carries_company_goals_for_head_marketing() -> (
    None
):
    """Same gap, Head of Marketing side — hit by the feature-spotlight
    exploration spawn's triage() call."""
    hm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="head_marketing", team="board")
    task_svc.list_strategic_for_board.return_value = []
    deps = _make_deps(task=task_svc)
    deps.evidence_repo.company_goals.return_value = {
        "north_star": "Win the market",
        "brand_voice": "Confident, dry wit.",
    }
    c = Choreographer(deps)

    env = await c.board_triage(hm_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    company_goals = body["context_briefing"]["company_goals"]
    assert company_goals["brand_voice"] == "Confident, dry wit."
    deps.evidence_repo.company_goals.assert_awaited_once()


@pytest.mark.asyncio
async def test_board_triage_idle_branch_omits_company_goals_when_charter_unset() -> (
    None
):
    """No regression: an unset charter (EvidenceRepo.company_goals() -> None)
    still yields no company_goals key — the idle branch does not fabricate a
    section that isn't there."""
    po_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="product_owner", team="board")
    task_svc.list_strategic_for_board.return_value = []
    deps = _make_deps(task=task_svc)
    deps.evidence_repo.company_goals.return_value = None
    c = Choreographer(deps)

    env = await c.board_triage(po_id)
    body = env.as_dict()
    assert "company_goals" not in body["context_briefing"]


@pytest.mark.asyncio
async def test_board_triage_strategic_branch_company_goals_unaffected() -> None:
    """No regression: the strategic-review branch already passed full=True and
    keeps doing exactly that — one company_goals fetch, unaffected by the
    idle-branch's narrower include_company_goals opt-in."""
    po_id = uuid4()
    strategic = MagicMock(
        id=uuid4(),
        status="awaiting_pm_review",
        title="strategic root",
        team="backend",
        parent_task_id=None,
    )
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="product_owner", team="board")
    task_svc.list_strategic_for_board.return_value = [strategic]
    deps = _make_deps(task=task_svc)
    deps.evidence_repo.company_goals.return_value = {"north_star": "Win"}
    c = Choreographer(deps)

    env = await c.board_triage(po_id)
    body = env.as_dict()
    assert body["context_briefing"]["company_goals"] == {"north_star": "Win"}
    deps.evidence_repo.company_goals.assert_awaited_once()
