"""Tests for ContentActions.notify — formal ack-required notifications.

Pre-gateway, PMs and Board could issue formal notifications requiring
acknowledgment via NotificationService. Gateway only had say/dm
(informal). This verb fills the gap by composing NotificationService
into the standard envelope path, role-gated to PMs and Board only
(content tools share one router, so the role check lives in the verb).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _make_deps(**overrides: AsyncMock) -> ContentActionsDeps:
    if "task" in overrides:
        task = overrides["task"]
    else:
        task = AsyncMock()
        task.get_active_task_for_agent.return_value = None
        task.get_journal_context_task_for_agent.return_value = None
        task.agent_for.return_value = MagicMock(role="cell_pm")

    git = overrides.get("git", AsyncMock())
    messaging = overrides.get("messaging", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    workspace = overrides.get("workspace", AsyncMock())
    notifications = overrides.get("notifications", AsyncMock())
    return ContentActionsDeps(
        task=task,
        git=git,
        messaging=messaging,
        a2a=a2a,
        journal=journal,
        workspace=workspace,
        notifications=notifications,
    )


@pytest.mark.asyncio
async def test_notify_pm_creates_ack_required_notification() -> None:
    """Cell PM calls notify(); NotificationService.send_ack_notification fired."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="cell_pm")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="be-dev-1",
        text="Please review the new acceptance criteria before resuming.",
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["status"] == "sent"
    notif_svc.send_ack_notification.assert_awaited_once()
    call_kwargs = notif_svc.send_ack_notification.call_args.kwargs
    assert call_kwargs["from_agent"] == agent_id
    assert call_kwargs["to_agent"] == "be-dev-1"
    assert "acceptance criteria" in call_kwargs["body"]


@pytest.mark.asyncio
async def test_notify_main_pm_succeeds() -> None:
    """Main PM is also allowed."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="main_pm")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="fe-pm",
        text="Please align frontend cell with new release timeline.",
    )
    body = env.as_dict()

    assert body["error"] is None
    notif_svc.send_ack_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_board_product_owner_succeeds() -> None:
    """Product Owner (Board) is allowed."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="main-pm",
        text="Roadmap priorities updated; please reflect in Q2 plan.",
    )
    body = env.as_dict()

    assert body["error"] is None
    notif_svc.send_ack_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_board_head_marketing_succeeds() -> None:
    """Head of Marketing (Board) is allowed."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="head_marketing")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="main-pm",
        text="Marketing launch dates confirmed; coordinate engineering deliverables.",
    )
    body = env.as_dict()

    assert body["error"] is None
    notif_svc.send_ack_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_developer_rejected_with_not_authorized() -> None:
    """Developer cannot send formal notifications; envelope is not_authorized."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="developer")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="be-pm",
        text="Heads up — I think the staging deploy is broken.",
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    assert "developer" in body["message"]
    notif_svc.send_ack_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_qa_rejected_with_not_authorized() -> None:
    """QA cannot send formal notifications."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="qa")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="be-pm",
        text="QA cannot proceed without environment access.",
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    notif_svc.send_ack_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_documenter_rejected_with_not_authorized() -> None:
    """Documenter cannot send formal notifications."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="documenter")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="be-pm",
        text="Documentation review requested.",
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    notif_svc.send_ack_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_auditor_rejected_with_not_authorized() -> None:
    """Auditor is read-only — cannot communicate outwardly via notifications."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="auditor")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="ceo",
        text="Quality concern detected.",
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    notif_svc.send_ack_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_auto_fills_task_id_from_active_task() -> None:
    """When the PM has an active task, notify auto-attaches it."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="awaiting_pm_review")
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    task_svc.get_journal_context_task_for_agent.return_value = task_obj
    task_svc.agent_for.return_value = MagicMock(role="cell_pm")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="be-dev-1",
        text="Heads up: this task has been escalated for CEO approval.",
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    call_kwargs = notif_svc.send_ack_notification.call_args.kwargs
    assert call_kwargs["task_id"] == task_id


@pytest.mark.asyncio
async def test_notify_unknown_role_rejected() -> None:
    """If task.agent_for returns None, treat as unknown role and reject."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = None
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="be-dev-1",
        text="Test message.",
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    notif_svc.send_ack_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_priority_high_passed_through() -> None:
    """Optional priority='high' is forwarded to NotificationService."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.agent_for.return_value = MagicMock(role="cell_pm")
    notif_svc = AsyncMock()

    deps = _make_deps(task=task_svc, notifications=notif_svc)
    ca = ContentActions(deps)

    env = await ca.notify(
        agent_id=agent_id,
        target="be-dev-1",
        text="Critical: production deployment failed; please join war room.",
        priority="high",
    )
    body = env.as_dict()

    assert body["error"] is None
    call_kwargs = notif_svc.send_ack_notification.call_args.kwargs
    assert call_kwargs["priority"] == "high"


# ---------------------------------------------------------------------------
# A dependency block is never a CEO signal — notify(target="ceo") is refused
# while the related task is waiting on an unfinished upstream.
# ---------------------------------------------------------------------------


def _ca_for_notify(
    role: str, task: object, unmet: list[object]
) -> tuple[ContentActions, AsyncMock]:
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role=role)
    task_svc.get.return_value = task
    task_svc.unmet_dependency_ids.return_value = unmet
    notif_svc = AsyncMock()
    ca = ContentActions(_make_deps(task=task_svc, notifications=notif_svc))
    # Ownership is exercised elsewhere; isolate the dependency-block gate.
    object.__setattr__(
        ca, "_verify_explicit_task_ownership", AsyncMock(return_value=None)
    )
    return ca, notif_svc


@pytest.mark.asyncio
async def test_notify_ceo_about_dependency_block_refused() -> None:
    """PO cannot page the CEO about a task that is just waiting on an upstream."""
    dep_id = uuid4()
    task = MagicMock(id=uuid4(), dependency_ids=[dep_id])
    ca, notif_svc = _ca_for_notify("product_owner", task, unmet=[dep_id])
    env = await ca.notify(
        agent_id=uuid4(),
        target="ceo",
        text="URGENT: relax the backend dependency so the cell can resume.",
        priority="urgent",
        task_id=task.id,
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "dependency block" in body["message"]
    notif_svc.send_ack_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_ceo_about_unblocked_task_allowed() -> None:
    """A CEO notification about a task with no open dependency still goes through."""
    task = MagicMock(id=uuid4(), dependency_ids=[])
    ca, notif_svc = _ca_for_notify("product_owner", task, unmet=[])
    env = await ca.notify(
        agent_id=uuid4(),
        target="ceo",
        text="Product review complete — ready for your go/no-go.",
        task_id=task.id,
    )
    assert env.as_dict()["error"] is None
    notif_svc.send_ack_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_noncel_target_about_blocked_task_allowed() -> None:
    """The gate is CEO-scoped: notifying another PM about a block is unaffected."""
    dep_id = uuid4()
    task = MagicMock(id=uuid4(), dependency_ids=[dep_id])
    ca, notif_svc = _ca_for_notify("main_pm", task, unmet=[dep_id])
    env = await ca.notify(
        agent_id=uuid4(),
        target="be-pm",
        text="Heads up: this task is waiting on the UX design.",
        task_id=task.id,
    )
    assert env.as_dict()["error"] is None
    notif_svc.send_ack_notification.assert_awaited_once()
