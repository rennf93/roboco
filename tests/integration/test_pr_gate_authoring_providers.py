"""``PRGateMixin._authoring_providers`` (round-1 pr_gate finding F-8b364f1c) --
resolves the REAL provider(s) that authored an assembled task's code from its
descendants' assignees, via the fleet's provider-routing seam, instead of
hardcoding ``ModelProvider.ANTHROPIC``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.db.tables import (
    AgentTable,
    ModelAssignmentTable,
    ProjectTable,
    ProviderConfigTable,
    TaskTable,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    AssignmentScope,
    ModelProvider,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _choreographer(db_session: AsyncSession) -> Choreographer:
    deps = ChoreographerDeps(
        task=TaskService(db_session),
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
    )
    return Choreographer(deps)


async def _seed_agent(
    session: AsyncSession, *, slug: str, role: AgentRole = AgentRole.DEVELOPER
) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=Team.BACKEND if role != AgentRole.SYSTEM else None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.flush()
    return agent


async def _seed_project(session: AsyncSession, created_by: Any) -> ProjectTable:
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=created_by,
    )
    session.add(project)
    await session.flush()
    return project


def _code_task(*, project_id: Any, created_by: Any, **overrides: Any) -> TaskTable:
    return TaskTable(
        id=uuid4(),
        title=overrides.pop("title", "task"),
        description="d",
        acceptance_criteria=["a"],
        status=overrides.pop("status", TaskStatus.AWAITING_PR_REVIEW),
        priority=0,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        team=Team.BACKEND,
        project_id=project_id,
        created_by=created_by,
        **overrides,
    )


@pytest.mark.asyncio
async def test_authoring_providers_resolves_distinct_providers_from_descendants(
    db_session: AsyncSession,
) -> None:
    """Two CODE descendants assigned to two different agents, one pinned to
    a non-Anthropic provider via a real AGENT_SLUG assignment, the other
    unassigned (legacy Anthropic fallback) -- both providers come back."""
    system = await _seed_agent(
        db_session, slug=f"system-{uuid4().hex[:6]}", role=AgentRole.SYSTEM
    )
    dev_a = await _seed_agent(db_session, slug=f"dev-a-{uuid4().hex[:6]}")
    dev_b = await _seed_agent(db_session, slug=f"dev-b-{uuid4().hex[:6]}")

    grok_provider = ProviderConfigTable(
        name="grok-test", type=ModelProvider.GROK, enabled=True
    )
    db_session.add(grok_provider)
    await db_session.flush()
    db_session.add(
        ModelAssignmentTable(
            scope=AssignmentScope.AGENT_SLUG,
            scope_value=dev_a.slug,
            provider_config_id=grok_provider.id,
            model_name="grok-build",
        )
    )
    await db_session.flush()

    project = await _seed_project(db_session, system.id)
    root = _code_task(project_id=project.id, created_by=system.id, title="root")
    db_session.add(root)
    await db_session.flush()
    leaf_a = _code_task(
        project_id=project.id,
        created_by=system.id,
        title="leaf a",
        status=TaskStatus.COMPLETED,
        parent_task_id=root.id,
        assigned_to=dev_a.id,
    )
    leaf_b = _code_task(
        project_id=project.id,
        created_by=system.id,
        title="leaf b",
        status=TaskStatus.COMPLETED,
        parent_task_id=root.id,
        assigned_to=dev_b.id,
    )
    db_session.add_all([leaf_a, leaf_b])
    await db_session.flush()

    c: Any = _choreographer(db_session)
    providers = await c._authoring_providers(root)

    assert set(providers) == {ModelProvider.GROK, ModelProvider.ANTHROPIC}


@pytest.mark.asyncio
async def test_authoring_providers_falls_back_to_anthropic_with_no_descendants(
    db_session: AsyncSession,
) -> None:
    """A task with no CODE descendants (e.g. not yet delegated) falls back
    to [ANTHROPIC], the fleet's always-enabled baseline."""
    system = await _seed_agent(
        db_session, slug=f"system-{uuid4().hex[:6]}", role=AgentRole.SYSTEM
    )
    project = await _seed_project(db_session, system.id)
    root = _code_task(project_id=project.id, created_by=system.id, title="root")
    db_session.add(root)
    await db_session.flush()

    c: Any = _choreographer(db_session)
    providers = await c._authoring_providers(root)

    assert providers == [ModelProvider.ANTHROPIC]
