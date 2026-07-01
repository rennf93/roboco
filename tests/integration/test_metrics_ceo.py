"""get_ceo_scorecard — the human CEO as a measured member (audit-log only).

Seeds CEO-attributed audit transitions and asserts approval dwell (incl. the
coordination-root reject that lands in `pending`), unblock dwell, and the
god-mode action count. The CEO never runs an LLM, so this reads only audit_log
(agent_role='ceo').
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AuditLogTable
from roboco.services.metrics import MetricsService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime.now(UTC)


def _audit(
    task_id: Any,
    status: str,
    ts: datetime,
    *,
    agent_role: str | None = None,
) -> AuditLogTable:
    return AuditLogTable(
        id=uuid4(),
        event_type=f"task.{status}",
        agent_id=None,
        target_type="task",
        target_id=task_id,
        severity="info",
        details={"to_status": status, "from_status": "prev", "agent_role": agent_role},
        timestamp=ts,
    )


@pytest_asyncio.fixture
async def svc(db_session: AsyncSession) -> AsyncIterator[MetricsService]:
    yield MetricsService(db_session)


@pytest.mark.asyncio
async def test_empty_window_returns_zeros(svc: MetricsService) -> None:
    card = await svc.get_ceo_scorecard(days=30)
    assert card.approval_count == 0
    assert card.unblock_count == 0
    assert card.godmode_actions == 0
    assert card.approval_p50_seconds == 0.0


@pytest.mark.asyncio
async def test_approval_unblock_dwell_and_godmode(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    base = _NOW - timedelta(hours=2)
    t_approve = uuid4()
    t_reject = uuid4()
    t_block = uuid4()
    db_session.add_all(
        [
            # Approval: awaiting -> completed(ceo) after 300s.
            _audit(t_approve, "awaiting_ceo_approval", base),
            _audit(
                t_approve, "completed", base + timedelta(seconds=300), agent_role="ceo"
            ),
            # Coordination-root reject: awaiting -> pending(ceo) after 120s.
            _audit(t_reject, "awaiting_ceo_approval", base),
            _audit(
                t_reject, "pending", base + timedelta(seconds=120), agent_role="ceo"
            ),
            # Unblock: blocked -> in_progress(ceo) after 600s.
            _audit(t_block, "blocked", base),
            _audit(
                t_block, "in_progress", base + timedelta(seconds=600), agent_role="ceo"
            ),
        ]
    )
    await db_session.flush()

    card = await svc.get_ceo_scorecard(days=30)
    # Two approval decisions (completed + coordination pending), median of 300/120.
    expected_approvals = 2
    assert card.approval_count == expected_approvals
    assert card.approval_p50_seconds == pytest.approx(210.0)
    # One unblock, 600s.
    assert card.unblock_count == 1
    assert card.unblock_p50_seconds == pytest.approx(600.0)
    # God-mode = every ceo-attributed transition: completed + pending + in_progress.
    expected_godmode = 3
    assert card.godmode_actions == expected_godmode


@pytest.mark.asyncio
async def test_non_ceo_transitions_are_not_counted(
    svc: MetricsService, db_session: AsyncSession
) -> None:
    tid = uuid4()
    db_session.add_all(
        [
            _audit(tid, "awaiting_ceo_approval", _NOW - timedelta(minutes=10)),
            # A QA fail (not the CEO) must not count as an approval or god-mode.
            _audit(tid, "needs_revision", _NOW - timedelta(minutes=5), agent_role="qa"),
        ]
    )
    await db_session.flush()
    card = await svc.get_ceo_scorecard(days=30)
    assert card.approval_count == 0
    assert card.godmode_actions == 0
