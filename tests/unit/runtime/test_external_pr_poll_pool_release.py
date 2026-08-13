"""_poll_external_prs_once must not hold a DB connection across GitHub's
list_open_prs HTTP call, per project, the 2026-07-29 pool-exhaustion
incident class. Mirrors the ci-watch / env-sync / dep-update engine fixes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.git import GitService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
ZERO = 0


@pytest_asyncio.fixture
async def db_session(_test_database_url: str) -> AsyncIterator[AsyncSession]:
    """Savepoint-isolated override, shadowing tests/conftest.py's plain-
    rollback fixture for THIS module only.

    ``_poll_external_prs_once`` now commits mid-cycle to release the pool
    connection before each project's ``list_open_prs`` GitHub call (the
    2026-07-29 pool-exhaustion fix). A plain rollback-at-teardown only undoes
    UNCOMMITTED state, so that mid-test commit would otherwise leak rows
    into the shared session-scoped test database. Mirrors
    tests/integration/services/conftest.py's fixture exactly.
    """
    engine = create_async_engine(_test_database_url, future=True)
    async with engine.connect() as connection:
        await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()
        await connection.rollback()
    await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    if await session.get(AgentTable, SYSTEM_UUID) is None:
        session.add(
            AgentTable(
                id=SYSTEM_UUID,
                name="system",
                slug="system",
                role=AgentRole.SYSTEM,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="x",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    session.add(
        ProjectTable(
            name="RoboCo",
            slug="roboco",
            git_url="https://github.com/x/roboco.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_poll_external_prs_once_releases_pool_before_list_open_prs(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit (releasing the pool connection) happens before each project's
    list_open_prs GitHub call. Without the release, the preceding work
    (get_project_service(...).list_all(...)) is a read-only SELECT, so
    commit is never called before list_open_prs runs; this fails without
    the fix."""
    await _seed(db_session)
    order: list[str] = []
    real_commit = db_session.commit

    async def _tracked_commit() -> None:
        order.append("commit")
        await real_commit()

    monkeypatch.setattr(db_session, "commit", _tracked_commit)

    async def _fake_list_open_prs(
        _self: GitService, _project_slug: str
    ) -> list[dict[str, Any]]:
        order.append("list_open_prs")
        return []

    monkeypatch.setattr(GitService, "list_open_prs", _fake_list_open_prs)

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    ingested = await orch._poll_external_prs_once(db_session)

    assert ingested == ZERO
    assert "commit" in order
    assert "list_open_prs" in order
    assert order.index("commit") < order.index("list_open_prs")
