"""Task #156: MessagingService.propagate_sessions_to_subtask.

Tests the helper's call shape and idempotency contract without going
through the DB. Integration coverage (real DB, real linking) lives in
``tests/integration/test_messaging_service.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.session import SessionTaskRelationshipType
from roboco.services.messaging import MessagingService


def _link(session_id: object, relationship_type: str) -> MagicMock:
    link = MagicMock()
    link.session_id = session_id
    link.relationship_type = relationship_type
    return link


@pytest.mark.asyncio
async def test_propagate_links_every_parent_session_to_subtask() -> None:
    """Every link on the parent gets re-attached to the new subtask."""
    svc = MessagingService.__new__(MessagingService)
    parent_session = uuid4()
    review_session = uuid4()
    svc.get_sessions_for_task = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            _link(parent_session, "discussion"),
            _link(review_session, "review"),
        ]
    )

    calls: list[dict[str, Any]] = []

    async def fake_link(**kwargs: Any) -> Any:
        calls.append(kwargs)
        link = MagicMock()
        link.session_id = kwargs["session_id"]
        link.task_id = kwargs["task_id"]
        return link

    svc.link_session_to_task = fake_link  # type: ignore[method-assign]

    parent_id = uuid4()
    subtask_id = uuid4()
    added_by = uuid4()
    expected_sessions = {parent_session, review_session}
    out = await svc.propagate_sessions_to_subtask(parent_id, subtask_id, added_by)
    assert len(out) == len(expected_sessions)
    assert {c["session_id"] for c in calls} == expected_sessions
    # Every propagated link must be non-primary — primary is the subtask's
    # own slot, never inherited from the parent.
    assert all(c["is_primary"] is False for c in calls)
    # Every call must target the new subtask (not the parent).
    assert {c["task_id"] for c in calls} == {subtask_id}
    # Relationship types preserved.
    rels = {c["relationship_type"] for c in calls}
    assert SessionTaskRelationshipType.DISCUSSION in rels
    assert SessionTaskRelationshipType.REVIEW in rels


@pytest.mark.asyncio
async def test_propagate_no_parent_sessions_returns_empty() -> None:
    """When the parent has no session links, propagation is a no-op."""
    svc = MessagingService.__new__(MessagingService)
    svc.get_sessions_for_task = AsyncMock(return_value=[])  # type: ignore[method-assign]
    svc.link_session_to_task = AsyncMock()  # type: ignore[method-assign]

    out = await svc.propagate_sessions_to_subtask(uuid4(), uuid4(), uuid4())
    assert out == []
    svc.link_session_to_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_propagate_unknown_relationship_type_defaults_to_discussion() -> None:
    """Garbage relationship_type on the parent link doesn't crash; it
    defaults to DISCUSSION so the subtask is still linked."""
    svc = MessagingService.__new__(MessagingService)
    svc.get_sessions_for_task = AsyncMock(  # type: ignore[method-assign]
        return_value=[_link(uuid4(), "definitely-not-a-real-type")]
    )
    calls: list[dict[str, Any]] = []

    async def fake_link(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return MagicMock()

    svc.link_session_to_task = fake_link  # type: ignore[method-assign]

    await svc.propagate_sessions_to_subtask(uuid4(), uuid4(), uuid4())
    assert len(calls) == 1
    assert calls[0]["relationship_type"] == SessionTaskRelationshipType.DISCUSSION
