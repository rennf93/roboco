"""Tests for ContentActions — commit, note, say, dm, evidence verbs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


def _make_deps(**overrides: AsyncMock) -> ContentActionsDeps:
    # Only set default return values on freshly-created mocks,
    # not on caller-supplied ones.
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


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_wip_is_rejected() -> None:
    """Short banned word "wip" fails commit validator before task lookup."""
    deps = _make_deps()
    ca = ContentActions(deps)
    agent_id = uuid4()

    env = await ca.commit(agent_id=agent_id, message="wip")
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    deps.task.get_active_task_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_descriptive_with_active_task_succeeds() -> None:
    """Descriptive subject succeeds; calls git.commit and task.add_progress."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id, status="in_progress", branch_name="feature/backend/abc"
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "deadbeef1234"}

    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.commit(
        agent_id=agent_id,
        message="feat(api): add /healthz endpoint for liveness checks",
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    git_svc.commit.assert_awaited_once()
    task_svc.add_progress.assert_awaited_once()
    # Progress message contains short sha
    call_args = task_svc.add_progress.call_args
    assert "deadbeef" in call_args.args[2]


@pytest.mark.asyncio
async def test_commit_no_active_task_returns_invalid_state() -> None:
    """Valid message but no active task → invalid_state."""
    deps = _make_deps()
    ca = ContentActions(deps)
    agent_id = uuid4()

    env = await ca.commit(
        agent_id=agent_id,
        message="feat(auth): implement JWT refresh token rotation logic",
    )
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "give_me_work" in body["remediate"]
    deps.git.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_strips_existing_task_prefix() -> None:
    """Agent-supplied [task-id] prefix is stripped before validation."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id, status="in_progress", branch_name="feature/backend/abc"
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "cafebabe"}

    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.commit(
        agent_id=agent_id,
        message="[ABC12345] feat(api): add rate limiting middleware to all routes",
    )
    body = env.as_dict()

    assert body["error"] is None
    # The subject passed to git.commit should not include the prefix
    call_kwargs = git_svc.commit.call_args.kwargs
    assert not call_kwargs["message"].startswith("[")


# ---------------------------------------------------------------------------
# note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_reflect_scope_succeeds() -> None:
    """scope='reflect' is valid; journal.write_entry is called."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    # When task_id is explicit, ownership is verified — agent must be assignee.
    task_svc.get.return_value = MagicMock(
        id=task_id, assigned_to=agent_id, status="in_progress"
    )
    journal_svc = AsyncMock()

    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="Reflected on approach: went with async generator pattern.",
        scope="reflect",
        task_id=task_id,
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["status"] == "noted"
    journal_svc.write_entry.assert_awaited_once()
    call_kwargs = journal_svc.write_entry.call_args.kwargs
    assert call_kwargs["scope"] == "reflect"


@pytest.mark.asyncio
async def test_note_invalid_scope_returns_invalid_state() -> None:
    """Unknown scope yields invalid_state with valid-scope hint."""
    deps = _make_deps()
    ca = ContentActions(deps)
    agent_id = uuid4()

    env = await ca.note(agent_id=agent_id, text="some text", scope="garbage")
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "garbage" in body["message"]
    assert "note" in body["remediate"]
    deps.journal.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_auto_fills_task_id_from_active_task() -> None:
    """When no task_id given but agent has active task, it is auto-filled."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    journal_svc = AsyncMock()

    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="Decided to use UUIDs instead of integer PKs for portability.",
        scope="decision",
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    call_kwargs = journal_svc.write_entry.call_args.kwargs
    assert call_kwargs["task_id"] == task_id


# ---------------------------------------------------------------------------
# say
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_say_auto_injects_task_id_when_active_task_exists() -> None:
    """Channel post auto-injects task_id from active task."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    messaging_svc = AsyncMock()

    deps = _make_deps(task=task_svc, messaging=messaging_svc)
    ca = ContentActions(deps)

    env = await ca.say(
        agent_id=agent_id, channel="backend-cell", text="Starting the auth refactor."
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    call_kwargs = messaging_svc.post_to_channel.call_args.kwargs
    assert call_kwargs["task_id"] == task_id


@pytest.mark.asyncio
async def test_say_succeeds_with_no_active_task_task_id_is_null() -> None:
    """say without active task still succeeds; task_id in response is None."""
    deps = _make_deps()
    ca = ContentActions(deps)
    agent_id = uuid4()

    env = await ca.say(
        agent_id=agent_id,
        channel="all-hands",
        text="Hello team, I am about to start work.",
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] is None
    deps.messaging.post_to_channel.assert_awaited_once()


# ---------------------------------------------------------------------------
# dm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dm_no_active_task_no_explicit_task_id_returns_invalid_state() -> None:
    """dm without any task context is rejected with a clear error."""
    deps = _make_deps()
    ca = ContentActions(deps)
    agent_id = uuid4()

    env = await ca.dm(
        agent_id=agent_id,
        recipient="be-qa-1",
        text="Can you review this when you have a moment?",
    )
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "task_id" in body["message"]
    deps.a2a.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_dm_with_active_task_succeeds() -> None:
    """dm auto-injects task_id from active task and sends."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    a2a_svc = AsyncMock()

    deps = _make_deps(task=task_svc, a2a=a2a_svc)
    ca = ContentActions(deps)

    env = await ca.dm(
        agent_id=agent_id,
        recipient="be-qa-1",
        text="PR is ready for review.",
        skill="code_review",
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    a2a_svc.send.assert_awaited_once()
    call_kwargs = a2a_svc.send.call_args.kwargs
    assert call_kwargs["to_agent"] == "be-qa-1"
    assert call_kwargs["skill"] == "code_review"


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_valid_task_returns_ok_with_pr_diff() -> None:
    """evidence() fetches branch, builds diff, returns EvidencePayload."""
    agent_id = uuid4()
    task_id = uuid4()
    ws_id = uuid4()
    pr_number = 42
    task_obj = MagicMock(
        id=task_id,
        status="awaiting_qa",
        # assigned_to=None covers post-handoff inspection (QA reviewing dev work)
        assigned_to=None,
        branch_name="feature/backend/abc",
        work_session_id=ws_id,
        commits=["sha1"],
        pr_number=pr_number,
        pr_url=f"https://github.com/org/repo/pull/{pr_number}",
        dev_notes="done",
        acceptance_criteria_status=[],
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = task_obj
    git_svc = AsyncMock()
    git_svc.diff.return_value = "diff --git a/foo.py b/foo.py\n+added line"
    workspace_svc = AsyncMock()

    deps = _make_deps(task=task_svc, git=git_svc, workspace=workspace_svc)
    ca = ContentActions(deps)

    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["task_id"] == str(task_id)
    assert body["evidence"]["pr_number"] == pr_number
    assert "diff --git" in body["evidence"]["pr_diff_summary"]
    workspace_svc.fetch_branch_for_inspection.assert_awaited_once()
    git_svc.diff.assert_awaited_once()


@pytest.mark.asyncio
async def test_evidence_task_not_found_returns_not_found() -> None:
    """evidence() returns not_found when task does not exist."""
    task_svc = AsyncMock()
    task_svc.get.return_value = None

    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)
    agent_id = uuid4()
    task_id = uuid4()

    env = await ca.evidence(agent_id=agent_id, task_id=task_id)
    body = env.as_dict()

    assert body["error"] == "not_found"
    assert str(task_id) in body["message"]
