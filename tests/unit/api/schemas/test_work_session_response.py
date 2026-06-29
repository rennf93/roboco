"""session_to_response / session_to_summary honesty for a null agent_id.

The ``work_sessions.agent_id`` column is ``nullable=True`` with
``ondelete="SET NULL"`` — if an agent row is deleted, the FK nulls out on every
session that agent ever held. The read path (``session_to_response`` ->
``WorkSessionResponse``) used to assume ``agent_id`` was always present: the
ORM ``Mapped[UUID]`` lied, the converter ``typing_cast("UUID", ...)``
papered over the lie, and ``WorkSessionResponse.agent_id: UUID`` rejected
``None`` outright. A session whose agent had been deleted therefore crashed
the GET endpoint with a pydantic ``ValidationError`` instead of serializing
``agent_id: null``. These tests pin the honest end-to-end read path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from roboco.api.schemas.work_session import (
    WorkSessionResponse,
    session_to_response,
    session_to_summary,
)
from roboco.db.tables import WorkSessionTable
from roboco.models.work_session import WorkSessionStatus


def _make_session(agent_id: UUID | None) -> WorkSessionTable:
    """Build a detached WorkSessionTable row with an explicit agent_id."""
    now = datetime.now(UTC)
    return WorkSessionTable(
        id=uuid4(),
        project_id=uuid4(),
        task_id=uuid4(),
        agent_id=agent_id,
        branch_name="feature/x",
        base_branch="main",
        target_branch="main",
        started_at=now,
        status=WorkSessionStatus.ACTIVE,
        commits=[],
        files_modified=[],
        created_at=now,
    )


def test_session_to_response_serializes_null_agent_id() -> None:
    """A SET-NULL'd session (agent deleted) must serialize agent_id as None."""
    session = _make_session(agent_id=None)
    result = session_to_response(session)
    assert isinstance(result, WorkSessionResponse)
    assert result.agent_id is None


def test_session_to_response_preserves_present_agent_id() -> None:
    """A normal session still carries its agent_id through unchanged."""
    agent_id = uuid4()
    result = session_to_response(_make_session(agent_id=agent_id))
    assert result.agent_id == agent_id


def test_session_to_summary_does_not_read_agent_id() -> None:
    """The summary view omits agent_id entirely, so a null agent must not raise."""
    result = session_to_summary(_make_session(agent_id=None))
    assert result.status == WorkSessionStatus.ACTIVE
