"""_sweep_member_performance — the granular per-member rollup, against real PG.

Seeds a day of spawn sessions + completed tasks + the new audit events
(escalated / unblocked_dependents / agent.idle / qa pass+fail / CEO decisions),
runs the sweep, and asserts the agent + CEO rows — plus idempotency (a second
sweep overwrites, never doubles).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentSpawnSessionTable,
    AgentTable,
    AuditLogTable,
    MemberPerformanceDailyTable,
    ProjectTable,
    TaskTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.runtime.orchestrator import AgentOrchestrator
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# Yesterday at noon UTC: safely inside the 7-day window and far from midnight,
# so started_at / completed_at (+1h) and all audit events share ONE date
# (the sweep legitimately splits cross-midnight work across days — not under test
# here). This keeps the seeded member's rollup on a single row.
_BASE = (datetime.now(UTC) - timedelta(days=1)).replace(
    hour=12, minute=0, second=0, microsecond=0
)

_ACTIVE_SECONDS = 600
_APPROVAL_DWELL_SECONDS = 300
_COMPLETED_TASKS = 2
_BLOCKED_OTHERS = 2
_QA_TOTAL = 2


class _NoCommitSession:
    """Delegates to the real test session but turns commit() into flush() so the
    per-test rollback isolation holds while the sweep still 'commits'."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def commit(self) -> None:
        await self._real.flush()


def _agent(role: AgentRole, slug: str) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


def _audit(
    target_id: Any,
    event_type: str,
    ts: datetime,
    *,
    agent_id: Any = None,
    details: dict[str, Any] | None = None,
) -> AuditLogTable:
    return AuditLogTable(
        id=uuid4(),
        event_type=event_type,
        agent_id=agent_id,
        target_type="task",
        target_id=target_id,
        severity="info",
        details=details or {},
        timestamp=ts,
    )


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> AsyncIterator[dict]:
    dev = _agent(AgentRole.DEVELOPER, f"be-dev-{uuid4().hex[:6]}")
    qa = _agent(AgentRole.QA, f"be-qa-{uuid4().hex[:6]}")
    db_session.add_all([dev, qa])
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
    )
    db_session.add(project)
    await db_session.flush()

    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.COMPLETED,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=dev.id,
        assigned_to=dev.id,
        revision_count=1,
        estimated_complexity=Complexity.MEDIUM,
        started_at=_BASE,
        completed_at=_BASE + timedelta(hours=1),
    )
    blocker = TaskTable(
        id=uuid4(),
        title="b",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.COMPLETED,
        team=Team.BACKEND,
        project_id=project.id,
        created_by=dev.id,
        assigned_to=dev.id,
        estimated_complexity=Complexity.MEDIUM,
        started_at=_BASE,
        completed_at=_BASE + timedelta(hours=1),
    )
    db_session.add_all([task, blocker])
    await db_session.flush()

    db_session.add(
        AgentSpawnSessionTable(
            id=uuid4(),
            agent_slug=dev.slug,
            team="backend",
            role="developer",
            model="claude",
            task_id=str(task.id),
            started_at=_BASE,
            ended_at=_BASE + timedelta(seconds=600),
            turns=5,
            tool_calls=10,
            tokens_input=100,
            tokens_output=80,
            estimated_cost_usd=1.5,
        )
    )
    db_session.add_all(
        [
            _audit(
                task.id,
                "task.qa_fail",
                _BASE,
                agent_id=qa.id,
                details={"agent_role": "qa"},
            ),
            _audit(
                task.id,
                "task.awaiting_documentation",
                _BASE + timedelta(minutes=1),
                agent_id=qa.id,
                details={"agent_role": "qa"},
            ),
            _audit(
                task.id,
                "task.escalated",
                _BASE,
                details={"escalator_slug": dev.slug, "target_slug": qa.slug},
            ),
            _audit(
                blocker.id, "task.unblocked_dependents", _BASE, details={"count": 2}
            ),
            _audit(
                dev.id,
                "agent.idle",
                _BASE + timedelta(minutes=5),
                agent_id=dev.id,
                details={"agent_slug": dev.slug},
            ),
            # CEO decision: approval + god-mode (to_status is what the pairing reads).
            _audit(
                task.id,
                "task.awaiting_ceo_approval",
                _BASE,
                details={"to_status": "awaiting_ceo_approval", "agent_role": "main_pm"},
            ),
            _audit(
                task.id,
                "task.completed",
                _BASE + timedelta(seconds=300),
                details={"to_status": "completed", "agent_role": "ceo"},
            ),
        ]
    )
    await db_session.flush()
    yield {"db": db_session, "dev": dev.slug, "qa": qa.slug}


async def _rows(db: AsyncSession, slug: str) -> list[Any]:
    return list(
        (
            await db.execute(
                select(MemberPerformanceDailyTable).where(
                    MemberPerformanceDailyTable.agent_slug == slug
                )
            )
        )
        .scalars()
        .all()
    )


async def _run_sweep(db: AsyncSession) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)

    @asynccontextmanager
    async def _cm() -> Any:
        yield _NoCommitSession(db)

    with patch("roboco.db.base.get_session_factory", return_value=_cm):
        await orch._sweep_member_performance()


@pytest.mark.asyncio
async def test_sweep_populates_agent_and_ceo_rows(seeded: dict) -> None:
    db = seeded["db"]
    await _run_sweep(db)

    dev_rows = await _rows(db, seeded["dev"])
    assert len(dev_rows) == 1
    dev = dev_rows[0]
    assert dev.member_kind == "agent"
    assert dev.active_runtime_seconds == _ACTIVE_SECONDS
    assert (dev.turns, dev.tool_calls, dev.tokens) == (5, 10, 180)
    assert dev.cost_usd == pytest.approx(1.5)
    assert dev.tasks_completed == _COMPLETED_TASKS  # task + blocker
    assert dev.tasks_first_pass == 1  # blocker had 0 revisions
    assert dev.revisions_received == 1
    assert dev.escalations == 1
    assert dev.blocked_others == _BLOCKED_OTHERS
    assert dev.idle_seconds > 0

    qa_rows = await _rows(db, seeded["qa"])
    assert len(qa_rows) == 1
    qa = qa_rows[0]
    assert qa.revisions_caused == 1  # the qa_fail
    assert qa.qa_reviews_passed == 1
    assert qa.qa_reviews_total == _QA_TOTAL  # 1 pass + 1 fail

    ceo_rows = await _rows(db, "")
    assert len(ceo_rows) == 1
    ceo = ceo_rows[0]
    assert ceo.member_kind == "ceo"
    assert ceo.godmode_actions == 1
    assert ceo.ceo_approval_dwell_seconds == _APPROVAL_DWELL_SECONDS


@pytest.mark.asyncio
async def test_sweep_is_idempotent(seeded: dict) -> None:
    db = seeded["db"]
    await _run_sweep(db)
    await _run_sweep(db)  # second pass must overwrite, not double

    dev = (await _rows(db, seeded["dev"]))[0]
    assert dev.active_runtime_seconds == _ACTIVE_SECONDS  # not 1200
    assert dev.tasks_completed == _COMPLETED_TASKS  # not 4
    assert dev.escalations == 1
    assert len(await _rows(db, seeded["dev"])) == 1
