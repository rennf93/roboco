"""Project registration triggers a best-effort conventions scaffold (flag-gated)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest_asyncio
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.project import ProjectCreate
from roboco.services.project import ProjectService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest
    from sqlalchemy.ext.asyncio import AsyncSession


class _SpyConventions:
    def __init__(self) -> None:
        self.scaffolded: list[ProjectTable] = []

    async def scaffold(self, project: ProjectTable) -> None:
        self.scaffolded.append(project)


class _BoomConventions:
    async def scaffold(self, _project: ProjectTable) -> None:
        raise RuntimeError("boom")


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    yield {"svc": ProjectService(db_session), "creator_id": agent.id}


def _payload() -> ProjectCreate:
    return ProjectCreate(
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://github.com/example/r.git",
        assigned_cell=Team.BACKEND,
    )


async def test_scaffold_invoked_when_flag_on(
    setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    spy = _SpyConventions()
    monkeypatch.setattr(
        "roboco.services.conventions.get_conventions_service", lambda _s: spy
    )
    project = await setup["svc"].create(_payload(), setup["creator_id"])
    assert spy.scaffolded == [project]


async def test_scaffold_not_invoked_when_flag_off(
    setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    spy = _SpyConventions()
    monkeypatch.setattr(
        "roboco.services.conventions.get_conventions_service", lambda _s: spy
    )
    await setup["svc"].create(_payload(), setup["creator_id"])
    assert spy.scaffolded == []


async def test_scaffold_failure_does_not_fail_registration(
    setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    monkeypatch.setattr(
        "roboco.services.conventions.get_conventions_service",
        lambda _s: _BoomConventions(),
    )
    project = await setup["svc"].create(_payload(), setup["creator_id"])
    assert project.id is not None
