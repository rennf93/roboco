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
        task.get_journal_context_task_for_agent.return_value = None
        # commit() checks caller role server-side; default-created mocks
        # need a default developer role so existing tests pass through.
        # Caller-supplied mocks must set agent_for themselves.

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
    task_svc.agent_for.return_value = MagicMock(role="developer")
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
    """Agent-supplied [task-id] prefix is stripped before validation;
    the canonical [task-id-short] is re-applied before git.commit."""
    agent_id = uuid4()
    task_id = uuid4()
    expected_prefix = f"[{str(task_id)[:8]}]"
    task_obj = MagicMock(
        id=task_id, status="in_progress", branch_name="feature/backend/abc"
    )
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = task_obj
    task_svc.agent_for.return_value = MagicMock(role="developer")
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
    # The subject passed to git.commit gets the canonical prefix re-applied,
    # not the user-supplied [ABC12345].
    call_kwargs = git_svc.commit.call_args.kwargs
    assert call_kwargs["message"].startswith(expected_prefix)
    assert "[ABC12345]" not in call_kwargs["message"]


# ---------------------------------------------------------------------------
# note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_rejects_pm_role() -> None:
    """Regression: PMs must not be able to call commit (smoke 2026-05-03)."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="main_pm")
    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.commit(agent_id=agent_id, message="fix: something")

    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "main_pm" in body["message"]
    assert "delegate" in body["remediate"]


@pytest.mark.asyncio
async def test_commit_rejects_qa_role() -> None:
    """QA cannot author commits — only developers and documenters can."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="qa")
    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.commit(agent_id=agent_id, message="fix: bug")

    assert env.as_dict()["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_commit_rejects_board_role() -> None:
    """Board members do not write code."""
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="product_owner")
    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)

    env = await ca.commit(agent_id=agent_id, message="fix: thing")

    assert env.as_dict()["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_commit_allows_documenter_role() -> None:
    """Documenters write doc commits — must not be rejected."""
    agent_id = uuid4()
    task_id = uuid4()
    task_obj = MagicMock(
        id=task_id, status="awaiting_documentation", branch_name="feature/backend/abc"
    )
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="documenter")
    task_svc.get_active_task_for_agent.return_value = task_obj
    git_svc = AsyncMock()
    git_svc.commit.return_value = {"sha": "deadbeef"}
    deps = _make_deps(task=task_svc, git=git_svc)
    ca = ContentActions(deps)

    env = await ca.commit(agent_id=agent_id, message="docs: add migration notes")

    assert env.error is None  # not rejected on role grounds


@pytest.mark.asyncio
async def test_note_reflect_scope_succeeds() -> None:
    """scope='reflect' is valid; journal.write_entry is called.

    Pre-gateway parity: reflect requires what_done / what_learned /
    what_struggled (each a non-empty string). The gateway returns
    `incomplete_input` if any is missing.
    """
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
        structured={
            "what_done": "Shipped the async generator pattern in service.py:120-180",
            "what_learned": "asyncio.shield wraps cancellation correctly here",
            "what_struggled": "Initially missed the cleanup race; commits 3a4f1 fix it",
        },
    )
    body = env.as_dict()

    assert body["error"] is None
    assert body["status"] == "noted"
    journal_svc.write_entry.assert_awaited_once()
    call_kwargs = journal_svc.write_entry.call_args.kwargs
    assert call_kwargs["scope"] == "reflect"


