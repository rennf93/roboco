"""Review claims are released, retryably, only while an outage is active."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import outage_release


def _tracker(active: bool) -> SimpleNamespace:
    state = {
        "outage_active": active,
        "type": "review_path_tools",
        "window_start": datetime(2026, 11, 4, tzinfo=UTC) if active else None,
        "recovered": False,
    }
    return SimpleNamespace(state=AsyncMock(return_value=state))


def _task_service() -> SimpleNamespace:
    return SimpleNamespace(
        release_review_claim_for_reaper=AsyncMock(),
        session=SimpleNamespace(commit=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_active_outage_releases_claim_and_names_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(True)
    )
    svc, task_id = _task_service(), uuid4()
    env = await outage_release.release_claim_on_outage(
        svc, task_id, {}, verb="claim_review"
    )
    assert env is not None
    assert env.error == "invalid_state"
    assert "review_path_tools" in (env.message or "")
    assert "retryable" in (env.message or "")
    svc.release_review_claim_for_reaper.assert_awaited_once_with(task_id)
    svc.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_outage_leaves_claim_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(False)
    )
    svc = _task_service()
    env = await outage_release.release_claim_on_outage(
        svc, uuid4(), {}, verb="claim_gate_review"
    )
    assert env is None
    svc.release_review_claim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_failure_falls_back_to_normal_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(True)
    )
    svc = _task_service()
    svc.release_review_claim_for_reaper.side_effect = RuntimeError("db down")
    env = await outage_release.release_claim_on_outage(
        svc, uuid4(), {}, verb="claim_review"
    )
    assert env is None
