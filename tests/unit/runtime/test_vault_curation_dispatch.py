"""Obsidian-vault root-completion Auditor spawn: gated on the flag, one-shot
per task (mirrors _dispatch_board_reviewer's guard shape), spawned WITHOUT a
bound task_id (mirrors _dispatch_audit_work's alert spawn — a `completed`
task_id would trip the readiness gate's role-for-status check).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


@pytest.mark.asyncio
async def test_dispatch_noop_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "obsidian_vault_enabled", False)
    orch = _make_orch()
    with patch("roboco.db.base.get_db_context") as get_ctx:
        await orch._dispatch_vault_curation_work(cast("Any", object()))
    get_ctx.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_spawn_vault_curation_spawns_auditor_without_task_id() -> None:
    orch = _make_orch()
    task_id = str(uuid4())
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_mark_vault_curation_dispatched", new=AsyncMock()),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_vault_curation(task_id, "A completed root")
    spawn.assert_awaited_once()
    assert spawn.await_args is not None
    assert spawn.await_args.kwargs["agent_id"] == "auditor"
    assert "task_id" not in spawn.await_args.kwargs
    assert task_id in spawn.await_args.kwargs["initial_prompt"]


@pytest.mark.asyncio
async def test_maybe_spawn_vault_curation_one_shot() -> None:
    orch = _make_orch()
    task_id = str(uuid4())
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_mark_vault_curation_dispatched", new=AsyncMock()),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_vault_curation(task_id, "A completed root")
        await orch._maybe_spawn_vault_curation(task_id, "A completed root")
    spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_spawn_vault_curation_skips_when_auditor_active() -> None:
    orch = _make_orch()
    task_id = str(uuid4())
    with (
        patch.object(orch, "_is_agent_active", return_value=True),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._maybe_spawn_vault_curation(task_id, "A completed root")
    spawn.assert_not_awaited()
    assert ("auditor", task_id) not in orch._board_dispatched
