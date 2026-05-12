"""Tests for the windowed satisfaction of the PM-decision tracing gate (C8).

The pre-gateway expectation is that PMs write a *fresh* journal:decision
around each decision point — not once at task creation and then forever.
``_check_pm_decision_required`` enforces this by treating only decisions
whose ``created_at`` is within ``settings.pm_decision_window_seconds`` of
``utc_now`` as satisfying the gate; stale or absent decisions fall through
to the standard tracing_gap envelope (with ``journal:decision`` in the
missing list).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings as _roboco_settings
from roboco.services.gateway.choreographer import (
    Choreographer,
    ChoreographerDeps,
)
from roboco.services.gateway.choreographer import _impl as _choreo_impl


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
    """Pin ``roboco.services.gateway.choreographer._impl.datetime.now()``.

    Used to make boundary assertions (decision age == window) precise —
    without freezing the clock, microsecond drift between the test's
    ``datetime.now(UTC)`` and the call inside ``_check_pm_decision_required``
    pushes the age slightly past the window and flakes the test.

    The stand-in accepts the same ``tz`` positional that the production
    call site passes (``datetime.now(UTC)``) and returns the fixed
    instant regardless — that's the whole point of freezing.
    """

    def _frozen_now(tz: Any) -> datetime:
        _ = tz  # accepted for signature parity with datetime.now(tz)
        return at

    monkeypatch.setattr(_choreo_impl, "datetime", SimpleNamespace(now=_frozen_now))


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    """Mirror tests/unit/gateway/test_choreographer_pm_extras.py::_make_deps.

    Async-mocks every service the Choreographer depends on. The session
    context-manager is stubbed because VerbRunner uses
    ``task.session.begin_nested()`` — not exercised here, but kept for
    parity with the rest of the suite.
    """
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
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    return ChoreographerDeps(**base)


def _make_task(task_id: Any) -> Any:
    """A task stub that the tracing gate accepts as-is.

    `_check_pm_decision_required` only consults the (agent, task) journal
    lookup — the task object itself is opaque to that check.
    """
    return MagicMock(id=task_id, status="in_progress")


# ---------------------------------------------------------------------------
# 1. No decision → tracing_gap with `journal:decision` in missing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_decision_emits_tracing_gap() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = None
    deps = _make_deps(journal=journal_svc)
    c = Choreographer(deps)

    env = await c._check_pm_decision_required(
        "delegate", agent_id, task_id, _make_task(task_id)
    )

    assert env is not None
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]
    journal_svc.latest_decision_at.assert_awaited_once_with(agent_id, task_id)


# ---------------------------------------------------------------------------
# 2. Recent decision (within window) → gate returns None (pass-through).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_decision_within_window_passes() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = datetime.now(UTC) - timedelta(
        seconds=60
    )
    deps = _make_deps(journal=journal_svc)
    c = Choreographer(deps)

    env = await c._check_pm_decision_required(
        "delegate", agent_id, task_id, _make_task(task_id)
    )

    assert env is None


# ---------------------------------------------------------------------------
# 3. Stale decision (outside window) → tracing_gap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_decision_outside_window_emits_tracing_gap() -> None:
    agent_id = uuid4()
    task_id = uuid4()
    journal_svc = AsyncMock()
    # Default window is 300s; 301s old must fall outside.
    journal_svc.latest_decision_at.return_value = datetime.now(UTC) - timedelta(
        seconds=_roboco_settings.pm_decision_window_seconds + 1
    )
    deps = _make_deps(journal=journal_svc)
    c = Choreographer(deps)

    env = await c._check_pm_decision_required(
        "delegate", agent_id, task_id, _make_task(task_id)
    )

    assert env is not None
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]


# ---------------------------------------------------------------------------
# 4. Decision at exactly the window boundary → passes (inclusive ``<=``).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_at_exact_window_boundary_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    task_id = uuid4()
    now = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)
    _freeze_clock(monkeypatch, now)
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = now - timedelta(
        seconds=_roboco_settings.pm_decision_window_seconds
    )
    deps = _make_deps(journal=journal_svc)
    c = Choreographer(deps)

    env = await c._check_pm_decision_required(
        "delegate", agent_id, task_id, _make_task(task_id)
    )

    assert env is None


# ---------------------------------------------------------------------------
# 5. Override the window via settings; decision at 90s must fail when
#    window is shrunk to 60s.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_respects_settings_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    task_id = uuid4()
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = datetime.now(UTC) - timedelta(
        seconds=90
    )
    deps = _make_deps(journal=journal_svc)
    c = Choreographer(deps)

    monkeypatch.setattr(_roboco_settings, "pm_decision_window_seconds", 60)

    env = await c._check_pm_decision_required(
        "delegate", agent_id, task_id, _make_task(task_id)
    )

    assert env is not None
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]
