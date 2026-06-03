"""Tests for ContentActions — commit, note, say, dm, evidence verbs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
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
        notification_delivery=notification_delivery,
        evidence_repo=evidence_repo,
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
        id=task_id,
        status="in_progress",
        branch_name="feature/backend/abc",
        active_claimant_id=agent_id,
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
        id=task_id,
        status="in_progress",
        branch_name="feature/backend/abc",
        active_claimant_id=agent_id,
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
        id=task_id,
        status="awaiting_documentation",
        branch_name="feature/backend/abc",
        active_claimant_id=agent_id,
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
async def test_note_reflect_missing_fields_records_with_placeholder() -> None:
    """Issue #15: a thin reflect note is recorded, not rejected.

    Missing narrative fields are defaulted to a visible placeholder so the
    entry still lands (audit value preserved) and the do-server circuit
    breaker never fires on a well-intentioned note.
    """
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

    assert body["error"] is None
    assert body["status"] == "noted"
    journal_svc.write_entry.assert_awaited_once()
    content = journal_svc.write_entry.call_args.kwargs["content"]
    # Missing what_done/what_learned/what_struggled render as the placeholder.
    assert "(not provided)" in content


@pytest.mark.asyncio
async def test_note_decision_thin_payload_records_not_rejected() -> None:
    """Issue #15: decision with missing/thin fields is recorded, not rejected.

    A single option is kept as-is (the min-2 gate no longer hard-blocks);
    missing context/chosen/rationale default to a placeholder.
    """
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

    # Bare decision: no structured fields → still recorded with placeholders.
    env = await ca.note(
        agent_id=agent_id,
        text="bare decision",
        scope="decision",
        task_id=task_id,
    )
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "noted"

    # Single option no longer fails — recorded as-is.
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
    assert body["error"] is None
    assert body["status"] == "noted"

    # Fully-filled decision still succeeds (no regression).
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


@pytest.mark.asyncio
async def test_note_decision_scalar_list_fields_are_coerced() -> None:
    """Issue #15: lone-scalar options/consequences are wrapped into lists.

    An agent that passes a single option dict or a single consequences
    string must not be rejected — the value is wrapped into a one-element
    list and rendered into the entry.
    """
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
        text="decision with scalar fields",
        scope="decision",
        task_id=task_id,
        structured={
            "context": "queue tech",
            # single dict instead of a list
            "options": {"name": "redis", "pros": "fast", "cons": "ephemeral"},
            "chosen": "redis",
            "rationale": "speed",
            # single string instead of a list
            "consequences": "we lose durability across restarts",
        },
    )
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "noted"
    content = journal_svc.write_entry.call_args.kwargs["content"]
    assert "redis" in content
    assert "we lose durability across restarts" in content


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
# Issue #15 — thin decision/reflect notes are recorded, never rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_thin_note_records_without_rejection() -> None:
    """A bare decision (no structured fields) is recorded, not rejected."""
    task = AsyncMock()
    task.get_active_task_for_agent.return_value = None
    task.get_journal_context_task_for_agent.return_value = None
    task.agent_for.return_value = MagicMock(role="cell_pm")
    journal_svc = AsyncMock()
    deps = _make_deps(task=task, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=uuid4(),
        text="bare decision",
        scope="decision",
    )
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "noted"
    journal_svc.write_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_reflect_thin_note_records_without_rejection() -> None:
    """A bare reflect (no structured fields) is recorded, not rejected."""
    task = AsyncMock()
    task.get_active_task_for_agent.return_value = None
    task.get_journal_context_task_for_agent.return_value = None
    task.agent_for.return_value = MagicMock(role="developer")
    journal_svc = AsyncMock()
    deps = _make_deps(task=task, journal=journal_svc)
    ca = ContentActions(deps)

    env = await ca.note(
        agent_id=uuid4(),
        text="bare reflect",
        scope="reflect",
    )
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "noted"
    journal_svc.write_entry.assert_awaited_once()


# ---------------------------------------------------------------------------
# #173: plan-driven progress — progress() marks a plan step + derives %.
# ---------------------------------------------------------------------------


def _active_task(agent_id: object) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        assigned_to=agent_id,
        active_claimant_id=agent_id,
        status="in_progress",
    )


@pytest.mark.asyncio
async def test_progress_plan_step_resolved_ok_with_derived_pct() -> None:
    agent_id = uuid4()
    t = _active_task(agent_id)
    task = AsyncMock()
    task.get.return_value = t
    task.record_plan_progress.return_value = {
        "task": t,
        "percentage": 50,
        "step_resolved": True,
        "valid_steps": ["s1", "s2"],
    }
    actions = ContentActions(_make_deps(task=task))

    env = await actions.progress(
        agent_id=agent_id, task_id=t.id, message="did step 1", plan_step="s1"
    )
    body = env.as_dict()
    assert body.get("error") is None, body
    assert "50%" in body["next"], body
    task.record_plan_progress.assert_awaited_once()
    assert task.record_plan_progress.await_args.kwargs["plan_step"] == "s1"


@pytest.mark.asyncio
async def test_progress_unknown_plan_step_invalid_state_lists_valid() -> None:
    agent_id = uuid4()
    t = _active_task(agent_id)
    task = AsyncMock()
    task.get.return_value = t
    task.record_plan_progress.return_value = {
        "task": t,
        "percentage": 0,
        "step_resolved": False,
        "valid_steps": ["s1", "s2"],
    }
    actions = ContentActions(_make_deps(task=task))

    env = await actions.progress(
        agent_id=agent_id, task_id=t.id, message="?", plan_step="bogus"
    )
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "s1" in body["remediate"] and "s2" in body["remediate"], body


@pytest.mark.asyncio
async def test_progress_narrative_without_plan_step_ok() -> None:
    agent_id = uuid4()
    t = _active_task(agent_id)
    task = AsyncMock()
    task.get.return_value = t
    task.record_plan_progress.return_value = {
        "task": t,
        "percentage": 25,
        "step_resolved": None,
        "valid_steps": ["s1"],
    }
    actions = ContentActions(_make_deps(task=task))

    env = await actions.progress(
        agent_id=agent_id, task_id=t.id, message="midway milestone"
    )
    assert env.as_dict().get("error") is None
    assert task.record_plan_progress.await_args.kwargs["plan_step"] is None


@pytest.mark.asyncio
async def test_progress_ownership_enforced() -> None:
    agent_id = uuid4()
    t = MagicMock(id=uuid4(), assigned_to=uuid4(), status="in_progress")
    task = AsyncMock()
    task.get.return_value = t
    actions = ContentActions(_make_deps(task=task))

    env = await actions.progress(agent_id=agent_id, task_id=t.id, message="x")
    assert env.as_dict()["error"] is not None
    task.record_plan_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_gate_reads_settings_min_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit gate reads settings.commit_subject_min_chars."""
    monkeypatch.setattr(settings, "commit_subject_min_chars", 500)
    ca = ContentActions(_make_deps())

    # Normally a fine descriptive subject, now shorter than the bumped minimum.
    env = await ca.commit(
        agent_id=uuid4(),
        message="feat(api): add /healthz endpoint for liveness checks",
    )
    assert env.as_dict()["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_commit_gate_reads_settings_banned_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit gate reads settings.commit_banned_words."""
    monkeypatch.setattr(settings, "commit_banned_words", ("bananaword",))
    monkeypatch.setattr(settings, "commit_subject_min_chars", 1)
    ca = ContentActions(_make_deps())

    env = await ca.commit(agent_id=uuid4(), message="bananaword")
    assert env.as_dict()["error"] == "invalid_state"
