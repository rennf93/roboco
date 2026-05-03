"""Every gateway commit message gets [task-id-short] prefix.

ContentActions.commit promises in the dev prompt that `[task-id]` is
auto-prefixed onto every commit. The gateway strips any user-supplied
prefix via `_TASK_ID_PREFIX_RE` but, before this guard, never re-added
the canonical one. These tests pin the contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _make_deps(**overrides: AsyncMock) -> ContentActionsDeps:
    """Build a ContentActionsDeps for tests; mirrors test_content_actions."""
    if "task" in overrides:
        task = overrides["task"]
    else:
        task = AsyncMock()
        task.get_active_task_for_agent.return_value = None

    if "git" in overrides:
        git = overrides["git"]
    else:
        git = AsyncMock()
        git.commit.return_value = {"sha": "abc12345"}
        git.diff.return_value = ""

    messaging = overrides.get("messaging", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    workspace = overrides.get("workspace", AsyncMock())
    return ContentActionsDeps(
        task=task,
        git=git,
        messaging=messaging,
        a2a=a2a,
        journal=journal,
        workspace=workspace,
    )


@pytest.mark.asyncio
async def test_commit_prefixes_with_task_id_short() -> None:
    """Plain message gets `[task-id-short] ` prepended."""
    aid = uuid4()
    tid = uuid4()
    expected_prefix = f"[{str(tid)[:8]}]"

    t = MagicMock(
        id=tid,
        assigned_to=aid,
        plan="x",
        status="in_progress",
        branch_name="feature/backend/abcd1234",
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = t
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "deadbeef"}

    deps = _make_deps(task=task_svc, git=git_svc)
    actions = ContentActions(deps)

    await actions.commit(
        agent_id=aid,
        message="feat(api): add login endpoint for session bootstrap",
    )

    git_svc.commit.assert_awaited()
    # git.commit takes keyword-only args (branch_name, message, task_id, files)
    msg = git_svc.commit.await_args.kwargs["message"]
    assert msg.startswith(expected_prefix), (
        f"expected {expected_prefix} prefix; got {msg!r}"
    )


@pytest.mark.asyncio
async def test_commit_strips_then_re_adds_prefix() -> None:
    """If the agent supplied a wrong prefix, strip it and add the canonical one."""
    aid = uuid4()
    tid = uuid4()
    expected_prefix = f"[{str(tid)[:8]}]"

    t = MagicMock(
        id=tid,
        assigned_to=aid,
        plan="x",
        status="in_progress",
        branch_name="feature/backend/abcd1234",
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = t
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "deadbeef"}

    deps = _make_deps(task=task_svc, git=git_svc)
    actions = ContentActions(deps)

    await actions.commit(
        agent_id=aid,
        message="[wrong-id] feat(api): add login endpoint for session bootstrap",
    )

    msg = git_svc.commit.await_args.kwargs["message"]
    assert msg.startswith(expected_prefix)
    assert "[wrong-id]" not in msg


@pytest.mark.asyncio
async def test_commit_prefix_collapses_multiple_spaces() -> None:
    """`[old]   foo` should become `[new] foo`, not `[new]   foo`."""
    aid = uuid4()
    tid = uuid4()
    expected_prefix = f"[{str(tid)[:8]}]"

    t = MagicMock(
        id=tid,
        assigned_to=aid,
        plan="x",
        status="in_progress",
        branch_name="feature/backend/abcd1234",
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = t
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "deadbeef"}

    deps = _make_deps(task=task_svc, git=git_svc)
    actions = ContentActions(deps)

    await actions.commit(
        agent_id=aid,
        message="[old]   feat(api): add login endpoint for session bootstrap",
    )

    msg = git_svc.commit.await_args.kwargs["message"]
    # Exactly one space after the prefix bracket
    assert msg.startswith(f"{expected_prefix} feat(api):"), (
        f"expected single-space prefix; got {msg!r}"
    )
