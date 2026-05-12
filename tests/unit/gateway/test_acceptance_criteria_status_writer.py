"""Wave C5 (2026-05-12): i_am_done writes per-criterion status.

Smoke run 3 showed evidence(task_id).acceptance_criteria_status always
[]. The i_am_done gate already validates each criterion against the
dev's journal:reflect; extending it to also persist the per-criterion
mapping so the panel can render checkmarks.
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
    """Build a task that satisfies all tracing AND field-level gates
    but has an empty acceptance_criteria_status so C5 must write it."""
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
        acceptance_criteria=["Must do X", "Must do Y", "Must do Z"],
        acceptance_criteria_status=[],
        commits=[{"sha": "deadbeef"}],
        documents=[],
        dev_notes="",
        quick_context=None,
    )


# ---------------------------------------------------------------------------
# C5.1 — successful i_am_done writes one entry per criterion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_writes_criteria_status_on_success() -> None:
    """A successful i_am_done writes acceptance_criteria_status with one
    entry per criterion.  The reflect note is the addressing artifact so
    all three criteria get addressed=True with artifact_ref=first-commit-sha.
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

    # i_am_done must succeed.
    assert body["error"] is None

    # set_acceptance_criteria_status must have been called once with
    # one dict per criterion in the same order as acceptance_criteria.
    task_svc.set_acceptance_criteria_status.assert_awaited_once()
    written_args = task_svc.set_acceptance_criteria_status.call_args

    # First positional arg is task_id, second is the list.
    written_task_id, written_status = written_args.args
    assert written_task_id == task_id

    expected_criteria = {"Must do X", "Must do Y", "Must do Z"}
    assert len(written_status) == len(expected_criteria)
    criteria_texts = {e["criterion"] for e in written_status}
    assert criteria_texts == expected_criteria

    for entry in written_status:
        assert entry["addressed"] is True
        # The reflect note is present and a commit exists → artifact_ref is the sha.
        assert entry["artifact_ref"] == "deadbeef"
        assert "checked_at" in entry
        # Entries must use the new shape (not the old referencing_artifact_id shape).
        assert "criterion" in entry


# ---------------------------------------------------------------------------
# C5.2 — no criteria → set_acceptance_criteria_status not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_with_no_criteria_does_not_write_status() -> None:
    """Task with acceptance_criteria=[] must not call
    set_acceptance_criteria_status — writing an empty list is a no-op
    that wastes a DB round-trip.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.acceptance_criteria = []
    t.acceptance_criteria_status = []
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
    task_svc.set_acceptance_criteria_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# C5.3 — all criteria already addressed → set_acceptance_criteria_status not called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_skips_write_when_all_criteria_already_addressed() -> None:
    """When every criterion in acceptance_criteria already has a non-empty
    referencing_artifact_id in acceptance_criteria_status, the writer must
    return early without calling set_acceptance_criteria_status.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.acceptance_criteria = ["Must do X", "Must do Y"]
    t.acceptance_criteria_status = [
        {"criterion": "Must do X", "referencing_artifact_id": "sha1"},
        {"criterion": "Must do Y", "referencing_artifact_id": "sha2"},
    ]
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
    task_svc.set_acceptance_criteria_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# C5.4 — gate rejection skips write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_gate_rejection_skips_criteria_write() -> None:
    """When the gate rejects (e.g. no reflect note), the criteria status
    write must NOT happen — the write only runs after a full gate pass.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    # Remove reflect — tracing gate will reject.
    t.acceptance_criteria = ["Must do X"]
    t.acceptance_criteria_status = []

    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id, role="developer", team="backend", slug=None
    )

    journal_svc = AsyncMock()
    journal_svc.has_reflect_for_task.return_value = False
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.latest_decision_at.return_value = None
    journal_svc.has_learning_for_task.return_value = False
    journal_svc.has_struggle_for_task.return_value = False

    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.i_am_done(agent_id, task_id, "done")
    body = env.as_dict()

    assert body["error"] is not None
    task_svc.set_acceptance_criteria_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# C5.5 — fallback to "reflect-note" when commit sha is absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i_am_done_uses_reflect_note_artifact_when_commit_sha_is_none() -> None:
    """When commits are present but carry no sha, the artifact_ref falls
    back to 'reflect-note' (the reflect note is what the gate accepted).
    commits=[] would be caught by COMMITS_AT_LEAST_ONE gate before the
    writer runs; this tests the edge case where sha is None inside a commit.
    """
    agent_id = uuid4()
    task_id = uuid4()
    t = _ready_task(task_id, agent_id)
    t.acceptance_criteria = ["Must do X"]
    t.acceptance_criteria_status = []
    # commit present but sha is None → sha extraction yields None.
    t.commits = [{"sha": None}]
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
    task_svc.set_acceptance_criteria_status.assert_awaited_once()
    _, written_status = task_svc.set_acceptance_criteria_status.call_args.args
    assert len(written_status) == 1
    assert written_status[0]["addressed"] is True
    assert written_status[0]["artifact_ref"] == "reflect-note"
