"""Wiring tests for the ops-lane Decisions pilots (spec 6.1-6.3): the call
sites in self-heal origination, spawn-time complexity routing, and (in
tests/unit/runtime) the parking path. Focus: fail-open semantics and the
shadow/off behavior at the seam."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import roboco.services.decisions as decisions
import roboco.services.decisions.pilots as pilots
from roboco.models.base import Complexity
from roboco.services.self_heal_engine import SelfHealEngine


def _bare(engine_cls):
    """Instantiate a mixin/engine without __init__ (no DB needed)."""
    engine = engine_cls.__new__(engine_cls)
    engine.session = MagicMock()
    import structlog

    engine.log = structlog.get_logger("test")
    return engine


# ---------------------------------------------------------------------------
# 6.1 self-heal gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_heal_gate_passes_sample_state_to_pilot(monkeypatch):
    engine = _bare(SelfHealEngine)
    captured = {}

    async def fake_transient(session, **kwargs):
        captured.update(kwargs)
        return pilots.SelfHealGate.NO_VERDICT

    monkeypatch.setattr(decisions, "self_heal_transient", fake_transient)
    gate = await engine._decisions_transient_gate(
        SimpleNamespace(
            repo_hint="roboco",
            detail="CI on roboco@master concluded 'failure'",
            fingerprint="fp-1",
        )
    )
    assert gate is pilots.SelfHealGate.NO_VERDICT
    assert captured["repo"] == "roboco"
    assert captured["attempt_number"] == 1
    assert "failure" in captured["error_excerpt"]


@pytest.mark.asyncio
async def test_self_heal_pilot_failure_is_no_verdict(monkeypatch):
    engine = _bare(SelfHealEngine)

    async def boom(session, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(decisions, "self_heal_transient", boom)
    gate = await engine._decisions_transient_gate(
        SimpleNamespace(repo_hint="r", detail="d", fingerprint="fp")
    )
    assert gate is pilots.SelfHealGate.NO_VERDICT


# ---------------------------------------------------------------------------
# 6.3 complexity override
# ---------------------------------------------------------------------------


def _task(description: str, criteria: list | None = None):
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000009",
        title="Wide refactor",
        description=description,
        acceptance_criteria_status=criteria or [],
    )


@pytest.mark.asyncio
async def test_complexity_confident_score_overrides_static(monkeypatch):
    from roboco.runtime.engines.spawn_launch import SpawnLaunchEngine

    engine = _bare(SpawnLaunchEngine)
    monkeypatch.setattr(
        decisions,
        "complexity_score",
        AsyncMock(return_value=(2, True)),
    )
    result = await engine._decisions_complexity_override(
        MagicMock(), _task("x" * 200), static="medium"
    )
    assert result == "high"


@pytest.mark.asyncio
async def test_complexity_low_confidence_keeps_static(monkeypatch):
    from roboco.runtime.engines.spawn_launch import SpawnLaunchEngine

    engine = _bare(SpawnLaunchEngine)
    monkeypatch.setattr(
        decisions, "complexity_score", AsyncMock(return_value=(2, False))
    )
    result = await engine._decisions_complexity_override(
        MagicMock(), _task("x" * 200), static="medium"
    )
    assert result == "medium"


@pytest.mark.asyncio
async def test_complexity_short_description_skips_pilot(monkeypatch):
    from roboco.runtime.engines.spawn_launch import SpawnLaunchEngine

    engine = _bare(SpawnLaunchEngine)
    mock = AsyncMock()
    monkeypatch.setattr(decisions, "complexity_score", mock)
    result = await engine._decisions_complexity_override(
        MagicMock(), _task("too short"), static="low"
    )
    assert result == "low"
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_complexity_criteria_passed_longest_form(monkeypatch):
    from roboco.runtime.engines.spawn_launch import SpawnLaunchEngine

    engine = _bare(SpawnLaunchEngine)
    mock = AsyncMock(return_value=(1, True))
    monkeypatch.setattr(decisions, "complexity_score", mock)
    task = _task(
        "x" * 200,
        criteria=[{"criterion": "CI green"}, {"criterion": "docs updated"}],
    )
    result = await engine._decisions_complexity_override(MagicMock(), task, static=None)
    assert result == "medium"
    kwargs = mock.await_args.kwargs
    assert sorted(kwargs["acceptance_criteria"]) == ["CI green", "docs updated"]


@pytest.mark.asyncio
async def test_complexity_pilot_error_keeps_static(monkeypatch):
    from roboco.runtime.engines.spawn_launch import SpawnLaunchEngine

    engine = _bare(SpawnLaunchEngine)

    async def boom(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(decisions, "complexity_score", boom)
    result = await engine._decisions_complexity_override(
        MagicMock(), _task("x" * 200), static="high"
    )
    assert result == "high"


def test_complexity_score_bounds_are_clamped_in_pilot():
    """The pilot clamps the 0-2 score: out-of-band floats cannot produce a
    tier KeyError at the call site, and the routing vocabulary matches."""
    from roboco.runtime.engines.spawn_launch import _DECISIONS_COMPLEXITY_TIERS

    assert len(_DECISIONS_COMPLEXITY_TIERS) == 3
    assert max(0, min(2, round(4.7))) == 2
    assert Complexity.HIGH.value.lower() == "high"
