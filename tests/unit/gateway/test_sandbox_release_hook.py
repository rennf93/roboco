"""Sandbox release hook — Choreographer._teardown_sandbox_best_effort.

CEO directive: sandboxes must die when an agent's engagement with its work
ends, not only when its container is removed. Ties the on-demand
request_sandbox subsystem's teardown to the SUCCESSFUL exit of the six
verbs whose completion means exactly that: i_am_done, unclaim, i_am_idle,
pass_review, fail_review, i_documented. A rejected/failed verb call must
NOT release (the work isn't done), and a release failure must never fail
the verb — best-effort, backstopped by the container-removal teardown +
janitor sweep that already exist.

Two layers:
  - the helper itself (`_teardown_sandbox_best_effort`), tested directly
    against a fake orchestrator;
  - the six call sites, tested by spying on the helper (patched onto the
    instance) so each verb's fixture stays the minimal one already proven
    by its own dedicated test file (test_choreographer_dev.py,
    test_unclaim.py, test_choreographer_idle_guards.py,
    test_choreographer_qa.py, test_choreographer_doc.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from structlog.testing import capture_logs


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    task = base["task"]
    # Covers both VerbRunner's savepoint (i_am_done/pass_review/fail_review/
    # i_documented) and i_documented's own session.flush() — one shared
    # setup for every verb exercised in this file.
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    task.session.flush = AsyncMock()
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_helper_noop_when_orchestrator_missing() -> None:
    c = Choreographer(_make_deps())  # orchestrator defaults to None

    await c._teardown_sandbox_best_effort(uuid4())  # must not raise


@pytest.mark.asyncio
async def test_helper_calls_release_sandbox_with_resolved_slug() -> None:
    agent_id = uuid4()
    orch = AsyncMock()
    c = Choreographer(_make_deps(orchestrator=orch))

    await c._teardown_sandbox_best_effort(agent_id)

    # Not seeded in AGENT_UUIDS, so _resolve_to_slug falls back to identity —
    # mirrors test_request_sandbox_verb.py's cross-agent-isolation assertion.
    orch.release_sandbox.assert_awaited_once_with(str(agent_id))


@pytest.mark.asyncio
async def test_helper_swallows_release_failure_and_logs() -> None:
    agent_id = uuid4()
    orch = AsyncMock()
    orch.release_sandbox.side_effect = RuntimeError("docker down")
    c = Choreographer(_make_deps(orchestrator=orch))

    with capture_logs() as logs:
        await c._teardown_sandbox_best_effort(agent_id)  # must not raise

    assert any("sandbox_release_failed" in str(e.get("event", "")) for e in logs)


# ---------------------------------------------------------------------------
# i_am_done
# ---------------------------------------------------------------------------


def _ready_i_am_done_task(task_id: Any, agent_id: Any, **overrides: Any) -> MagicMock:
    base = {
        "id": task_id,
        "status": "in_progress",
        "assigned_to": agent_id,
        "plan": {"x": 1},
        "branch_name": "feature/backend/abc",
        "work_session_id": uuid4(),
        "self_verified": False,
        "pr_number": 8,
        "pr_url": "https://x/pr/8",
        "team": "backend",
        "progress_updates": [{"message": "did x"}],
        "acceptance_criteria": [],
        "acceptance_criteria_status": [],
        "commits": [{"sha": "deadbeef"}],
        "documents": [],
        "dev_notes": "Implemented the change and added tests covering the new path.",
        "quick_context": None,
    }
    base.update(overrides)
    return MagicMock(**base)


@pytest.mark.asyncio
async def test_i_am_done_success_releases_sandbox() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_i_am_done_task(task_id, agent_id)
    after_verify = MagicMock(
        **{**t.__dict__, "self_verified": True, "status": "verifying"}
    )
    after_submit = MagicMock(**{**after_verify.__dict__, "status": "awaiting_qa"})

    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.submit_verification.return_value = after_verify
    task_svc.submit_qa.return_value = after_submit
    task_svc.qa_agent_for_team.return_value = MagicMock(
        id=uuid4(), skills=[{"id": "code_review"}]
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.i_am_done(agent_id, task_id, "done")

    assert env.error is None
    c._teardown_sandbox_best_effort.assert_awaited_once_with(agent_id)


@pytest.mark.asyncio
async def test_i_am_done_rejection_does_not_release_sandbox() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.i_am_done(agent_id, task_id, "done")

    assert env.error == "not_found"
    c._teardown_sandbox_best_effort.assert_not_awaited()


# ---------------------------------------------------------------------------
# unclaim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_success_releases_sandbox() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="claimed", assigned_to=agent_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.unclaim_for_agent.return_value = MagicMock(
        id=task_id, status="pending", assigned_to=None
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.unclaim(agent_id, task_id)

    assert env.error is None
    c._teardown_sandbox_best_effort.assert_awaited_once_with(agent_id)


@pytest.mark.asyncio
async def test_unclaim_rejection_does_not_release_sandbox() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.unclaim(agent_id, task_id)

    assert env.error == "not_found"
    c._teardown_sandbox_best_effort.assert_not_awaited()


# ---------------------------------------------------------------------------
# i_am_idle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_idle_success_releases_sandbox() -> None:
    agent_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = []
    task_svc.list_in_progress_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.i_am_idle(agent_id)

    assert env.error is None
    assert env.status == "idle"
    c._teardown_sandbox_best_effort.assert_awaited_once_with(agent_id)


@pytest.mark.asyncio
async def test_i_am_idle_with_unread_does_not_release_sandbox() -> None:
    """idle_with_unread sends the agent back to work — not a real exit, so
    the container does not shut down and the sandbox must survive."""
    agent_id = uuid4()
    deps = _make_deps()
    deps.evidence_repo.list_unread_a2a.return_value = [{"from": "x", "task_id": "t1"}]
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.i_am_idle(agent_id)

    assert env.status == "idle_with_unread"
    c._teardown_sandbox_best_effort.assert_not_awaited()


@pytest.mark.asyncio
async def test_i_am_idle_guard_rejection_does_not_release_sandbox() -> None:
    agent_id = uuid4()
    pending = MagicMock(id=uuid4(), status="pending")
    task_svc = AsyncMock()
    task_svc.list_assigned_for_agent.return_value = [pending]
    task_svc.list_in_progress_for_agent.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.i_am_idle(agent_id)

    assert env.error == "invalid_state"
    c._teardown_sandbox_best_effort.assert_not_awaited()


# ---------------------------------------------------------------------------
# pass_review / fail_review
# ---------------------------------------------------------------------------


def _qa_owned_task(task_id: Any, qa_id: Any, **overrides: Any) -> MagicMock:
    base = {
        "id": task_id,
        "status": "awaiting_qa",
        "task_type": "code",
        "team": "backend",
        "assigned_to": qa_id,
        "qa_evidence_inspected": True,
        "quick_context": None,
    }
    base.update(overrides)
    return MagicMock(**base)


def _qa_agent_mock(qa_id: Any) -> MagicMock:
    return MagicMock(id=qa_id, role="qa", team="backend", slug=None)


@pytest.mark.asyncio
async def test_pass_review_success_releases_sandbox() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    t = _qa_owned_task(task_id, qa_id)
    after = MagicMock(
        id=task_id,
        status="awaiting_documentation",
        assigned_to=qa_id,
        team="backend",
        pr_url="https://x/pr/8",
        qa_evidence_inspected=True,
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _qa_agent_mock(qa_id)
    task_svc.qa_pass.return_value = after
    task_svc.documenter_for_team.return_value = MagicMock(id=uuid4())
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    notes = (
        "Reviewed PR carefully. Branch convention correct. Commit prefix "
        "verified. README diff matches spec. All acceptance criteria met."
    )
    env = await c.pass_review(qa_id, task_id, notes=notes)

    assert env.error is None
    c._teardown_sandbox_best_effort.assert_awaited_once_with(qa_id)


@pytest.mark.asyncio
async def test_pass_review_rejection_does_not_release_sandbox() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.pass_review(qa_id, task_id, notes="x")

    assert env.error == "not_found"
    c._teardown_sandbox_best_effort.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_review_success_releases_sandbox() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    dev_id = uuid4()
    t = _qa_owned_task(task_id, qa_id)
    after = MagicMock(
        id=task_id,
        status="needs_revision",
        assigned_to=dev_id,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _qa_agent_mock(qa_id)
    task_svc.qa_fail.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_learning_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    issues = [
        "Missing unit test coverage for /healthz endpoint — add an assertion",
        "Lint errors in /api/foo.py: unused import and missing return type",
    ]
    env = await c.fail_review(qa_id, task_id, issues)

    assert env.error is None
    c._teardown_sandbox_best_effort.assert_awaited_once_with(qa_id)


@pytest.mark.asyncio
async def test_fail_review_rejection_does_not_release_sandbox() -> None:
    qa_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.fail_review(qa_id, task_id, ["some issue"])

    assert env.error == "not_found"
    c._teardown_sandbox_best_effort.assert_not_awaited()


# ---------------------------------------------------------------------------
# i_documented
# ---------------------------------------------------------------------------


def _doc_owned_task(task_id: Any, doc_id: Any, **overrides: Any) -> MagicMock:
    base = {
        "id": task_id,
        "status": "awaiting_documentation",
        "task_type": "code",
        "team": "backend",
        "assigned_to": doc_id,
        "quick_context": None,
    }
    base.update(overrides)
    return MagicMock(**base)


def _doc_agent_mock(doc_id: Any) -> MagicMock:
    return MagicMock(id=doc_id, role="documenter", team="backend", slug=None)


@pytest.mark.asyncio
async def test_i_documented_success_releases_sandbox() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    t = _doc_owned_task(task_id, doc_id)
    after = MagicMock(
        id=task_id, status="awaiting_pm_review", assigned_to=doc_id, team="backend"
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = _doc_agent_mock(doc_id)
    task_svc.docs_complete.return_value = after
    task_svc.cell_pm_for_team.return_value = MagicMock(id=uuid4())
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    notes = "Wrote backend/guides/feature-x.md with usage examples and config notes."
    files = ["backend/guides/feature-x.md"]
    env = await c.i_documented(doc_id, task_id, notes=notes, files=files)

    assert env.error is None
    c._teardown_sandbox_best_effort.assert_awaited_once_with(doc_id)


@pytest.mark.asyncio
async def test_i_documented_rejection_does_not_release_sandbox() -> None:
    doc_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)
    c._teardown_sandbox_best_effort = AsyncMock()

    env = await c.i_documented(doc_id, task_id, notes="x" * 30, files=["docs.md"])

    assert env.error == "not_found"
    c._teardown_sandbox_best_effort.assert_not_awaited()
