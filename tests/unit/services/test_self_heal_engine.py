"""Self-heal regression engine — pure assess + gated, notify-only run_cycle.

``assess()`` turns breaching telemetry samples into observations with no side
effects; ``run_cycle()`` is a no-op unless ``self_heal_enabled`` and otherwise
only notifies the CEO (this slice never originates, starts, merges, or deploys).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.config import settings as cfg
from roboco.services.notification import NotificationService
from roboco.services.self_heal_engine import SelfHealEngine
from roboco.services.telemetry import TelemetrySample


class _FakeSource:
    """A TelemetrySource stand-in returning canned samples."""

    def __init__(self, samples: list[TelemetrySample]) -> None:
        self._samples = samples

    async def fetch(self) -> list[TelemetrySample]:
        return list(self._samples)


def _sample(value: float) -> TelemetrySample:
    return TelemetrySample(
        signal_name="ci_conclusion:roboco",
        value=value,
        threshold=1.0,
        window="latest_completed_run",
        repo_hint="roboco",
        observed_at="2026-06-17T00:00:00Z",
        raw_ref="https://github.com/x/roboco/actions/runs/1",
        detail="CI on roboco@master concluded 'failure'",
    )


def _engine(samples: list[TelemetrySample]) -> SelfHealEngine:
    return SelfHealEngine(MagicMock(), source=_FakeSource(samples))


@pytest.mark.asyncio
async def test_assess_breach_yields_observation() -> None:
    obs = await _engine([_sample(1.0)]).assess()
    assert len(obs) == 1
    assert obs[0].repo_hint == "roboco"
    assert obs[0].signal_name == "ci_conclusion:roboco"
    assert obs[0].fingerprint  # non-empty stable hash


@pytest.mark.asyncio
async def test_assess_no_breach_yields_nothing() -> None:
    assert await _engine([_sample(0.0)]).assess() == []


@pytest.mark.asyncio
async def test_assess_is_pure_no_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", send)
    await _engine([_sample(1.0)]).assess()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_cycle_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "self_heal_enabled", False)
    send = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", send)
    assert await _engine([_sample(1.0)]).run_cycle() == []
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_cycle_notifies_ceo_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    send = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", send)
    obs = await _engine([_sample(1.0)]).run_cycle()
    assert len(obs) == 1
    send.assert_awaited_once()
    call = send.await_args
    assert call is not None
    assert call.kwargs["from_agent"] == "system"
    assert call.kwargs["to_agent"] == "ceo"
    assert "[self-heal]" in call.kwargs["body"]


@pytest.mark.asyncio
async def test_fingerprint_is_stable() -> None:
    a = (await _engine([_sample(1.0)]).assess())[0].fingerprint
    b = (await _engine([_sample(1.0)]).assess())[0].fingerprint
    assert a == b
