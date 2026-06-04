"""Integration tests for Prompter API routes.

Covers: model catalog, session CRUD, SSE chat streaming,
and the launch action (task creation + session status transition).

All tests that touch the database require a live Postgres instance and are
skipped automatically when Postgres is unreachable (see top-level conftest.py).
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.prompter import router as prompter_router
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext
from roboco.services.prompter import PromptService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse_events(content: bytes) -> list[dict[str, str]]:
    """Parse raw SSE bytes into a list of ``{event, data}`` dicts."""
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in content.decode().splitlines():
        line = raw_line.rstrip()
        if line.startswith("event:"):
            current["event"] = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current["data"] = line[len("data:") :].strip()
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def prompt_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
    """Yield a dict with an httpx client and seeded DB objects."""
    agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
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
        name="P",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(prompter_router, prefix="/api/prompter")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=UUID(str(agent.id)),
            role=AgentRole.MAIN_PM,
            team=None,
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "agent": agent,
            "project": project,
            "db": db_session,
        }
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Minimal client for tests that don't touch the database (e.g. /models).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def prompter_app_client() -> AsyncIterator[AsyncClient]:
    """Yield an httpx client wired to the prompter router only.

    This fixture does NOT require a live Postgres instance — it overrides
    ``get_agent_context`` with a stub returning a synthetic AgentContext and
    does not override ``get_db`` (the models endpoint never touches the DB).
    """
    stub_agent_id = uuid4()

    app = FastAPI()
    app.include_router(prompter_router, prefix="/api/prompter")

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=stub_agent_id,
            role=AgentRole.MAIN_PM,
            team=None,
        )

    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Model catalog (no DB required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_models_returns_200_and_list(
    prompter_app_client: AsyncClient,
) -> None:
    """GET /api/prompter/models returns a non-empty list with label+description."""
    response = await prompter_app_client.get("/api/prompter/models")

    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    first = data[0]
    assert "id" in first
    assert "label" in first
    assert "description" in first


@pytest.mark.asyncio
async def test_get_models_no_raw_provider_ids(
    prompter_app_client: AsyncClient,
) -> None:
    """Labels must not contain raw Claude model IDs (e.g. 'claude-opus-4-6')."""
    response = await prompter_app_client.get("/api/prompter/models")

    data = response.json()
    for model in data:
        # Raw Claude model IDs follow the pattern "claude-*-N[-YYYMMDD]"
        assert "claude-" not in model["label"], (
            f"Raw model ID found in label: {model['label']}"
        )


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_returns_201(prompt_client: dict) -> None:
    """POST /api/prompter/sessions creates a DRAFT session."""
    client = prompt_client["client"]
    response = await client.post(
        "/api/prompter/sessions",
        json={"model": "sonnet"},
    )
    assert response.status_code == HTTPStatus.CREATED
    data = response.json()
    assert data["status"] == "draft"
    assert data["model"] == "sonnet"
    assert data["turns"] == []


@pytest.mark.asyncio
async def test_list_sessions_returns_200(prompt_client: dict) -> None:
    """GET /api/prompter/sessions returns sessions created in this test."""
    client = prompt_client["client"]
    await client.post("/api/prompter/sessions", json={})
    await client.post("/api/prompter/sessions", json={})

    response = await client.get("/api/prompter/sessions")
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert isinstance(data, list)
    _MIN_SESSIONS = 2
    assert len(data) >= _MIN_SESSIONS


@pytest.mark.asyncio
async def test_get_session_by_id(prompt_client: dict) -> None:
    """GET /api/prompter/sessions/{id} returns the session with turns list."""
    client = prompt_client["client"]
    create_resp = await client.post(
        "/api/prompter/sessions",
        json={"system_prompt": "Be helpful."},
    )
    session_id = create_resp.json()["id"]

    response = await client.get(f"/api/prompter/sessions/{session_id}")
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    assert data["id"] == session_id
    assert data["system_prompt"] == "Be helpful."
    assert data["turns"] == []


@pytest.mark.asyncio
async def test_get_session_not_found(prompt_client: dict) -> None:
    """GET /api/prompter/sessions/{unknown_id} returns 404."""
    client = prompt_client["client"]
    response = await client.get(f"/api/prompter/sessions/{uuid4()}")
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_patch_session_status(prompt_client: dict) -> None:
    """PATCH /api/prompter/sessions/{id}/status transitions the status."""
    client = prompt_client["client"]
    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/api/prompter/sessions/{session_id}/status",
        json={"status": "abandoned"},
    )
    assert patch_resp.status_code == HTTPStatus.OK
    assert patch_resp.json()["status"] == "abandoned"


@pytest.mark.asyncio
async def test_patch_session_status_not_found(prompt_client: dict) -> None:
    """PATCH on an unknown session returns 404."""
    client = prompt_client["client"]
    response = await client.patch(
        f"/api/prompter/sessions/{uuid4()}/status",
        json={"status": "abandoned"},
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Chat streaming
# ---------------------------------------------------------------------------


class _MockTextStream:
    """Async iterator that yields a preset list of text tokens."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = iter(tokens)

    def __aiter__(self) -> _MockTextStream:
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._tokens)
        except StopIteration:
            raise StopAsyncIteration from None


