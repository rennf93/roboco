"""TaskService.create_subtask must raise on empty acceptance_criteria.

The pre-migration silent fallback at services/task.py:5061-5062
substituted ['completed and reviewed by assignee']. After this task,
that fallback is gone — empty input raises TaskCompletenessError, and
the legacy placeholder phrase itself is denylisted by
foundation.policy.task_completeness so callers cannot smuggle it in
either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation.policy.task_completeness import TaskCompletenessError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskType
from roboco.models.task import TaskCreateRequest
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def task_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Minimal seed: one developer + one project, returning a TaskService."""
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
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="No-Fallback-Proj",
        slug=f"no-fb-{uuid4().hex[:8]}",
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


def _request(setup: dict, **overrides: object) -> TaskCreateRequest:
    """Build a TaskCreateRequest with sensible defaults; overrides win.

    `TaskCreateRequest` is a plain dataclass — no Pydantic validation —
    so a caller CAN construct it with `acceptance_criteria=[]` and the
    only thing standing between that input and a skeleton row in the DB
    is the service-layer check we are adding in this task.
    """
    parent_id = overrides.pop("parent_task_id", uuid4())
    return TaskCreateRequest(
        title=overrides.pop("title", "ok"),
        description=overrides.pop(
            "description",
            "A description that's at least twenty chars long for the constraint.",
        ),
        acceptance_criteria=overrides.pop("acceptance_criteria", ["ac"]),
        team=overrides.pop("team", Team.BACKEND),
        created_by=setup["agent_id"],
        project_id=setup["project_id"],
        parent_task_id=parent_id,
        task_type=overrides.pop("task_type", TaskType.CODE),
        nature=overrides.pop("nature", TaskNature.TECHNICAL),
        estimated_complexity=overrides.pop("estimated_complexity", Complexity.MEDIUM),
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_create_subtask_raises_on_empty_acceptance_criteria(
    task_setup: dict,
) -> None:
    """Empty list → TaskCompletenessError (no silent placeholder substitution)."""
    svc: TaskService = task_setup["svc"]
    request = _request(task_setup, acceptance_criteria=[])
    with pytest.raises(TaskCompletenessError) as exc_info:
        await svc.create_subtask(request)
    assert "acceptance_criteria" in exc_info.value.missing


@pytest.mark.asyncio
async def test_create_subtask_rejects_legacy_fallback_phrase(
    task_setup: dict,
) -> None:
    """The legacy fallback string is denylisted at the policy layer.

    Even if a caller hand-types the exact phrase the deleted fallback used to
    insert, the completeness check rejects it as a known evasion.
    """
    svc: TaskService = task_setup["svc"]
    request = _request(
        task_setup,
        acceptance_criteria=["completed and reviewed by assignee"],
    )
    with pytest.raises(TaskCompletenessError) as exc_info:
        await svc.create_subtask(request)
    assert "acceptance_criteria" in exc_info.value.missing
