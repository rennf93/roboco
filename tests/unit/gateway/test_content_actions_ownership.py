"""Gate Set D: content-tool ownership guards in ContentActions.

When a caller passes a ``task_id`` to commit / say / dm / note / evidence,
the gateway must verify ``task.assigned_to == caller_agent_id`` before
allowing the side effect.

Pre-gateway equivalent: agents could only act on tasks they owned because
the MCP handlers resolved task from session context. With the gateway
exposing the task_id parameter, the ownership check must be explicit.

Exception: ``say`` and ``dm`` without a task_id (channel-only / off-task
A2A) are exempt — used for channel announcements.
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

    # commit() now checks caller role; default to developer.

    task.agent_for.return_value = MagicMock(role="developer")

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
    notifications = overrides.get("notifications", AsyncMock())
    if "evidence_repo" in overrides:
        evidence_repo = overrides["evidence_repo"]
    else:
        evidence_repo = AsyncMock()
        evidence_repo.journal_highlights_for_task.return_value = []
    return ContentActionsDeps(
        task=task,
        git=git,
        messaging=messaging,
        a2a=a2a,
        journal=journal,
        workspace=workspace,
        notifications=notifications,
        evidence_repo=evidence_repo,
    )


# ---------------------------------------------------------------------------
# commit — uses get_active_task_for_agent which is already a self-scoped query.
# But if a caller had explicit task_id semantics, we'd guard them. The current
# commit verb uses active task; tests below verify it cannot reach a task that
# isn't theirs (active task query handles this naturally).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_active_task_returned_must_be_caller_owned() -> None:
    """get_active_task_for_agent already self-scopes; verify call signature."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        active_claimant_id=agent_id,
        branch_name="feature/backend/abc",
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "deadbeef1234"}

    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.commit(
        agent_id=agent_id,
        message="feat(api): add /healthz endpoint",
    )
    assert env.error is None
    task_svc.get_active_task_for_agent.assert_awaited_once_with(agent_id)


# ---------------------------------------------------------------------------
# note — explicit task_id ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_with_task_id_blocks_when_not_assignee() -> None:
    """note(task_id=X) where X is owned by someone else is rejected."""
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress", assigned_to=other_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="I am putting a note on someone else's task",
        scope="reflect",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert str(task_id) in body["message"] or "assignee" in body["message"]
    journal_svc.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_with_task_id_allows_when_assignee() -> None:
    """note(task_id=X) where X is owned by caller succeeds."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress", assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    task_svc.get_active_task_for_agent.return_value = None
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    # Uses scope='note' (no structured-field requirement) — the test
    # exercises the ownership gate, not the journal-shape gate (which is
    # covered separately in test_content_actions.py).
    env = await ca.note(
        agent_id=agent_id,
        text="Working on my own task",
        scope="note",
        task_id=task_id,
    )
    assert env.error is None
    journal_svc.write_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_note_without_task_id_skips_ownership_check() -> None:
    """note without task_id is fine (might be a general entry)."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="A general note with enough length",
        scope="note",
    )
    assert env.error is None
    journal_svc.write_entry.assert_awaited_once()


