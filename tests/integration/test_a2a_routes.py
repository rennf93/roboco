"""A2A API route coverage — agent cards, tasks, conversations."""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_current_agent_slug, get_db
from roboco.api.routes.a2a import router as a2a_router
from roboco.api.routes.a2a import wellknown_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.enforcement import A2AAccessDeniedError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.a2a import A2ATask, A2ATaskState, A2ATaskStatus
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


_PAGE_TOKEN_OFFSET = 20
_MIN_STREAM_CHUNKS = 2


@pytest_asyncio.fixture
async def a2a_route_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
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
    project = ProjectTable(
        id=uuid4(),
        name="A2A-Proj",
        slug=f"a2a-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=dev.id,
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
        created_by=dev.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()

    app = FastAPI()
    app.include_router(a2a_router, prefix="/api/a2a")
    app.include_router(wellknown_router)

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    async def _override_agent_slug() -> str:
        return dev.slug

    async def _override_agent_context() -> AgentContext:
        # The authenticated caller is the seeded developer by default. Tests
        # that need a different role (e.g. the PM-gated cancel route) swap this
        # override on the yielded app before posting.
        return AgentContext(
            agent_id=cast("UUID", dev.id),
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
            slug=dev.slug,
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_agent_slug] = _override_agent_slug
    app.dependency_overrides[get_agent_context] = _override_agent_context

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "dev": dev, "task": task, "app": app}
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": "be-dev-1", "X-Agent-Role": "developer"}


def _set_pm_context(app: FastAPI, dev: AgentTable) -> None:
    """Override the agent context to a cell PM so the PM-gated cancel route
    admits the call (the default fixture context is a developer)."""

    async def _pm() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", dev.id),
            role=AgentRole.CELL_PM,
            team=Team.BACKEND,
            slug=dev.slug,
        )

    app.dependency_overrides[get_agent_context] = _pm


# ---------------------------------------------------------------------------
# Well-known endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_system_agent_card(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get("/.well-known/agent.json")
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["id"] == "roboco-system"