class _MockAnthropicStream:
    """Async context manager that exposes a fake text_stream."""

    def __init__(self, tokens: list[str]) -> None:
        self.text_stream = _MockTextStream(tokens)

    async def __aenter__(self) -> _MockAnthropicStream:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


@pytest.mark.asyncio
async def test_chat_streaming_happy_path(prompt_client: dict) -> None:
    """POST /api/prompter/chat streams tokens then a done event."""
    client = prompt_client["client"]

    # Create a session to chat on.
    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]

    tokens = ["Hello", " world", "!"]
    mock_stream = _MockAnthropicStream(tokens)

    mock_anthropic_instance = MagicMock()
    mock_anthropic_instance.messages.stream.return_value = mock_stream

    with patch("anthropic.AsyncAnthropic", return_value=mock_anthropic_instance):
        response = await client.post(
            "/api/prompter/chat",
            json={"session_id": session_id, "message": "Hello!"},
        )

    assert response.status_code == HTTPStatus.OK
    assert "text/event-stream" in response.headers.get("content-type", "")

    events = _parse_sse_events(response.content)
    token_events = [e for e in events if e.get("event") == "token"]
    done_events = [e for e in events if e.get("event") == "done"]

    assert len(token_events) == len(tokens)
    for i, ev in enumerate(token_events):
        assert ev["data"] == tokens[i]

    assert len(done_events) == 1
    assert done_events[0]["data"] == "Hello world!"


@pytest.mark.asyncio
async def test_chat_persists_turns(
    prompt_client: dict,
    db_session: AsyncSession,
) -> None:
    """After chat, a user turn and an assistant turn are saved to the session."""
    client = prompt_client["client"]

    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]

    tokens = ["Test", " response"]
    mock_stream = _MockAnthropicStream(tokens)
    mock_instance = MagicMock()
    mock_instance.messages.stream.return_value = mock_stream

    with patch("anthropic.AsyncAnthropic", return_value=mock_instance):
        await client.post(
            "/api/prompter/chat",
            json={"session_id": session_id, "message": "Test question"},
        )

    # Refresh via service.
    svc = PromptService(db_session)
    turns = await svc.list_turns(UUID(session_id))
    _EXPECTED_TURNS = 2
    assert len(turns) == _EXPECTED_TURNS
    assert turns[0].role == "user"
    assert turns[0].content == "Test question"
    assert turns[1].role == "assistant"
    assert turns[1].content == "Test response"


