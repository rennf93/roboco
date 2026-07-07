"""soft_block_task_for_agent forwards the typed resolver_type unchanged.

The schema 422s a typo at the boundary; this test pins the handler side —
a HUMAN request stays HUMAN, not silently downgraded to AGENT.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    BlockerResolverType,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext
from roboco.models.task import TaskCreateRequest
from roboco.services.task import SoftBlockInput, TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def soft_block_setup(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    async def _no_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _no_commit)

    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
    }


@pytest.mark.asyncio
async def test_soft_block_handler_preserves_human_resolver(
    soft_block_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = soft_block_setup["svc"]
    agent_id = soft_block_setup["agent_id"]
    task = await svc.create(
        TaskCreateRequest(
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            team=Team.BACKEND,
            created_by=agent_id,
            project_id=soft_block_setup["project_id"],
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
        )
    )
    task.assigned_to = agent_id
    task.status = TaskStatus.IN_PROGRESS
    await db_session.flush()

    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_notification_delivery_service",
        lambda _s: AsyncMock(),
    )

    req = SoftBlockInput(
        blocker_type="external",
        reason="r",
        what_needed="w",
        resolver_type=BlockerResolverType.HUMAN,
    )
    out = await svc.soft_block_task_for_agent(
        task.id,
        AgentContext(
            agent_id=agent_id, role=AgentRole.DEVELOPER, team=Team.BACKEND, slug="s"
        ),
        req,
    )
    assert out.blocker_resolver_type == BlockerResolverType.HUMAN
