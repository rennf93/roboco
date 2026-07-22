"""Gate Set D: content-tool ownership guards in ContentActions.

When a caller passes a ``task_id`` to commit / dm / note / evidence,
the gateway must verify ``task.assigned_to == caller_agent_id`` before
allowing the side effect.

Pre-gateway equivalent: agents could only act on tasks they owned because
the MCP handlers resolved task from session context. With the gateway
exposing the task_id parameter, the ownership check must be explicit.

Exception: ``dm`` without a task_id (channel-only / off-task
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
    task_obj = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        active_claimant_id=agent_id,
    )
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


@pytest.mark.asyncio
async def test_evidence_allows_dependency_inspection() -> None:
    """A caller whose own assigned task depends on the target may read it.

    A frontend cell waiting on a UX design task must be able to inspect the
    dependency it is blocked on, even though another agent owns it.
    """
    agent_id = uuid4()
    other_id = uuid4()
    dep_task_id = uuid4()
    target = MagicMock(
        id=dep_task_id,
        status="in_progress",
        assigned_to=other_id,
        branch_name="feature/ux_ui/abc",
        work_session_id=uuid4(),
        commits=[],
        pr_number=None,
        pr_url="",
        dev_notes="",
        acceptance_criteria_status=[],
    )
    callers_task = MagicMock(id=uuid4(), dependency_ids=[dep_task_id])
    task_svc = AsyncMock()
    task_svc.get.return_value = target
    task_svc.list_assigned_for_agent.return_value = [callers_task]
    git_svc = AsyncMock()
    git_svc.diff.return_value = ""
    git_svc.list_changed_files.return_value = []
    workspace_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc, workspace=workspace_svc)
    ca = ContentActions(deps)

    env = await ca.evidence(agent_id=agent_id, task_id=dep_task_id)
    assert env.error is None
    task_svc.list_assigned_for_agent.assert_awaited()


# ---------------------------------------------------------------------------
# Reaped/handoff window: assigned_to persists, active_claimant_id is cleared
# on release. A reaped agent must not keep posting to its former task.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_reaped_assignee_cannot_post_to_former_task() -> None:
    """A reaped agent (assigned_to=caller, active_claimant_id=None) is rejected
    on note(task_id=X) — its claim was released, so it must not journal on the
    task it no longer actively holds."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,  # persists across the reap
        active_claimant_id=None,  # cleared on release — the reaped window
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="Posting after my claim was reaped",
        scope="note",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] == "not_authorized", body
    assert "active claim" in body["message"], body
    journal_svc.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_active_owner_can_still_post() -> None:
    """No-regression: an active owner (assigned_to=caller AND
    active_claimant_id=caller) still posts. The reaped-window rejection must
    not weaken the legitimate active-owner path."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        active_claimant_id=agent_id,  # active claim — the normal case
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    task_svc.get_active_task_for_agent.return_value = None
    journal_svc = AsyncMock()
    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="Working on my actively-claimed task",
        scope="note",
        task_id=task_id,
    )
    assert env.error is None, env.as_dict()
    journal_svc.write_entry.assert_awaited_once()
