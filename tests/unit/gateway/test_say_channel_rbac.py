"""ContentActions.say must enforce channel write-access via send_message.

The gateway path (post_to_channel -> send_message) historically called
send_message WITHOUT agent_slug, which bypassed validate_channel_access.
Pre-gateway returned a friendly ChannelAccessDeniedError listing the
agent's writable channels. These tests pin the restored behaviour:

1. say() converts ChannelAccessDeniedError into a not_authorized Envelope
   that names the offending channel and includes a remediation hint
   listing the writable channels for the agent's role.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.enforcement.channel_access import ChannelAccessDeniedError
from roboco.services.gateway.content_actions import (
    ContentActions,
    ContentActionsDeps,
)


def _make_deps(**overrides: AsyncMock) -> ContentActionsDeps:
    """Mirrors test_content_actions._make_deps."""
    if "task" in overrides:
        task = overrides["task"]
    else:
        task = AsyncMock()
        task.get_active_task_for_agent.return_value = None

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
async def test_say_returns_not_authorized_envelope_on_access_denied() -> None:
    """When messaging.post_to_channel raises ChannelAccessDeniedError, say()
    converts it into a not_authorized Envelope with a writable-channels hint.
    """
    aid = uuid4()
    msg_svc = AsyncMock()
    msg_svc.post_to_channel.side_effect = ChannelAccessDeniedError(
        agent_id="be-dev-1",
        channel_slug="announcements",
        action="write",
    )
    deps = _make_deps(messaging=msg_svc)
    actions = ContentActions(deps)

    env = await actions.say(agent_id=aid, channel="announcements", text="hi")
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    assert "announcements" in body["message"]
    # Remediation should hint at the channels the agent may write to (or
    # that they have none). The list is resolved from CHANNEL_ACCESS via
    # get_agent_channels using the agent's slug — note the wording is
    # slug-keyed ("channels you may write to") rather than role-keyed,
    # since CHANNEL_ACCESS is keyed by slug not role.
    assert body["remediate"] is not None
    assert "channels you may write to" in body["remediate"].lower()


@pytest.mark.asyncio
async def test_say_success_path_returns_posted_envelope() -> None:
    """Sanity: when messaging accepts the post, say() returns the
    standard posted/continue Envelope (no regression in the happy path).
    """
    aid = uuid4()
    msg_svc = AsyncMock()
    deps = _make_deps(messaging=msg_svc)
    actions = ContentActions(deps)

    env = await actions.say(agent_id=aid, channel="dev-all", text="hello")
    body = env.as_dict()

    assert body["error"] is None
    assert body["status"] == "posted"
    assert body["next"] == "continue"
    msg_svc.post_to_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_say_returns_not_authorized_when_agent_lookup_fails() -> None:
    """If get_agent_slug returns None (deleted agent), say must fail closed.

    Pins the I1 fix: post_to_channel raises ChannelAccessDeniedError directly
    when the slug lookup fails, so send_message's `if agent_slug:` can no
    longer skip validate_channel_access for unknown/removed agents. The
    Envelope conversion in say() carries that through to the agent.
    """
    aid = uuid4()
    msg_svc = AsyncMock()
    msg_svc.post_to_channel.side_effect = ChannelAccessDeniedError(
        agent_id=str(aid),
        channel_slug="dev-all",
        action="write",
        message="agent not found",
    )
    deps = _make_deps(messaging=msg_svc)
    actions = ContentActions(deps)

    env = await actions.say(agent_id=aid, channel="dev-all", text="hi")
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    assert "dev-all" in body["message"]
