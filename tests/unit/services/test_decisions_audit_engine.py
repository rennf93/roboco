"""Unit tests for the Decisions Audit board-program engine (#15): the
daily aggregates brief, the armed/dedup gates, and retention pruning."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.services.decisions_audit_engine as engine_mod
from roboco.db.tables import DecisionLogTable
from roboco.services.decisions_audit_engine import DecisionsAuditEngine


def _engine() -> DecisionsAuditEngine:
    e = DecisionsAuditEngine.__new__(DecisionsAuditEngine)
    e.session = MagicMock()
    import structlog

    e.log = structlog.get_logger("test")
    return e


def _row(pilot="self_heal", mode="on", conf=0.9, cost=0.0001, hours_ago=2):
    return DecisionLogTable(
        pilot=pilot,
        tier="laya",
        mode=mode,
        answers={"gate": 0.9},
        confidence={"gate": conf},
        action="allow",
        cost=cost,
        created_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


def _session_returning(rows):
    s = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    s.execute = AsyncMock(return_value=result)
    return s


@pytest.mark.asyncio
async def test_daily_brief_empty_when_no_rows():
    e = _engine()
    e.session = _session_returning([])
    assert await e.daily_brief() == ""


@pytest.mark.asyncio
async def test_daily_brief_carries_pilot_aggregates():
    e = _engine()
    rows = [
        _row(pilot="self_heal", mode="on", conf=0.88, hours_ago=1),
        _row(pilot="self_heal", mode="on", conf=0.92, hours_ago=2),
        _row(pilot="parking", mode="shadow", conf=0.75, hours_ago=3),
        # baseline rows: 3 days ago (not in yesterday's window)
        _row(pilot="self_heal", mode="on", conf=0.9, hours_ago=72),
    ]
    e.session = _session_returning(rows)
    brief = await e.daily_brief()
    assert "self_heal: 2 verdicts (on=2/shadow=0)" in brief
    assert "mean confidence 0.9" in brief
    assert "parking: 1 verdicts (on=0/shadow=1)" in brief
    assert "baseline" in brief  # the 3-day-old row feeds the trailing window


@pytest.mark.asyncio
async def test_run_cycle_noop_when_not_armed(monkeypatch):
    e = _engine()
    e.session.execute = AsyncMock()
    monkeypatch.setattr(engine_mod, "program_armed", AsyncMock(return_value=False))
    monkeypatch.setattr(e, "prune_retention", AsyncMock(return_value=0))
    assert await e.run_cycle() is None
    e.session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_run_cycle_noop_when_open_cycle_exists(monkeypatch):
    e = _engine()
    monkeypatch.setattr(engine_mod, "program_armed", AsyncMock(return_value=True))
    monkeypatch.setattr(e, "prune_retention", AsyncMock(return_value=0))
    monkeypatch.setattr(e, "_open_cycle_exists", AsyncMock(return_value=True))
    assert await e.run_cycle() is None


@pytest.mark.asyncio
async def test_run_cycle_originates_and_notifies(monkeypatch):
    e = _engine()
    e.session = MagicMock()
    e.session.flush = AsyncMock()
    monkeypatch.setattr(engine_mod, "program_armed", AsyncMock(return_value=True))
    monkeypatch.setattr(e, "prune_retention", AsyncMock(return_value=0))
    monkeypatch.setattr(e, "_open_cycle_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(
        e,
        "_roboco_project",
        AsyncMock(return_value=SimpleNamespace(id="proj-uuid")),
    )
    created = MagicMock()
    task_svc = MagicMock()
    task_svc.create = AsyncMock(return_value=created)
    monkeypatch.setattr(
        engine_mod, "get_task_service", MagicMock(return_value=task_svc)
    )
    monkeypatch.setattr(
        engine_mod,
        "program_armed",
        AsyncMock(return_value=True),
    )
    briefs = []

    async def _capture(brief):
        briefs.append(brief)

    monkeypatch.setattr(e, "_notify_ceo", _capture)
    monkeypatch.setattr(
        e, "daily_brief", AsyncMock(return_value="- self_heal: 3 verdicts")
    )
    task = await e.run_cycle()
    assert task is created
    create_kwargs = task_svc.create.await_args.args[0]
    assert create_kwargs.source == "board_decisions_audit"
    assert create_kwargs.confirmed_by_human is False  # held, board-dispatched
    assert len(briefs) == 1
    assert "self_heal" in briefs[0]


@pytest.mark.asyncio
async def test_prune_retention_deletes_past_window(monkeypatch):
    e = _engine()
    result = MagicMock()
    result.rowcount = 7
    e.session = MagicMock()
    e.session.execute = AsyncMock(return_value=result)
    e.session.flush = AsyncMock()
    removed = await e.prune_retention()
    assert removed == 7


def test_arming_falls_back_to_nas_signature():
    """program_armed's legacy fallback: the audit loop arms wherever the
    Decisions master is on AND pilots are env-armed (the NAS deploy
    signature), and stays off on user-facing deploys (both lists empty)."""
    import roboco.services.board_programs as bp

    monkey_vars = {
        "decisions_enabled": True,
        "decisions_pilots_on": "self_heal",
        "decisions_pilots_shadow": "",
    }
    import roboco.config as cfg

    old = {k: getattr(cfg.settings, k) for k in monkey_vars}
    for k, v in monkey_vars.items():
        object.__setattr__(cfg.settings, k, v)
    try:
        assert bp._legacy_enabled("decisions_audit") is True
        object.__setattr__(cfg.settings, "decisions_pilots_on", "")
        object.__setattr__(cfg.settings, "decisions_pilots_shadow", "")
        assert bp._legacy_enabled("decisions_audit") is False
        object.__setattr__(cfg.settings, "decisions_enabled", False)
        object.__setattr__(cfg.settings, "decisions_pilots_on", "self_heal")
        assert bp._legacy_enabled("decisions_audit") is False
    finally:
        for k, v in old.items():
            object.__setattr__(cfg.settings, k, v)
