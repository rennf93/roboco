"""Tests for Auditor Choreographer methods.

Covers: auditor_triage (read-only anomaly surfacing).
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_auditor_triage_returns_anomaly_when_present() -> None:
    auditor_id = uuid4()
    anomaly = MagicMock(
        id=uuid4(),
        status="blocked",
        title="long-running blocked",
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="auditor", team="board")
    task_svc.list_long_running_blocked.return_value = [anomaly]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.auditor_triage(auditor_id)
    body = env.as_dict()
    assert body["task_id"] == str(anomaly.id)
    assert "reflect" in body["next"].lower()


@pytest.mark.asyncio
async def test_auditor_triage_returns_idle_when_no_anomalies() -> None:
    auditor_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="auditor", team="board")
    task_svc.list_long_running_blocked.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.auditor_triage(auditor_id)
    body = env.as_dict()
    assert body["status"] == "idle"
    assert body["task_id"] is None
    assert "i_am_idle" in body["next"]


@pytest.mark.asyncio
async def test_auditor_triage_only_first_anomaly_surfaces() -> None:
    """Auditor gets the most-stale blocked task; others wait until next call."""
    auditor_id = uuid4()
    first = MagicMock(id=uuid4(), status="blocked", title="oldest", team="backend")
    second = MagicMock(id=uuid4(), status="blocked", title="newer", team="frontend")
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="auditor", team="board")
    task_svc.list_long_running_blocked.return_value = [first, second]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.auditor_triage(auditor_id)
    body = env.as_dict()
    assert body["task_id"] == str(first.id)
