"""Wave B3 (2026-05-12): say() returns canonical 'posted' status.

Smoke run 3 showed main-pm got status='sent' while be-pm got status='posted'
for the same verb. This test pins the canonical past-tense pattern:
  note()   -> 'noted'
  say()    -> 'posted'   (this file)
  notify_ack() -> 'acked'

dm() and notify() return 'sent' — those are different verbs with their own
semantics and are intentionally not touched here.
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
        task.agent_for.return_value = MagicMock(role="developer", slug="be-dev-1")
        task.get_active_task_for_agent.return_value = None
        task.get_journal_context_task_for_agent.return_value = None

    git = overrides.get("git", AsyncMock())
    messaging = overrides.get("messaging", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    workspace = overrides.get("workspace", AsyncMock())
    notifications = overrides.get("notifications", AsyncMock())
    notification_delivery = overrides.get("notification_delivery", AsyncMock())
    return ContentActionsDeps(
        task=task,
        git=git,
        messaging=messaging,
        a2a=a2a,
        journal=journal,
        workspace=workspace,
        notifications=notifications,
        notification_delivery=notification_delivery,
    )


@pytest.mark.asyncio
async def test_say_returns_posted_status() -> None:
    """say() returns status='posted' (past-tense, aligned with 'noted'/'acked')."""
    ca = ContentActions(_make_deps())
    env = await ca.say(
        agent_id=uuid4(),
        channel="backend-cell",
        text="hello team",
    )
    body = env.as_dict()
    assert body["error"] is None, body
    assert body["status"] == "posted", body


@pytest.mark.asyncio
async def test_say_posted_status_with_active_task() -> None:
    """say() returns 'posted' even when an active task is auto-injected."""
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer", slug="be-dev-1")
    task_svc.get_active_task_for_agent.return_value = task_obj
    task_svc.get_journal_context_task_for_agent.return_value = task_obj

    ca = ContentActions(_make_deps(task=task_svc))
    env = await ca.say(
        agent_id=uuid4(),
        channel="backend-cell",
        text="progress update",
    )
    body = env.as_dict()
    assert body["error"] is None, body
    assert body["status"] == "posted", body
    assert body["task_id"] == str(task_id), body


@pytest.mark.asyncio
async def test_say_next_is_continue() -> None:
    """say() sets next='continue' so agents know to keep working."""
    ca = ContentActions(_make_deps())
    env = await ca.say(
        agent_id=uuid4(),
        channel="dev-all",
        text="syncing up",
    )
    body = env.as_dict()
    assert body["next"] == "continue", body
