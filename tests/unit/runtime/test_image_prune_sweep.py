"""The orchestrator sweeper prunes dangling Docker images (gated + throttled).

Each agent-image rebuild orphans the prior build's layers as an untagged
``<none>`` image; over many deploys these pile up. The sweeper reclaims them
with ``docker image prune -f --filter dangling=true`` (dangling only — never a
tagged image or one backing a running container), gated by
``image_prune_enabled`` and throttled to ``image_prune_interval_seconds``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)  # bypass __init__
    orch._last_image_prune = None
    return orch


def _fake_proc(returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"Total reclaimed space: 1.2GB", b""))
    return proc


@pytest.mark.asyncio
async def test_prunes_dangling_when_enabled_and_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "image_prune_enabled", True)
    orch = _orch()
    spawn = AsyncMock(return_value=_fake_proc())
    with patch("roboco.runtime.orchestrator.asyncio.create_subprocess_exec", spawn):
        await orch._sweep_dangling_images()
    spawn.assert_awaited_once()
    assert spawn.await_args is not None
    args: tuple[Any, ...] = spawn.await_args.args
    assert args[:6] == (
        "docker",
        "image",
        "prune",
        "-f",
        "--filter",
        "dangling=true",
    )
    assert orch._last_image_prune is not None


@pytest.mark.asyncio
async def test_no_prune_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "image_prune_enabled", False)
    orch = _orch()
    spawn = AsyncMock()
    with patch("roboco.runtime.orchestrator.asyncio.create_subprocess_exec", spawn):
        await orch._sweep_dangling_images()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_throttled_when_recently_pruned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "image_prune_enabled", True)
    orch = _orch()
    orch._last_image_prune = datetime.now(UTC)  # pruned moments ago
    spawn = AsyncMock()
    with patch("roboco.runtime.orchestrator.asyncio.create_subprocess_exec", spawn):
        await orch._sweep_dangling_images()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_best_effort_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "image_prune_enabled", True)
    orch = _orch()
    spawn = AsyncMock(side_effect=RuntimeError("no docker"))
    with patch("roboco.runtime.orchestrator.asyncio.create_subprocess_exec", spawn):
        await orch._sweep_dangling_images()  # must not raise
