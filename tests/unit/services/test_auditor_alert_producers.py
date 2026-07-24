"""Unit tests for auditor-targeted rework alert producers.

Covers the reactive audit dispatch path: ``NotificationDeliveryService``
creates ``ALERT`` notifications addressed to the auditor, and ``TaskService``
invokes that producer at the QA-fail / rework chokepoints
(``fail_qa``, ``pr_fail``, ``request_changes``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.models.base import (
    NotificationPriority,
    NotificationType,
    TaskStatus,
)
from roboco.services.notification_delivery import NotificationDeliveryService
from roboco.services.task import TaskService
from roboco.utils.converters import require_uuid


def _mock_task(
    task_id: UUID | None = None, status: Any = TaskStatus.AWAITING_QA
) -> MagicMock:
    task = MagicMock()
    task.id = task_id or uuid4()
    task.title = "Test task"
    task.status = status
    task.assigned_to = uuid4()
    task.claimed_by = None
    task.active_claimant_id = None
    task.team = MagicMock()
    task.team.value = "backend"
    task.qa_verified = True
    task.qa_notes = None
    task.dev_notes = None
    task.orchestration_markers = {}
    task.notes_structured = None
    task.revision_count = 0
    return task


def _mock_agent(*, role: str = "auditor", slug: str = "auditor") -> MagicMock:
    agent = MagicMock()
    agent.id = uuid4()
    agent.role = role
    agent.slug = slug
    return agent


def _session_with_agent(agent: MagicMock | None) -> MagicMock:
    """A session whose execute().scalars().first() returns ``agent``.

    ``session.add`` assigns a fresh UUID to the notification so that
    ``_persist_and_deliver`` can call ``require_uuid(notification.id)``.
    """
    session = MagicMock()

    def _assign_id(obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()

    session.add = MagicMock(side_effect=_assign_id)
    session.flush = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = agent
    result.scalar_one_or_none.return_value = agent
    session.execute = AsyncMock(return_value=result)
    return session


# ---------------------------------------------------------------------------
# NotificationDeliveryService.notify_auditor_of_rework
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_auditor_of_rework_creates_alert_to_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delivery service builds an ALERT notification targeted at the auditor."""
    auditor = _mock_agent(role="auditor", slug="auditor")
    actor = _mock_agent(role="qa", slug="be-qa")
    session = _session_with_agent(auditor)
    svc = NotificationDeliveryService(session)
    monkeypatch.setattr(svc, "_get_auditor_agent", AsyncMock(return_value=auditor))
    monkeypatch.setattr(svc, "_get_agent_by_id", AsyncMock(return_value=actor))
    monkeypatch.setattr(svc, "deliver", AsyncMock(return_value=True))

    task = _mock_task()
    with patch(
        "roboco.services.notification_delivery.all_recipients_recently_notified",
        AsyncMock(return_value=False),
    ):
        await svc.notify_auditor_of_rework(
            task=task,
            task_id=require_uuid(task.id),
            reason="QA review failed",
            actor_agent_id=actor.id,
            actor_role="qa",
        )

    added = [c for c in session.add.call_args_list if c.args]
    assert len(added) == 1
    notification = added[0].args[0]
    assert notification.type == NotificationType.ALERT
    assert notification.priority == NotificationPriority.HIGH
    assert notification.to_agents == [auditor.id]
    assert notification.from_agent == actor.id
    assert "QA review failed" in notification.body
    assert "Actor role: qa" in notification.body
    assert notification.requires_ack is True


