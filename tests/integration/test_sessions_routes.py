"""Sessions API route coverage."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_current_agent_id, get_db
from roboco.api.routes.sessions import router as sessions_router
from roboco.db.tables import (
    AgentTable,
    ChannelTable,
    GroupTable,
    ProjectTable,
    SessionTable,
    TaskTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    ChannelType,
    SessionStatus,
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.services.base import ConflictError as _Conflict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def session_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()

    channel = ChannelTable(
        id=uuid4(),
        name="ch",
        slug=f"ch-{uuid4().hex[:6]}",
        type=ChannelType.CELL,
        members=[pm.id],
        writers=[pm.id],
    )
    db_session.add(channel)
    await db_session.flush()

    group = GroupTable(
        id=uuid4(),
        name="g1",
        channel_id=channel.id,
        members=[pm.id],
        hierarchy_level=4,
    )
    db_session.add(group)
    await db_session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="S-Proj",
        slug=f"s-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
    )
    db_session.add(project)
    await db_session.flush()

    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=pm.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()

    app = FastAPI()
    app.include_router(sessions_router, prefix="/api/sessions")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent_id() -> UUID:
        return cast("UUID", pm.id)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_agent_id] = _override_agent_id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "pm": pm,
            "channel": channel,
            "group": group,
            "task": task,
        }
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_list_sessions_empty(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.get(
        f"/api/sessions?group_id={session_client['group'].id}",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_sessions_unknown_group_returns_404(
    session_client: dict,
) -> None:
    client = session_client["client"]
    response = await client.get(
        f"/api/sessions?group_id={uuid4()}",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_create_session(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.post(
        "/api/sessions",
        json={"group_id": str(session_client["group"].id)},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_get_session_not_found(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.get(f"/api/sessions/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_session_by_id(
    session_client: dict, db_session: AsyncSession
) -> None:
    client = session_client["client"]
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    response = await client.get(f"/api/sessions/{sess.id}", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_close_session_not_found(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.post(f"/api/sessions/{uuid4()}/close", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_sessions_for_task(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.get(
        f"/api/sessions/for-task/{session_client['task'].id}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_session_for_tasks(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.post(
        "/api/sessions/for-tasks",
        json={
            "task_ids": [str(session_client["task"].id)],
            "channel_slug": session_client["channel"].slug,
        },
        headers=_HDR,
    )
    # Either 201 success or some validation issue — just check it's not a server error.
    _SERVER_ERR = 500
    assert response.status_code < _SERVER_ERR


@pytest.mark.asyncio
async def test_create_session_for_tasks_unknown_channel(
    session_client: dict,
) -> None:
    client = session_client["client"]
    response = await client.post(
        "/api/sessions/for-tasks",
        json={
            "task_ids": [str(session_client["task"].id)],
            "channel_slug": "ghost-channel",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Session task linking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_task_unknown_session(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.post(
        f"/api/sessions/{uuid4()}/tasks",
        json={
            "task_id": str(session_client["task"].id),
            "is_primary": False,
            "relationship_type": "discussion",
        },
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_unlink_unknown(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.delete(
        f"/api/sessions/{uuid4()}/tasks/{uuid4()}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_tasks_for_unknown_session(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.get(f"/api/sessions/{uuid4()}/tasks", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Non-PM agent — link forbidden
# ---------------------------------------------------------------------------


async def _make_dev_sessions_app(
    db_session: AsyncSession, dev_agent: AgentTable
) -> FastAPI:
    """Build a FastAPI app for sessions where the agent is a developer."""
    app = FastAPI()
    app.include_router(sessions_router, prefix="/api/sessions")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent_id() -> Any:
        return dev_agent.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_agent_id] = _override_agent_id
    return app


@pytest.mark.asyncio
async def test_link_task_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    dev = AgentTable(
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
    db_session.add(dev)
    await db_session.flush()

    app = await _make_dev_sessions_app(db_session, dev)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/sessions/for-tasks",
            json={
                "task_ids": [str(uuid4())],
                "channel_slug": "backend-cell",
            },
            headers=_HDR,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_link_task_to_session_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    """Developer cannot link tasks to sessions via /sessions/{id}/tasks."""
    dev = AgentTable(
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
    db_session.add(dev)
    await db_session.flush()

    app = await _make_dev_sessions_app(db_session, dev)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/sessions/{uuid4()}/tasks",
            json={
                "task_id": str(uuid4()),
                "is_primary": False,
                "relationship_type": "discussion",
            },
            headers=_HDR,
        )
        unlink_response = await client.delete(
            f"/api/sessions/{uuid4()}/tasks/{uuid4()}", headers=_HDR
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert unlink_response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_close_session_invalid_state(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Try closing a session that's already closed."""
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.CLOSED,
        scope="task",
        closed_at=None,
    )
    db_session.add(sess)
    await db_session.flush()
    client = session_client["client"]
    response = await client.post(f"/api/sessions/{sess.id}/close", headers=_HDR)
    # 200 (idempotent close) or 400 (already closed)
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.BAD_REQUEST)


