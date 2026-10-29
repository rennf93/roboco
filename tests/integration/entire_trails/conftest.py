"""The ``bridge`` fixture: the trails bridge armed against the test
database, with the Entire CLI replayed (see ``support``)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest_asyncio
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation.identity import agent_for_slug
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.entire_credentials import get_entire_credentials_service
from roboco.services.entire_trails import drain
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.entire_trails.support import Bridge, ReplayEntire

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest


@pytest_asyncio.fixture
async def bridge(
    db_session: AsyncSession, _test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Bridge]:
    monkeypatch.setattr(settings, "entire_trails_enabled", True)
    engine = create_async_engine(_test_database_url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    @asynccontextmanager
    async def db_context(pool: str = "primary") -> AsyncIterator[AsyncSession]:
        _ = pool
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    replay = ReplayEntire()
    # The seeded senders and recipients the CEO alert resolves by slug.
    for slug, role in (("system", AgentRole.SYSTEM), ("ceo", AgentRole.CEO)):
        db_session.add(
            AgentTable(
                id=agent_for_slug(slug).uuid,
                name=slug,
                slug=slug,
                role=role,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt=slug,
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    monkeypatch.setattr(drain, "get_db_context", db_context)
    monkeypatch.setattr(drain, "run_entire", replay)
    agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="App",
        slug=f"app-{uuid4().hex[:6]}",
        git_url="https://github.com/acme/app.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
        entire_trails_enabled=True,
    )
    db_session.add(project)
    await get_entire_credentials_service(db_session).set_token("entire-token")
    await db_session.commit()
    try:
        yield Bridge(db_session, factory, replay, project, agent)
    finally:
        await engine.dispose()
