"""The vault-janitor orchestrator loop is fully dormant unless the vault
umbrella flag is on (default off).

With the flag off, ``_vault_janitor_loop`` must return immediately — no
sleep, no DB, no filesystem access — so a standard deployment behaves
exactly as today.
"""

from __future__ import annotations

import asyncio
import types
from typing import cast

import pytest
from roboco.config import settings as cfg
from roboco.runtime.orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_vault_janitor_loop_returns_immediately_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "obsidian_vault_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(AgentOrchestrator._vault_janitor_loop(stub), timeout=1.0)
