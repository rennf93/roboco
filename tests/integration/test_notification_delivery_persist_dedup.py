"""NotificationDeliveryService._persist_and_deliver DB purpose-dedup.

`_persist_and_deliver` (used by the task-handoff helpers: notify_pm_of_block,
escalate_and_notify, notify_assignee_of_unblock, etc.) used to run ONLY the
60s Redis re-fire guard and skip DB purpose-dedup entirely — a retried
`i_am_blocked` or a re-issued escalate past the 60s window re-created a
second unacked notification for the same (sender, type, task, recipients).

These integration tests seed an unacked notification directly (mirroring an
already-persisted prior send), then drive `_persist_and_deliver` with a
duplicate-shaped notification while bypassing the Redis guard (monkeypatched
to return False, matching tests/unit/test_notification_delivery_refire.py),
and assert no second row is created while the first stays unacked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, NotificationTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, NotificationPriority, NotificationType
from roboco.models.base import TaskNature, TaskStatus, TaskType, Team
from roboco.services.notification_delivery import get_notification_delivery_service
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _make_agent(db: AsyncSession, *, role: AgentRole) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=f"{role.value}-{uuid4().hex[:6]}",
        slug=f"{role.value}-{uuid4().hex[:8]}",
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="agent",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(agent)
    await db.flush()
    return agent


async def _make_task(db: AsyncSession, *, created_by: UUID) -> TaskTable:
    """Real Project + Task rows — notifications.related_task_id FKs to tasks."""
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=created_by,
    )
    db.add(project)
    await db.flush()
    task = TaskTable(
        id=uuid4(),
        title="Blocked task",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.BLOCKED,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=created_by,
        team=Team.BACKEND,
    )
    db.add(task)
    await db.flush()
    return task


async def _count_matching(
    db: AsyncSession,
    *,
    from_agent: UUID,
    notification_type: NotificationType,
    related_task_id: UUID,
) -> int:
    result = await db.execute(
        select(NotificationTable).where(
            NotificationTable.from_agent == from_agent,
            NotificationTable.type == notification_type,
            NotificationTable.related_task_id == related_task_id,
        )
    )
    return len(result.scalars().all())


@pytest.mark.asyncio
async def test_persist_and_deliver_suppresses_db_duplicate_past_redis_window(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retried blocker escalation past the 60s Redis window must not
    create a second unacked row for the same (sender, type, task,
    recipients) — the DB purpose-dedup gap `_persist_and_deliver` used to
    skip entirely."""
    sender = await _make_agent(db_session, role=AgentRole.DEVELOPER)
    pm = await _make_agent(db_session, role=AgentRole.CELL_PM)
    task = await _make_task(db_session, created_by=cast("UUID", sender.id))
    task_id = cast("UUID", task.id)

    # Seed the prior, still-unacked notification for this exact purpose.
    prior = NotificationTable(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=cast("UUID", sender.id),
        to_agents=[cast("UUID", pm.id)],
        subject="ACTION REQUIRED: Blocked - task",
        body="first send",
        related_task_id=task_id,
        requires_ack=True,
        read_by=[],
        acked_by=[],
    )
    db_session.add(prior)
    await db_session.flush()

    # Bypass the 60s Redis re-fire guard so the DB check is the only guard
    # left standing — matches tests/unit/test_notification_delivery_refire.py.
    monkeypatch.setattr(
        "roboco.services.notification_delivery.all_recipients_recently_notified",
        AsyncMock(return_value=False),
    )

    service = get_notification_delivery_service(db_session)
    deliver_mock = AsyncMock()
    monkeypatch.setattr(service, "deliver", deliver_mock)

    retry = NotificationTable(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=cast("UUID", sender.id),
        to_agents=[cast("UUID", pm.id)],
        subject="ACTION REQUIRED: Blocked - task (retry)",
        body="retried send",
        related_task_id=task_id,
        requires_ack=True,
        read_by=[],
        acked_by=[],
    )

    persisted = await service._persist_and_deliver(retry)

    assert persisted is False
    deliver_mock.assert_not_awaited()
    count = await _count_matching(
        db_session,
        from_agent=cast("UUID", sender.id),
        notification_type=NotificationType.BLOCKER_ESCALATION,
        related_task_id=task_id,
    )
    assert count == 1  # only the seeded prior — the retry was suppressed


@pytest.mark.asyncio
async def test_persist_and_deliver_allows_distinct_recipient_set(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A strictly-different recipient set is NOT a duplicate — overlap alone
    must not suppress (only exact-set equality does)."""
    sender = await _make_agent(db_session, role=AgentRole.DEVELOPER)
    pm = await _make_agent(db_session, role=AgentRole.CELL_PM)
    main_pm = await _make_agent(db_session, role=AgentRole.MAIN_PM)
    task = await _make_task(db_session, created_by=cast("UUID", sender.id))
    task_id = cast("UUID", task.id)

    prior = NotificationTable(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=cast("UUID", sender.id),
        to_agents=[cast("UUID", pm.id)],
        subject="ACTION REQUIRED: Blocked - task",
        body="first send",
        related_task_id=task_id,
        requires_ack=True,
        read_by=[],
        acked_by=[],
    )
    db_session.add(prior)
    await db_session.flush()

    monkeypatch.setattr(
        "roboco.services.notification_delivery.all_recipients_recently_notified",
        AsyncMock(return_value=False),
    )

    service = get_notification_delivery_service(db_session)
    monkeypatch.setattr(service, "deliver", AsyncMock())

    escalated = NotificationTable(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=cast("UUID", sender.id),
        to_agents=[cast("UUID", pm.id), cast("UUID", main_pm.id)],
        subject="ACTION REQUIRED: Blocked - task (escalated)",
        body="re-escalated to main-pm too",
        related_task_id=task_id,
        requires_ack=True,
        read_by=[],
        acked_by=[],
    )

    persisted = await service._persist_and_deliver(escalated)

    assert persisted is True
    count = await _count_matching(
        db_session,
        from_agent=cast("UUID", sender.id),
        notification_type=NotificationType.BLOCKER_ESCALATION,
        related_task_id=task_id,
    )
    expected_rows = 2  # the prior + the distinct-recipient-set escalation
    assert count == expected_rows
