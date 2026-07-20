"""Unit coverage for the shared notification-text helpers.

``task_display``/``agent_display`` are the producer-side fix so a human
reading a notification (panel, bell, Telegram) sees a task title / agent
slug instead of a raw UUID — see ``roboco/services/notification_text.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.foundation.identity import AGENTS
from roboco.services.notification_text import agent_display, task_display


class _Task:
    def __init__(self, title: str | None) -> None:
        self.title = title


def test_task_display_prefers_title_from_row() -> None:
    task_id = uuid4()
    display = task_display(_Task("Fix login bug"), task_id)
    assert display == f"'Fix login bug' (#{str(task_id)[:8]})"


def test_task_display_accepts_bare_title_string() -> None:
    task_id = uuid4()
    display = task_display("Fix login bug", task_id)
    assert display == f"'Fix login bug' (#{str(task_id)[:8]})"


def test_task_display_truncates_long_titles() -> None:
    long_title = "x" * 100
    task_id = uuid4()
    display = task_display(long_title, task_id)
    assert display == f"'{'x' * 40}' (#{str(task_id)[:8]})"


def test_task_display_falls_back_to_short_id_when_no_title() -> None:
    task_id = uuid4()
    assert task_display(None, task_id) == f"#{str(task_id)[:8]}"
    assert task_display(_Task(None), task_id) == f"#{str(task_id)[:8]}"
    assert task_display(_Task(""), task_id) == f"#{str(task_id)[:8]}"


@pytest.mark.asyncio
async def test_agent_display_resolves_via_static_map() -> None:
    """A fixed-roster agent's UUID resolves to its slug with zero DB I/O."""
    row = AGENTS["be-dev-1"]
    assert await agent_display(row.uuid) == "be-dev-1"
    assert await agent_display(str(row.uuid)) == "be-dev-1"


@pytest.mark.asyncio
async def test_agent_display_falls_back_to_db_for_unknown_uuid() -> None:
    """A UUID absent from the static map resolves via ``get_agent_slug`` when
    a db session is supplied."""
    unknown_uuid = uuid4()
    db: Any = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "fresh-agent"
    db.execute = AsyncMock(return_value=result)

    assert await agent_display(unknown_uuid, db) == "fresh-agent"


@pytest.mark.asyncio
async def test_agent_display_raw_fallback_without_db() -> None:
    """No static-map hit + no db session ⇒ the raw value passes through."""
    unknown_uuid = uuid4()
    assert await agent_display(unknown_uuid) == str(unknown_uuid)


@pytest.mark.asyncio
async def test_agent_display_raw_fallback_for_non_uuid_slug() -> None:
    """A plain slug string that isn't in the UUID-keyed map passes through
    unchanged (e.g. a value that's already a friendly slug)."""
    assert await agent_display("some-slug") == "some-slug"


@pytest.mark.asyncio
async def test_agent_display_passes_through_none() -> None:
    """None stays None — "unassigned"/"its owner" wording stays at call sites."""
    assert await agent_display(None) is None
