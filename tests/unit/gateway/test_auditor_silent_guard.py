"""dm() sender-side no-comms guard: prompter/secretary refused; auditor and
pr_reviewer now pass through (they carry dm/read_a2a so the CEO can DM a
mid-flight one and it can reply in-thread).

Spec §5.5 originally made the auditor's silence absolute (no dm surface at
all). It's now scoped: the auditor still never INITIATES peer A2A — that's
enforced in agents_config.can_a2a_direct, not by ContentActions.dm's role
gate — but the gate itself (``_NO_COMMS_ROLES``, derived from
foundation.policy.communications.NO_COMMS_ROLES) no longer blocks it or
pr_reviewer. These tests pin that the handler-level guard (defense-in-depth
for any call that bypassed the manifest) matches the current NO_COMMS_ROLES
set exactly.
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
async def test_developer_dm_passes_auditor_guard() -> None:
    """dm() for a non-auditor role is not blocked by the no-comms guard."""
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
# The no-comms invariant now covers only prompter/secretary (CLAUDE.md):
# they're restricted to note + evidence — human-only, own dedicated chat
# pages. Auditor and pr_reviewer carry dm/read_a2a on their manifests (a CEO
# can DM either and they can reply in-thread) so they must NOT hit this
# guard — the auditor's silence toward PEERS is enforced separately, in
# agents_config.can_a2a_direct, not here.
# ---------------------------------------------------------------------------


_NO_COMMS_ROLES = ("prompter", "secretary")
_DM_CAPABLE_ROLES = ("auditor", "pr_reviewer")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", _NO_COMMS_ROLES)
async def test_no_comms_role_dm_returns_not_authorized(role: str) -> None:
    """prompter / secretary may not dm() — handler-level guard.

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


@pytest.mark.asyncio
@pytest.mark.parametrize("role", _DM_CAPABLE_ROLES)
async def test_auditor_and_pr_reviewer_dm_pass_no_comms_guard(role: str) -> None:
    """auditor / pr_reviewer are no longer refused by the no-comms guard.

    Mirrors test_developer_dm_passes_auditor_guard: whatever else dm() does
    downstream (ownership checks on the fake task_id), it must not be the
    no-comms ("silent") rejection — that guard no longer names these roles.
    """
    deps = _make_deps(role)
    actions = ContentActions(deps)

    env = await actions.dm(
        agent_id=uuid4(),
        recipient=str(uuid4()),
        text="hi",
        task_id=uuid4(),
    )
    body = env.as_dict()

    if body.get("error") == "not_authorized":
        haystack = (body.get("message") or "") + " " + (body.get("remediate") or "")
        assert "silent" not in haystack.lower()
