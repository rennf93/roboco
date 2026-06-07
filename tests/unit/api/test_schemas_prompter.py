"""Unit tests for Prompter API schemas.

Covers schema validation for both the session-based and legacy schemas.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from roboco.api.schemas.prompter import (
    ChatMessage,
    PrompterChatRequest,
    PrompterDraftTask,
    PrompterMessageRequest,
    PrompterSessionCreateRequest,
    TaskConfirmRequest,
)

# =============================================================================
# ChatMessage
# =============================================================================


def test_chat_message_valid_roles() -> None:
    for role in ("user", "assistant", "system"):
        msg = ChatMessage(role=role, content="Hello")
        assert msg.role == role


def test_chat_message_invalid_role() -> None:
    with pytest.raises(PydanticValidationError) as exc_info:
        ChatMessage(role="admin", content="Hello")
    assert "role must be one of" in str(exc_info.value)


def test_chat_message_empty_content() -> None:
    with pytest.raises(PydanticValidationError):
        ChatMessage(role="user", content="")


# =============================================================================
# PrompterSessionCreateRequest
# =============================================================================


def test_session_create_request_defaults() -> None:
    req = PrompterSessionCreateRequest()
    assert req.context == {}


def test_session_create_request_with_context() -> None:
    req = PrompterSessionCreateRequest(context={"team": "backend"})
    assert req.context == {"team": "backend"}


# =============================================================================
# PrompterMessageRequest
# =============================================================================


def test_message_request_valid() -> None:
    req = PrompterMessageRequest(content="I need a feature")
    assert req.content == "I need a feature"
    assert req.context == {}


def test_message_request_empty_content() -> None:
    with pytest.raises(PydanticValidationError):
        PrompterMessageRequest(content="")


def test_message_request_with_context() -> None:
    req = PrompterMessageRequest(content="Hello", context={"key": "value"})
    assert req.context["key"] == "value"


# =============================================================================
# TaskConfirmRequest
# =============================================================================


def test_task_confirm_request_all_optional() -> None:
    req = TaskConfirmRequest()
    assert req.project_id is None
    assert req.product_id is None
    assert req.assigned_to is None
    assert req.overrides == {}


def test_task_confirm_request_with_project() -> None:
    pid = uuid4()
    req = TaskConfirmRequest(project_id=pid)
    assert req.project_id == pid


# =============================================================================
# PrompterDraftTask
# =============================================================================


def test_draft_task_valid() -> None:
    draft = PrompterDraftTask(
        title="Add login page",
        description="Implement a secure login page with email and password",
        acceptance_criteria=["User can log in"],
        team="frontend",
        task_type="code",
        nature="technical",
        estimated_complexity="medium",
    )
    assert draft.title == "Add login page"
    assert draft.source == "prompter"
    assert draft.confirmed_by_human is False


def test_draft_task_title_too_long() -> None:
    with pytest.raises(PydanticValidationError):
        PrompterDraftTask(
            title="x" * 201,
            description="Implement a secure login page with email and password",
            acceptance_criteria=["User can log in"],
            team="frontend",
            task_type="code",
            nature="technical",
            estimated_complexity="medium",
        )


def test_draft_task_description_too_short() -> None:
    with pytest.raises(PydanticValidationError):
        PrompterDraftTask(
            title="Add login page",
            description="short",  # <20 chars
            acceptance_criteria=["User can log in"],
            team="frontend",
            task_type="code",
            nature="technical",
            estimated_complexity="medium",
        )


def test_draft_task_empty_acceptance_criteria() -> None:
    with pytest.raises(PydanticValidationError):
        PrompterDraftTask(
            title="Add login page",
            description="Implement a secure login page with email and password",
            acceptance_criteria=[],
            team="frontend",
            task_type="code",
            nature="technical",
            estimated_complexity="medium",
        )


def test_draft_task_invalid_team() -> None:
    with pytest.raises(PydanticValidationError):
        PrompterDraftTask(
            title="Add login page",
            description="Implement a secure login page with email and password",
            acceptance_criteria=["User can log in"],
            team="infra",  # invalid
            task_type="code",
            nature="technical",
            estimated_complexity="medium",
        )


def test_draft_task_priority_bounds() -> None:
    # Valid bounds
    for p in (0, 1, 2, 3):
        d = PrompterDraftTask(
            title="Add login page",
            description="Implement a secure login page with email and password",
            acceptance_criteria=["User can log in"],
            team="frontend",
            task_type="code",
            nature="technical",
            estimated_complexity="medium",
            priority=p,
        )
        assert d.priority == p

    # Out of bounds
    with pytest.raises(PydanticValidationError):
        PrompterDraftTask(
            title="Add login page",
            description="Implement a secure login page with email and password",
            acceptance_criteria=["User can log in"],
            team="frontend",
            task_type="code",
            nature="technical",
            estimated_complexity="medium",
            priority=4,
        )


# =============================================================================
# PrompterChatRequest (legacy)
# =============================================================================


def test_chat_request_requires_messages() -> None:
    with pytest.raises(PydanticValidationError):
        PrompterChatRequest(messages=[])


def test_chat_request_valid() -> None:
    req = PrompterChatRequest(messages=[ChatMessage(role="user", content="Hello")])
    assert len(req.messages) == 1
    assert req.context == {}
