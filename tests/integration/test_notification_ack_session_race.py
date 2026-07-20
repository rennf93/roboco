"""send_ack_notification must be able to join the caller's transaction.

The origination engines notify about a task their OWN open transaction just
created; a fresh session cannot see that uncommitted row, so the
notifications.related_task_id FK rejects the insert and the bell
notification is silently lost (the 0.26.0 release-proposal case, caught in
the live postgres log). Real-Postgres coverage for both directions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, NotificationTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.notification import NotificationService
from sqlalchemy import select

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(db_session: AsyncSession) -> tuple[AgentTable, TaskTable]:
    unique = uuid4().hex[:6]
    agent = AgentTable(
        id=uuid4(),
        name=f"ceo-{unique}",
        slug=f"ceo-{unique}",
        role=AgentRole.CEO,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="Release proposal: v9.9.9",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        status=TaskStatus.PENDING,
        team=Team.BOARD,
        created_by=agent.id,
        estimated_complexity=Complexity.LOW,
    )
    db_session.add(task)
    # Flushed but NOT committed — exactly the engine's state at notify time.
    await db_session.flush()
    return agent, task


@pytest.mark.asyncio
async def test_ack_notification_joins_the_callers_transaction(
    db_session: AsyncSession,
) -> None:
    agent, task = await _seed(db_session)
    await NotificationService().send_ack_notification(
        from_agent=agent.slug,
        to_agent=agent.slug,
        body="v9.9.9 ready",
        task_id=cast("UUID", task.id),
        db_session=db_session,
    )
    row = (
        await db_session.execute(
            select(NotificationTable).where(
                NotificationTable.related_task_id == task.id
            )
        )
    ).scalar_one()
    assert row.subject == "v9.9.9 ready"
