"""EvidenceRepo real-DB query coverage.

Exercises the actual SQL (array ``contains``, the a2a slug ``or_``, team+status
filters) against a live Postgres — the part the mocked unit tests can't catch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    A2AConversationTable,
    AgentTable,
    NotificationTable,
    ProjectTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    Complexity,
    NotificationPriority,
    NotificationType,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.task import TaskCreateRequest
from roboco.services.gateway.evidence_repo import EvidenceRepo
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="BE PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="EV-Proj",
        slug=f"ev-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "repo": EvidenceRepo(db_session),
        "svc": TaskService(db_session),
        "agent": agent,
        "project_id": project.id,
        "db": db_session,
    }


@pytest.mark.asyncio
async def test_pending_notifications_returns_unacked_for_agent(setup: dict) -> None:
    agent = setup["agent"]
    setup["db"].add(
        NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.HIGH,
            from_agent=agent.id,
            to_agents=[agent.id],
            subject="CEO change request",
            body="redo the API contract",
            timestamp=datetime.now(UTC),
        )
    )
    await setup["db"].flush()
    out = await setup["repo"].list_pending_notifications(agent.id)
    assert len(out) == 1
    assert out[0]["subject"] == "CEO change request"
    # An unrelated agent sees nothing (array membership filter works).
    assert await setup["repo"].list_pending_notifications(uuid4()) == []


@pytest.mark.asyncio
async def test_unread_mentions_returns_unacked_mention_notifications(
    setup: dict,
) -> None:
    """list_unread_mentions surfaces only UNACKED MENTION-type notifications, so
    an agent can clear them via notify_ack and satisfy i_am_idle's soft-block."""
    agent = setup["agent"]
    db = setup["db"]
    db.add(
        NotificationTable(
            type=NotificationType.MENTION,
            priority=NotificationPriority.NORMAL,
            from_agent=agent.id,
            to_agents=[agent.id],
            subject="You were mentioned in #backend-cell",
            body="hey can you look at this",
            timestamp=datetime.now(UTC),
        )
    )
    # A non-mention notification must NOT surface here (type filter).
    db.add(
        NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.HIGH,
            from_agent=agent.id,
            to_agents=[agent.id],
            subject="not a mention",
            body="x",
            timestamp=datetime.now(UTC),
        )
    )
    # An already-acked mention must NOT surface (acked_by is the read signal).
    db.add(
        NotificationTable(
            type=NotificationType.MENTION,
            priority=NotificationPriority.NORMAL,
            from_agent=agent.id,
            to_agents=[agent.id],
            acked_by=[agent.id],
            subject="already handled",
            body="y",
            timestamp=datetime.now(UTC),
        )
    )
    await db.flush()
    out = await setup["repo"].list_unread_mentions(agent.id)
    assert [o["subject"] for o in out] == ["You were mentioned in #backend-cell"]
    # An unrelated agent sees nothing.
    assert await setup["repo"].list_unread_mentions(uuid4()) == []


@pytest.mark.asyncio
async def test_unread_a2a_returns_conversations_with_unread(setup: dict) -> None:
    agent = setup["agent"]
    now = datetime.now(UTC)
    seeded_unread = 2
    setup["db"].add(
        A2AConversationTable(
            agent_a=agent.slug,
            agent_b="main-pm",
            unread_by_a=seeded_unread,
            unread_by_b=0,
            topic="rework",
            created_at=now,
            updated_at=now,
        )
    )
    await setup["db"].flush()
    out = await setup["repo"].list_unread_a2a(agent.id)
    assert len(out) == 1
    assert out[0]["from_agent"] == "main-pm"
    assert out[0]["unread"] == seeded_unread


@pytest.mark.asyncio
async def test_blockers_and_recent_activity_scoped_to_team(setup: dict) -> None:
    svc, agent = setup["svc"], setup["agent"]
    task = await svc.create(
        TaskCreateRequest(
            title="blocked thing",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=agent.id,
            project_id=setup["project_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.status = TaskStatus.BLOCKED
    await setup["db"].flush()

    blockers = await setup["repo"].blockers_in_lane(agent.id)
    assert any(b["task_id"] == str(task.id) for b in blockers)
    recent = await setup["repo"].recent_team_activity(agent.id)
    assert any(r["task_id"] == str(task.id) for r in recent)


@pytest.mark.asyncio
async def test_task_metadata_gaps_flags_missing(setup: dict) -> None:
    svc, agent = setup["svc"], setup["agent"]
    task = await svc.create(
        TaskCreateRequest(
            title="thin",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=agent.id,
            project_id=setup["project_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.acceptance_criteria = []
    task.description = ""
    await setup["db"].flush()
    gaps = await setup["repo"].task_metadata_gaps(task.id)
    assert "no acceptance criteria" in gaps
    assert "no description" in gaps
