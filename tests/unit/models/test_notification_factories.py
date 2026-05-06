"""Notification model factory function coverage."""

from __future__ import annotations

from uuid import uuid4

from roboco.models import NotificationPriority, NotificationType
from roboco.models.notification import (
    create_alert,
    create_blocker_escalation,
    create_broadcast,
    create_documentation_request,
    create_priority_change,
    create_review_request,
    create_task_assignment,
)


def test_create_task_assignment() -> None:
    pm_id = uuid4()
    agent_id = uuid4()
    task_id = uuid4()
    n = create_task_assignment(
        from_pm=pm_id,
        to_agent=agent_id,
        task_id=task_id,
        task_title="Build feature X",
    )
    assert n.type == NotificationType.TASK_ASSIGNMENT
    assert n.from_agent == pm_id
    assert n.to_agents == [agent_id]
    assert n.related_task_id == task_id
    assert "Build feature X" in n.subject


def test_create_blocker_escalation() -> None:
    from_pm = uuid4()
    to_pm = uuid4()
    task_id = uuid4()
    n = create_blocker_escalation(
        from_pm=from_pm,
        to_pm=to_pm,
        task_id=task_id,
        blocker_description="DB unreachable",
    )
    assert n.type == NotificationType.BLOCKER_ESCALATION
    assert n.priority == NotificationPriority.HIGH
    assert n.body == "DB unreachable"
    assert n.related_task_id == task_id


def test_create_review_request() -> None:
    from_pm = uuid4()
    to_qa = uuid4()
    task_id = uuid4()
    n = create_review_request(
        from_pm=from_pm,
        to_qa=to_qa,
        task_id=task_id,
        task_title="Refactor login",
    )
    assert n.type == NotificationType.REVIEW_REQUEST
    assert "Refactor login" in n.subject
    assert n.to_agents == [to_qa]


def test_create_documentation_request() -> None:
    from_pm = uuid4()
    to_doc = uuid4()
    task_id = uuid4()
    n = create_documentation_request(
        from_pm=from_pm,
        to_documenter=to_doc,
        task_id=task_id,
        task_title="API endpoint",
    )
    assert n.type == NotificationType.DOCUMENTATION_REQUEST
    assert "API endpoint" in n.subject
    assert "needs documentation" in n.body


def test_create_priority_change_p0_marks_urgent() -> None:
    sender = uuid4()
    recipients = [uuid4()]
    task_id = uuid4()
    n = create_priority_change(
        from_agent=sender,
        to_agents=recipients,
        task_id=task_id,
        task_title="Critical task",
        new_priority=0,
    )
    assert n.type == NotificationType.PRIORITY_CHANGE
    assert n.priority == NotificationPriority.URGENT
    assert "P0" in n.body


def test_create_priority_change_high_label() -> None:
    sender = uuid4()
    recipients = [uuid4()]
    task_id = uuid4()
    n = create_priority_change(
        from_agent=sender,
        to_agents=recipients,
        task_id=task_id,
        task_title="P1 task",
        new_priority=1,
    )
    assert n.priority == NotificationPriority.HIGH
    assert "P1" in n.body


def test_create_priority_change_unknown_priority_uses_fallback_label() -> None:
    sender = uuid4()
    recipients = [uuid4()]
    task_id = uuid4()
    n = create_priority_change(
        from_agent=sender,
        to_agents=recipients,
        task_id=task_id,
        task_title="Custom",
        new_priority=99,
    )
    # Body falls through to f"P{new_priority}".
    assert "P99" in n.body


def test_create_alert() -> None:
    sender = uuid4()
    recipients = [uuid4(), uuid4()]
    n = create_alert(
        from_agent=sender,
        to_agents=recipients,
        subject="System down",
        body="rebooting now",
    )
    assert n.type == NotificationType.ALERT
    assert n.priority == NotificationPriority.URGENT
    assert n.subject == "System down"


def test_create_broadcast_does_not_require_ack() -> None:
    sender = uuid4()
    recipients = [uuid4()]
    n = create_broadcast(
        from_agent=sender,
        to_agents=recipients,
        subject="All hands",
        body="please review",
    )
    assert n.type == NotificationType.BROADCAST
    assert n.requires_ack is False