@pytest.mark.asyncio
async def test_chat_session_not_found(prompt_client: dict) -> None:
    """POST /api/prompter/chat with unknown session_id returns 404."""
    client = prompt_client["client"]
    response = await client.post(
        "/api/prompter/chat",
        json={"session_id": str(uuid4()), "message": "hi"},
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Launch action
# ---------------------------------------------------------------------------

_VALID_TASK_JSON = json.dumps(
    {
        "title": "Add user authentication endpoint",
        "description": "Implement a POST /auth/login endpoint that validates creds.",
        "acceptance_criteria": [
            "Returns 200 with JWT on valid credentials",
            "Returns 401 on invalid credentials",
        ],
        "team": "backend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
    }
)


@pytest.mark.asyncio
async def test_launch_creates_task_and_sets_launched(
    prompt_client: dict,
    db_session: AsyncSession,
) -> None:
    """POST /launch creates a task and transitions session to LAUNCHED."""
    client = prompt_client["client"]
    project = prompt_client["project"]

    # Create a session and add an assistant turn with valid task JSON.
    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]

    svc = PromptService(db_session)
    session_uuid = UUID(session_id)
    await svc.create_turn(
        session_uuid, role="user", content="Create a login endpoint.", turn_index=0
    )
    await svc.create_turn(
        session_uuid,
        role="assistant",
        content=f"Sure! Here is the task:\n\n```json\n{_VALID_TASK_JSON}\n```",
        turn_index=1,
    )

    response = await client.post(
        f"/api/prompter/sessions/{session_id}/launch",
        json={"project_id": str(project.id)},
    )

    assert response.status_code == HTTPStatus.CREATED, response.text
    data = response.json()
    assert "task_id" in data
    assert data["session_id"] == session_id
    assert data["session_status"] == "launched"


@pytest.mark.asyncio
async def test_launch_requires_project_or_product(prompt_client: dict) -> None:
    """Omitting both project_id and product_id returns 400."""
    client = prompt_client["client"]
    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/prompter/sessions/{session_id}/launch",
        json={},
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_launch_both_project_and_product_returns_400(
    prompt_client: dict,
) -> None:
    """Supplying both project_id and product_id returns 400."""
    client = prompt_client["client"]
    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/prompter/sessions/{session_id}/launch",
        json={"project_id": str(uuid4()), "product_id": str(uuid4())},
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_launch_no_assistant_turns_returns_400(prompt_client: dict) -> None:
    """Launching a session with no assistant turns returns 400."""
    client = prompt_client["client"]
    project = prompt_client["project"]

    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/prompter/sessions/{session_id}/launch",
        json={"project_id": str(project.id)},
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_launch_invalid_json_in_turn_returns_400(
    prompt_client: dict,
    db_session: AsyncSession,
) -> None:
    """An assistant turn with no extractable JSON returns 400."""
    client = prompt_client["client"]
    project = prompt_client["project"]

    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]
    session_uuid = UUID(session_id)

    svc = PromptService(db_session)
    await svc.create_turn(
        session_uuid,
        role="assistant",
        content="I don't have a JSON block here.",
        turn_index=0,
    )

    response = await client.post(
        f"/api/prompter/sessions/{session_id}/launch",
        json={"project_id": str(project.id)},
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_launch_missing_required_fields_returns_400(
    prompt_client: dict,
    db_session: AsyncSession,
) -> None:
    """A JSON block missing required fields returns 400."""
    client = prompt_client["client"]
    project = prompt_client["project"]

    create_resp = await client.post("/api/prompter/sessions", json={})
    session_id = create_resp.json()["id"]
    session_uuid = UUID(session_id)

    incomplete_json = json.dumps({"title": "Only title"})
    svc = PromptService(db_session)
    await svc.create_turn(
        session_uuid,
        role="assistant",
        content=f"```json\n{incomplete_json}\n```",
        turn_index=0,
    )

    response = await client.post(
        f"/api/prompter/sessions/{session_id}/launch",
        json={"project_id": str(project.id)},
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_launch_session_not_found(prompt_client: dict) -> None:
    """Launching an unknown session returns 404."""
    client = prompt_client["client"]
    project = prompt_client["project"]

    response = await client.post(
        f"/api/prompter/sessions/{uuid4()}/launch",
        json={"project_id": str(project.id)},
    )
    assert response.status_code == HTTPStatus.NOT_FOUND