# ---------------------------------------------------------------------------
# say — explicit task_id ownership (only when task_id provided)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_say_with_task_id_blocks_when_not_assignee() -> None:
    """say(task_id=X) where X is owned by someone else is rejected."""
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress", assigned_to=other_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    messaging_svc = AsyncMock()
    deps = _make_deps(task=task_svc, messaging=messaging_svc)
    ca = ContentActions(deps)

    env = await ca.say(
        agent_id=agent_id,
        channel="backend-cell",
        text="Posting on someone else's task",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    messaging_svc.post_to_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_say_without_task_id_is_exempt() -> None:
    """say() with NO task_id and no active task: channel announcement, allowed."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    messaging_svc = AsyncMock()
    deps = _make_deps(task=task_svc, messaging=messaging_svc)
    ca = ContentActions(deps)

    env = await ca.say(
        agent_id=agent_id, channel="all-hands", text="General announcement"
    )
    assert env.error is None
    messaging_svc.post_to_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_say_with_explicit_task_id_owned_by_caller_succeeds() -> None:
    """say(task_id=X) when caller owns X: allowed."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress", assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    messaging_svc = AsyncMock()
    deps = _make_deps(task=task_svc, messaging=messaging_svc)
    ca = ContentActions(deps)

    env = await ca.say(
        agent_id=agent_id,
        channel="backend-cell",
        text="Working on my task",
        task_id=task_id,
    )
    assert env.error is None
    messaging_svc.post_to_channel.assert_awaited_once()


# ---------------------------------------------------------------------------
# dm — explicit task_id ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dm_with_task_id_blocks_when_not_assignee() -> None:
    """dm(task_id=X) where X is owned by someone else is rejected."""
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress", assigned_to=other_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    a2a_svc = AsyncMock()
    deps = _make_deps(task=task_svc, a2a=a2a_svc)
    ca = ContentActions(deps)

    env = await ca.dm(
        agent_id=agent_id,
        recipient="be-qa-1",
        text="DM about someone else's task",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    a2a_svc.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_dm_with_task_id_owned_by_caller_succeeds() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress", assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    a2a_svc = AsyncMock()
    deps = _make_deps(task=task_svc, a2a=a2a_svc)
    ca = ContentActions(deps)

    env = await ca.dm(
        agent_id=agent_id,
        recipient="be-qa-1",
        text="DM about my own task",
        task_id=task_id,
    )
    assert env.error is None
    a2a_svc.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# evidence — explicit task_id ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_blocks_when_not_assignee() -> None:
    """evidence(task_id=X) where X is owned by someone else is rejected.

    Exception: QA/documenter agents legitimately review tasks they don't
    own. We allow the read when assigned_to is None (post-handoff state)
    or matches the caller. The strict gate is for posting/writing only.

    For this test we use a task with another developer as assignee — the
    inspect call is denied because there's no role-based exemption logic
    yet at the gateway layer for content-tools.
    """
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=other_id,
        branch_name="feature/backend/abc",
        work_session_id=uuid4(),
        commits=["sha1"],
        pr_number=8,
        pr_url="x",
        dev_notes="",
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    git_svc = AsyncMock()
    git_svc.diff.return_value = ""
    workspace_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc, workspace=workspace_svc)
    ca = ContentActions(deps)

    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    workspace_svc.fetch_branch_for_inspection.assert_not_awaited()
    git_svc.diff.assert_not_awaited()


# ---------------------------------------------------------------------------
# Board co-review exemption (cluster C5): a board role may record its review
# note/say on a board/coordination task held by the OTHER board member.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_role_may_note_coordination_task_held_by_other_board() -> None:
    """A board/coordination task (project_id=None, product_id set) is reviewed
    by BOTH board members; the non-assignee board reviewer may still note it."""
    agent_id = uuid4()
    other_board_id = uuid4()
    task_id = uuid4()
    coord_task = MagicMock(
        id=task_id,
        status="pending",
        assigned_to=other_board_id,
        project_id=None,
        product_id=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = coord_task
    task_svc.get_active_task_for_agent.return_value = None
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    # _make_deps defaults agent_for to a developer; override AFTER so the
    # board co-review exemption sees a board role.
    task_svc.agent_for.return_value = MagicMock(role="head_marketing")
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="UX + positioning review of the board task",
        scope="note",
        task_id=task_id,
    )
    assert env.error is None
    journal_svc.write_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_board_role_blocked_on_project_task_held_by_other() -> None:
    """The exemption is narrow: a board role still cannot post to a normal
    project-backed task assigned to someone else."""
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    project_task = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=other_id,
        project_id=uuid4(),
        product_id=None,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = project_task
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    # Board role, but a project-backed task — exemption must NOT apply.
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="trying to note a code task I don't own",
        scope="note",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    journal_svc.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_board_role_blocked_on_coordination_task_held_by_other() -> None:
    """The exemption is board-only: a developer cannot piggyback on it."""
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    coord_task = MagicMock(
        id=task_id,
        status="pending",
        assigned_to=other_id,
        project_id=None,
        product_id=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = coord_task
    task_svc.agent_for.return_value = MagicMock(role="developer")
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="dev trying to note a coordination task",
        scope="note",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    journal_svc.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_evidence_unassigned_task_allows_inspection() -> None:
    """A task with assigned_to=None (between handoffs) may be inspected.

    This handles the QA-after-dev-submit case where assigned_to is briefly
    None until reassigned. Strict ownership only applies to write-side.
    """
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id,
        status="awaiting_qa",
        assigned_to=None,
        branch_name="feature/backend/abc",
        work_session_id=uuid4(),
        commits=["sha1"],
        pr_number=8,
        pr_url="x",
        dev_notes="",
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    git_svc = AsyncMock()
    git_svc.diff.return_value = "diff content"
    workspace_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc, workspace=workspace_svc)
    ca = ContentActions(deps)

    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    assert env.error is None
