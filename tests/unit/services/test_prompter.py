"""Unit tests for PrompterService.

Tests the service layer logic with mocked LLM calls. Uses an in-memory
async session (via conftest fixtures) for DB-backed tests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable
from roboco.models.base import AgentRole, AgentStatus
from roboco.services.base import NotFoundError, ServiceError, ValidationError
from roboco.services.prompter import (
    PrompterService,
    _build_chat_prompt,
    _build_draft_prompt,
    _build_reasoning,
    _detect_draft_ready,
    get_prompter_service,
)

# =============================================================================
# Pure function tests (no DB)
# =============================================================================


def test_detect_draft_ready_signals() -> None:
    signals = [
        "I have enough information to proceed",
        "Ready to generate a draft now.",
        "ready to draft the task",
        "i can now draft this.",
        "draft_ready=true",
        "The task is draft ready",
    ]
    for text in signals:
        assert _detect_draft_ready(text), f"Expected True for: {text!r}"


def test_detect_draft_ready_negative() -> None:
    not_signals = [
        "Tell me more about the feature.",
        "Could you clarify the acceptance criteria?",
        "Let's continue the conversation.",
    ]
    for text in not_signals:
        assert not _detect_draft_ready(text), f"Expected False for: {text!r}"


def test_build_chat_prompt_basic() -> None:
    messages = [
        {"role": "user", "content": "I need a feature"},
        {"role": "assistant", "content": "Tell me more"},
    ]
    prompt = _build_chat_prompt(messages, None)
    assert "user: I need a feature" in prompt
    assert "assistant: Tell me more" in prompt
    assert "Continue the conversation" in prompt


def test_build_chat_prompt_with_context() -> None:
    messages = [{"role": "user", "content": "hello"}]
    prompt = _build_chat_prompt(messages, {"team": "backend"})
    assert "Context:" in prompt
    assert "team: backend" in prompt


def test_build_draft_prompt() -> None:
    messages = [{"role": "user", "content": "I need a login page"}]
    prompt = _build_draft_prompt(messages, None)
    assert "valid JSON" in prompt
    assert "user: I need a login page" in prompt


def test_build_reasoning() -> None:
    messages = [{"role": "user", "content": "Hello"}] * 3
    draft = {"title": "My Task", "team": "backend", "estimated_complexity": "medium"}
    reasoning = _build_reasoning(messages, draft)
    assert "My Task" in reasoning
    assert "backend" in reasoning
    assert "medium" in reasoning
    assert "3 messages" in reasoning


# =============================================================================
# Factory
# =============================================================================


def test_get_prompter_service_no_db() -> None:
    service = get_prompter_service()
    assert isinstance(service, PrompterService)
    assert service._db is None


def test_get_prompter_service_raises_without_db_for_session_methods() -> None:
    service = get_prompter_service()
    with pytest.raises(ServiceError, match="DB session"):
        _ = service._session


# =============================================================================
# Stateless chat / draft (with mocked LLM)
# =============================================================================


@pytest.mark.asyncio
async def test_chat_success_with_mock_llm() -> None:
    service = get_prompter_service()

    with patch.object(
        service,
        "_create_message",
        new_callable=AsyncMock,
        return_value="Great, let's continue!",
    ):
        result = await service.chat(
            messages=[{"role": "user", "content": "I need a feature"}]
        )

    assert result["message"] == "Great, let's continue!"
    assert result["draft_ready"] is False


@pytest.mark.asyncio
async def test_chat_draft_ready_signal() -> None:
    service = get_prompter_service()

    with patch.object(
        service,
        "_create_message",
        new_callable=AsyncMock,
        return_value="I have enough information. Ready to draft.",
    ):
        result = await service.chat(
            messages=[{"role": "user", "content": "I need a feature"}]
        )

    assert result["draft_ready"] is True


@pytest.mark.asyncio
async def test_chat_raises_on_empty_response() -> None:
    service = get_prompter_service()

    with (
        patch.object(
            service, "_create_message", new_callable=AsyncMock, return_value=""
        ),
        pytest.raises(ServiceError, match="LLM returned empty content"),
    ):
        await service.chat(messages=[{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_chat_raises_on_llm_error() -> None:
    service = get_prompter_service()

    with (
        patch.object(
            service,
            "_create_message",
            new_callable=AsyncMock,
            side_effect=Exception("API unavailable"),
        ),
        pytest.raises(ServiceError, match="LLM chat failed"),
    ):
        await service.chat(messages=[{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_draft_success_with_mock_llm() -> None:
    service = get_prompter_service()

    draft_data = {
        "title": "Add login",
        "description": "Implement login functionality with JWT tokens",
        "acceptance_criteria": ["User can log in"],
        "team": "backend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
    }

    with patch.object(
        service,
        "_create_message",
        new_callable=AsyncMock,
        return_value=json.dumps(draft_data),
    ):
        result = await service.draft(
            messages=[{"role": "user", "content": "I need a login feature"}]
        )

    assert result["draft"]["title"] == "Add login"
    assert result["draft"]["source"] == "prompter"
    assert result["draft"]["confirmed_by_human"] is False
    assert "reasoning" in result


@pytest.mark.asyncio
async def test_draft_raises_on_invalid_json() -> None:
    service = get_prompter_service()

    with (
        patch.object(
            service,
            "_create_message",
            new_callable=AsyncMock,
            return_value="Not JSON at all",
        ),
        pytest.raises(ValidationError, match="not valid JSON"),
    ):
        await service.draft(messages=[{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_draft_raises_on_llm_error() -> None:
    service = get_prompter_service()

    with (
        patch.object(
            service,
            "_create_message",
            new_callable=AsyncMock,
            side_effect=Exception("API unavailable"),
        ),
        pytest.raises(ServiceError, match="LLM draft generation failed"),
    ):
        await service.draft(messages=[{"role": "user", "content": "Hello"}])


# =============================================================================
# Session-based: create_session (DB-backed via conftest)
# =============================================================================


@pytest.mark.asyncio
async def test_create_session_db(db_session: Any) -> None:
    """create_session persists a PrompterSessionTable row."""
    service = get_prompter_service(db=db_session)

    agent = AgentTable(
        id=uuid4(),
        name="TestAgent",
        slug=f"test-{uuid4().hex[:8]}",
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

    session = await service.create_session(agent_id=agent.id)  # type: ignore[arg-type]
    assert session.id is not None
    assert session.status == "active"
    assert session.agent_id == agent.id


@pytest.mark.asyncio
async def test_get_session_not_found(db_session: Any) -> None:
    """_get_session raises NotFoundError for unknown session ID."""
    service = get_prompter_service(db=db_session)
    with pytest.raises(NotFoundError):
        await service._get_session(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_get_draft_empty_session_raises(db_session: Any) -> None:
    """get_or_generate_draft raises ValidationError if no messages exist."""
    service = get_prompter_service(db=db_session)

    agent = AgentTable(
        id=uuid4(),
        name="TestAgent",
        slug=f"test-{uuid4().hex[:8]}",
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

    session = await service.create_session(agent_id=agent.id)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="empty conversation"):
        await service.get_or_generate_draft(
            session_id=session.id,  # type: ignore[arg-type]
            agent_id=agent.id,  # type: ignore[arg-type]
        )