@pytest.mark.asyncio
async def test_notify_auditor_of_rework_skips_when_no_auditor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the auditor agent is absent, the producer is a silent no-op."""
    session = _session_with_agent(None)
    svc = NotificationDeliveryService(session)
    monkeypatch.setattr(svc, "_get_auditor_agent", AsyncMock(return_value=None))
    deliver_spy = AsyncMock()
    monkeypatch.setattr(svc, "deliver", deliver_spy)

    task = _mock_task()
    await svc.notify_auditor_of_rework(
        task=task,
        task_id=require_uuid(task.id),
        reason="QA review failed",
    )

    assert not session.add.called
    assert not deliver_spy.called


# ---------------------------------------------------------------------------
# TaskService._alert_auditor_of_rework
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_auditor_of_rework_calls_delivery_service() -> None:
    """TaskService delegates to the notification delivery service."""
    session = MagicMock()
    session.flush = AsyncMock()
    task = _mock_task()
    svc = TaskService(session)

    fake_delivery = AsyncMock()
    fake_delivery.notify_auditor_of_rework = AsyncMock()

    with patch(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: fake_delivery,
    ):
        await svc._alert_auditor_of_rework(
            task,
            reason="QA review failed",
            actor_agent_id=task.assigned_to,
            actor_role="qa",
        )

    fake_delivery.notify_auditor_of_rework.assert_awaited_once()
    call = fake_delivery.notify_auditor_of_rework.await_args
    assert call.kwargs["task"] is task
    assert call.kwargs["reason"] == "QA review failed"
    assert call.kwargs["actor_role"] == "qa"
    assert call.kwargs["actor_agent_id"] == task.assigned_to


@pytest.mark.asyncio
async def test_alert_auditor_of_rework_is_best_effort() -> None:
    """A delivery failure must not raise out of the producer."""
    session = MagicMock()
    task = _mock_task()
    svc = TaskService(session)

    with patch(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        side_effect=RuntimeError("redis down"),
    ):
        await svc._alert_auditor_of_rework(
            task, reason="QA review failed", actor_role="qa"
        )


# ---------------------------------------------------------------------------
# Chokepoint wiring: fail_qa, pr_fail, request_changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_qa_emits_auditor_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fail_qa`` calls the auditor rework producer with QA attribution."""
    session = MagicMock()
    session.flush = AsyncMock()
    # fail_qa releases the QA agent's fleet marker via session.get — default
    # to "no matching row" for a test that doesn't care about that side effect.
    session.get = AsyncMock(return_value=None)
    task = _mock_task(status=TaskStatus.AWAITING_QA)
    task.orchestration_markers = {"original_developer": str(uuid4())}

    svc = TaskService(session)
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "_validate_and_set_status", MagicMock())
    alert_spy = AsyncMock()
    monkeypatch.setattr(svc, "_alert_auditor_of_rework", alert_spy)

    with (
        patch(
            "roboco.services.task.extract_original_developer",
            return_value=str(uuid4()),
        ),
        patch("roboco.services.task.asyncio.create_task", MagicMock()),
    ):
        out = await svc.fail_qa(task.id, notes="missing tests")

    assert out is task
    alert_spy.assert_awaited_once()
    call = alert_spy.await_args
    assert call is not None
    assert call.args[0] is task
    assert call.kwargs["reason"] == "missing tests"
    assert call.kwargs["actor_role"] == "qa"


@pytest.mark.asyncio
async def test_pr_fail_emits_auditor_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pr_fail`` calls the auditor rework producer with reviewer attribution."""
    session = MagicMock()
    session.flush = AsyncMock()
    # pr_fail releases the reviewer's fleet marker via session.get — default
    # to "no matching row" for a test that doesn't care about that side effect.
    session.get = AsyncMock(return_value=None)
    reviewer_id = uuid4()
    pm_id = uuid4()
    task = _mock_task(status=TaskStatus.AWAITING_PR_REVIEW)
    task.claimed_by = reviewer_id

    pm = MagicMock()
    pm.id = pm_id

    svc = TaskService(session)
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "_validate_and_set_status", MagicMock())
    monkeypatch.setattr(svc, "_revision_pm_for_task", AsyncMock(return_value=pm))
    alert_spy = AsyncMock()
    monkeypatch.setattr(svc, "_alert_auditor_of_rework", alert_spy)

    out = await svc.pr_fail(
        reviewer_id, task.id, notes="convention violation", issues=["mv model"]
    )

    assert out is task
    alert_spy.assert_awaited_once()
    call = alert_spy.await_args
    assert call is not None
    assert call.args[0] is task
    assert call.kwargs["reason"] == "convention violation"
    assert call.kwargs["actor_role"] == "pr_reviewer"
    assert call.kwargs["actor_agent_id"] == reviewer_id


@pytest.mark.asyncio
async def test_request_changes_emits_auditor_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``request_changes`` calls the auditor rework producer with PM attribution."""
    session = MagicMock()
    session.flush = AsyncMock()
    pm_id = uuid4()
    pm = MagicMock()
    pm.id = pm_id
    task = _mock_task(status=TaskStatus.AWAITING_PM_REVIEW)
    task.claimed_by = pm_id

    svc = TaskService(session)
    monkeypatch.setattr(svc, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(svc, "_validate_and_set_status", MagicMock())
    monkeypatch.setattr(svc, "_revision_pm_for_task", AsyncMock(return_value=pm))
    alert_spy = AsyncMock()
    monkeypatch.setattr(svc, "_alert_auditor_of_rework", alert_spy)

    with patch("roboco.services.task.extract_original_developer", return_value=None):
        out = await svc.request_changes(
            pm_id,
            task.id,
            notes="AC missing",
            issues=["add test"],
            agent_role="cell_pm",
        )

    assert out is task
    alert_spy.assert_awaited_once()
    call = alert_spy.await_args
    assert call is not None
    assert call.args[0] is task
    assert call.kwargs["reason"] == "AC missing"
    assert call.kwargs["actor_role"] == "cell_pm"
    assert call.kwargs["actor_agent_id"] == pm_id
