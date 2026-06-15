"""roboco.services.research_quota — per-agent daily quota (mocked Redis)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from roboco.services import research_quota
from roboco.services.research_quota import ResearchQuotaTracker

_EXPIRY_SECONDS = 86400
_OVER_LIMIT_USED = 3


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl

    async def aclose(self) -> None:
        return None


class _BrokenRedis:
    async def incr(self, _key: str) -> int:
        raise ConnectionError("redis down")


def _patch_redis(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    monkeypatch.setattr(research_quota.redis, "from_url", lambda _url: fake)


@pytest.mark.asyncio
async def test_first_call_increments_and_sets_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    tracker = ResearchQuotaTracker(redis_url="redis://x")
    now = datetime(2026, 6, 15, tzinfo=UTC)
    result = await tracker.check_and_consume("agent-1", 50, now=now)
    assert result.allowed is True
    assert result.used == 1
    assert result.day == "2026-06-15"
    # expiry set exactly once, on the first increment
    key = "roboco:research_quota:agent-1:2026-06-15"
    assert fake.expires[key] == _EXPIRY_SECONDS


@pytest.mark.asyncio
async def test_blocks_when_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    tracker = ResearchQuotaTracker(redis_url="redis://x")
    now = datetime(2026, 6, 15, tzinfo=UTC)
    first = await tracker.check_and_consume("a", 2, now=now)
    second = await tracker.check_and_consume("a", 2, now=now)
    third = await tracker.check_and_consume("a", 2, now=now)
    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.used == _OVER_LIMIT_USED


@pytest.mark.asyncio
async def test_separate_counters_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    tracker = ResearchQuotaTracker(redis_url="redis://x")
    d1 = await tracker.check_and_consume("a", 50, now=datetime(2026, 6, 15, tzinfo=UTC))
    d2 = await tracker.check_and_consume("a", 50, now=datetime(2026, 6, 16, tzinfo=UTC))
    assert d1.used == 1
    assert d2.used == 1


@pytest.mark.asyncio
async def test_fails_open_when_redis_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, _BrokenRedis())
    tracker = ResearchQuotaTracker(redis_url="redis://x")
    result = await tracker.check_and_consume(
        "a", 50, now=datetime(2026, 6, 15, tzinfo=UTC)
    )
    assert result.allowed is True
    assert result.used == 0
