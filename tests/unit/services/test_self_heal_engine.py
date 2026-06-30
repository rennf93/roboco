"""Self-heal regression engine — pure assess + gated, notify-only run_cycle.

``assess()`` turns breaching telemetry samples into observations with no side
effects; ``run_cycle()`` is a no-op unless ``self_heal_enabled`` and otherwise
only notifies the CEO (this slice never originates, starts, merges, or deploys).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as redis_asyncio
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
    engine = _engine([_sample(1.0)])
    # Isolate the notify-fires assertion from Redis/dedupe + the open-task
    # lookup (run_cycle now dedupes per fingerprint and links a task_id).
    monkeypatch.setattr(engine, "_already_notified", AsyncMock(return_value=False))
    monkeypatch.setattr(engine, "_mark_notified", AsyncMock(return_value=None))
    monkeypatch.setattr(
        engine, "_open_self_heal_task_ids_by_fp", AsyncMock(return_value={})
    )
    obs = await engine.run_cycle()
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


# ---------------------------------------------------------------------------
# #43: per-fingerprint notify dedupe + task_id linking + fail-open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_dedupes_repeated_fingerprint_across_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#43: a regression that stays red across cycles must notify the CEO ONCE
    per episode, not every cycle. The engine keeps a per-fingerprint "already
    notified" guard so a persistent red state doesn't spam the CEO each tick."""
    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_originate_enabled", False)
    send = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", send)

    notified: set[str] = set()

    async def _already(fp: str) -> bool:
        return fp in notified

    async def _mark(fp: str) -> None:
        notified.add(fp)

    async def _open_map() -> dict[str, object]:
        return {}

    engine = _engine([_sample(1.0)])
    monkeypatch.setattr(engine, "_already_notified", _already)
    monkeypatch.setattr(engine, "_mark_notified", _mark)
    monkeypatch.setattr(engine, "_open_self_heal_task_ids_by_fp", _open_map)

    await engine.run_cycle()
    await engine.run_cycle()  # same red state — must NOT re-notify

    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_cycle_links_task_id_when_open_task_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#43: the CEO notification carries the open self-heal fix task's id so the
    panel can route the CEO to the fix (instead of a free-floating alert)."""
    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_originate_enabled", False)
    send = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", send)

    fp = (await _engine([_sample(1.0)]).assess())[0].fingerprint
    task_uuid = "11111111-1111-1111-1111-111111111111"

    async def _already(_fp: str) -> bool:
        return False

    async def _mark(_fp: str) -> None:
        return None

    async def _open_map() -> dict[str, object]:
        return {fp: task_uuid}

    engine = _engine([_sample(1.0)])
    monkeypatch.setattr(engine, "_already_notified", _already)
    monkeypatch.setattr(engine, "_mark_notified", _mark)
    monkeypatch.setattr(engine, "_open_self_heal_task_ids_by_fp", _open_map)

    await engine.run_cycle()

    send.assert_awaited_once()
    call = send.await_args
    assert call is not None
    assert call.kwargs.get("task_id") == task_uuid


@pytest.mark.asyncio
async def test_run_cycle_notifies_when_dedupe_check_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#43: a Redis outage in the dedupe check must FAIL OPEN — better to risk
    a duplicate CEO ping than to silently swallow a regression alert. The real
    _already_notified catches the Redis error and returns False (notify)."""
    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_originate_enabled", False)
    send = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", send)

    def _boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("redis down")

    monkeypatch.setattr(redis_asyncio, "from_url", _boom)

    async def _mark(_fp: str) -> None:
        return None

    async def _open_map() -> dict[str, object]:
        return {}

    engine = _engine([_sample(1.0)])
    monkeypatch.setattr(engine, "_mark_notified", _mark)
    monkeypatch.setattr(engine, "_open_self_heal_task_ids_by_fp", _open_map)

    await engine.run_cycle()

    send.assert_awaited_once()
