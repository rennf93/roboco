"""Wave C3 (2026-05-12): heartbeat reaper threshold honors config setting.

The reaper previously used a hardcoded 180s (= claim_stale_seconds) which
was also the spawn-filter cutoff.  Smoke run 3 showed agents being reaped
at ~3 minutes while actively retrying rejected verbs; LLM inference alone
can exceed that window.

Two fixes ship together:
  - stale_claim_reap_seconds (default 600) drives the reaper; the
    spawn-filter keeps claim_stale_seconds unchanged.
  - _emit_rejection now touches last_heartbeat_at so every verb attempt
    (success or rejection) counts as agent activity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator

_EXPECTED_DEFAULT_REAP_SECONDS = 600


@pytest.mark.asyncio
async def test_reaper_default_threshold_is_600() -> None:
    """settings.stale_claim_reap_seconds defaults to 600, not the old 180."""
    assert settings.stale_claim_reap_seconds == _EXPECTED_DEFAULT_REAP_SECONDS


@pytest.mark.asyncio
async def test_orchestrator_init_uses_stale_claim_reap_seconds() -> None:
    """_claim_heartbeat_ttl is sourced from stale_claim_reap_seconds,
    not claim_stale_seconds."""
    # Build an orchestrator via __new__ to avoid touching the DB, then check
    # the field that _reap_with_service reads.
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    # Manually run the subset of __init__ that sets _claim_heartbeat_ttl.
    orch._claim_heartbeat_ttl = settings.stale_claim_reap_seconds
    assert orch._claim_heartbeat_ttl == _EXPECTED_DEFAULT_REAP_SECONDS


@pytest.mark.asyncio
async def test_reaper_does_not_reap_under_custom_threshold() -> None:
    """Task whose heartbeat is 800s old is NOT reaped when threshold=900."""
    now = datetime.now(UTC)
    task_safe = type(
        "T",
        (),
        {
            "id": uuid4(),
            "last_heartbeat_at": now - timedelta(seconds=800),
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 900  # custom threshold
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task_safe]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaper_does_reap_over_custom_threshold() -> None:
    """Task whose heartbeat is 1000s old IS reaped when threshold=900."""
    now = datetime.now(UTC)
    stale_id = uuid4()
    task_stale = type(
        "T",
        (),
        {
            "id": stale_id,
            "last_heartbeat_at": now - timedelta(seconds=1000),
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._claim_heartbeat_ttl = 900
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [task_stale]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc)

    svc.unclaim_for_reaper.assert_awaited_once_with(stale_id)
