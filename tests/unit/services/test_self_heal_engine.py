"""Self-heal regression engine — pure assess + gated, notify-only run_cycle.

``assess()`` turns breaching telemetry samples into observations with no side
effects; ``run_cycle()`` is a no-op unless ``self_heal_enabled`` and otherwise
only notifies the CEO (this slice never originates, starts, merges, or deploys).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis_asyncio
from roboco.config import settings as cfg
from roboco.services.decisions import persist as decisions_persist
from roboco.services.notification import NotificationService
from roboco.services.self_heal_engine import SelfHealEngine, _fingerprint
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
    # AsyncMock, not MagicMock: run_cycle now awaits session.commit() to
    # release the pool connection right after the telemetry fetch.
    return SelfHealEngine(AsyncMock(), source=_FakeSource(samples))


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


def test_fingerprint_docstring_records_by_design_rationale() -> None:
    """The dedup invariant is one open fix task per signal, NOT per run — the
    docstring records this so a future reader doesn't 'fix' it into a per-run
    hash and reintroduce the dedupe-collision spam (a persistent red signal
    would re-open a fix task every cycle)."""
    assert _fingerprint.__doc__ is not None
    doc = _fingerprint.__doc__.lower()
    assert "by-design" in doc
    assert "per-signal" in doc


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


# ---------------------------------------------------------------------------
# Outcome labeling: ground truth for the transient-gate training corpus
# ---------------------------------------------------------------------------


class _FakeRows:
    def __init__(self, rows: list[tuple[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, object]]:
        return list(self._rows)


class _FakeLabelSession:
    """execute() answers the gated-rows SELECT; commit() ends the
    transaction like the real pool release."""

    def __init__(self, rows: list[tuple[str, object]]) -> None:
        self._rows = rows
        self.committed = 0

    async def execute(self, _stmt: object) -> _FakeRows:
        return _FakeRows(self._rows)

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        return None


def _fp() -> str:
    return _fingerprint("ci_conclusion:roboco")


def _sample_at(value: float, observed_at: str) -> TelemetrySample:
    base = _sample(value)
    return TelemetrySample(
        signal_name=base.signal_name,
        value=base.value,
        threshold=base.threshold,
        window=base.window,
        repo_hint=base.repo_hint,
        observed_at=observed_at,
        raw_ref=base.raw_ref,
        detail=base.detail,
    )


def _label_engine(
    monkeypatch: pytest.MonkeyPatch,
    gated: list[tuple[str, object]],
    samples: list[TelemetrySample],
) -> tuple[SelfHealEngine, list[dict[str, object]]]:
    """An engine whose labeler runs against a fake session, with
    record_outcome captured instead of hitting the DB."""
    session = _FakeLabelSession(gated)
    engine = SelfHealEngine(session, source=_FakeSource(samples))
    labeled: list[dict[str, object]] = []

    async def _capture(_session: object, **kwargs: object) -> int:
        labeled.append(dict(kwargs))
        return 1

    monkeypatch.setattr(decisions_persist, "record_outcome", _capture)
    return engine, labeled


@pytest.mark.asyncio
async def test_green_reading_after_gate_labels_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_time = datetime(2026, 6, 17, tzinfo=UTC)
    engine, labeled = _label_engine(
        monkeypatch,
        gated=[(f"selfheal:{_fp()}", gate_time)],
        samples=[_sample_at(0.0, "2026-06-17T01:00:00Z")],
    )
    await engine.assess()
    await engine._label_transient_outcomes({})
    assert len(labeled) == 1
    assert labeled[0]["pilot"] == "self_heal"
    assert labeled[0]["session_id"] == f"selfheal:{_fp()}"
    assert labeled[0]["outcome"] == "cleared_after_gate"


@pytest.mark.asyncio
async def test_pre_gate_red_reading_stays_unlabeled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_time = datetime.now(UTC) - timedelta(
        hours=cfg.self_heal_outcome_window_hours + 12
    )
    observed = (gate_time - timedelta(hours=1)).isoformat()
    engine, labeled = _label_engine(
        monkeypatch,
        gated=[(f"selfheal:{_fp()}", gate_time)],
        # The sample's run completed BEFORE the gate: it is the very breach
        # the gate saw, so it proves nothing about what happened after.
        samples=[_sample_at(1.0, observed)],
    )
    await engine.assess()
    await engine._label_transient_outcomes({})
    assert labeled == []


@pytest.mark.asyncio
async def test_red_reading_after_gate_past_window_labels_still_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_time = datetime.now(UTC) - timedelta(
        hours=cfg.self_heal_outcome_window_hours + 12
    )
    observed = (gate_time + timedelta(hours=1)).isoformat()
    engine, labeled = _label_engine(
        monkeypatch,
        gated=[(f"selfheal:{_fp()}", gate_time)],
        samples=[_sample_at(1.0, observed)],
    )
    await engine.assess()
    await engine._label_transient_outcomes({})
    assert len(labeled) == 1
    assert labeled[0]["outcome"] == "still_failing_after_window"


@pytest.mark.asyncio
async def test_red_reading_inside_window_stays_unlabeled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_time = datetime.now(UTC) - timedelta(hours=1)
    observed = (gate_time + timedelta(minutes=30)).isoformat()
    engine, labeled = _label_engine(
        monkeypatch,
        gated=[(f"selfheal:{_fp()}", gate_time)],
        samples=[_sample_at(1.0, observed)],
    )
    await engine.assess()
    await engine._label_transient_outcomes({})
    # A post-gate red reading inside the window: wait for proof.
    assert labeled == []


@pytest.mark.asyncio
async def test_no_post_gate_reading_stays_unlabeled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_time = datetime.now(UTC) - timedelta(hours=1)
    observed = (gate_time - timedelta(minutes=30)).isoformat()
    engine, labeled = _label_engine(
        monkeypatch,
        gated=[(f"selfheal:{_fp()}", gate_time)],
        # No run completed after the gate: nothing provable yet.
        samples=[_sample_at(1.0, observed)],
    )
    await engine.assess()
    await engine._label_transient_outcomes({})
    assert labeled == []


@pytest.mark.asyncio
async def test_open_fix_task_marks_row_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_time = datetime(2026, 6, 17, tzinfo=UTC)
    engine, labeled = _label_engine(
        monkeypatch,
        gated=[(f"selfheal:{_fp()}", gate_time)],
        samples=[_sample_at(0.0, "2026-06-17T01:00:00Z")],
    )
    object_id = object()
    await engine.assess()
    await engine._label_transient_outcomes({_fp(): object_id})
    assert len(labeled) == 1
    assert labeled[0]["outcome"] == "superseded_by_fix_task"


@pytest.mark.asyncio
async def test_labeler_runs_on_all_green_sweeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the green sweep: past 'transient' verdicts become
    provably right exactly when today's readings are all green."""
    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    engine = _engine([_sample(0.0)])
    monkeypatch.setattr(
        engine, "_open_self_heal_task_ids_by_fp", AsyncMock(return_value={})
    )
    labeler = AsyncMock()
    monkeypatch.setattr(engine, "_label_transient_outcomes", labeler)
    obs = await engine.run_cycle()
    assert obs == []
    labeler.assert_awaited_once()


@pytest.mark.asyncio
async def test_labeler_db_failure_never_breaks_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The labeler's own contract: a failure inside the gated-rows SELECT
    is logged and swallowed; the sweep completes untouched."""

    class _BoomSession:
        async def execute(self, _stmt: object) -> object:
            raise RuntimeError("db down")

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    monkeypatch.setattr(cfg, "self_heal_enabled", True)
    monkeypatch.setattr(cfg, "self_heal_originate_enabled", False)
    send = AsyncMock()
    monkeypatch.setattr(NotificationService, "send_ack_notification", send)
    engine = SelfHealEngine(_BoomSession(), source=_FakeSource([_sample(1.0)]))
    monkeypatch.setattr(engine, "_already_notified", AsyncMock(return_value=True))
    monkeypatch.setattr(
        engine, "_open_self_heal_task_ids_by_fp", AsyncMock(return_value={})
    )
    obs = await engine.run_cycle()
    assert len(obs) == 1
    send.assert_not_awaited()  # dedupe held; the sweep itself completed
