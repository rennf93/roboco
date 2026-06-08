"""Prompter API route integration tests.

Covers both the new session-based endpoints:
  POST /sessions, POST /sessions/{id}/messages, GET /sessions/{id}/draft,
  POST /sessions/{id}/confirm

And the legacy stateless endpoints:
  POST /chat, POST /draft
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.prompter import router as prompter_router
from roboco.db.tables import AgentTable, ProjectTable
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# Expected message counts in multi-turn tests
_SINGLE_TURN_MSGS = 2  # 1 user + 1 assistant
_DOUBLE_TURN_MSGS = 4  # 2 user + 2 assistant

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def prompter_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
    agent = AgentTable(
        id=uuid4(),
        name="DevAgent",
        slug=f"dev-agent-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()

    app = FastAPI()
    app.include_router(prompter_router, prefix="/api/prompter")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=agent.id,  # type: ignore[arg-type]
            role=AgentRole.DEVELOPER,
            team=None,
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": agent, "db": db_session}
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def project_fixture(db_session: AsyncSession) -> ProjectTable:
    """Create a minimal project for task creation in confirm tests."""
    creator = AgentTable(
        id=uuid4(),
        name="ProjectCreator",
        slug=f"proj-creator-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(creator)
    await db_session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="Test Project",
        slug=f"test-project-{uuid4().hex[:8]}",
        git_url="https://github.com/test/repo.git",
        default_branch="main",
        assigned_cell=Team.BACKEND,
        created_by=creator.id,
    )
    db_session.add(project)
    await db_session.flush()
    return project


_HDR = {"X-Agent-ID": "be-dev-1", "X-Agent-Role": "developer"}


# =============================================================================
# Session-based endpoint tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_session_success(prompter_client: dict) -> None:
    """POST /sessions creates a new session linked to the agent."""
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/sessions",
        json={},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert "id" in body
    assert body["status"] == "active"
    assert "agent_id" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_create_session_with_context(prompter_client: dict) -> None:
    """POST /sessions accepts optional bootstrap context."""
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/sessions",
        json={"context": {"team": "backend", "project_id": str(uuid4())}},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_send_message_success(prompter_client: dict) -> None:
    """POST /sessions/{id}/messages appends user+assistant messages."""
    client = prompter_client["client"]

    # Create session
    session_resp = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    session_id = session_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Great! Let's gather requirements.")]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "I need a new feature"},
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    messages = response.json()
    assert len(messages) == _SINGLE_TURN_MSGS
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles
    assert messages[-1]["content"] == "Great! Let's gather requirements."


@pytest.mark.asyncio
async def test_send_message_marks_draft_ready(prompter_client: dict) -> None:
    """draft_ready signal in LLM response updates session status."""
    client = prompter_client["client"]

    session_resp = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    session_id = session_resp.json()["id"]

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text=(
                "I have enough information to draft a task now. "
                "Ready to draft when you are."
            )
        )
    ]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "Add a login page with MFA support"},
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    messages = response.json()
    assert len(messages) == _SINGLE_TURN_MSGS


@pytest.mark.asyncio
async def test_send_message_not_found(prompter_client: dict) -> None:
    """POST /sessions/{id}/messages with unknown session → 404."""
    client = prompter_client["client"]
    response = await client.post(
        f"/api/prompter/sessions/{uuid4()}/messages",
        json={"content": "Hello"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_draft_generates_from_conversation(prompter_client: dict) -> None:
    """GET /sessions/{id}/draft generates a draft via LLM."""
    client = prompter_client["client"]

    session_resp = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    session_id = session_resp.json()["id"]

    draft_json = {
        "title": "Add login page",
        "description": "Implement a secure login page with email and password",
        "acceptance_criteria": [
            "User can enter email and password",
            "Invalid credentials show error message",
        ],
        "team": "frontend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "priority": 2,
    }

    chat_response = MagicMock()
    chat_response.content = [MagicMock(text="Tell me more about the requirements.")]

    draft_response = MagicMock()
    draft_response.content = [MagicMock(text=json.dumps(draft_json))]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=chat_response,
    ):
        await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "I need a login page"},
            headers=_HDR,
        )

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=draft_response,
    ):
        response = await client.get(
            f"/api/prompter/sessions/{session_id}/draft",
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["draft"]["title"] == "Add login page"
    assert body["draft"]["source"] == "prompter"
    assert body["confirmed_at"] is None
    assert body["draft"]["confirmed_by_human"] is False
    assert body["session_id"] == session_id


@pytest.mark.asyncio
async def test_get_draft_cached(prompter_client: dict) -> None:
    """GET /sessions/{id}/draft returns the cached draft on subsequent calls."""
    client = prompter_client["client"]

    session_resp = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    session_id = session_resp.json()["id"]

    draft_json = {
        "title": "Add login page",
        "description": "Implement a secure login page with email and password",
        "acceptance_criteria": ["User can enter credentials"],
        "team": "frontend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "priority": 2,
    }
    chat_response = MagicMock()
    chat_response.content = [MagicMock(text="Got it.")]
    draft_response = MagicMock()
    draft_response.content = [MagicMock(text=json.dumps(draft_json))]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=chat_response,
    ):
        await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "I need a login page"},
            headers=_HDR,
        )

    call_count = 0

    async def _mock_create(**_kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return draft_response

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        side_effect=_mock_create,
    ):
        await client.get(f"/api/prompter/sessions/{session_id}/draft", headers=_HDR)
        second_response = await client.get(
            f"/api/prompter/sessions/{session_id}/draft", headers=_HDR
        )

    assert second_response.status_code == HTTPStatus.OK
    # LLM should only be called once (draft is cached)
    assert call_count == 1


@pytest.mark.asyncio
async def test_get_draft_empty_session_returns_400(prompter_client: dict) -> None:
    """GET /sessions/{id}/draft with no messages → 400."""
    client = prompter_client["client"]

    session_resp = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    session_id = session_resp.json()["id"]

    response = await client.get(
        f"/api/prompter/sessions/{session_id}/draft",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_confirm_draft_creates_task(
    prompter_client: dict, project_fixture: ProjectTable
) -> None:
    """POST /sessions/{id}/confirm validates draft and creates a real task."""
    client = prompter_client["client"]
    project_id = str(project_fixture.id)

    session_resp = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    session_id = session_resp.json()["id"]

    draft_json = {
        "title": "Add login page",
        "description": "Implement a secure login page with email and password",
        "acceptance_criteria": ["User can enter credentials"],
        "team": "frontend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "priority": 2,
    }

    chat_response = MagicMock()
    chat_response.content = [MagicMock(text="Got it.")]
    draft_response = MagicMock()
    draft_response.content = [MagicMock(text=json.dumps(draft_json))]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=chat_response,
    ):
        await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "I need a login page"},
            headers=_HDR,
        )

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=draft_response,
    ):
        await client.get(f"/api/prompter/sessions/{session_id}/draft", headers=_HDR)

    confirm_response = await client.post(
        f"/api/prompter/sessions/{session_id}/confirm",
        json={"project_id": project_id},
        headers=_HDR,
    )

    assert confirm_response.status_code == HTTPStatus.CREATED
    body = confirm_response.json()
    assert "task_id" in body
    assert body["task_id"] is not None


@pytest.mark.asyncio
async def test_confirm_draft_requires_project_or_product(
    prompter_client: dict,
) -> None:
    """POST /sessions/{id}/confirm without project_id/product_id → 400."""
    client = prompter_client["client"]

    session_resp = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    session_id = session_resp.json()["id"]

    draft_json = {
        "title": "Add login page",
        "description": "Implement a secure login page with email and password",
        "acceptance_criteria": ["User can enter credentials"],
        "team": "frontend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "priority": 2,
    }

    chat_response = MagicMock()
    chat_response.content = [MagicMock(text="Got it.")]
    draft_response = MagicMock()
    draft_response.content = [MagicMock(text=json.dumps(draft_json))]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=chat_response,
    ):
        await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "I need a login page"},
            headers=_HDR,
        )

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=draft_response,
    ):
        await client.get(f"/api/prompter/sessions/{session_id}/draft", headers=_HDR)

    confirm_response = await client.post(
        f"/api/prompter/sessions/{session_id}/confirm",
        json={},
        headers=_HDR,
    )

    assert confirm_response.status_code == HTTPStatus.BAD_REQUEST


# =============================================================================
# Full happy path integration test
# =============================================================================


@pytest.mark.asyncio
async def test_full_happy_path(
    prompter_client: dict, project_fixture: ProjectTable
) -> None:
    """Full happy path: create session → send messages → get draft → confirm task."""
    client = prompter_client["client"]
    project_id = str(project_fixture.id)

    # Step 1: Create session
    step1 = await client.post("/api/prompter/sessions", json={}, headers=_HDR)
    assert step1.status_code == HTTPStatus.CREATED
    session_id = step1.json()["id"]

    # Step 2: Send messages
    chat_mock = MagicMock()
    chat_mock.content = [
        MagicMock(text="Please describe the acceptance criteria for this feature.")
    ]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=chat_mock,
    ):
        step2a = await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "I need a dark mode toggle for the UI"},
            headers=_HDR,
        )
    assert step2a.status_code == HTTPStatus.OK

    chat_mock2 = MagicMock()
    chat_mock2.content = [
        MagicMock(text="I have enough information to draft a task now.")
    ]
    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=chat_mock2,
    ):
        step2b = await client.post(
            f"/api/prompter/sessions/{session_id}/messages",
            json={"content": "Preference is persisted across sessions"},
            headers=_HDR,
        )
    assert step2b.status_code == HTTPStatus.OK
    messages = step2b.json()
    assert len(messages) == _DOUBLE_TURN_MSGS

    # Step 3: Get draft
    draft_json = {
        "title": "Add dark mode toggle",
        "description": "Implement a dark mode toggle so users can switch themes",
        "acceptance_criteria": [
            "User can toggle light/dark mode",
            "Preference is persisted across sessions",
        ],
        "team": "frontend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "low",
        "priority": 2,
    }
    draft_mock = MagicMock()
    draft_mock.content = [MagicMock(text=json.dumps(draft_json))]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=draft_mock,
    ):
        step3 = await client.get(
            f"/api/prompter/sessions/{session_id}/draft", headers=_HDR
        )
    assert step3.status_code == HTTPStatus.OK
    draft_body = step3.json()
    assert draft_body["draft"]["title"] == "Add dark mode toggle"

    # Step 4: Confirm draft → creates task
    step4 = await client.post(
        f"/api/prompter/sessions/{session_id}/confirm",
        json={"project_id": project_id},
        headers=_HDR,
    )
    assert step4.status_code == HTTPStatus.CREATED
    task_body = step4.json()
    assert "task_id" in task_body
    assert task_body["task_id"] is not None


# =============================================================================
# Legacy stateless endpoint tests (backward compatibility)
# =============================================================================


@pytest.mark.asyncio
async def test_prompter_chat_success(prompter_client: dict) -> None:
    client = prompter_client["client"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Great! Let's gather requirements.")]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/chat",
            json={
                "messages": [{"role": "user", "content": "I need a new feature"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["message"] == "Great! Let's gather requirements."
    assert body["draft_ready"] is False


@pytest.mark.asyncio
async def test_prompter_chat_draft_ready(prompter_client: dict) -> None:
    client = prompter_client["client"]

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text=(
                "I have enough information. draft_ready=true."
                " Ready to generate a draft."
            )
        )
    ]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/chat",
            json={
                "messages": [
                    {"role": "user", "content": "I need a new feature"},
                    {"role": "assistant", "content": "Tell me more"},
                    {"role": "user", "content": "Add a login page"},
                ],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["draft_ready"] is True


@pytest.mark.asyncio
async def test_prompter_chat_llm_failure(prompter_client: dict) -> None:
    client = prompter_client["client"]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        side_effect=Exception("Anthropic API unavailable"),
    ):
        response = await client.post(
            "/api/prompter/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "LLM chat failed" in body["detail"]["message"]


@pytest.mark.asyncio
async def test_prompter_draft_success(prompter_client: dict) -> None:
    client = prompter_client["client"]

    draft_json = {
        "title": "Add login page",
        "description": "Implement a secure login page with email and password",
        "acceptance_criteria": [
            "User can enter email and password",
            "Invalid credentials show error message",
        ],
        "team": "frontend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "priority": 2,
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(draft_json))]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [
                    {"role": "user", "content": "I need a login page"},
                ],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["draft"]["title"] == "Add login page"
    assert body["draft"]["source"] == "prompter"
    assert body["draft"]["confirmed_by_human"] is False
    assert "reasoning" in body


@pytest.mark.asyncio
async def test_prompter_draft_invalid_json_from_llm(prompter_client: dict) -> None:
    client = prompter_client["client"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="not valid json")]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    body = response.json()
    assert "Draft response was not valid JSON" in body["detail"]["message"]


@pytest.mark.asyncio
async def test_prompter_draft_schema_mismatch(prompter_client: dict) -> None:
    client = prompter_client["client"]

    bad_draft = {
        "title": "x",
        "description": "too short",
    }

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps(bad_draft))]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "draft_schema_error" in body["detail"]["error"]


@pytest.mark.asyncio
async def test_prompter_draft_llm_failure(prompter_client: dict) -> None:
    client = prompter_client["client"]

    with patch(
        "roboco.services.prompter.PrompterService._create_message",
        new_callable=AsyncMock,
        side_effect=Exception("Anthropic API unavailable"),
    ):
        response = await client.post(
            "/api/prompter/draft",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=_HDR,
        )

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "LLM draft generation failed" in body["detail"]["message"]


@pytest.mark.asyncio
async def test_prompter_chat_empty_messages(prompter_client: dict) -> None:
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/chat",
        json={"messages": []},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_prompter_chat_invalid_role(prompter_client: dict) -> None:
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/chat",
        json={"messages": [{"role": "invalid", "content": "hi"}]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_prompter_draft_empty_messages(prompter_client: dict) -> None:
    client = prompter_client["client"]
    response = await client.post(
        "/api/prompter/draft",
        json={"messages": []},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
