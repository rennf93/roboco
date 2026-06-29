"""``i_am_blocked`` must surface ``invalid_state`` instead of 500 when the
block action returns ``None`` (no escalation target resolvable) — the
choreographer emits a re-fetch + escalate-to-CEO rejection rather than
dereferencing ``None.status``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_evidence_repo() -> AsyncMock:
    repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
        "similar_memory",
    ):
        getattr(repo, method).return_value = []
    return repo


def _make_task_svc(agent_id: object, task_id: object) -> AsyncMock:
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        pre_block_state=None,
        task_type="code",
        team="backend",
        dependency_ids=[],
        acceptance_criteria=[],
        quick_context=None,
        notes_structured=None,
    )
    task_svc = AsyncMock()
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id,
        role="developer",
        team="backend",
        slug="be-dev-1",
    )
    # escalate resolves no escalation target for this role → None.
    task_svc.escalate.return_value = None
    return task_svc


def _make_deps(agent_id: object, task_id: object) -> ChoreographerDeps:
    return ChoreographerDeps(
        task=_make_task_svc(agent_id, task_id),
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=_make_evidence_repo(),
    )


@pytest.mark.asyncio
async def test_i_am_blocked_surfaces_invalid_state_when_escalate_returns_none() -> None:
    """No escalation target resolvable → invalid_state envelope, not a 500."""
    agent_id = uuid4()
    task_id = uuid4()
    deps = _make_deps(agent_id, task_id)
    c = Choreographer(deps)

    env = await c.i_am_blocked(agent_id, task_id, "waiting on external API access")

    # Must be a clean invalid_state rejection the agent can act on — not a
    # crash/500 from None.status deref.
    assert env.error is not None, env.as_dict()
    assert env.error == "invalid_state", env.as_dict()
    # The task transition did NOT happen (escalate returned None).
    deps.task.escalate.assert_awaited_once()
