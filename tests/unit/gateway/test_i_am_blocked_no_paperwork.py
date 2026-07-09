"""``i_am_blocked`` must never demand paperwork — a non-empty ``reason`` is
the only requirement (blocker_type / what_needed stay optional), so a wedged
agent can bail with one sentence. Also pins the awaiting_qa bail message: a
dev whose task already moved to QA review is not blocked, it's done — the
rejection must say so and point at i_am_idle(), not list allowed states like
a wall.

Mirrors the fake-dependency shape of test_i_am_blocked_no_escalation_target.py.
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


def _make_task(agent_id: object, task_id: object, status: str) -> MagicMock:
    return MagicMock(
        id=task_id,
        status=status,
        assigned_to=agent_id,
        pre_block_state=None,
        task_type="code",
        team="backend",
        dependency_ids=[],
        acceptance_criteria=[],
        quick_context=None,
        notes_structured=None,
    )


def _make_task_svc(agent_id: object, task: object) -> AsyncMock:
    task_svc = AsyncMock()
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    task_svc.get.return_value = task
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id,
        role="developer",
        team="backend",
        slug="be-dev-1",
    )
    return task_svc


def _make_deps(task_svc: AsyncMock) -> ChoreographerDeps:
    return ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=_make_evidence_repo(),
    )


@pytest.mark.asyncio
async def test_i_am_blocked_succeeds_with_only_reason() -> None:
    """No blocker_type/what_needed supplied — a bare reason is enough."""
    agent_id = uuid4()
    task_id = uuid4()
    task = _make_task(agent_id, task_id, "in_progress")
    task_svc = _make_task_svc(agent_id, task)
    blocked_task = _make_task(agent_id, task_id, "blocked")
    task_svc.escalate.return_value = blocked_task
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.i_am_blocked(agent_id, task_id, "wedged, cannot push, need help")

    assert env.error is None, env.as_dict()
    assert env.status == "blocked"
    task_svc.escalate.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_blocked_from_awaiting_qa_names_i_am_idle() -> None:
    """A task that already moved to QA is not this dev's to block anymore —
    the rejection must say the truth (done, QA owns it) and point at
    i_am_idle(), not a generic 'find a task in [...]' dead end."""
    agent_id = uuid4()
    task_id = uuid4()
    task = _make_task(agent_id, task_id, "awaiting_qa")
    task_svc = _make_task_svc(agent_id, task)
    deps = _make_deps(task_svc)
    c = Choreographer(deps)

    env = await c.i_am_blocked(agent_id, task_id, "stuck on this task")

    assert env.error == "invalid_state", env.as_dict()
    assert "awaiting_qa" in (env.message or "")
    remediate = env.remediate or ""
    assert "i_am_idle" in remediate
    assert "QA" in remediate
    task_svc.escalate.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
