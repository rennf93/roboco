"""The DevOps co-claim mechanism (the Stage-3 second-reviewer gate).

devops-1 co-reviews a gate task WITHOUT disturbing the primary reviewer's
claim/ownership: the co-claim rides the ``devops_gate_claimant`` marker (never
``active_claimant_id`` / ``assigned_to`` / ``claimed_by``), ``unclaim`` by
devops releases ONLY the co-claim, and the co-claim is what lets devops call
``record_devops_review`` / ``pr_pass`` / ``pr_fail`` despite not owning the
task. Flag off => every path is inert.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.content import markers
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.task import TaskService


def _make_deps(task: AsyncMock) -> ChoreographerDeps:
    task.session.commit = AsyncMock()
    return ChoreographerDeps(
        task=task,
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
    )


def _gate_task() -> Any:
    primary = uuid4()
    return SimpleNamespace(
        id=uuid4(),
        status="awaiting_pr_review",
        assigned_to=primary,
        claimed_by=primary,
        active_claimant_id=primary,
        orchestration_markers=None,
        notes_structured={},
        parent_task_id=uuid4(),
        task_type="code",
        dependency_ids=[],
        team="backend",
        pr_number=139,
        pr_url="https://example/pr/139",
        branch_name="feature/backend/root",
        batch_id=None,
    )


# ---------------------------------------------------------------------------
# claim_gate_review: the devops co-claim branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_gate_review_devops_co_claims_without_touching_primary() -> None:
    task_svc = AsyncMock()
    t = _gate_task()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="devops", slug="devops-1")
    task_svc.devops_gate_co_claim = AsyncMock(return_value=t)
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    c = Choreographer(_make_deps(task_svc))
    cc: Any = c
    cc._build_gate_review_evidence = AsyncMock(return_value={"pr_number": 139})

    env = await c.claim_gate_review(uuid4(), t.id)

    assert env.error is None, env.as_dict()
    task_svc.devops_gate_co_claim.assert_awaited_once()
    # The single-claimant path is never touched by the co-claim lane.
    task_svc.pr_gate_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_gate_review_primary_reviewer_path_unchanged() -> None:
    task_svc = AsyncMock()
    t = _gate_task()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="pr_reviewer", slug="be-pr-reviewer"
    )
    task_svc.pr_gate_claim = AsyncMock(return_value=t)
    task_svc.devops_gate_co_claim = AsyncMock()
    task_svc.list_in_progress_for_agent.return_value = []
    task_svc.list_paused_for_agent.return_value = []
    task_svc.unmet_dependency_ids = AsyncMock(return_value=[])
    task_svc.has_earlier_incomplete_code_sibling.return_value = False
    c = Choreographer(_make_deps(task_svc))
    cc: Any = c
    cc._build_gate_review_evidence = AsyncMock(return_value={"pr_number": 139})

    env = await c.claim_gate_review(uuid4(), t.id)

    assert env.error is None, env.as_dict()
    task_svc.pr_gate_claim.assert_awaited_once()
    task_svc.devops_gate_co_claim.assert_not_awaited()


# ---------------------------------------------------------------------------
# record_devops_review
# ---------------------------------------------------------------------------


def _stub_record_preflight(c: Choreographer, t: Any, *, role: str) -> Any:
    cc: Any = c
    agent = MagicMock(
        role=role, slug="devops-1" if role == "devops" else "be-pr-reviewer"
    )
    cc._claim_gate_preflight = AsyncMock(return_value=(t, role, {"briefing": True}))
    cc._gate_tracing = AsyncMock(return_value=None)
    return agent


@pytest.mark.asyncio
async def test_record_devops_review_requires_devops_role() -> None:
    task_svc = AsyncMock()
    t = _gate_task()
    c = Choreographer(_make_deps(task_svc))
    _stub_record_preflight(c, t, role="pr_reviewer")

    env = await c.record_devops_review(uuid4(), t.id, "Infra surface verified.")

    assert env.error == "not_authorized"
    task_svc.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_devops_review_requires_co_claim() -> None:
    task_svc = AsyncMock()
    t = _gate_task()
    c = Choreographer(_make_deps(task_svc))
    _stub_record_preflight(c, t, role="devops")

    env = await c.record_devops_review(uuid4(), t.id, "Infra surface verified.")

    assert env.error == "not_authorized"
    assert "claim_gate_review" in (env.remediate or "")


@pytest.mark.asyncio
async def test_record_devops_review_writes_note_with_head_sha() -> None:
    task_svc = AsyncMock()
    devops_id = uuid4()
    t = _gate_task()
    markers.set_devops_gate_claimant(t, devops_id)
    c = Choreographer(_make_deps(task_svc))
    _stub_record_preflight(c, t, role="devops")
    cc: Any = c
    cc._capture_pr_head_sha = AsyncMock(return_value="h1")

    env = await c.record_devops_review(devops_id, t.id, "Infra surface verified sound.")

    assert env.error is None, env.as_dict()
    section = t.notes_structured["devops_review"]
    assert section["verdict"] == "passed"
    assert section["head_sha"] == "h1"
    # The primary reviewer's slot is never touched by a devops verdict.
    assert "pr_review" not in t.notes_structured
    # Durability boundary: the note IS the deliverable.
    task_svc.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_devops_review_rejects_invalid_payload_cleanly() -> None:
    task_svc = AsyncMock()
    devops_id = uuid4()
    t = _gate_task()
    markers.set_devops_gate_claimant(t, devops_id)
    c = Choreographer(_make_deps(task_svc))
    _stub_record_preflight(c, t, role="devops")
    cc: Any = c
    cc._capture_pr_head_sha = AsyncMock(return_value="h1")

    # A too-trivial summary fails the content model - the verb must reject
    # cleanly instead of recording nothing while reporting success.
    env = await c.record_devops_review(devops_id, t.id, "ok")

    assert env.error == "invalid_state"
    assert "devops_review" not in (t.notes_structured or {})
    task_svc.session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# The ownership guard's flag-gated devops carve-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ownership_flag_off_rejects_devops_co_claimant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", False)
    task_svc = AsyncMock()
    t = _gate_task()
    task_svc.get.return_value = t
    devops_id = uuid4()
    markers.set_devops_gate_claimant(t, devops_id)
    task_svc.agent_for.return_value = MagicMock(role="devops", slug="devops-1")
    c = Choreographer(_make_deps(task_svc))

    env = await c._gate_ownership_or_rejection(devops_id, t.id, "pr_pass")

    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_ownership_flag_on_accepts_co_claimant_for_gate_verbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    task_svc = AsyncMock()
    t = _gate_task()
    task_svc.get.return_value = t
    devops_id = uuid4()
    markers.set_devops_gate_claimant(t, devops_id)
    task_svc.agent_for.return_value = MagicMock(role="devops", slug="devops-1")
    c = Choreographer(_make_deps(task_svc))

    result = await c._gate_ownership_or_rejection(devops_id, t.id, "pr_pass")

    assert result is t


@pytest.mark.asyncio
async def test_ownership_flag_on_still_rejects_unclaimed_devops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    task_svc = AsyncMock()
    t = _gate_task()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="devops", slug="devops-1")
    c = Choreographer(_make_deps(task_svc))

    env = await c._gate_ownership_or_rejection(uuid4(), t.id, "pr_pass")

    assert env.error == "not_authorized"


# ---------------------------------------------------------------------------
# Service-level co-claim / co-unclaim
# ---------------------------------------------------------------------------


def _task_service_with(task: Any) -> Any:
    svc = cast("Any", TaskService(MagicMock()))
    svc.get = AsyncMock(return_value=task)
    svc.session = MagicMock()
    svc.session.flush = AsyncMock()
    svc.session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=task))
    )
    return svc


@pytest.mark.asyncio
async def test_co_claim_sets_marker_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    t = _gate_task()
    primary, devops_id = t.assigned_to, uuid4()
    svc = _task_service_with(t)

    claimed = await svc.devops_gate_co_claim(devops_id, t.id)

    assert claimed is t
    assert markers.get_devops_gate_claimant(t) == str(devops_id)
    # The primary reviewer's claim/ownership columns are untouched.
    assert t.assigned_to == primary
    assert t.claimed_by == primary
    assert t.active_claimant_id == primary


@pytest.mark.asyncio
async def test_co_claim_idempotent_for_same_agent_rejects_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    t = _gate_task()
    devops_id = uuid4()
    svc = _task_service_with(t)
    assert await svc.devops_gate_co_claim(devops_id, t.id) is t
    assert await svc.devops_gate_co_claim(devops_id, t.id) is t
    assert await svc.devops_gate_co_claim(uuid4(), t.id) is None


@pytest.mark.asyncio
async def test_co_claim_inert_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", False)
    t = _gate_task()
    svc = _task_service_with(t)

    assert await svc.devops_gate_co_claim(uuid4(), t.id) is None
    assert markers.get_devops_gate_claimant(t) is None


@pytest.mark.asyncio
async def test_co_unclaim_releases_only_the_co_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    t = _gate_task()
    primary, devops_id = t.assigned_to, uuid4()
    svc = _task_service_with(t)
    await svc.devops_gate_co_claim(devops_id, t.id)

    assert await svc.devops_gate_co_unclaim(devops_id, t.id) is t
    assert markers.get_devops_gate_claimant(t) is None
    # The primary reviewer's claim survives the devops exit untouched.
    assert t.assigned_to == primary
    assert t.claimed_by == primary
    assert t.active_claimant_id == primary
    assert t.status == "awaiting_pr_review"


@pytest.mark.asyncio
async def test_co_unclaim_noop_for_non_holder() -> None:
    t = _gate_task()
    svc = _task_service_with(t)

    assert await svc.devops_gate_co_unclaim(uuid4(), t.id) is None


@pytest.mark.asyncio
async def test_service_pr_pass_clears_the_co_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate decisions clear the co-claim so a stale marker can never let
    devops decide a LATER round without re-claiming."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    t = _gate_task()
    t.status = "awaiting_pr_review"
    markers.set_devops_gate_claimant(t, uuid4())
    svc = _task_service_with(t)
    pm = MagicMock(id=uuid4())
    svc._revision_pm_for_task = AsyncMock(return_value=pm)
    svc._clear_agent_current_task = AsyncMock()
    svc._validate_and_set_status = MagicMock()

    await svc.pr_pass(uuid4(), t.id, "Assembled PR verified end to end")

    assert markers.get_devops_gate_claimant(t) is None


# ---------------------------------------------------------------------------
# Lifecycle spec surface
# ---------------------------------------------------------------------------


def test_record_devops_review_spec_surface() -> None:
    intent = spec_module._INTENT_VERBS["record_devops_review"]
    assert intent.allowed_roles == frozenset({spec_module.Role.DEVOPS})
    # No transition: the primary reviewer's pr_pass composes off the verdict.
    assert intent.composes == ()
