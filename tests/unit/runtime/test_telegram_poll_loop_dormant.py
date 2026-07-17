"""The Telegram inbound orchestrator loop is fully dormant when disabled
(default) — mirrors ``test_x_engine_loop_dormant.py``.

With either ``telegram_enabled`` or ``telegram_inbound_enabled`` off,
``_telegram_poll_loop`` must return immediately — no sleep, no HTTP, no DB —
so a standard deployment (and even a V1-only deployment with just
``telegram_enabled`` on) behaves exactly as today.
"""

from __future__ import annotations

import asyncio
import types
from typing import cast

import pytest
from roboco.config import settings as cfg
from roboco.runtime.orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_telegram_poll_loop_returns_immediately_when_both_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "telegram_enabled", False)
    monkeypatch.setattr(cfg, "telegram_inbound_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._telegram_poll_loop(stub), timeout=1.0)


@pytest.mark.asyncio
async def test_telegram_poll_loop_dormant_when_only_v1_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1-only deployment (notifications, no inbound): the poll loop still
    never runs."""
    monkeypatch.setattr(cfg, "telegram_enabled", True)
    monkeypatch.setattr(cfg, "telegram_inbound_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._telegram_poll_loop(stub), timeout=1.0)


@pytest.mark.asyncio
async def test_telegram_poll_loop_dormant_when_only_inbound_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sub-switch alone (V1 master switch off) is still fully inert."""
    monkeypatch.setattr(cfg, "telegram_enabled", False)
    monkeypatch.setattr(cfg, "telegram_inbound_enabled", True)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._telegram_poll_loop(stub), timeout=1.0)
