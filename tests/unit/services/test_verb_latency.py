"""Aggregation + Sentinel integration for per-verb latency telemetry.

Uses the ``db_session`` fixture (real Postgres, ``Base.metadata.create_all``)
to insert ``VerbLatencySampleTable`` rows directly, then verifies:

- ``MetricsService.get_verb_latency_stats`` computes correct p50/p95 per verb,
  sorted by p95 descending with sample counts.
- ``SentinelEngine.evidence_context`` includes a latency section that renders
  the slowest verbs and flags any whose p95 crosses its matching server-side
  timeout.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import VerbLatencySampleTable
from roboco.services.metrics import get_metrics_service
from roboco.services.sentinel_engine import SentinelEngine
from sqlalchemy import delete

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_samples(  # noqa: PLR0913
    session: AsyncSession,
    verb: str,
    durations: list[float],
    *,
    role: str = "developer",
    outcome: str = "success",
    status_code: int | None = 200,
    age_hours: float = 0.0,
) -> None:
    created = datetime.now(UTC) - timedelta(hours=age_hours)
    for d in durations:
        session.add(
            VerbLatencySampleTable(
                id=uuid4(),
                verb=verb,
                role=role,
                duration_ms=d,
                outcome=outcome,
                status_code=status_code,
                created_at=created,
            )
        )
    await session.flush()


# ---------------------------------------------------------------------------
# MetricsService.get_verb_latency_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verb_latency_stats_percentiles(db_session: AsyncSession) -> None:
    """Known samples produce exact p50/p95 values, sorted by p95 descending."""
    await db_session.execute(delete(VerbLatencySampleTable))
    # 10 samples: 10,20,30,...,100 ms → p50=55, p95=95.5
    await _seed_samples(db_session, "give_me_work", list(range(10, 101, 10)))
    # 4 samples: 100,200,300,400 ms → p50=250, p95=385 (linear interpolation)
    await _seed_samples(db_session, "i_am_done", [100, 200, 300, 400])
    await db_session.commit()

    stats = await get_metrics_service(db_session).get_verb_latency_stats(hours=24)
    assert len(stats) == 2  # noqa: PLR2004

    # Sorted by p95 descending: i_am_done (385) first, give_me_work (95.5) second
    assert stats[0].verb == "i_am_done"
    assert stats[0].p95_ms == pytest.approx(385, abs=1)
    assert stats[0].p50_ms == pytest.approx(250, abs=1)
    assert stats[0].sample_count == 4  # noqa: PLR2004

    assert stats[1].verb == "give_me_work"
    assert stats[1].p95_ms == pytest.approx(95.5, abs=1)
    assert stats[1].p50_ms == pytest.approx(55, abs=1)
    assert stats[1].sample_count == 10  # noqa: PLR2004


@pytest.mark.asyncio
async def test_verb_latency_stats_empty(db_session: AsyncSession) -> None:
    """No samples → empty list (not an error)."""
    await db_session.execute(delete(VerbLatencySampleTable))
    await db_session.commit()
    stats = await get_metrics_service(db_session).get_verb_latency_stats(hours=24)
    assert stats == []


@pytest.mark.asyncio
async def test_verb_latency_stats_respects_time_window(
    db_session: AsyncSession,
) -> None:
    """Samples older than the window are excluded."""
    await db_session.execute(delete(VerbLatencySampleTable))
    await _seed_samples(db_session, "give_me_work", [50], age_hours=0)
    await _seed_samples(db_session, "give_me_work", [500], age_hours=48)
    await db_session.commit()

    stats = await get_metrics_service(db_session).get_verb_latency_stats(hours=24)
    assert len(stats) == 1
    assert stats[0].verb == "give_me_work"
    assert stats[0].sample_count == 1
    assert stats[0].p50_ms == pytest.approx(50, abs=1)


@pytest.mark.asyncio
async def test_verb_latency_stats_to_dict(db_session: AsyncSession) -> None:
    """to_dict rounds and includes all fields."""
    await db_session.execute(delete(VerbLatencySampleTable))
    await _seed_samples(db_session, "open_pr", [100, 200, 300])
    await db_session.commit()

    stats = await get_metrics_service(db_session).get_verb_latency_stats(hours=24)
    assert len(stats) == 1
    d = stats[0].to_dict()
    assert d["verb"] == "open_pr"
    assert "p50_ms" in d
    assert "p95_ms" in d
    assert d["sample_count"] == 3  # noqa: PLR2004


# ---------------------------------------------------------------------------
# SentinelEngine.evidence_context — latency section
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_context_includes_latency_section(
    db_session: AsyncSession,
) -> None:
    """The latency section appears when samples exist and renders verb names
    with p50/p95 values."""
    await db_session.execute(delete(VerbLatencySampleTable))
    await _seed_samples(db_session, "give_me_work", [10, 20, 30, 40, 50])
    await db_session.commit()

    context = await SentinelEngine(db_session).evidence_context()
    assert "Per-verb latency (p50/p95)" in context
    assert "give_me_work" in context
    assert "p50=" in context
    assert "p95=" in context


@pytest.mark.asyncio
async def test_evidence_context_latency_section_flags_timeout_breach(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verb whose p95 crosses the normal timeout (120s) is flagged with the
    timeout warning. A verb under its timeout is not flagged."""
    monkeypatch.setattr(settings, "flow_verb_timeout_seconds", 120.0)
    monkeypatch.setattr(settings, "flow_verb_slow_timeout_seconds", 900)

    await db_session.execute(delete(VerbLatencySampleTable))
    # give_me_work: p95 well under 120s → no flag
    await _seed_samples(db_session, "give_me_work", [10, 20, 30, 40, 50])
    # claim_review: p95 at 200s → exceeds 120s normal timeout → flagged
    await _seed_samples(db_session, "claim_review", [100, 150, 180, 190, 200_000])
    await db_session.commit()

    context = await SentinelEngine(db_session).evidence_context()
    assert "Per-verb latency (p50/p95)" in context
    assert "claim_review" in context
    assert "exceeds timeout" in context


@pytest.mark.asyncio
async def test_evidence_context_latency_section_empty_when_no_samples(
    db_session: AsyncSession,
) -> None:
    """No samples → the latency section is dropped by the ``if lines`` guard
    (empty list → falsy → section omitted)."""
    await db_session.execute(delete(VerbLatencySampleTable))
    await db_session.commit()

    context = await SentinelEngine(db_session).evidence_context()
    assert "Per-verb latency (p50/p95)" not in context


@pytest.mark.asyncio
async def test_evidence_context_slow_verb_uses_slow_timeout(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SLOW_VERBS verb (e.g. i_am_done) gets the 900s threshold, not 120s —
    a p95 of 200s should NOT flag it even though it would flag a normal verb."""
    monkeypatch.setattr(settings, "flow_verb_timeout_seconds", 120.0)
    monkeypatch.setattr(settings, "flow_verb_slow_timeout_seconds", 900)

    await db_session.execute(delete(VerbLatencySampleTable))
    # i_am_done: p95=200s, well under the 900s slow timeout → no flag
    await _seed_samples(db_session, "i_am_done", [100, 150, 180, 190, 200_000])
    await db_session.commit()

    context = await SentinelEngine(db_session).evidence_context()
    assert "Per-verb latency (p50/p95)" in context
    assert "i_am_done" in context
    assert "exceeds timeout" not in context
