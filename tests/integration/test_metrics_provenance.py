"""MetricsService.get_provenance_metrics: human vs agent origination.

Seeds root/child hierarchies against a real Postgres and asserts the report
classifies EVERY task (root or delegated subtask) by its root ancestor's
source, not its own. This is the fix for ``tasks.source`` misreporting a
delegated subtask's origin as "manual" no matter what actually kicked it off.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.metrics import ProvenanceReport
from roboco.services.metrics import MetricsService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime.now(UTC)
_EXPECTED_TOTAL = 5
_EXPECTED_HUMAN = 2
_EXPECTED_AGENT = 3


def _agent() -> AgentTable:
    slug = f"system-{uuid4().hex[:6]}"
    return AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


def _task(
    project_id: Any,
    created_by: Any,
    *,
    source: str,
    parent_task_id: Any = None,
    created_at: datetime = _NOW,
) -> TaskTable:
    return TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.PENDING,
        team=Team.BACKEND,
        project_id=project_id,
        created_by=created_by,
        estimated_complexity=Complexity.MEDIUM,
        source=source,
        parent_task_id=parent_task_id,
        created_at=created_at,
    )


@pytest_asyncio.fixture
async def provenance_metrics_setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    agent = _agent()
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": MetricsService(db_session),
        "db": db_session,
        "project_id": project.id,
        "agent_id": agent.id,
    }


@pytest.mark.asyncio
async def test_provenance_breakdown_walks_delegated_children_to_root(
    provenance_metrics_setup: dict,
) -> None:
    """``get_provenance_metrics`` is an unscoped, org-wide window count — a
    shared suite DB can hold other tests' committed tasks inside the same
    30-day window, so every assertion here is a DELTA around this test's own
    5 rows, not an absolute total."""
    db = provenance_metrics_setup["db"]
    pid = provenance_metrics_setup["project_id"]
    aid = provenance_metrics_setup["agent_id"]
    svc = provenance_metrics_setup["svc"]

    baseline = await svc.get_provenance_metrics(days=30)

    human_root = _task(pid, aid, source="manual")
    db.add(human_root)
    await db.flush()
    # Delegated child of a human root, own source "manual", correctly
    # counted human via the root walk (would be counted human either way,
    # but proves the walk doesn't double count / miscount roots).
    human_child = _task(pid, aid, source="manual", parent_task_id=human_root.id)
    db.add(human_child)

    mirror_root = _task(pid, aid, source="mirror")
    db.add(mirror_root)
    await db.flush()
    # Delegated children of an agent-originated root, own source "manual"
    # (the bug), must be counted agent-authored via the root walk.
    mirror_child_1 = _task(pid, aid, source="manual", parent_task_id=mirror_root.id)
    mirror_child_2 = _task(pid, aid, source="manual", parent_task_id=mirror_root.id)
    db.add_all([mirror_child_1, mirror_child_2])
    await db.flush()

    report = await svc.get_provenance_metrics(days=30)

    # 5 rows added: human_root, human_child, mirror_root, mirror_child_1/2
    assert report.total - baseline.total == _EXPECTED_TOTAL
    assert (
        report.human_authored - baseline.human_authored == _EXPECTED_HUMAN
    )  # human_root + human_child
    assert (
        report.agent_authored - baseline.agent_authored == _EXPECTED_AGENT
    )  # mirror_root + its 2 children
    # human_rate's division math is DB-shape-independent — pinned directly
    # against the dataclass instead of the (potentially polluted) global
    # total this test can no longer assert exactly.
    isolated = ProvenanceReport(
        total=_EXPECTED_TOTAL,
        human_authored=_EXPECTED_HUMAN,
        agent_authored=_EXPECTED_AGENT,
    )
    assert isolated.to_dict()["human_rate"] == pytest.approx(
        _EXPECTED_HUMAN / _EXPECTED_TOTAL
    )


@pytest.mark.asyncio
async def test_provenance_excludes_tasks_outside_window(
    provenance_metrics_setup: dict,
) -> None:
    db = provenance_metrics_setup["db"]
    pid = provenance_metrics_setup["project_id"]
    aid = provenance_metrics_setup["agent_id"]
    svc = provenance_metrics_setup["svc"]

    baseline = await svc.get_provenance_metrics(days=30)

    old_root = _task(pid, aid, source="manual", created_at=_NOW - timedelta(days=60))
    db.add(old_root)
    recent_root = _task(pid, aid, source="manual")
    db.add(recent_root)
    await db.flush()

    report = await svc.get_provenance_metrics(days=30)
    # old_root falls outside the 30-day window; only recent_root is counted.
    assert report.total - baseline.total == 1
    assert report.human_authored - baseline.human_authored == 1


@pytest.mark.asyncio
async def test_provenance_empty_window_reports_zero(
    provenance_metrics_setup: dict,
) -> None:
    """This test adds no task of its own: the report must not move relative
    to baseline, and must stay internally consistent — an absolute zero
    can't be asserted in a suite-shared DB. ``human_rate``'s division-by-zero
    safety on a genuinely empty report is pinned directly against the
    dataclass instead."""
    svc = provenance_metrics_setup["svc"]
    baseline = await svc.get_provenance_metrics(days=30)
    report = await svc.get_provenance_metrics(days=30)
    assert report.total == baseline.total
    assert report.human_authored == baseline.human_authored
    assert report.agent_authored == baseline.agent_authored
    assert report.total == report.human_authored + report.agent_authored

    empty = ProvenanceReport(total=0, human_authored=0, agent_authored=0)
    assert empty.to_dict()["human_rate"] == 0.0
