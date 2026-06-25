"""External-PR review dedupe is repo-scoped (git_url), not project-scoped.

A monorepo registers several cell-projects on one repo. Ingesting the same PR
for a sibling project — or re-pointing an existing review task to a sibling —
must NOT open a second review (the duplicate the operator hit: PR #131 reviewed
once per cell-project after a re-point). Re-review on a new head SHA still works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
_REPO = "https://github.com/rennf93/guard-core-app"
_OTHER_REPO = "https://github.com/rennf93/other-app"


def _pr(head_sha: str, number: int = 131) -> dict[str, Any]:
    return {
        "number": number,
        "url": f"{_REPO}/pull/{number}",
        "title": "build(deps): bump the dependencies",
        "head_sha": head_sha,
    }


async def _seed(db: AsyncSession, slug: str, git_url: str) -> ProjectTable:
    if await db.get(AgentTable, SYSTEM_UUID) is None:
        db.add(
            AgentTable(
                id=SYSTEM_UUID,
                name="System",
                slug=f"system-{uuid4().hex[:8]}",
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
        await db.flush()
    project = ProjectTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        git_url=git_url,
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
    )
    db.add(project)
    await db.flush()
    return project


@pytest.mark.asyncio
async def test_sibling_project_same_pr_is_deduped(db_session: AsyncSession) -> None:
    fe = await _seed(db_session, "gca-frontend", _REPO)
    be = await _seed(db_session, "gca-backend", _REPO)
    svc = get_task_service(db_session)

    first = await svc.ingest_external_pr(
        project_id=fe.id, pr=_pr("abc123"), created_by=SYSTEM_UUID, team=Team.FRONTEND
    )
    assert first is not None  # first review opens
    # Same PR + same head, sibling project on the SAME repo → no second review.
    dup = await svc.ingest_external_pr(
        project_id=be.id, pr=_pr("abc123"), created_by=SYSTEM_UUID, team=Team.BACKEND
    )
    assert dup is None


@pytest.mark.asyncio
async def test_exists_is_repo_scoped(db_session: AsyncSession) -> None:
    fe = await _seed(db_session, "gca-frontend", _REPO)
    be = await _seed(db_session, "gca-backend", _REPO)
    svc = get_task_service(db_session)
    await svc.ingest_external_pr(
        project_id=fe.id, pr=_pr("abc123"), created_by=SYSTEM_UUID, team=Team.FRONTEND
    )

    # The sibling project sees the existing review (the fix); a new head SHA does not.
    assert await svc.external_review_task_exists(be.id, 131, "abc123") is True
    assert await svc.external_review_task_exists(be.id, 131, "newsha") is False


@pytest.mark.asyncio
async def test_different_repo_not_deduped(db_session: AsyncSession) -> None:
    fe = await _seed(db_session, "gca-frontend", _REPO)
    other = await _seed(db_session, "other", _OTHER_REPO)
    svc = get_task_service(db_session)
    await svc.ingest_external_pr(
        project_id=fe.id, pr=_pr("abc123"), created_by=SYSTEM_UUID, team=Team.FRONTEND
    )

    # A genuinely different repo with the same PR number is reviewed independently.
    created = await svc.ingest_external_pr(
        project_id=other.id, pr=_pr("abc123"), created_by=SYSTEM_UUID, team=Team.BACKEND
    )
    assert created is not None
