"""Dispatch-time claim-gate prefilter (churn reduction).

`_dispatch_pm_work` fetches every PENDING task each tick with no
dependency/sequence filter, so a later-wave / dependency-blocked task got a
doomed claim attempt every tick — harmless (the claim chokepoint already
refuses it via `TaskService._claim_blocked_by_sequencing`) but pure churn,
each probe paying for the guards' sibling queries a second time.
`_route_unassigned_pm_task` now consults `_pending_claim_blocked` (backed by
the public `TaskService.is_pending_claim_blocked`) before ever calling
`_claim_task_for_agent`, so a held task is skipped for the tick instead of
round-tripping the claim endpoint.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _pending_task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "pending",
        "team": "backend",
        "task_type": "code",
        "title": "Some code task",
        "assigned_to": None,
        "created_by": None,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# _route_unassigned_pm_task — the prefilter must gate _claim_task_for_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_held_task_is_not_claim_attempted() -> None:
    """A blocked task (unmet dependency / lower-sequence sibling) skips the
    claim call entirely — no HTTP round trip, no downstream routing."""
    orch = _orch()
    task = _pending_task()
    client = cast("httpx.AsyncClient", object())
    with (
        patch.object(orch, "_pending_claim_blocked", new=AsyncMock(return_value=True)),
        patch.object(orch, "_classify_task_routing") as classify,
        patch.object(orch, "_claim_task_for_agent", new=AsyncMock()) as claim,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._route_unassigned_pm_task(client, task)

    claim.assert_not_awaited()
    spawn.assert_not_awaited()
    classify.assert_not_called()  # never even reaches routing classification


@pytest.mark.asyncio
async def test_ready_task_still_dispatches() -> None:
    """A clear task (prefilter returns False) proceeds through the normal
    claim + spawn flow unchanged."""
    orch = _orch()
    task = _pending_task()
    client = cast("httpx.AsyncClient", object())
    with (
        patch.object(orch, "_pending_claim_blocked", new=AsyncMock(return_value=False)),
        patch.object(orch, "_classify_task_routing", return_value="dev"),
        patch.object(orch, "_get_routing_target", return_value="be-dev-1"),
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(
            orch, "_claim_task_for_agent", new=AsyncMock(return_value=True)
        ) as claim,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._route_unassigned_pm_task(client, task)

    claim.assert_awaited_once_with(client, task["id"], "be-dev-1")
    spawn.assert_awaited_once()


# ---------------------------------------------------------------------------
# _pending_claim_blocked — the DB-backed probe itself
# ---------------------------------------------------------------------------


def _patch_task_service_db(task_svc: AsyncMock) -> tuple[Any, Any]:
    @asynccontextmanager
    async def _fake_ctx() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    return (
        patch("roboco.db.base.get_db_context", _fake_ctx),
        patch("roboco.services.task.TaskService", return_value=task_svc),
    )


@pytest.mark.asyncio
async def test_pending_claim_blocked_delegates_to_task_service() -> None:
    orch = _orch()
    task_svc = AsyncMock()
    task_svc.is_pending_claim_blocked = AsyncMock(return_value=True)
    task_id = str(uuid4())

    db_ctx, task_ctx = _patch_task_service_db(task_svc)
    with db_ctx, task_ctx:
        assert await orch._pending_claim_blocked(task_id) is True

    task_svc.is_pending_claim_blocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_claim_blocked_false_without_task_id() -> None:
    """No task id — nothing to probe; never touches the DB."""
    orch = _orch()
    with patch("roboco.db.base.get_db_context") as ctx:
        assert await orch._pending_claim_blocked(None) is False
    ctx.assert_not_called()


class _BoomCtx:
    """Async context manager that blows up on entry — simulates a DB hiccup."""

    async def __aenter__(self) -> None:
        raise RuntimeError("db unavailable")

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_pending_claim_blocked_fails_open_on_error() -> None:
    """A DB/lookup error never blocks dispatch — the claim attempt is the
    real safety net and will surface its own error."""
    orch = _orch()
    with patch("roboco.db.base.get_db_context", return_value=_BoomCtx()):
        assert await orch._pending_claim_blocked(str(uuid4())) is False


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