@pytest.mark.asyncio
async def test_note_reflect_missing_required_fields_returns_incomplete_input() -> None:
    """Pre-gateway parity: reflect without structured fields fails fast."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.get.return_value = MagicMock(
        id=task_id, assigned_to=agent_id, status="in_progress"
    )
    journal_svc = AsyncMock()

    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="bare reflect with no structured fields",
        scope="reflect",
        task_id=task_id,
    )
    body = env.as_dict()

    assert body["error"] == "incomplete_input"
    missing = set(body["missing"])
    assert {"what_done", "what_learned", "what_struggled"}.issubset(missing)
    journal_svc.write_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_decision_requires_options_and_more() -> None:
    """Pre-gateway parity: decision requires context/options(>=2)/chosen/rationale."""
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get_active_task_for_agent.return_value = None
    task_svc.get.return_value = MagicMock(
        id=task_id, assigned_to=agent_id, status="in_progress"
    )
    journal_svc = AsyncMock()

    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    # Missing everything structured → incomplete_input listing all required.
    env = await ca.note(
        agent_id=agent_id,
        text="bare decision",
        scope="decision",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input"
    assert {"context", "options", "chosen", "rationale"}.issubset(set(body["missing"]))

    # Single option still fails (min 2).
    env = await ca.note(
        agent_id=agent_id,
        text="decision with one option",
        scope="decision",
        task_id=task_id,
        structured={
            "context": "needed a queue",
            "options": [{"name": "redis", "pros": "fast", "cons": "ephemeral"}],
            "chosen": "redis",
            "rationale": "speed beats durability for this case",
        },
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input"
    assert "options" in body["missing"]

    # Two options + all required → success.
    env = await ca.note(
        agent_id=agent_id,
        text="real decision",
        scope="decision",
        task_id=task_id,
        structured={
            "context": "needed a queue",
            "options": [
                {"name": "redis", "pros": "fast", "cons": "ephemeral"},
                {"name": "postgres", "pros": "durable", "cons": "slower writes"},
            ],
            "chosen": "redis",
            "rationale": "speed beats durability for ephemeral work",
        },
    )
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "noted"

    # Three+ options also pass — 2 is the floor, not the ceiling.
    env = await ca.note(
        agent_id=agent_id,
        text="three-way decision",
        scope="decision",
        task_id=task_id,
        structured={
            "context": "queue tech choice",
            "options": [
                {"name": "redis", "pros": "fast", "cons": "ephemeral"},
                {"name": "postgres", "pros": "durable", "cons": "slower"},
                {"name": "rabbitmq", "pros": "ordered", "cons": "ops overhead"},
            ],
            "chosen": "rabbitmq",
            "rationale": "ordering matters more than raw speed here",
        },
    )
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "noted"


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
    task_svc.get_journal_context_task_for_agent.return_value = task_obj
    journal_svc = AsyncMock()

    deps = _make_deps(task=task_svc, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=agent_id,
        text="Decided to use UUIDs instead of integer PKs for portability.",
        scope="decision",
        structured={
            "context": "Choosing primary-key strategy for the new tables",
            "options": [
                {"name": "int", "pros": "compact", "cons": "leaks volume"},
                {"name": "uuid", "pros": "portable", "cons": "wider rows"},
            ],
            "chosen": "uuid",
            "rationale": "portability matters more than 8 bytes/row",
        },
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
    task_svc.get_journal_context_task_for_agent.return_value = task_obj
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
    task_svc.get_journal_context_task_for_agent.return_value = task_obj
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


# ---------------------------------------------------------------------------
# notify: invalid priority and explicit-ownership rejections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_invalid_priority_rejected() -> None:
    """Line 342: priority not in valid set → invalid_state."""
    deps = _make_deps()
    ca = ContentActions(deps)
    agent_id = uuid4()
    env = await ca.notify(
        agent_id=agent_id,
        target="be-pm",
        text="hello",
        priority="meteoric",
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "invalid priority" in body["message"]


@pytest.mark.asyncio
async def test_notify_explicit_task_not_found_rejected() -> None:
    """Lines 365-366: explicit task_id with task missing → not_found."""
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="cell_pm")
    task_svc.get.return_value = None  # task lookup fails
    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)
    agent_id = uuid4()
    env = await ca.notify(
        agent_id=agent_id,
        target="be-dev-1",
        text="hi",
        priority="normal",
        task_id=uuid4(),
    )
    body = env.as_dict()
    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_verify_explicit_task_ownership_returns_not_found() -> None:
    """Line 184: helper returns not_found envelope when task missing."""
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    ca = ContentActions(deps)
    agent_id = uuid4()
    task_id = uuid4()
    env = await ca._verify_explicit_task_ownership(agent_id, task_id)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "not_found"


# ---------------------------------------------------------------------------
# B4 — decision/reflect remediate includes a literal call example
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_incomplete_input_includes_call_example() -> None:
    """Decision rejection includes a literal note(scope='decision', ...) template."""
    task = AsyncMock()
    task.get_active_task_for_agent.return_value = None
    task.agent_for.return_value = MagicMock(role="cell_pm")
    deps = _make_deps(task=task)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=uuid4(),
        text="bare decision",
        scope="decision",
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input"
    remediate = body.get("remediate", "")
    # Must include a literal call template, not just a field list
    assert "note(scope='decision'" in remediate, remediate
    assert "context=" in remediate, remediate
    assert "options=[" in remediate, remediate
    assert "chosen=" in remediate, remediate
    assert "rationale=" in remediate, remediate


@pytest.mark.asyncio
async def test_reflect_incomplete_input_includes_call_example() -> None:
    """Reflect rejection includes a literal note(scope='reflect', ...) call template."""
    task = AsyncMock()
    task.get_active_task_for_agent.return_value = None
    task.agent_for.return_value = MagicMock(role="developer")
    deps = _make_deps(task=task)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=uuid4(),
        text="bare reflect",
        scope="reflect",
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input"
    remediate = body.get("remediate", "")
    assert "note(scope='reflect'" in remediate, remediate
    assert "what_done=" in remediate, remediate
    assert "what_learned=" in remediate, remediate
    assert "what_struggled=" in remediate, remediate
