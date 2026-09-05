"""``roboco/services/gateway/choreographer/second_review_gate.py`` — the
cross-vendor second-review pass's gate-facing outcome + ledger insertion.

Pins:
  * not-applicable (flag off / below threshold) short-circuits before ever
    touching the resolver or the session — the flag-off regression the
    in-path gate depends on.
  * a resolver skip (single provider enabled) never raises and reports
    ``ran=False`` with the skip reason.
  * a resolved provider runs the injected runner and reports ``ran=True``
    with the provider + finding count.
  * ``insert_second_review_findings`` writes real rows into the existing
    ``task_review_findings`` ledger under the dedicated ``second_review``
    origin, in the same per-finding shape every other producer uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, TaskTable
from roboco.foundation.policy.content import Finding, Severity
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    ModelProvider,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.task import Task
from roboco.services.gateway.choreographer.second_review_gate import (
    SECOND_REVIEW_ORIGIN,
    SecondReviewOutcome,
    insert_second_review_findings,
    run_second_review_for_gate,
)
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.second_review import SecondReviewSelection

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_FINDING_LINE = 42


class _PoisonSession:
    """A session stub that fails the test if anything on it is touched."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"session.{name} must not be accessed when not applicable")


def _task(
    *,
    priority: int = 2,
    adds_migration: bool = False,
    touches_shared: bool = False,
) -> Task:
    return Task(
        title="Add user lookup endpoint",
        description="Add GET /v1/users/{id} returning user JSON.",
        acceptance_criteria=["returns 404 for unknown user"],
        created_by=uuid4(),
        team=Team.BACKEND,
        priority=priority,
        adds_migration=adds_migration,
        touches_shared=touches_shared,
    )


# ---------------------------------------------------------------------------
# run_second_review_for_gate — applicability + skip + resolved paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_applicable_when_flag_off_never_touches_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", False)
    outcome = await run_second_review_for_gate(
        _PoisonSession(),
        _task(priority=0, adds_migration=True),
        "diff text",
        authoring_provider=ModelProvider.ANTHROPIC,
    )
    assert outcome == SecondReviewOutcome.not_applicable()
    assert outcome.as_evidence() is None


@pytest.mark.asyncio
async def test_not_applicable_below_risk_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    outcome = await run_second_review_for_gate(
        _PoisonSession(),
        _task(priority=3),  # routine, non-migration, non-shared task
        "diff text",
        authoring_provider=ModelProvider.ANTHROPIC,
    )
    assert outcome.applicable is False
    assert outcome.as_evidence() is None


@pytest.mark.asyncio
async def test_resolver_skip_never_blocks_and_reports_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)

    class _SkipService:
        async def resolve_second_reviewer(
            self, _authoring_provider: ModelProvider
        ) -> SecondReviewSelection:
            return SecondReviewSelection.skip("only anthropic enabled fleet-wide")

    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.second_review_gate.get_second_review_service",
        lambda _session: _SkipService(),
    )

    outcome = await run_second_review_for_gate(
        object(),
        _task(priority=0),
        "diff text",
        authoring_provider=ModelProvider.ANTHROPIC,
    )

    assert outcome.applicable is True
    assert outcome.skipped is True
    assert outcome.provider is None
    assert outcome.as_evidence() == {
        "second_review": {
            "ran": False,
            "skip_reason": "only anthropic enabled fleet-wide",
        }
    }


@pytest.mark.asyncio
async def test_resolved_provider_runs_injected_runner_and_reports_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)

    class _ResolvedService:
        async def resolve_second_reviewer(
            self, _authoring_provider: ModelProvider
        ) -> SecondReviewSelection:
            return SecondReviewSelection.resolved(ModelProvider.GROK)

    monkeypatch.setattr(
        "roboco.services.gateway.choreographer.second_review_gate.get_second_review_service",
        lambda _session: _ResolvedService(),
    )
    found = [
        Finding(
            file="roboco/api/routes/users.py",
            line=_FINDING_LINE,
            severity=Severity.MAJOR,
            expected="returns 404 for unknown user",
            actual="raises an unhandled KeyError instead",
        )
    ]

    async def _runner(
        _task: Task, _diff: str, provider: ModelProvider
    ) -> list[Finding]:
        assert provider == ModelProvider.GROK
        return found

    outcome = await run_second_review_for_gate(
        object(),
        _task(priority=0),
        "diff text",
        authoring_provider=ModelProvider.ANTHROPIC,
        runner=_runner,
    )

    assert outcome.applicable is True
    assert outcome.skipped is False
    assert outcome.provider == ModelProvider.GROK
    assert outcome.findings == tuple(found)
    assert outcome.as_evidence() == {
        "second_review": {"ran": True, "provider": "grok", "findings_count": 1}
    }


# ---------------------------------------------------------------------------
# insert_second_review_findings — the ledger-insertion path (real DB)
# ---------------------------------------------------------------------------


async def _seed_agent(session: AsyncSession) -> UUID:
    agent = AgentTable(
        id=uuid4(),
        name="Second Review Test Agent",
        slug=f"second-review-test-{uuid4().hex[:8]}",
        role=AgentRole.PR_REVIEWER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="second review test",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return UUID(str(agent.id))


async def _seed_task(session: AsyncSession, created_by: UUID) -> UUID:
    task = TaskTable(
        id=uuid4(),
        title="second review seed task",
        description="seed",
        acceptance_criteria=["seeded"],
        status=TaskStatus.AWAITING_PR_REVIEW,
        priority=0,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
        created_by=created_by,
    )
    session.add(task)
    await session.flush()
    return UUID(str(task.id))


@pytest.mark.asyncio
async def test_insert_second_review_findings_writes_dedicated_origin_row(
    db_session: AsyncSession,
) -> None:
    """AC: a second-pass flagged miss produces a task_review_findings row
    with the dedicated origin, in the existing per-finding shape."""
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)
    finding = Finding(
        file="roboco/api/routes/users.py",
        line=_FINDING_LINE,
        severity=Severity.MAJOR,
        expected="returns 404 for unknown user",
        actual="raises an unhandled KeyError instead",
    )

    rows = await insert_second_review_findings(
        db_session,
        task_id=task_id,
        round=1,
        author_slug="be-pr-reviewer",
        findings=[finding],
    )

    assert len(rows) == 1
    repo = ReviewFindingsRepository(db_session)
    ledger_rows = await repo.list_for_task(task_id)
    assert len(ledger_rows) == 1
    row = ledger_rows[0]
    assert row.origin == SECOND_REVIEW_ORIGIN
    assert row.file == "roboco/api/routes/users.py"
    assert row.line == _FINDING_LINE
    assert row.severity == "major"
    assert row.actual == "raises an unhandled KeyError instead"
    assert row.status == "open"


@pytest.mark.asyncio
async def test_insert_second_review_findings_noop_on_empty_list(
    db_session: AsyncSession,
) -> None:
    agent_id = await _seed_agent(db_session)
    task_id = await _seed_task(db_session, agent_id)

    rows = await insert_second_review_findings(
        db_session, task_id=task_id, round=1, author_slug="be-pr-reviewer", findings=[]
    )

    assert rows == []
    repo = ReviewFindingsRepository(db_session)
    assert await repo.list_for_task(task_id) == []
