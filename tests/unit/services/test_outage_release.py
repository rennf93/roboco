"""Review claims are released, retryably, only while an outage is active."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import outage_release
from roboco.services.gateway.choreographer.pr_gate import PRGateMixin


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
        session=SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock()),
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


def _gaps(result: dict[str, object]) -> bool:
    return bool(result.get("evidence_gaps"))


async def _boom() -> dict[str, object]:
    raise RuntimeError("evidence died")


@pytest.mark.asyncio
async def test_build_raise_during_outage_rolls_back_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(True)
    )
    svc, task_id = _task_service(), uuid4()
    result, env = await outage_release.build_or_release(
        svc, task_id, {}, verb="claim_review", build=_boom, has_gaps=_gaps
    )
    assert result is None
    assert env is not None
    assert "review_path_tools" in (env.message or "")
    svc.session.rollback.assert_awaited_once()
    svc.release_review_claim_for_reaper.assert_awaited_once_with(task_id)


@pytest.mark.asyncio
async def test_build_raise_without_outage_reraises_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(False)
    )
    svc = _task_service()
    with pytest.raises(RuntimeError, match="evidence died"):
        await outage_release.build_or_release(
            svc, uuid4(), {}, verb="claim_review", build=_boom, has_gaps=_gaps
        )
    svc.release_review_claim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_gaps_during_outage_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(True)
    )
    svc, task_id = _task_service(), uuid4()

    async def build() -> dict[str, object]:
        return {"evidence_gaps": ["git fetch timed out"]}

    result, env = await outage_release.build_or_release(
        svc, task_id, {}, verb="claim_gate_review", build=build, has_gaps=_gaps
    )
    assert result is None
    assert env is not None
    svc.release_review_claim_for_reaper.assert_awaited_once_with(task_id)


@pytest.mark.asyncio
async def test_gaps_without_outage_returns_result_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(False)
    )
    svc = _task_service()
    gapped: dict[str, object] = {"evidence_gaps": ["git fetch timed out"]}

    async def build() -> dict[str, object]:
        return gapped

    result, env = await outage_release.build_or_release(
        svc, uuid4(), {}, verb="claim_gate_review", build=build, has_gaps=_gaps
    )
    assert result is gapped
    assert env is None
    svc.release_review_claim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_non_releasable_builds_without_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        outage_release, "get_review_path_tracker", lambda: _tracker(True)
    )
    svc = _task_service()
    built = {"evidence_gaps": ["x"]}
    fake_self: Any = SimpleNamespace(
        task=svc, _build_gate_review_evidence=AsyncMock(return_value=built)
    )
    result, env = await PRGateMixin._gate_evidence_or_release(
        fake_self, object(), uuid4(), {}, releasable=False
    )
    assert result is built
    assert env is None
    svc.release_review_claim_for_reaper.assert_not_awaited()
