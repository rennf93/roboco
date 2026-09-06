"""The in-path PR gate's cross-vendor second-review wiring (``pr_pass``).

Pins the two behaviors the task calls out explicitly:

  * flag OFF (default) leaves ``pr_pass``'s envelope byte-for-byte unchanged
    — no ``evidence`` payload, and the git service is never even asked for
    the diff (the second pass must not cost anything when inert).
  * flag ON + above the risk threshold: a second-pass finding the runner
    raises inserts into the EXISTING ``task_review_findings`` ledger under
    the dedicated ``second_review`` origin, and the envelope records which
    provider ran the pass. A single-provider-enabled skip never blocks.

Follows the ``_make_choreographer`` / ``_stub_gate_path`` pattern from
``test_pr_pass_ci_status_guard.py`` — only the ownership/tracing plumbing is
stubbed; the real ``_gate_decision`` / ``_run_second_review_pass`` run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation.policy import lifecycle as spec_module
from roboco.foundation.policy.content import Finding, Severity
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    ModelProvider,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer.second_review_gate import SecondReviewOutcome
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.second_review import SecondReviewSelection

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def _make_choreographer() -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    return Choreographer(ChoreographerDeps(**base))


def _stub_gate_path(
    c: Choreographer, *, reviewer_id: Any, t_before: Any, t_after: Any
) -> None:
    """Drive ``_gate_decision`` past preflight/tracing/CI into the real
    ``pr_pass`` success path — only ownership/tracing/CI plumbing is
    stubbed."""
    agent = MagicMock(role="pr_reviewer", slug="be-pr-reviewer")
    cc: Any = c
    cc._gate_preflight = AsyncMock(
        return_value=(
            t_before,
            agent,
            "pr_reviewer",
            {},
            spec_module.Context(actor_id=reviewer_id),
        )
    )
    cc._gate_tracing = AsyncMock(return_value=None)
    cc._pr_pass_blocked = AsyncMock(return_value=(None, None))
    cc._stamp_gate_findings_verified_or_rejection = AsyncMock(return_value=None)
    cc._record_gate_verdict = MagicMock()
    cc._post_gate_review_to_pr = AsyncMock()
    # _authoring_providers walks the task's descendants for authoring agents;
    # no descendants seeded here -> falls back to [ANTHROPIC], matching the
    # single-authoring-provider assumption these tests already model.
    c.task.get_all_descendants = AsyncMock(return_value=[])
    runner = MagicMock()
    runner.run_intent = AsyncMock(return_value=t_after)
    cc._verb_runner = MagicMock(return_value=runner)


def _t(*, task_id: UUID | None = None, priority: int = 2) -> MagicMock:
    return MagicMock(
        id=task_id or uuid4(),
        assigned_to=None,
        pr_number=42,
        parent_task_id=uuid4(),
        status="awaiting_pr_review",
        branch_name="feature/backend/abc123",
        priority=priority,
        adds_migration=False,
        touches_shared=False,
        title="Rotate the encryption key",
        description="Rotate the Fernet secret used to encrypt git tokens.",
        # A real int, not an auto-vivified MagicMock — `next_round` computes
        # `revision_count + 1`, and MagicMock's default `__index__`/`__int__`
        # both happen to coerce to 1 too, which would mask a real regression.
        revision_count=0,
    )


async def _seed_task_row(session: AsyncSession, task_id: UUID) -> None:
    """Seed a real ``tasks`` row so the FK'd ``task_review_findings`` insert
    the real path exercises has something to reference."""
    agent = AgentTable(
        id=uuid4(),
        name="Second Review Gate Test Agent",
        slug=f"second-review-gate-test-{uuid4().hex[:8]}",
        role=AgentRole.PR_REVIEWER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="second review gate test",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    task = TaskTable(
        id=task_id,
        title="seed task for second-review gate wiring",
        description="seed",
        acceptance_criteria=["seeded"],
        status=TaskStatus.AWAITING_PR_REVIEW,
        priority=0,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
        created_by=agent.id,
    )
    session.add(task)
    await session.flush()


# ---------------------------------------------------------------------------
# Flag OFF (default) — byte-for-byte unchanged regression pin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_pass_evidence_is_none_and_diff_never_fetched_when_flag_off() -> None:
    reviewer_id = uuid4()
    task_id = uuid4()
    t_before = _t(task_id=task_id)
    t_after = _t(task_id=task_id)
    t_after.status = "awaiting_pm_review"
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    assert settings.cross_vendor_review_enabled is False  # default

    env = await c.pr_pass(reviewer_id, task_id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    assert env.evidence is None
    c.git.diff.assert_not_called()


# ---------------------------------------------------------------------------
# Flag ON + above threshold — findings insertion + envelope recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_pass_inserts_second_review_finding_under_dedicated_origin(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    """AC: a second-pass flagged miss produces a task_review_findings row
    with the dedicated origin, in the existing per-finding shape — and the
    envelope records which provider ran the pass."""
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_max_priority", 1)
    task_id = uuid4()
    await _seed_task_row(db_session, task_id)

    reviewer_id = uuid4()
    t_before = _t(task_id=task_id, priority=0)  # P0 — above the risk threshold
    t_after = _t(task_id=task_id)
    t_after.status = "awaiting_pm_review"
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    cc: Any = c
    cc.task.session = db_session
    cc.git.diff = AsyncMock(return_value="diff --git a/foo.py b/foo.py")

    async def _fake_run_second_review_for_gate(*_a: object, **_kw: object) -> object:
        finding = Finding(
            file="roboco/services/second_review.py",
            line=100,
            severity=Severity.MAJOR,
            expected="raises on an unresolvable provider",
            actual="silently returns None instead of raising",
        )
        return SecondReviewOutcome.ran(ModelProvider.GROK, [finding])

    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.pr_gate.run_second_review_for_gate",
        _fake_run_second_review_for_gate,
    )

    env = await c.pr_pass(reviewer_id, task_id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.evidence == {
        "second_review": {
            "ran": True,
            "provider": "grok",
            "findings_count": 1,
        }
    }
    repo = ReviewFindingsRepository(db_session)
    rows = await repo.list_for_task(task_id)
    assert len(rows) == 1
    assert rows[0].round == 1
    assert rows[0].origin == "second_review"
    assert rows[0].file == "roboco/services/second_review.py"
    assert rows[0].actual == "silently returns None instead of raising"
    # Pre-addressed, not open -- this origin lands on an already-assembled
    # task with no dev to route an open finding's resolution to.
    assert rows[0].status == "addressed"


@pytest.mark.asyncio
async def test_pr_pass_never_blocks_on_single_provider_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: a single-provider-enabled skip from the sibling service never
    blocks the gate — pr_pass proceeds exactly as the existing single-review
    path, and the envelope just records the skip + reason."""
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_max_priority", 1)

    class _SkipService:
        async def resolve_second_reviewer(
            self, _authoring_provider: ModelProvider
        ) -> SecondReviewSelection:
            return SecondReviewSelection.skip("only anthropic enabled fleet-wide")

    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.second_review_gate.get_second_review_service",
        lambda _session: _SkipService(),
    )

    reviewer_id = uuid4()
    task_id = uuid4()
    t_before = _t(task_id=task_id, priority=0)
    t_after = _t(task_id=task_id)
    t_after.status = "awaiting_pm_review"
    c = _make_choreographer()
    _stub_gate_path(c, reviewer_id=reviewer_id, t_before=t_before, t_after=t_after)
    cc: Any = c
    cc.git.diff = AsyncMock(return_value="diff --git a/foo.py b/foo.py")

    env = await c.pr_pass(reviewer_id, task_id, "Looks clean to me.")

    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pm_review"
    assert env.evidence == {
        "second_review": {
            "ran": False,
            "skip_reason": "only anthropic enabled fleet-wide",
        }
    }
