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

from datetime import UTC, datetime
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
    # VerbRunner uses task.session.begin_nested() as a savepoint context
    # manager. Keep `session` itself an AsyncMock so other awaited methods
    # (e.g. flush) still work, and override begin_nested with a sync
    # MagicMock that returns the async-context-manager protocol.
    task = base["task"]
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
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
        # i_am_done obligates the developer's dev_notes section (>=40 chars).
        dev_notes="Implemented the change and added tests covering the new path.",
    )


# ---------------------------------------------------------------------------
# self_verified is no longer a gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_auto_runs_submit_verification_when_in_progress() -> None:
    """Strict i_am_done auto-runs submit_verification (in_progress→verifying)
    so the dev doesn't need a separate verb. The previous NOT_SELF_VERIFIED
    gate required submit_for_verification which wasn't on any manifest.

    The pre-flight tracing gate filters SELF_VERIFIED (it is set by the
    auto-run submit_verification action and re-asserted by the spec's
    own preconditions), so an unverified in_progress task can still
    enter i_am_done.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.self_verified = False
    t.status = "in_progress"
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
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: ≥1 decision/learning/struggle.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = ["foo.py"]
    deps = _make_deps(task=task_svc, journal=journal_svc, work_session=work_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] is None
    task_svc.submit_verification.assert_awaited_once()
    task_svc.submit_qa.assert_awaited_once()


# ---------------------------------------------------------------------------
# E.2 NO_COMMITS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_no_commits() -> None:
    """Spec's PRECONDITION_COMMITS rejects with the canonical
    `commits>=1` missing token before any state mutation."""
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.commits = []
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: ≥1 decision/learning/struggle.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    # Spec emits "commits>=1" via PRECONDITION_COMMITS.
    assert "commits>=1" in body["missing"] or "NO_COMMITS" in body["missing"]
    task_svc.submit_qa.assert_not_awaited()


# ---------------------------------------------------------------------------
# E.3 NO_PR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_no_pr() -> None:
    """Defense-in-depth field gate fires NO_PR after the spec gate accepts.

    The spec doesn't yet model PR-existence; the field-gate helper still
    enforces it post-spec.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.pr_number = None
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: ≥1 decision/learning/struggle.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    # foundation.policy.tracing emits "pr_open" via PR_OPEN; the legacy
    # _check_submit_qa_field_gates path emitted "NO_PR" but tracing now
    # short-circuits before that field gate runs.
    assert (
        "pr_open" in body["missing"]
        or "NO_PR" in body["missing"]
        or "pr_number" in body["missing"]
    )
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
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: ≥1 decision/learning/struggle.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    # progress>=1 is the existing tracing_gate Requirement key.
    assert "progress>=1" in body["missing"] or "NO_PROGRESS" in body["missing"]
    task_svc.submit_qa.assert_not_awaited()


# ---------------------------------------------------------------------------
# Role note-section obligation: dev_notes must be filled (note(scope='handoff'))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_when_dev_notes_empty() -> None:
    """i_am_done obligates the developer's dev_notes section; an empty
    dev_notes (the dev never called note(scope='handoff')) fails the gate."""
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.dev_notes = ""
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "dev_notes>=min" in body["missing"]
    assert "scope='handoff'" in body["remediate"]
    task_svc.submit_qa.assert_not_awaited()


# ---------------------------------------------------------------------------
# E.5 happy path: all gates pass → submit_qa runs (NO catch-up)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_proceeds_when_all_gates_pass() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    # Pre-verifying state (caller already ran submit_for_verification or
    # task is already in `verifying`). i_am_done skips the auto-verify
    # step and goes straight to submit_qa.
    t.status = "verifying"
    t.self_verified = True
    after_submit = MagicMock(
        **{**t.__dict__, "status": "awaiting_qa"},
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    task_svc.submit_qa.return_value = after_submit
    task_svc.qa_agent_for_team.return_value = MagicMock(
        id=uuid4(), skills=[{"id": "code_review"}]
    )
    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = True
    # JOURNAL_DURING_WORK_AT_LEAST_ONE: ≥1 decision/learning/struggle.
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False
    work_svc = AsyncMock()
    work_svc.files_changed.return_value = ["foo.py"]
    deps = _make_deps(task=task_svc, journal=journal_svc, work_session=work_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "all done")
    body = env.as_dict()
    assert body["error"] is None
    assert body["status"] == "awaiting_qa"
    task_svc.submit_qa.assert_awaited_once()
    # Already-verifying status: recovery path runs only submit_qa, never
    # the composed submit_verification action.
    task_svc.submit_verification.assert_not_awaited()


# ---------------------------------------------------------------------------
# Removed: i_am_done_with_catchup verb deleted.
# Its functionality is now split between submit_for_qa (push + PR) and
# i_am_done (auto-run submit_verification then submit_qa).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_blocks_unauthorized() -> None:
    """A caller that does not own the task gets a clear not_authorized that
    steers to give_me_work — not the owns_task tracing_gap it would retry.

    A reassignment short-circuit runs before the spec gate, so a stale /
    superseded agent is told plainly the task is no longer its own (instead
    of reading PRECONDITION_OWNERSHIP's tracing_gap as a fixable precondition
    and looping i_am_done — the observed owns_task burn-loop).
    """
    agent_id = uuid4()
    other_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, other_id)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "no longer assigned" in (body.get("message") or "").lower()
    assert "give_me_work" in (body.get("remediate") or "")
