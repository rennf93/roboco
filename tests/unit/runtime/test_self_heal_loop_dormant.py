"""The self-heal orchestrator loop is fully dormant when disabled (the default).

With ``self_heal_enabled`` off, ``_self_heal_loop`` must return immediately —
no sleep, no CI call, no DB — so a standard deployment behaves exactly as today.
"""

from __future__ import annotations

import asyncio
import types
from typing import cast

import pytest
from roboco.config import settings as cfg
from roboco.runtime.orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_self_heal_loop_returns_immediately_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "self_heal_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    # Gated off → returns at once. If the gate were missing it would sleep the
    # full interval and this wait_for would time out.
    await asyncio.wait_for(AgentOrchestrator._self_heal_loop(stub), timeout=1.0)
