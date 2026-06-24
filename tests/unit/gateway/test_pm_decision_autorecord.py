"""Tests for ``_ensure_pm_decision`` — the write-then-gate auto-record.

A PM verb that carries a substantive rationale (complete/submit_up/
submit_root ``notes``, escalate ``reason``, or a synthesized unblock line)
records it as the journal:decision the tracing gate requires *before* the
gate runs. This removes the dominant stall where a loaded/weak-model PM
forgot the separate note(scope='decision') call and looped on a
tracing_gap → respawn. The gate itself is unchanged (see
test_pm_decision_window.py) — this only ensures a fresh decision exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings as _roboco_settings
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
)


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    """Async-mock every service the Choreographer depends on."""
    base = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    task = base["task"]
    task.session = MagicMock()
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_writes_decision_when_none_exists() -> None:
    agent_id, task_id = uuid4(), uuid4()
    journal = AsyncMock()
    journal.latest_decision_at.return_value = None
    c = Choreographer(_make_deps(journal=journal))

    await c._ensure_pm_decision(agent_id, task_id, "Merging PR #120; all ACs verified")

    journal.write_decision.assert_awaited_once()
    _args, kwargs = journal.write_decision.call_args
    assert kwargs["agent_id"] == agent_id
    assert kwargs["task_id"] == task_id
    assert "Merging PR #120" in kwargs["content"]


@pytest.mark.asyncio
async def test_skips_when_fresh_decision_already_exists() -> None:
    journal = AsyncMock()
    journal.latest_decision_at.return_value = datetime.now(UTC) - timedelta(seconds=60)
    c = Choreographer(_make_deps(journal=journal))

    await c._ensure_pm_decision(uuid4(), uuid4(), "rationale text here")

    journal.write_decision.assert_not_awaited()


@pytest.mark.asyncio
async def test_writes_when_existing_decision_is_stale() -> None:
    journal = AsyncMock()
    journal.latest_decision_at.return_value = datetime.now(UTC) - timedelta(
        seconds=_roboco_settings.pm_decision_window_seconds + 1
    )
    c = Choreographer(_make_deps(journal=journal))

    await c._ensure_pm_decision(uuid4(), uuid4(), "fresh rationale around this point")

    journal.write_decision.assert_awaited_once()


@pytest.mark.asyncio
async def test_noop_on_empty_rationale() -> None:
    journal = AsyncMock()
    c = Choreographer(_make_deps(journal=journal))

    await c._ensure_pm_decision(uuid4(), uuid4(), "   ")
    await c._ensure_pm_decision(uuid4(), uuid4(), None)

    journal.latest_decision_at.assert_not_awaited()
    journal.write_decision.assert_not_awaited()


@pytest.mark.asyncio
async def test_swallows_write_failure_best_effort() -> None:
    """A journal write failure must not crash the verb — the gate then
    rejects normally (the pre-fix behaviour), never a 500."""
    journal = AsyncMock()
    journal.latest_decision_at.return_value = None
    journal.write_decision.side_effect = RuntimeError("db down")
    c = Choreographer(_make_deps(journal=journal))

    # Must not raise.
    await c._ensure_pm_decision(uuid4(), uuid4(), "rationale that triggers a write")
