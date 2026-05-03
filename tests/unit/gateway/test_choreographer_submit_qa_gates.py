"""Gate Set E: submit-qa field-level gates in Choreographer.i_am_done.

Pre-gateway location: roboco/api/routes/tasks.py:903-940 (route layer).
The four field-level gates returned 400 errors when the dev tried to
submit for QA without:

- NOT_SELF_VERIFIED: task.self_verified must be true.
- NO_COMMITS: task.commits must be non-empty.
- NO_PR: task.pr_number must be set.
- NO_PROGRESS: task.progress_updates must have at least one entry.

The gateway's i_am_done previously called _run_catch_up which silently
auto-ran the full chain. That hid the missing-commits failure mode
(catch-up tried to push nothing, opened an empty PR, etc.).

Now i_am_done is strict and tells the dev exactly which prerequisite
is missing. A separate i_am_done_with_catchup verb retains the smart-
catch-up behavior for the explicit-opt-in case.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


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
    return ChoreographerDeps(**base)


def _ready_task(task_id: Any, agent_id: Any) -> MagicMock:
    """Build a task that satisfies tracing AND field-level gates."""
    return MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        plan={"x": 1},
        branch_name="feature/backend/abc--def",
        work_session_id=uuid4(),
        self_verified=True,
        pr_number=8,
        pr_url="https://x/pr/8",
        team="backend",
        progress_updates=[{"message": "did x"}],
        acceptance_criteria=["AC1"],
        acceptance_criteria_status=[
            {"criterion": "AC1", "referencing_artifact_id": "c1"}
        ],
        commits=[{"sha": "abc"}],
        documents=[],
        dev_notes="",
    )


# ---------------------------------------------------------------------------
# E.1 NOT_SELF_VERIFIED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_not_self_verified() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.self_verified = False
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "NOT_SELF_VERIFIED" in body["missing"] or "self_verified" in body["missing"]
    task_svc.submit_qa.assert_not_awaited()


# ---------------------------------------------------------------------------
# E.2 NO_COMMITS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_no_commits() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.commits = []
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "NO_COMMITS" in body["missing"] or "commits" in body["missing"]
    task_svc.submit_qa.assert_not_awaited()


# ---------------------------------------------------------------------------
# E.3 NO_PR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_no_pr() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.pr_number = None
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "NO_PR" in body["missing"] or "pr_number" in body["missing"]
    task_svc.submit_qa.assert_not_awaited()


# ---------------------------------------------------------------------------
# E.4 NO_PROGRESS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_no_progress() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.progress_updates = []
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    # progress>=1 is the existing tracing_gate Requirement key.
    assert "progress>=1" in body["missing"] or "NO_PROGRESS" in body["missing"]
    task_svc.submit_qa.assert_not_awaited()


# ---------------------------------------------------------------------------
# E.5 happy path: all gates pass → submit_qa runs (NO catch-up)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_proceeds_when_all_gates_pass() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    after_submit = MagicMock(
        **{**t.__dict__, "status": "awaiting_qa"},
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.submit_qa.return_value = after_submit
    task_svc.qa_agent_for_team.return_value = MagicMock(
        id=uuid4(), skills=[{"id": "code_review"}]
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = ["foo.py"]
    deps = _make_deps(task=task_svc, journal=journal_svc, work_session=work_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "all done")
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "awaiting_qa"
    task_svc.submit_qa.assert_awaited_once()
    # Strict path must NOT call submit_verification, push, or create_pr —
    # those are catch-up side effects which are now opt-in only.
    task_svc.submit_verification.assert_not_awaited()


# ---------------------------------------------------------------------------
# E.6 i_am_done_with_catchup retains the smart-catch-up convenience.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_with_catchup_runs_full_chain() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    initial = _ready_task(task_id, agent_id)
    initial.self_verified = False
    initial.pr_number = None
    after_verify = MagicMock(
        **{**initial.__dict__, "self_verified": True, "status": "verifying"}
    )
    after_pr_refresh = MagicMock(
        **{**after_verify.__dict__, "pr_number": 8, "pr_url": "https://x/pr/8"}
    )
    after_submit = MagicMock(**{**after_pr_refresh.__dict__, "status": "awaiting_qa"})
    task_svc = AsyncMock()
    task_svc.get.side_effect = [initial, after_pr_refresh]
    task_svc.submit_verification.return_value = after_verify
    task_svc.submit_qa.return_value = after_submit
    task_svc.qa_agent_for_team.return_value = MagicMock(
        id=uuid4(), skills=[{"id": "code_review"}]
    )
    work_svc = AsyncMock()
    work_svc.has_unpushed_commits.return_value = True
    work_svc.files_changed.return_value = ["foo.py"]
    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 8, "pr_url": "https://x/pr/8"}
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(
        task=task_svc,
        journal=journal_svc,
        work_session=work_svc,
        git=git_svc,
    )
    c = Choreographer(deps)

    env = await c.i_am_done_with_catchup(agent_id, task_id, "all done")
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "awaiting_qa"
    task_svc.submit_verification.assert_awaited_once()
    git_svc.push_branch.assert_awaited_once()
    git_svc.create_pr.assert_awaited_once()


@pytest.mark.asyncio
async def test_i_am_done_blocks_unauthorized() -> None:
    """Existing not_authorized check still applies."""
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, other_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "not_authorized"
