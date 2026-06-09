"""Unit tests for Prompter API schemas.

Covers schema validation for both the session-based and legacy schemas.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from roboco.api.schemas.prompter import (
    CellWork,
    ChatMessage,
    PrompterChatRequest,
    PrompterDraftTask,
    PrompterMessageRequest,
    PrompterTurnResponse,
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
# Structured spec fields
# =============================================================================


def test_cell_work_valid() -> None:
    cw = CellWork(team="backend", summary="Build the endpoint", items=["Route", "Test"])
    assert cw.team.value == "backend"
    assert cw.items == ["Route", "Test"]


def test_cell_work_requires_summary() -> None:
    with pytest.raises(PydanticValidationError):
        CellWork(team="backend", summary="")


def test_draft_task_structured_fields_default_empty() -> None:
    draft = PrompterDraftTask(
        title="Add login page",
        description="Implement a secure login page with email and password",
        acceptance_criteria=["User can log in"],
        team="frontend",
        task_type="code",
        nature="technical",
        estimated_complexity="medium",
    )
    assert draft.objective is None
    assert draft.what_this_builds == []
    assert draft.the_work == []
    assert draft.notes == []


def test_draft_task_with_structured_fields() -> None:
    draft = PrompterDraftTask(
        title="Ship the Prompter",
        description="A board-led feature spanning three cells, fully wired.",
        acceptance_criteria=["It works end to end"],
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity="high",
        objective="Let humans chat a task into existence.",
        what_this_builds=["A /prompter page", "A chat endpoint"],
        the_work=[
            CellWork(team="backend", summary="Chat endpoint", items=["Route"]),
            CellWork(team="frontend", summary="Chat UI", items=["Page"]),
        ],
        notes=["Reuse the LLM service"],
    )
    assert [w.team.value for w in draft.the_work] == ["backend", "frontend"]


def test_confirm_request_carries_edited_draft() -> None:
    draft = PrompterDraftTask(
        title="Edited title",
        description="An edited description that clears the minimum length.",
        acceptance_criteria=["Done"],
        team="frontend",
        task_type="code",
        nature="technical",
        estimated_complexity="low",
    )
    req = TaskConfirmRequest(project_id=uuid4(), draft=draft)
    assert req.draft is not None
    assert req.draft.title == "Edited title"


def test_turn_response_shape() -> None:
    resp = PrompterTurnResponse(messages=[], draft_ready=True, scale="multi")
    assert resp.draft_ready is True
    assert resp.scale == "multi"
    assert resp.messages == []


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
