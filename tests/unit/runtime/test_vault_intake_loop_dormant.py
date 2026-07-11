"""The vault-intake orchestrator loop is fully dormant unless BOTH the vault
AND intake flags are on (default).

With either flag off, ``_vault_intake_loop`` must return immediately — no
sleep, no DB, no filesystem scan — so a standard deployment behaves exactly
as today.
"""

from __future__ import annotations

import asyncio
import types
from typing import cast

import pytest
from roboco.config import settings as cfg
from roboco.runtime.orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_vault_intake_loop_returns_immediately_when_both_flags_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "obsidian_vault_enabled", False)
    monkeypatch.setattr(cfg, "vault_intake_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._vault_intake_loop(stub), timeout=1.0)


@pytest.mark.asyncio
async def test_vault_intake_loop_returns_immediately_when_intake_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "obsidian_vault_enabled", True)
    monkeypatch.setattr(cfg, "vault_intake_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._vault_intake_loop(stub), timeout=1.0)


@pytest.mark.asyncio
async def test_vault_intake_loop_returns_immediately_when_vault_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "obsidian_vault_enabled", False)
    monkeypatch.setattr(cfg, "vault_intake_enabled", True)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._vault_intake_loop(stub), timeout=1.0)
