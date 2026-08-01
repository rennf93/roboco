"""_re_escalate_recipient must not be suppressed by its own prior attempt.

PR #742's DB purpose-dedup (`duplicate_unacked_notification_exists`) closed
the retried-first-send-blocker gap, but applied unconditionally inside
`_persist_and_deliver` — so a SECOND re-escalation attempt at the same
recipient (identical sender/type/task/recipient-set, and the prior attempt
is unacked BY DEFINITION) was silently suppressed right after
`_claim_reescalation_slot` had already burned the attempt slot, permanently
stalling the re-escalation ladder.

`_re_escalate_recipient` now passes `bypass_purpose_dedup=True` into
`_persist_and_deliver`, so this dedup check never applies to this call path.
This test drives `_re_escalate_recipient` twice against a real db_session,
seeding nothing but real agent rows, and asserts both attempts persist and
deliver a row rather than the second being silently dropped.
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


async def _make_agent(db: AsyncSession, *, slug: str, role: AgentRole) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=None,
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
    db: AsyncSession, *, from_agent: UUID, related_task_id: UUID
) -> int:
    result = await db.execute(
        select(NotificationTable).where(
            NotificationTable.from_agent == from_agent,
            NotificationTable.type == NotificationType.BLOCKER_ESCALATION,
            NotificationTable.related_task_id == related_task_id,
        )
    )
    return len(result.scalars().all())


@pytest.mark.asyncio
async def test_two_sequential_due_reescalations_both_deliver(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sequential re-escalation attempts at the same recipient (the shape
    a real ladder produces on successive DUE sweep ticks) must both persist +
    deliver — not just the first."""
    # Random (not canonical be-pm/main-pm) slugs: this DB already carries the
    # real seeded fleet agents, so a literal canonical slug would collide on
    # the unique index. get_escalation_target is monkeypatched below since it
    # keys off the real ESCALATION_CHAIN's literal canonical slugs.
    sender = await _make_agent(
        db_session, slug=f"dev-{uuid4().hex[:8]}", role=AgentRole.DEVELOPER
    )
    recipient = await _make_agent(
        db_session, slug=f"pm-{uuid4().hex[:8]}", role=AgentRole.CELL_PM
    )
    target = await _make_agent(
        db_session, slug=f"main-{uuid4().hex[:8]}", role=AgentRole.MAIN_PM
    )
    task = await _make_task(db_session, created_by=cast("UUID", sender.id))
    task_id = cast("UUID", task.id)

    original = NotificationTable(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=cast("UUID", sender.id),
        to_agents=[cast("UUID", recipient.id)],
        subject="ACTION REQUIRED: Blocked - task",
        body="first send",
        related_task_id=task_id,
        requires_ack=True,
        read_by=[],
        acked_by=[],
    )
    db_session.add(original)
    await db_session.flush()

    monkeypatch.setattr(
        "roboco.services.notification_delivery.all_recipients_recently_notified",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_escalation_target",
        lambda slug: target.slug if slug == recipient.slug else None,
    )

    service = get_notification_delivery_service(db_session)
    monkeypatch.setattr(service, "deliver", AsyncMock())

    first_ok = await service._re_escalate_recipient(
        original, cast("UUID", recipient.id)
    )
    second_ok = await service._re_escalate_recipient(
        original, cast("UUID", recipient.id)
    )

    assert first_ok is True
    assert second_ok is True, (
        "second DUE re-escalation must still deliver — it must not be "
        "suppressed by the first, unacked, re-escalation row"
    )
    count = await _count_matching(
        db_session, from_agent=cast("UUID", sender.id), related_task_id=task_id
    )
    expected_total_rows = 3  # the seeded original + both re-escalation attempts
    assert count == expected_total_rows
    # Both re-escalation rows must be addressed to target.id (the escalation
    # target), proving the ladder re-fired to the right recipient — not a
    # tautology on target.id which was uuid4() at construction.
    all_rows = (
        (
            await db_session.execute(
                select(NotificationTable).where(
                    NotificationTable.from_agent == cast("UUID", sender.id),
                    NotificationTable.type == NotificationType.BLOCKER_ESCALATION,
                    NotificationTable.related_task_id == task_id,
                )
            )
        )
        .scalars()
        .all()
    )
    re_escalation_rows = [
        r for r in all_rows if cast("UUID", target.id) in (r.to_agents or [])
    ]
    expected_re_escalation_rows = 2  # one per _re_escalate_recipient call
    assert len(re_escalation_rows) == expected_re_escalation_rows, (
        f"expected {expected_re_escalation_rows} re-escalation rows addressed "
        f"to target ({target.slug}), got {len(re_escalation_rows)}"
    )