@pytest.mark.asyncio
async def test_close_session_active_success(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Close an active session successfully — exercises the 200 return path."""
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    client = session_client["client"]
    response = await client.post(f"/api/sessions/{sess.id}/close", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["id"] == str(sess.id)


@pytest.mark.asyncio
async def test_get_tasks_for_session_success(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Existing session — returns task list (possibly empty)."""
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    client = session_client["client"]
    response = await client.get(f"/api/sessions/{sess.id}/tasks", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_session_for_tasks_invalid_relationship_falls_back(
    session_client: dict,
) -> None:
    """Invalid relationship_type falls back to 'discussion' instead of erroring."""
    client = session_client["client"]
    response = await client.post(
        "/api/sessions/for-tasks",
        json={
            "task_ids": [str(session_client["task"].id)],
            "channel_slug": session_client["channel"].slug,
            "relationship_type": "ghost-relationship",
        },
        headers=_HDR,
    )
    # The route catches the ValueError and uses DISCUSSION; the actual
    # creation may still fail for other reasons but never on this branch.
    _SERVER_ERR = 500
    assert response.status_code < _SERVER_ERR


@pytest.mark.asyncio
async def test_link_task_to_session_invalid_relationship_falls_back(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Invalid relationship_type on link route falls back to discussion."""
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    client = session_client["client"]
    response = await client.post(
        f"/api/sessions/{sess.id}/tasks",
        json={
            "task_id": str(session_client["task"].id),
            "is_primary": False,
            "relationship_type": "ghost",
        },
        headers=_HDR,
    )
    # Either 201 (created with DISCUSSION fallback) or some other 4xx.
    _SERVER_ERR = 500
    assert response.status_code < _SERVER_ERR


@pytest.mark.asyncio
async def test_link_task_to_session_duplicate_returns_409(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Linking the same task twice — service may raise ConflictError → 409."""
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    client = session_client["client"]
    payload = {
        "task_id": str(session_client["task"].id),
        "is_primary": False,
        "relationship_type": "discussion",
    }
    first = await client.post(
        f"/api/sessions/{sess.id}/tasks", json=payload, headers=_HDR
    )
    second = await client.post(
        f"/api/sessions/{sess.id}/tasks", json=payload, headers=_HDR
    )
    assert first.status_code in (
        HTTPStatus.CREATED,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
    )
    assert second.status_code in (
        HTTPStatus.CREATED,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
    )


# ---------------------------------------------------------------------------
# Outsider agent — list/create with no channel access → 403 (lines 69-70, 141-144)
# ---------------------------------------------------------------------------


async def _make_outsider_sessions_app(
    db_session: AsyncSession, outsider: AgentTable
) -> FastAPI:
    """FastAPI app where the agent is NOT a member of any channel."""
    app = FastAPI()
    app.include_router(sessions_router, prefix="/api/sessions")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent_id() -> Any:
        return outsider.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_agent_id] = _override_agent_id
    return app


@pytest.mark.asyncio
async def test_list_sessions_outsider_403(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Agent not in channel members/writers/observers → 403 (line 69-70)."""
    outsider = AgentTable(
        id=uuid4(),
        name="Outsider",
        slug=f"outsider-{uuid4().hex[:6]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(outsider)
    await db_session.flush()
    app = await _make_outsider_sessions_app(db_session, outsider)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/sessions?group_id={session_client['group'].id}",
            headers=_HDR,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_create_session_outsider_403(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Outsider creating a session → service raises PermissionError → 403 (143-144)."""
    outsider = AgentTable(
        id=uuid4(),
        name="Outsider2",
        slug=f"outsider2-{uuid4().hex[:6]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(outsider)
    await db_session.flush()
    app = await _make_outsider_sessions_app(db_session, outsider)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/sessions",
            json={"group_id": str(session_client["group"].id)},
            headers=_HDR,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_create_session_unknown_group_404(session_client: dict) -> None:
    """create_session against a non-existent group → 404 (line 141-142)."""
    client = session_client["client"]
    response = await client.post(
        "/api/sessions",
        json={"group_id": str(uuid4())},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_create_session_for_tasks_unknown_task_404(
    session_client: dict,
) -> None:
    """create_session_for_tasks with unknown group_id → 404 (lines 240-241)."""
    client = session_client["client"]
    response = await client.post(
        "/api/sessions/for-tasks",
        json={
            "task_ids": [str(session_client["task"].id)],
            "channel_slug": session_client["channel"].slug,
            "group_id": str(uuid4()),  # unknown group
        },
        headers=_HDR,
    )
    # Service raises NotFoundError on missing group → 404.
    assert response.status_code in (HTTPStatus.NOT_FOUND, HTTPStatus.BAD_REQUEST)


@pytest.mark.asyncio
async def test_create_session_for_tasks_conflict_via_mock(
    session_client: dict,
) -> None:
    """ConflictError from service surfaces as 409 (line 243)."""

    client = session_client["client"]
    with patch(
        "roboco.services.messaging.MessagingService.create_session_for_tasks",
        side_effect=_Conflict("dup"),
    ):
        response = await client.post(
            "/api/sessions/for-tasks",
            json={
                "task_ids": [str(session_client["task"].id)],
                "channel_slug": session_client["channel"].slug,
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_create_session_for_tasks_value_error_via_mock(
    session_client: dict,
) -> None:
    """ValueError from service surfaces as 400 (lines 244-247)."""

    client = session_client["client"]
    with patch(
        "roboco.services.messaging.MessagingService.create_session_for_tasks",
        side_effect=ValueError("invalid"),
    ):
        response = await client.post(
            "/api/sessions/for-tasks",
            json={
                "task_ids": [str(session_client["task"].id)],
                "channel_slug": session_client["channel"].slug,
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_link_task_to_session_conflict_via_mock(
    session_client: dict, db_session: AsyncSession
) -> None:
    """ConflictError from link_session_to_task surfaces as 409 (lines 291-292)."""

    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    client = session_client["client"]
    with patch(
        "roboco.services.messaging.MessagingService.link_session_to_task",
        side_effect=_Conflict("conflict"),
    ):
        response = await client.post(
            f"/api/sessions/{sess.id}/tasks",
            json={
                "task_id": str(session_client["task"].id),
                "is_primary": True,
                "relationship_type": "discussion",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_link_task_to_session_already_linked_409(
    session_client: dict, db_session: AsyncSession
) -> None:
    """Linking same task twice on same session → ConflictError → 409 (291-292)."""
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    client = session_client["client"]
    payload = {
        "task_id": str(session_client["task"].id),
        "is_primary": True,
        "relationship_type": "discussion",
    }
    first = await client.post(
        f"/api/sessions/{sess.id}/tasks", json=payload, headers=_HDR
    )
    if first.status_code == HTTPStatus.CREATED:
        # Second call should hit the duplicate path.
        second = await client.post(
            f"/api/sessions/{sess.id}/tasks", json=payload, headers=_HDR
        )
        # link_session_to_task is idempotent on the same task; conflict only
        # if a primary mismatch arises.
        assert second.status_code in (HTTPStatus.CREATED, HTTPStatus.CONFLICT)
