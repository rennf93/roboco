"""Tests for indexes.base — None-safe doc_source builder."""

from __future__ import annotations

from roboco.services.optimal_brain.indexes.base import build_doc_source
from roboco.services.optimal_brain.indexes.conversations import ConversationsIndexPlugin
from roboco.services.optimal_brain.indexes.journals import JournalsIndexPlugin

# ---------------------------------------------------------------------------
# Tests for build_doc_source (module-level helper)
# ---------------------------------------------------------------------------


def test_doc_source_returns_none_when_id_missing() -> None:
    assert build_doc_source(kind="journals", id_=None) is None


def test_doc_source_with_id() -> None:
    result = build_doc_source(kind="journals", id_="abc-123")
    assert result == "roboco://journals/abc-123"


def test_doc_source_conversations_with_id() -> None:
    result = build_doc_source(kind="conversations", id_="sess-001-agent-007")
    assert result == "roboco://conversations/sess-001-agent-007"


def test_doc_source_conversations_returns_none_when_id_missing() -> None:
    assert build_doc_source(kind="conversations", id_=None) is None


# ---------------------------------------------------------------------------
# Tests for JournalsIndexPlugin.build_source_uri
# ---------------------------------------------------------------------------


def test_journals_plugin_build_source_uri_returns_none_when_entry_id_none() -> None:
    """build_source_uri returns None when entry_id kwarg is None (the spam scenario)."""
    plugin = JournalsIndexPlugin.__new__(JournalsIndexPlugin)
    result = plugin.build_source_uri(doc_id=None, entry_id=None)
    assert result is None


def test_journals_plugin_build_source_uri_with_entry_id() -> None:
    """build_source_uri returns correct URI when entry_id is set."""
    plugin = JournalsIndexPlugin.__new__(JournalsIndexPlugin)
    result = plugin.build_source_uri(doc_id=None, entry_id="entry-abc-123")
    assert result == "roboco://journals/entry-abc-123"


def test_journals_plugin_build_source_uri_falls_back_to_doc_id() -> None:
    """build_source_uri falls back to doc_id when entry_id kwarg is absent."""
    plugin = JournalsIndexPlugin.__new__(JournalsIndexPlugin)
    result = plugin.build_source_uri(doc_id="fallback-id")
    assert result == "roboco://journals/fallback-id"


# ---------------------------------------------------------------------------
# Tests for ConversationsIndexPlugin.build_source_uri
# ---------------------------------------------------------------------------


def test_conversations_plugin_returns_none_when_session_id_none() -> None:
    """build_source_uri returns None when session_id kwarg is None."""
    plugin = ConversationsIndexPlugin.__new__(ConversationsIndexPlugin)
    result = plugin.build_source_uri(doc_id=None, session_id=None, agent_id="agent-1")
    assert result is None


def test_conversations_plugin_build_source_uri_with_session_id() -> None:
    """build_source_uri returns correct URI when session_id is set."""
    plugin = ConversationsIndexPlugin.__new__(ConversationsIndexPlugin)
    result = plugin.build_source_uri(
        doc_id=None, session_id="sess-999", agent_id="agent-007"
    )
    assert result == "roboco://conversations/sess-999-agent-007"
