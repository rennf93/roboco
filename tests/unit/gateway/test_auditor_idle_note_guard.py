"""Tests for the auditor's i_am_idle note obligation (_auditor_note_guard).

Every role with a dedicated note section is obligated to populate it, like
journals. The auditor owns no delivery task and has no delivery verb, so its
obligation is session-scoped: it must have recorded an observation within the
window before it may go idle. Inert for every other role.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_auditor_idle_blocked_without_recent_observation() -> None:
    """An auditor with no recent journal entry is refused idle."""
    auditor_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="auditor", team="board")
    journal = AsyncMock()
    journal.has_recent_entry.return_value = False
    c = Choreographer(_make_deps(task=task_svc, journal=journal))

    guard = await c._auditor_note_guard(auditor_id, briefing={})

    assert guard is not None
    body = guard.as_dict()
    assert body["error"] == "invalid_state"
    assert "auditor_notes" in body["message"]
    assert "note(scope='reflect'" in body["remediate"]
    # The window query was actually consulted.
    journal.has_recent_entry.assert_awaited_once()


@pytest.mark.asyncio
async def test_auditor_idle_allowed_with_recent_observation() -> None:
    """An auditor that recorded an observation recently may idle."""
    auditor_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="auditor", team="board")
    journal = AsyncMock()
    journal.has_recent_entry.return_value = True
    c = Choreographer(_make_deps(task=task_svc, journal=journal))

    guard = await c._auditor_note_guard(auditor_id, briefing={})

    assert guard is None


@pytest.mark.asyncio
async def test_idle_note_guard_inert_for_non_auditor() -> None:
    """A developer never trips the auditor guard (no journal lookup at all)."""
    dev_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer", team="backend")
    journal = AsyncMock()
    c = Choreographer(_make_deps(task=task_svc, journal=journal))

    guard = await c._auditor_note_guard(dev_id, briefing={})

    assert guard is None
    journal.has_recent_entry.assert_not_awaited()
