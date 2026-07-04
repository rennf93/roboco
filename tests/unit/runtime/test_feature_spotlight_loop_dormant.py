"""The feature-spotlight orchestrator loop is fully dormant unless BOTH the X
engine and the feature-spotlight sub-switch are enabled (both default off).

With either flag off, ``_x_feature_spotlight_loop`` must return immediately —
no sleep, no HTTP, no DB, no Head-of-Marketing spawn — so a standard
deployment (or one running only release posts / mention replies) behaves
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
async def test_x_feature_spotlight_loop_returns_immediately_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "x_engine_enabled", False)
    monkeypatch.setattr(cfg, "x_feature_spotlight_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    # Gated off -> returns at once. If the gate were missing it would sleep the
    # full interval and this wait_for would time out.
    await asyncio.wait_for(
        AgentOrchestrator._x_feature_spotlight_loop(stub), timeout=1.0
    )


@pytest.mark.asyncio
async def test_x_feature_spotlight_loop_dormant_when_only_subswitch_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """x_engine_enabled on but the feature-spotlight sub-switch off: still
    dormant — the engine still runs release posts/mention replies via their
    own loops, unaffected."""
    monkeypatch.setattr(cfg, "x_engine_enabled", True)
    monkeypatch.setattr(cfg, "x_feature_spotlight_enabled", False)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(
        AgentOrchestrator._x_feature_spotlight_loop(stub), timeout=1.0
    )


@pytest.mark.asyncio
async def test_x_feature_spotlight_loop_dormant_when_only_engine_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subswitch alone is not enough — x_engine_enabled must also be on."""
    monkeypatch.setattr(cfg, "x_engine_enabled", False)
    monkeypatch.setattr(cfg, "x_feature_spotlight_enabled", True)
    stub = cast("AgentOrchestrator", types.SimpleNamespace(_running=True))
    await asyncio.wait_for(
        AgentOrchestrator._x_feature_spotlight_loop(stub), timeout=1.0
    )
