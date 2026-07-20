"""CEO completion notification — granular effort breakdown (phase 6).

The pure body formatter (real effort vs wall-clock; degrades to wall-clock-only)
+ notify_ceo_of_completion end to end against real PG.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentSpawnSessionTable,
    AgentTable,
    NotificationTable,
    ProjectTable,
    TaskTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    NotificationType,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.metrics import TaskMetrics
from roboco.services.notification_delivery import (
    _format_completion_body,
    get_notification_delivery_service,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _metrics(**over: Any) -> TaskMetrics:
    base: dict[str, Any] = {
        "task_id": str(uuid4()),
        "active_runtime_seconds": 3600,
        "wall_clock_seconds": 7200,
        "turns": 42,
        "tool_calls": 99,
        "tokens": 1000,
        "cost_usd": 4.2,
        "revision_count": 2,
        "qa_fails": 1,
        "pr_fails": 1,
        "stints": 3,
        "stages": [],
    }
    base.update(over)
    return TaskMetrics(**base)


def test_format_body_with_metrics() -> None:
    body = _format_completion_body(
        cast("TaskTable", SimpleNamespace(title="Auth flow")), _metrics()
    )
    assert "Auth flow" in body
    assert "Active effort: 1.0h across 3 stint(s)" in body
    assert "42 turns" in body
    assert "Wall-clock: 2.0h" in body
    assert "2 (1 QA / 1 PR)" in body
    assert "$4.2" in body


def test_format_body_turns_na_when_zero() -> None:
    body = _format_completion_body(
        cast("TaskTable", SimpleNamespace(title="T")), _metrics(turns=0)
    )
    assert "n/a turns" in body  # pre-turns-migration / Grok


def test_format_body_degrades_without_metrics() -> None:
    body = _format_completion_body(cast("TaskTable", SimpleNamespace(title="T")), None)
    assert body == "Task 'T' completed."


@pytest_asyncio.fixture
async def env(db_session: AsyncSession) -> AsyncIterator[dict]:
    # The CEO is a singleton in the real system, and `_get_ceo_agent()` resolves
    # it by `role == CEO` with `scalar_one_or_none()`. The session-scoped test DB
    # is shared across the run, and a sibling real-DB test commits a role=CEO
    # agent (slug="ceo") without cleanup, so it can already be present here.
    # Reuse an existing CEO rather than inserting a second one — creating another
    # would both collide on the unique slug and make `_get_ceo_agent()` raise
    # MultipleResultsFound. Order-independent: in isolation we create one.
    existing_ceo = (
        (
            await db_session.execute(
                select(AgentTable).where(AgentTable.role == AgentRole.CEO)
            )
        )
        .scalars()
        .first()
    )
    ceo = existing_ceo or AgentTable(
        id=uuid4(),
        name="CEO",
        slug=f"ceo-{uuid4().hex[:6]}",
        role=AgentRole.CEO,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    if existing_ceo is None:
        db_session.add(ceo)
    dev = AgentTable(
        id=uuid4(),
        name="dev",
        slug=f"be-dev-{uuid4().hex[:6]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(dev)
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
    yield {"db": db_session, "ceo": ceo, "dev": dev, "project_id": project.id}


@pytest.mark.asyncio
async def test_notify_ceo_of_completion_creates_alert(env: dict) -> None:
    db = env["db"]
    base = datetime.now(UTC) - timedelta(hours=2)
    task = TaskTable(
        id=uuid4(),
        title="Ship it",
        description="d",
        acceptance_criteria=["ac"],
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        status=TaskStatus.COMPLETED,
        team=Team.BACKEND,
        project_id=env["project_id"],
        created_by=env["dev"].id,
        assigned_to=env["dev"].id,
        estimated_complexity=Complexity.MEDIUM,
        started_at=base,
        completed_at=base + timedelta(seconds=600),
    )
    db.add(task)
    await db.flush()
    db.add(
        AgentSpawnSessionTable(
            id=uuid4(),
            agent_slug=env["dev"].slug,
            team="backend",
            role="developer",
            model="claude",
            task_id=str(task.id),
            started_at=base,
            ended_at=base + timedelta(seconds=300),
            turns=7,
            tool_calls=12,
            tokens_input=10,
            tokens_output=5,
            estimated_cost_usd=0.5,
        )
    )
    await db.flush()

    delivery = get_notification_delivery_service(db)
    await delivery.notify_ceo_of_completion(task=task, task_id=cast("UUID", task.id))

    rows = (
        (
            await db.execute(
                select(NotificationTable).where(
                    NotificationTable.related_task_id == task.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    note = rows[0]
    assert note.type == NotificationType.ALERT
    assert env["ceo"].id in note.to_agents
    assert "Active effort" in note.body
    assert "Ship it" in note.subject


@pytest.mark.asyncio
async def test_get_ceo_agent_tolerates_duplicate_ceo_rows(env: dict) -> None:
    """A second role=CEO row (a real hazard: sibling tests commit one into the
    shared session DB, and nothing forbids two in prod) must not make
    `_get_ceo_agent()` raise MultipleResultsFound — it resolves the
    earliest-created CEO, mirroring `_get_auditor_agent`."""
    db = env["db"]
    later_ceo = AgentTable(
        id=uuid4(),
        name="CEO 2",
        slug=f"ceo-{uuid4().hex[:6]}",
        role=AgentRole.CEO,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
        created_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(later_ceo)
    await db.flush()

    delivery = get_notification_delivery_service(db)
    resolved = await delivery._get_ceo_agent()

    # Does not raise, and pins to the earliest-created (never the later row).
    assert resolved is not None
    assert resolved.id != later_ceo.id
