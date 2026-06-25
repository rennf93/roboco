"""The release-manager orchestrator loop is fully dormant when disabled (default).

With ``release_manager_enabled`` off, ``_release_manager_loop`` must return
immediately — no sleep, no clone, no DB — so a standard deployment behaves
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
async def test_release_manager_loop_returns_immediately_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "release_manager_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    # Gated off → returns at once. If the gate were missing it would sleep the
    # full interval and this wait_for would time out.
    await asyncio.wait_for(AgentOrchestrator._release_manager_loop(stub), timeout=1.0)
