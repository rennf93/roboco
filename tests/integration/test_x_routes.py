"""X engine route coverage — CEO-only list/approve/reject + credentials."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.x import router as x_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation.policy.content import markers
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext
from roboco.services.task import X_POST_SOURCE
from roboco.services.x_client import XClient, XMention, XPostResult
from roboco.services.x_post_service import XPostService

HISTORY_LIMIT = 2

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agent(session: AsyncSession, role: AgentRole, slug: str) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=f"{slug}-{uuid4().hex[:6]}",
        role=role,
        team=None,
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


async def _seed_draft(session: AsyncSession) -> TaskTable:
    system = await _seed_agent(session, AgentRole.SYSTEM, "system")
    secretary = await _seed_agent(session, AgentRole.SECRETARY, "secretary")
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo",
        slug=f"roboco-{uuid4().hex[:6]}",
        git_url="https://example.com/roboco.git",
        assigned_cell=Team.BACKEND,
        created_by=system.id,
    )
    session.add(project)
    await session.flush()
    task = TaskTable(
        id=uuid4(),
        title="X post: release v0.17.0",
        description="draft body",
        acceptance_criteria=["CEO approves or rejects"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.ADMINISTRATIVE,
        nature=TaskNature.NON_TECHNICAL,
        estimated_complexity=Complexity.LOW,
        project_id=project.id,
        created_by=system.id,
        assigned_to=secretary.id,
        team=Team.MAIN_PM,
        source=X_POST_SOURCE,
        confirmed_by_human=False,
    )
    session.add(task)
    await session.flush()
    markers.set_x_draft_body(task, "draft body")
    markers.set_x_release_version(task, "0.17.0")
    await session.flush()
    return task


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(x_router, prefix="/api/x")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _build_app(db_session, AgentRole.CEO, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


class _StubClient(XClient):
    @property
    def configured(self) -> bool:
        return True

    async def post_tweet(self, text: str) -> XPostResult:
        _ = text
        return XPostResult(posted=True, tweet_id="42", detail="posted")

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        _ = (since_id, max_results)
        return []


@pytest.mark.asyncio
async def test_list_posts_returns_open_draft(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    project = await db_session.get(ProjectTable, task.project_id)
    resp = await ceo_client.get("/api/x/posts")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    assert body[0]["task_id"] == str(task.id)
    assert body[0]["body"] == "draft body"
    assert body[0]["release_version"] == "0.17.0"
    assert project is not None
    assert body[0]["project_slug"] == project.slug
    assert body[0]["project_name"] == project.name


@pytest.mark.asyncio
async def test_approve_posts_and_returns_tweet_id(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    with (
        patch(
            "roboco.services.x_post_service.build_x_client",
            return_value=_StubClient(),
        ),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        resp = await ceo_client.post(f"/api/x/posts/{task.id}/approve", json={})
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["status"] == "posted"
    assert body["tweet_id"] == "42"
    await db_session.refresh(task)
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_approve_edited_body_over_limit_is_400(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    resp = await ceo_client.post(
        f"/api/x/posts/{task.id}/approve", json={"edited_body": "x" * 281}
    )
    assert resp.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_approve_missing_task_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.post(f"/api/x/posts/{uuid4()}/approve", json={})
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_reject_cancels_and_records_reason(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    task = await _seed_draft(db_session)
    with (
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        resp = await ceo_client.post(
            f"/api/x/posts/{task.id}/reject", json={"reason": "Not our voice"}
        )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["reject_reason"] == "Not our voice"
    refreshed = await db_session.get(TaskTable, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_history_returns_posted_and_rejected_newest_first(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    rejected = await _seed_draft(db_session)
    with (
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        await ceo_client.post(
            f"/api/x/posts/{rejected.id}/reject", json={"reason": "off-brand tone"}
        )
    posted = await _seed_draft(db_session)
    posted_project = await db_session.get(ProjectTable, posted.project_id)
    with (
        patch(
            "roboco.services.x_post_service.build_x_client",
            return_value=_StubClient(),
        ),
        patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
        patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
    ):
        await ceo_client.post(f"/api/x/posts/{posted.id}/approve", json={})

    resp = await ceo_client.get("/api/x/posts/history")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    ids = [row["task_id"] for row in body]
    assert str(posted.id) in ids
    assert str(rejected.id) in ids
    assert ids.index(str(posted.id)) < ids.index(str(rejected.id))
    posted_row = next(row for row in body if row["task_id"] == str(posted.id))
    assert posted_row["status"] == "completed"
    assert posted_row["tweet_id"] == "42"
    assert posted_project is not None
    assert posted_row["project_slug"] == posted_project.slug
    assert posted_row["project_name"] == posted_project.name
    rejected_row = next(row for row in body if row["task_id"] == str(rejected.id))
    assert rejected_row["status"] == "cancelled"
    assert rejected_row["reject_reason"] == "off-brand tone"


@pytest.mark.asyncio
async def test_history_excludes_open_drafts(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    """Every approve/reject route in this file commits durably (the route
    always calls db.commit()), so other tests' posted/rejected rows persist
    in this shared-DB test session — history is never provably empty. Assert
    identity instead: THIS still-open draft must not appear."""
    open_task = await _seed_draft(db_session)
    resp = await ceo_client.get("/api/x/posts/history")
    assert resp.status_code == HTTPStatus.OK
    ids = [row["task_id"] for row in resp.json()]
    assert str(open_task.id) not in ids


@pytest.mark.asyncio
async def test_history_respects_limit(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    for _ in range(3):
        t = await _seed_draft(db_session)
        with (
            patch.object(XPostService, "_acquire_lock", AsyncMock(return_value="tok")),
            patch.object(XPostService, "_release_lock", AsyncMock(return_value=None)),
        ):
            await ceo_client.post(
                f"/api/x/posts/{t.id}/reject", json={"reason": "not relevant"}
            )
    resp = await ceo_client.get("/api/x/posts/history", params={"limit": HISTORY_LIMIT})
    assert resp.status_code == HTTPStatus.OK
    assert len(resp.json()) == HISTORY_LIMIT


@pytest.mark.asyncio
async def test_history_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/x/posts/history")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_credentials_default_is_unset(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/x/credentials")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["has_credentials"] is False


@pytest.mark.asyncio
async def test_set_credentials_reports_status_never_plaintext(
    ceo_client: AsyncClient,
) -> None:
    resp = await ceo_client.post(
        "/api/x/credentials",
        json={
            "api_key": "secret-key-value",
            "api_secret": "secret-apisecret-value",
            "access_token": "secret-token-value",
            "access_token_secret": "secret-tokensecret-value",
        },
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"has_credentials": True}
    assert "secret-key-value" not in resp.text
    assert "secret-apisecret-value" not in resp.text
    assert "secret-token-value" not in resp.text
    assert "secret-tokensecret-value" not in resp.text

    status_resp = await ceo_client.get("/api/x/credentials")
    assert status_resp.json()["has_credentials"] is True


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_draft(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_resp = await client.get("/api/x/posts")
        creds_resp = await client.get("/api/x/credentials")
    assert list_resp.status_code == HTTPStatus.FORBIDDEN
    assert creds_resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()
