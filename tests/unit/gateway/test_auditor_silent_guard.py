"""Auditor is silent — runtime guard refuses dm().

Spec §5.5: the auditor is a silent observer. The spawn manifest already
omits `dm` from the auditor's tool surface, but that is a convention-only
defense. These tests pin a defense-in-depth runtime guard inside
ContentActions.dm: if the caller's role is "auditor", the verb refuses with
Envelope.not_authorized regardless of how the call arrived.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import (
    ContentActions,
    ContentActionsDeps,
)


def _make_deps(agent_role: str, **overrides: AsyncMock) -> ContentActionsDeps:
    """Build a ContentActionsDeps whose task.agent_for returns the given role."""
    if "task" in overrides:
        task = overrides["task"]
    else:
        task = AsyncMock()
        task.get_active_task_for_agent.return_value = None
        task.agent_for.return_value = MagicMock(role=agent_role)

    git = overrides.get("git", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    workspace = overrides.get("workspace", AsyncMock())
    notifications = overrides.get("notifications", AsyncMock())
    return ContentActionsDeps(
        task=task,
        git=git,
        a2a=a2a,
        journal=journal,
        workspace=workspace,
        notifications=notifications,
    )


@pytest.mark.asyncio
async def test_auditor_dm_returns_not_authorized() -> None:
    """Auditor role calling dm() is refused regardless of manifest."""
    auditor_id = uuid4()
    deps = _make_deps("auditor")
    actions = ContentActions(deps)

    env = await actions.dm(
        agent_id=auditor_id,
        recipient=str(uuid4()),
        text="hi",
        task_id=uuid4(),
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    haystack = (body.get("message") or "") + " " + (body.get("remediate") or "")
    assert "silent" in haystack.lower() or "auditor" in haystack.lower()
    deps.a2a.send.assert_not_called()


@pytest.mark.asyncio
async def test_developer_dm_passes_auditor_guard() -> None:
    """dm() for a non-auditor role is not blocked by the new guard."""
    dev_id = uuid4()
    deps = _make_deps("developer")
    actions = ContentActions(deps)

    env = await actions.dm(
        agent_id=dev_id,
        recipient=str(uuid4()),
        text="hi",
        task_id=uuid4(),
    )
    body = env.as_dict()

    if body.get("error") == "not_authorized":
        haystack = (body.get("message") or "") + " " + (body.get("remediate") or "")
        assert "silent" not in haystack.lower()


# ---------------------------------------------------------------------------
# The same no-comms invariant covers pr_reviewer / prompter / secretary
# (CLAUDE.md): pr_reviewer "posts its change-request on the PR itself — no
# say/dm"; prompter + secretary are "restricted to note + evidence — no
# say/dm/notify". The auditor guard's own comment claims defence-in-depth for
# "any call that bypassed the manifest" — that rationale must hold for these
# three roles too, or the claimed defence-in-depth is only 1 of 4 silent roles.
# ---------------------------------------------------------------------------


_NO_COMMS_ROLES = ("pr_reviewer", "prompter", "secretary")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", _NO_COMMS_ROLES)
async def test_no_comms_role_dm_returns_not_authorized(role: str) -> None:
    """pr_reviewer / prompter / secretary may not dm() — handler-level guard.

    Asserts the no-comms signal ("silent") in the message so the test fails for
    the right reason on RED: without the role guard, dm() with an unowned
    task_id still returns not_authorized from the ownership check, but that
    reject message does NOT carry the silent-role signal. The role guard firing
    FIRST (before the ownership check) is what makes "silent" appear."""
    deps = _make_deps(role)
    actions = ContentActions(deps)

    env = await actions.dm(
        agent_id=uuid4(),
        recipient=str(uuid4()),
        text="hi",
        task_id=uuid4(),
    )
    body = env.as_dict()

    assert body["error"] == "not_authorized"
    haystack = (body.get("message") or "") + " " + (body.get("remediate") or "")
    assert "silent" in haystack.lower()
    deps.a2a.send.assert_not_called()
