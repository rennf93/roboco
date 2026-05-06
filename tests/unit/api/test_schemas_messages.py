"""Coverage for roboco.api.schemas.messages helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from roboco.api.schemas.messages import message_to_response


def _stub_message(*, edit_history: list | None = None, edited_at=None):
    """Build a MessageTable-shaped object."""
    return SimpleNamespace(
        id=uuid4(),
        agent_id=uuid4(),
        channel_id=uuid4(),
        group_id=uuid4(),
        session_id=uuid4(),
        type="dialogue",
        content="hello",
        content_length=5,
        is_reply=False,
        reply_to=None,
        mentions=[],
        task_id=None,
        commit_ref=None,
        timestamp=datetime.now(UTC),
        edited_at=edited_at,
        edit_history=edit_history,
    )


def test_message_to_response_was_edited_true_when_history_present() -> None:
    """Lines 100-101: was_edited is None and edit_history non-empty → True."""
    msg = _stub_message(edit_history=[{"old": "x"}])
    response = message_to_response(msg)
    assert response.was_edited is True


def test_message_to_response_was_edited_false_when_no_history() -> None:
    msg = _stub_message(edit_history=None)
    response = message_to_response(msg)
    assert response.was_edited is False


def test_message_to_response_explicit_was_edited_overrides() -> None:
    msg = _stub_message(edit_history=None)
    response = message_to_response(msg, was_edited=True)
    assert response.was_edited is True
