"""_poll_external_prs_once must not hold a DB connection across GitHub's
list-open-PRs HTTP call, per project, the 2026-07-29 pool-exhaustion
incident class. Mirrors the ci-watch / env-sync / dep-update engine fixes.

The pool-checkout assertion below runs against a REAL Postgres pool and the
REAL GitService call path (``resolve_repo_and_token`` + ``list_open_prs_for``)
- only the outermost network boundary (``_fetch_open_prs``) is faked. A
prior version of this test monkeypatched ``GitService.list_open_prs`` itself
with a fake that performed no DB reads, which passed whether or not the fix
actually released the connection: ``list_open_prs``'s own first statements
are fresh ``get_by_slug``/token DB reads, immediately followed by the HTTP
call, so stubbing the whole method away hides exactly the bug this test
exists to catch (proven empirically against a real pool: a commit drops
``checkedout()`` to 0, and the very next ``session.execute()`` - here, the
DB read inside ``list_open_prs``/``resolve_repo_and_token`` - puts it back
to 1, held through the HTTP call).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.git import GitService
from roboco.services.project import ProjectService
from roboco.utils.crypto import encrypt_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.pool import QueuePool

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
ZERO = 0
ONE = 1


def _checkedout(engine: object) -> int:
    """``engine.pool.checkedout()`` - a QueuePool-only method typed on the
    concrete class, not the ``Pool`` base ``AsyncEngine.pool`` is annotated
    with; asyncpg always backs onto AsyncAdaptedQueuePool at runtime. The
    cast's target types are string literals, so no runtime import is needed."""
    return cast("QueuePool", cast("AsyncEngine", engine).pool).checkedout()


async def _seed(session: AsyncSession, slug: str) -> ProjectTable:
    """Real, committed rows: a system agent + one project with a real
    (Fernet-encrypted) token, so ``resolve_repo_and_token`` genuinely
    resolves and the loop reaches the HTTP boundary for real."""
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
    project = ProjectTable(
        name="Poll Pool Release Test",
        slug=slug,
        git_url="https://github.com/x/roboco.git",
        git_token_encrypted=encrypt_token("ghp_fake_test_token"),
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
    )
    session.add(project)
    await session.commit()
    return project


@pytest.mark.asyncio
async def test_poll_external_prs_once_releases_pool_before_http_call(
    _test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool connection checked out by the project/token DB reads must be
    released (``checkedout() == 0``) at the moment the real HTTP boundary
    (``_fetch_open_prs``) is entered - never held through it. Fails against
    the pre-split call shape (release, then straight into the unsplit
    ``list_open_prs``, whose own DB reads re-check-out a connection and hold
    it through the HTTP call)."""
    slug = f"poll-pool-release-{uuid4().hex[:8]}"
    engine = create_async_engine(
        _test_database_url, pool_size=1, max_overflow=0, future=True
    )
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as seed_session:
            project = await _seed(seed_session, slug)

        # Isolate from any other project row the shared session-scoped test
        # DB may carry: list_all only ever returns this test's own project.
        monkeypatch.setattr(
            ProjectService, "list_all", AsyncMock(return_value=[project])
        )

        checkouts: list[int] = []
        calls = 0

        async def _fake_fetch_open_prs(
            _self: GitService, _project_slug: str, _repo_ref: Any, _git_token: str
        ) -> list[dict[str, Any]]:
            nonlocal calls
            calls += 1
            checkouts.append(_checkedout(engine))
            return []

        monkeypatch.setattr(GitService, "_fetch_open_prs", _fake_fetch_open_prs)

        async with factory() as db:
            orch = AgentOrchestrator.__new__(AgentOrchestrator)
            ingested = await orch._poll_external_prs_once(db)

        assert ingested == ZERO
        assert calls == ONE
        assert checkouts == [ZERO]
    finally:
        async with factory() as cleanup:
            row = (
                await cleanup.execute(
                    select(ProjectTable).where(ProjectTable.slug == slug)
                )
            ).scalar_one_or_none()
            if row is not None:
                await cleanup.delete(row)
                await cleanup.commit()
        await engine.dispose()