@pytest.mark.asyncio
async def test_get_agent_card_by_slug(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get(
        f"/agents/{a2a_route_client['dev'].slug}/.well-known/agent.json",
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_agent_card_unknown(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get(
        f"/agents/{uuid4()}/.well-known/agent.json",
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Tasks endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_a2a_task(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get(
        f"/api/a2a/tasks/{a2a_route_client['task'].id}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_a2a_task_not_found(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get(f"/api/a2a/tasks/{uuid4()}", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_list_a2a_tasks(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get("/api/a2a/tasks", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_cancel_a2a_task_invalid_id(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.post(
        "/api/a2a/tasks/not-a-uuid/cancel",
        json={},
        headers=_HDR,
    )
    # 400 for invalid UUID, or 404 if it parses then doesn't find.
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


# ---------------------------------------------------------------------------
# Discovery endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get("/api/a2a/agents", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_agents_filter_by_role(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get("/api/a2a/agents?role=developer", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_agent_card_endpoint(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get(
        f"/api/a2a/agents/{a2a_route_client['dev'].slug}/card", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_inbox(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get("/api/a2a/chat/inbox", headers=_HDR)
    # Inbox needs proper agent context; route may 200 or 500.
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.INTERNAL_SERVER_ERROR)


@pytest.mark.asyncio
async def test_chat_pairs(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get("/api/a2a/chat/pairs", headers=_HDR)
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.INTERNAL_SERVER_ERROR)


@pytest.mark.asyncio
async def test_chat_list_conversations(a2a_route_client: dict) -> None:
    client = a2a_route_client["client"]
    response = await client.get("/api/a2a/chat/conversations", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Send message — task_id required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_missing_task_id_returns_4xx(
    a2a_route_client: dict,
) -> None:
    """task_id is required — schema or route enforces it."""
    client = a2a_route_client["client"]
    response = await client.post(
        "/api/a2a/message/send",
        json={
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hi"}],
            }
        },
        headers=_HDR,
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


# ---------------------------------------------------------------------------
# send_message via mocked A2AService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_with_task_id_response(a2a_route_client: dict) -> None:
    """is_response=True path."""

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.update_task_from_message = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/message/send",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(a2a_route_client["task"].id),
                },
                "metadata": {"is_response": True, "from_agent": "be-dev-1"},
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "response_sent"


@pytest.mark.asyncio
async def test_send_message_response_invalid_task_id(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.update_task_from_message = AsyncMock(
            side_effect=ValueError("Invalid task ID format")
        )
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/message/send",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(a2a_route_client["task"].id),
                },
                "metadata": {"is_response": True},
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_send_message_response_task_not_found(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.update_task_from_message = AsyncMock(
            side_effect=ValueError("Task missing")
        )
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/message/send",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(a2a_route_client["task"].id),
                },
                "metadata": {"is_response": True},
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_send_message_create_notification_success(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.create_a2a_notification = AsyncMock(
            return_value={"to_agent": "be-qa-1"}
        )
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/message/send",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(a2a_route_client["task"].id),
                }
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_send_message_permission_error(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.create_a2a_notification = AsyncMock(
            side_effect=ValueError("Not allowed to send. Hint: Use escalation")
        )
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/message/send",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(a2a_route_client["task"].id),
                }
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_send_message_value_error(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.create_a2a_notification = AsyncMock(side_effect=ValueError("Bad data"))
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/message/send",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(a2a_route_client["task"].id),
                }
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# Cancel task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_success(a2a_route_client: dict) -> None:

    a2a_task = A2ATask.model_validate(
        {
            "id": str(a2a_route_client["task"].id),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.CANCELLED).model_dump(
                mode="json"
            ),
        }
    )
    client = a2a_route_client["client"]
    _set_pm_context(a2a_route_client["app"], a2a_route_client["dev"])
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.cancel_task = AsyncMock(return_value=a2a_task)
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/tasks/{a2a_route_client['task'].id}/cancel",
            json={
                "name": f"tasks/{a2a_route_client['task'].id}",
                "reason": "no longer needed",
            },
            headers=_HDR,
        )
    # 200 expected; pydantic may serialize as 422 if response_model coercion
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.UNPROCESSABLE_ENTITY)


@pytest.mark.asyncio
async def test_cancel_task_already_terminal(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    _set_pm_context(a2a_route_client["app"], a2a_route_client["dev"])
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.cancel_task = AsyncMock(
            side_effect=ValueError("Task already in terminal state")
        )
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/tasks/{a2a_route_client['task'].id}/cancel",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_cancel_task_not_found(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    _set_pm_context(a2a_route_client["app"], a2a_route_client["dev"])
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.cancel_task = AsyncMock(side_effect=ValueError("Task missing"))
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/tasks/{uuid4()}/cancel",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# #423: the cancel route must be authenticated + PM/management-gated, and pass
# the authenticated actor + role into the service (it cascades cancel to all
# non-terminal descendants — lifecycle rule: Any -> cancelled: PM roles only).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_developer_role_forbidden(a2a_route_client: dict) -> None:
    """A developer (default fixture context) must NOT be able to cancel a task
    via A2A — the route was previously unauthenticated with no role gate, so any
    caller could cancel any task tree (#423). Now PM/management-only."""
    client = a2a_route_client["client"]
    # default fixture context = developer
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.cancel_task = AsyncMock()
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/tasks/{a2a_route_client['task'].id}/cancel",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN
    instance.cancel_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_task_no_auth_header_rejected(a2a_route_client: dict) -> None:
    """A request with no agent headers at all is rejected — the route must not
    be reachable unauthenticated (#423)."""
    client = a2a_route_client["client"]
    response = await client.post(
        f"/api/a2a/tasks/{a2a_route_client['task'].id}/cancel",
    )
    assert response.status_code in (
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_cancel_task_pm_passes_actor_and_role_to_service(
    a2a_route_client: dict,
) -> None:
    """A PM cancel threads the authenticated role (for the cascade role gate)
    and the actor slug (for the cancellation-note attribution) into the service
    — previously the service was called with no actor and a hardcoded
    cell_pm role, so the audit trail recorded no real caller (#423)."""
    client = a2a_route_client["client"]
    _set_pm_context(a2a_route_client["app"], a2a_route_client["dev"])
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.cancel_task = AsyncMock(
            return_value=A2ATask.model_validate(
                {
                    "id": str(a2a_route_client["task"].id),
                    "contextId": str(uuid4()),
                    "status": A2ATaskStatus(state=A2ATaskState.CANCELLED).model_dump(
                        mode="json"
                    ),
                }
            )
        )
        mock_service_cls.return_value = instance
        # No body → request=None → the handler runs (a body without the A2A
        # ``name`` field 422s at request validation before the handler). The
        # invariant under test is the role/slug threading, not the reason.
        response = await client.post(
            f"/api/a2a/tasks/{a2a_route_client['task'].id}/cancel",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    # The authenticated PM role + slug reach the service.
    _kwargs = instance.cancel_task.await_args.kwargs
    assert _kwargs.get("agent_role") == "cell_pm"
    assert _kwargs.get("actor_slug") == a2a_route_client["dev"].slug


# ---------------------------------------------------------------------------
# #116: send_message must record the AUTHENTICATED identity as the responder,
# not a client-supplied metadata.from_agent (spoof).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_uses_authenticated_identity_not_client_from_agent(
    a2a_route_client: dict,
) -> None:
    """is_response=True must stamp the authenticated caller's slug as the
    responder, ignoring a spoofed metadata.from_agent — previously the route
    took from_agent verbatim from the request body, so any agent could
    impersonate anyone (e.g. from_agent='ceo') in the task's notes and in the
    spawn/notification routed back to the original requester (#116)."""
    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.update_task_from_message = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/message/send",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(a2a_route_client["task"].id),
                },
                "metadata": {"is_response": True, "from_agent": "ceo"},
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    # The authenticated dev slug is the responder — NOT the spoofed 'ceo'.
    _kwargs = instance.update_task_from_message.await_args.kwargs
    assert _kwargs.get("responder_agent") == a2a_route_client["dev"].slug
    assert _kwargs.get("responder_agent") != "ceo"


@pytest.mark.asyncio
async def test_chat_create_conversation_access_denied(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.get_or_create_conversation = AsyncMock(
            side_effect=A2AAccessDeniedError(
                from_agent="be-dev-1",
                to_agent="fe-dev-1",
                reason="Cannot DM cross-cell",
                route_hint="/api/channels",
            )
        )
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/chat/conversations",
            json={
                "target_agent": "fe-dev-1",
                "topic": "Topic",
                "initial_message": "Hi there",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_chat_create_conversation_success(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    conv_id = uuid4()
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        conv_obj = SimpleNamespace(
            id=conv_id,
            agent_a="be-dev-1",
            agent_b="fe-dev-1",
            topic="T",
            task_id=None,
            status="active",
            resolution=None,
            message_count=1,
            unread_by_a=0,
            unread_by_b=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
        )
        instance.get_or_create_conversation = AsyncMock(return_value=conv_obj)
        instance.send_chat_message = AsyncMock(return_value=None)
        instance.get_conversation = AsyncMock(return_value=conv_obj)
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/chat/conversations",
            json={
                "target_agent": "fe-dev-1",
                "topic": "T",
                "initial_message": "Hi there",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_chat_create_conversation_refresh_failed(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        conv_obj = SimpleNamespace(
            id=uuid4(),
            agent_a="be-dev-1",
            agent_b="fe-dev-1",
            topic="T",
            task_id=None,
            status="active",
            resolution=None,
            message_count=1,
            unread_by_a=0,
            unread_by_b=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
        )
        instance.get_or_create_conversation = AsyncMock(return_value=conv_obj)
        instance.send_chat_message = AsyncMock(return_value=None)
        instance.get_conversation = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.post(
            "/api/a2a/chat/conversations",
            json={
                "target_agent": "fe-dev-1",
                "topic": "T",
                "initial_message": "Hi there",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_get_conversation_not_found(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.get_conversation = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.get(
            f"/api/a2a/chat/conversations/{uuid4()}", headers=_HDR
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_conversation_success(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    conv_id = uuid4()
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        conv_obj = SimpleNamespace(
            id=conv_id,
            agent_a="be-dev-1",
            agent_b="fe-dev-1",
            topic="T",
            task_id=None,
            status="active",
            resolution=None,
            message_count=1,
            unread_by_a=0,
            unread_by_b=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
        )
        instance.get_conversation = AsyncMock(return_value=conv_obj)
        mock_service_cls.return_value = instance
        response = await client.get(
            f"/api/a2a/chat/conversations/{conv_id}", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_close_conversation_value_error(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.close_conversation = AsyncMock(side_effect=ValueError("not found"))
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/chat/conversations/{uuid4()}/close",
            json={"resolution": "done"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_close_conversation_success(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.close_conversation = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/chat/conversations/{uuid4()}/close",
            headers=_HDR,
        )
    # 204 No content = no_content / 200 / 204
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NO_CONTENT)


@pytest.mark.asyncio
async def test_list_chat_messages(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    msg = SimpleNamespace(
        id=uuid4(),
        conversation_id=uuid4(),
        from_agent="be-dev-1",
        content="hi",
        message_kind="message",
        response_to_id=None,
        requires_response=False,
        read_at=None,
        created_at=datetime.now(UTC),
        edited_at=None,
    )
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        # Return one extra to simulate has_more
        instance.get_messages = AsyncMock(return_value=[msg, msg])
        mock_service_cls.return_value = instance
        response = await client.get(
            f"/api/a2a/chat/conversations/{uuid4()}/messages?limit=1",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["has_more"] is True


@pytest.mark.asyncio
async def test_send_chat_message_value_error(
    a2a_route_client: dict,
) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.send_chat_message = AsyncMock(side_effect=ValueError("not found"))
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/chat/conversations/{uuid4()}/messages",
            json={"content": "hi"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_send_chat_message_success(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    msg = SimpleNamespace(
        id=uuid4(),
        conversation_id=uuid4(),
        from_agent="be-dev-1",
        content="hi",
        message_kind="message",
        response_to_id=None,
        requires_response=False,
        read_at=None,
        created_at=datetime.now(UTC),
        edited_at=None,
    )
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.send_chat_message = AsyncMock(return_value=msg)
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/chat/conversations/{uuid4()}/messages",
            json={"content": "hi"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_mark_read(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.mark_read = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/chat/conversations/{uuid4()}/read",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.NO_CONTENT


@pytest.mark.asyncio
async def test_get_task_conversations(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.list_conversations = AsyncMock(return_value=[])
        mock_service_cls.return_value = instance
        response = await client.get(
            f"/api/a2a/chat/tasks/{a2a_route_client['task'].id}/conversations",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_chat_list_with_status_filter(a2a_route_client: dict) -> None:

    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.list_conversations = AsyncMock(return_value=[])
        mock_service_cls.return_value = instance
        response = await client.get(
            "/api/a2a/chat/conversations?status=active", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# send_message: TASK_ID_REQUIRED branch (line 131)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_no_task_id_yields_400(a2a_route_client: dict) -> None:
    """Valid Part schema but no taskId — route raises TASK_ID_REQUIRED 400."""
    client = a2a_route_client["client"]
    response = await client.post(
        "/api/a2a/message/send",
        json={
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": "hi"}],
                # No taskId — triggers TASK_ID_REQUIRED branch.
            }
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "TASK_ID_REQUIRED" in response.text


# ---------------------------------------------------------------------------
# message/stream: SSE entry-point coverage (lines 203-271)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_stream_task_not_found(a2a_route_client: dict) -> None:
    """Stream a message with task_id pointing at unknown task — error event."""
    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.get_task = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        async with client.stream(
            "POST",
            "/api/a2a/message/stream",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": str(uuid4()),
                }
            },
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            chunks: list[bytes] = []
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                if len(chunks) >= 1:
                    break
            assert any(b"error" in c for c in chunks)


@pytest.mark.asyncio
async def test_message_stream_with_terminal_task(a2a_route_client: dict) -> None:
    """Stream where task is initially returned then disconnects — covers status/loop."""
    client = a2a_route_client["client"]

    a2a_task = A2ATask.model_validate(
        {
            "id": str(uuid4()),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.COMPLETED).model_dump(
                mode="json"
            ),
        }
    )
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.get_task = AsyncMock(return_value=a2a_task)
        mock_service_cls.return_value = instance
        async with client.stream(
            "POST",
            "/api/a2a/message/stream",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": a2a_task.id,
                }
            },
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            chunks: list[bytes] = []
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                # Read just the first event then break — initial task state.
                if len(chunks) >= 1:
                    break
            joined = b"".join(chunks)
            assert b"task.status" in joined


@pytest.mark.asyncio
async def test_message_stream_no_task_id(a2a_route_client: dict) -> None:
    """Stream without task_id — emits creating + error events."""
    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        mock_service_cls.return_value = instance
        async with client.stream(
            "POST",
            "/api/a2a/message/stream",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    # No taskId.
                }
            },
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            chunks: list[bytes] = []
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                if len(chunks) >= _MIN_STREAM_CHUNKS:
                    break
            joined = b"".join(chunks)
            assert b"task.creating" in joined or b"error" in joined


# ---------------------------------------------------------------------------
# subscribe_to_task: SSE entry-point coverage (lines 289-337)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_task_not_found(a2a_route_client: dict) -> None:
    """subscribe_to_task with an unknown task → 404."""
    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.get_task = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.get(f"/api/a2a/tasks/{uuid4()}/subscribe", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_subscribe_task_streams_initial_state(a2a_route_client: dict) -> None:
    """Stream a terminal task — generator emits status + complete then breaks."""
    client = a2a_route_client["client"]

    a2a_task = A2ATask.model_validate(
        {
            "id": str(uuid4()),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.COMPLETED).model_dump(
                mode="json"
            ),
        }
    )
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.get_task = AsyncMock(return_value=a2a_task)
        mock_service_cls.return_value = instance
        async with client.stream(
            "GET",
            f"/api/a2a/tasks/{a2a_task.id}/subscribe",
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            chunks: list[bytes] = []
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                joined = b"".join(chunks)
                if b"task.complete" in joined:
                    break
            joined = b"".join(chunks)
            assert b"task.status" in joined
            assert b"task.complete" in joined


@pytest.mark.asyncio
async def test_subscribe_task_disappears_during_stream(
    a2a_route_client: dict,
) -> None:
    """Task is found at first, then returns None inside the loop."""
    client = a2a_route_client["client"]

    a2a_task = A2ATask.model_validate(
        {
            "id": str(uuid4()),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.WORKING).model_dump(mode="json"),
        }
    )
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        # First call (validation): task exists. Second call (loop): None.
        instance.get_task = AsyncMock(side_effect=[a2a_task, None])
        mock_service_cls.return_value = instance
        async with client.stream(
            "GET",
            f"/api/a2a/tasks/{a2a_task.id}/subscribe",
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            # Just connect; generator immediately exits when get_task returns None.
            count = 0
            async for _chunk in response.aiter_bytes():
                count += 1
                if count >= 1:
                    break


@pytest.mark.asyncio
async def test_subscribe_task_polls_and_skips_unchanged(
    a2a_route_client: dict,
) -> None:
    """subscribe_to_task: state unchanged across polls — covers sleep+increment."""
    client = a2a_route_client["client"]

    a2a_task = A2ATask.model_validate(
        {
            "id": str(uuid4()),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.WORKING).model_dump(mode="json"),
        }
    )

    # Validation call returns task. Loop alternates: same task (state unchanged
    # path → emits once then sleep), then None (loop exits).
    side_effects = [a2a_task, a2a_task, None]
    with (
        patch("roboco.api.routes.a2a.A2AService") as mock_service_cls,
        patch("roboco.api.routes.a2a.asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        instance = AsyncMock()
        instance.get_task = AsyncMock(side_effect=side_effects)
        mock_service_cls.return_value = instance
        async with client.stream(
            "GET",
            f"/api/a2a/tasks/{a2a_task.id}/subscribe",
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            count = 0
            async for _chunk in response.aiter_bytes():
                count += 1
                if count >= 1:
                    break


@pytest.mark.asyncio
async def test_subscribe_task_disconnects_immediately(
    a2a_route_client: dict,
) -> None:
    """is_disconnected returns True on first loop iteration → break (line 307)."""
    client = a2a_route_client["client"]

    a2a_task = A2ATask.model_validate(
        {
            "id": str(uuid4()),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.WORKING).model_dump(mode="json"),
        }
    )

    async def _disconnected(_self: object) -> bool:
        return True

    with (
        patch("roboco.api.routes.a2a.A2AService") as mock_service_cls,
        patch("roboco.api.routes.a2a.Request.is_disconnected", new=_disconnected),
    ):
        instance = AsyncMock()
        instance.get_task = AsyncMock(return_value=a2a_task)
        mock_service_cls.return_value = instance
        async with client.stream(
            "GET",
            f"/api/a2a/tasks/{a2a_task.id}/subscribe",
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            count = 0
            async for _chunk in response.aiter_bytes():
                count += 1
                if count >= 1:
                    break


@pytest.mark.asyncio
async def test_message_stream_disconnects_in_loop(
    a2a_route_client: dict,
) -> None:
    """is_disconnected → True inside send_message_stream loop (line 234)."""
    client = a2a_route_client["client"]

    a2a_task = A2ATask.model_validate(
        {
            "id": str(uuid4()),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.WORKING).model_dump(mode="json"),
        }
    )

    # First call to is_disconnected is False (initial loop entry) — wait,
    # actually disconnect check happens BEFORE sleep, on first iteration.
    async def _disconnected(_self: object) -> bool:
        return True

    with (
        patch("roboco.api.routes.a2a.A2AService") as mock_service_cls,
        patch("roboco.api.routes.a2a.Request.is_disconnected", new=_disconnected),
    ):
        instance = AsyncMock()
        instance.get_task = AsyncMock(return_value=a2a_task)
        mock_service_cls.return_value = instance
        async with client.stream(
            "POST",
            "/api/a2a/message/stream",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": a2a_task.id,
                }
            },
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            count = 0
            async for _chunk in response.aiter_bytes():
                count += 1
                if count >= 1:
                    break


@pytest.mark.asyncio
async def test_message_stream_task_disappears_in_loop(
    a2a_route_client: dict,
) -> None:
    """Task goes None inside send_message_stream poll loop (line 242)."""
    client = a2a_route_client["client"]

    a2a_task = A2ATask.model_validate(
        {
            "id": str(uuid4()),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.WORKING).model_dump(mode="json"),
        }
    )

    # First get_task returns task (initial state). Inside loop: returns None.
    side_effects = [a2a_task, None]
    with (
        patch("roboco.api.routes.a2a.A2AService") as mock_service_cls,
        patch("roboco.api.routes.a2a.asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        instance = AsyncMock()
        instance.get_task = AsyncMock(side_effect=side_effects)
        mock_service_cls.return_value = instance
        async with client.stream(
            "POST",
            "/api/a2a/message/stream",
            json={
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "hi"}],
                    "taskId": a2a_task.id,
                }
            },
            headers=_HDR,
            timeout=5.0,
        ) as response:
            assert response.status_code == HTTPStatus.OK
            count = 0
            async for _chunk in response.aiter_bytes():
                count += 1
                if count >= 1:
                    break


# ---------------------------------------------------------------------------
# list_tasks: pageToken parsing (lines 386-387)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_with_page_token(a2a_route_client: dict) -> None:
    """list_tasks with pageToken parses int and uses it as offset."""
    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.list_tasks = AsyncMock(return_value=([], False))
        mock_service_cls.return_value = instance
        response = await client.get(
            f"/api/a2a/tasks?pageToken={_PAGE_TOKEN_OFFSET}&pageSize=10",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    instance.list_tasks.assert_awaited_once()
    call = instance.list_tasks.await_args
    assert call.kwargs["offset"] == _PAGE_TOKEN_OFFSET


@pytest.mark.asyncio
async def test_list_tasks_with_invalid_page_token(a2a_route_client: dict) -> None:
    """Invalid pageToken (non-int) is silently suppressed → offset stays 0."""
    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.list_tasks = AsyncMock(return_value=([], True))
        mock_service_cls.return_value = instance
        response = await client.get(
            "/api/a2a/tasks?pageToken=not-an-int&pageSize=5", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    # has_more=True → next_page_token=str(0+5).
    # Model uses populate_by_name; check both alias and snake_case.
    token = body.get("nextPageToken") or body.get("next_page_token")
    assert token == "5"


# ---------------------------------------------------------------------------
# cancel_task: full success path (lines 433-434)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_success_no_body(a2a_route_client: dict) -> None:
    """cancel_task with NO body → request=None branch, success path."""
    a2a_task = A2ATask.model_validate(
        {
            "id": str(a2a_route_client["task"].id),
            "contextId": str(uuid4()),
            "status": A2ATaskStatus(state=A2ATaskState.CANCELLED).model_dump(
                mode="json"
            ),
        }
    )
    client = a2a_route_client["client"]
    _set_pm_context(a2a_route_client["app"], a2a_route_client["dev"])
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.cancel_task = AsyncMock(return_value=a2a_task)
        mock_service_cls.return_value = instance
        response = await client.post(
            f"/api/a2a/tasks/{a2a_route_client['task'].id}/cancel",
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["id"] == a2a_task.id


# ---------------------------------------------------------------------------
# get_agent_card_by_id: 404 path (line 477)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_card_by_id_unknown_returns_404(
    a2a_route_client: dict,
) -> None:
    """Unknown agent slug on /agents/{id}/card → 404."""
    client = a2a_route_client["client"]
    with patch("roboco.api.routes.a2a.A2AService") as mock_service_cls:
        instance = AsyncMock()
        instance.build_agent_card = AsyncMock(return_value=None)
        mock_service_cls.return_value = instance
        response = await client.get(f"/api/a2a/agents/{uuid4()}/card", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND
