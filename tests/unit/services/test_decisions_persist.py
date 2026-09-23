"""Tests for the decision_log persistence layer: buffered fire-and-forget
writes from the log_action chokepoint, drop-on-failure degradation, and the
master-flag gate."""

from unittest.mock import AsyncMock

import pytest
import roboco.config as cfg
from roboco.services.decisions import persist
from roboco.services.decisions.pilots import PilotMode, log_action
from roboco.services.decisions.schemas import parse_decisions_payload


@pytest.fixture(autouse=True)
def _clean_state():
    persist.reset_persist_state()
    yield
    persist.reset_persist_state()


def _result(noul=0.9):
    payload = {
        "model": "convaiinnovations/laya",
        "answers": {"gate": {"type": "noul", "noul": noul, "confidence": noul}},
        "usage": {"input_tokens": 100, "output_tokens": 5, "cost": 0.0001},
    }
    return parse_decisions_payload(payload, tier="laya", session_id="selfheal:run-1")


def test_log_action_buffers_a_row_when_flag_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    log_action("self_heal", PilotMode.ON, "originate", "allow origination", _result())
    assert len(persist._pending) == 1
    row = persist._pending[0]
    assert row["pilot"] == "self_heal"
    assert row["mode"] == "on"
    assert row["tier"] == "laya"
    assert row["answers"] == {"gate": 0.9}
    assert row["cost"] == pytest.approx(0.0001)
    assert row["session_id"] == "selfheal:run-1"


def test_log_action_off_flag_buffers_nothing(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    log_action("self_heal", PilotMode.ON, "originate", "action", _result())
    assert len(persist._pending) == 0


def test_shadow_mode_is_recorded_with_its_mode_label(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    log_action(
        "parking", PilotMode.SHADOW, "retry_soon", "no-op (shadow)", _result(0.7)
    )
    row = persist._pending[0]
    assert row["mode"] == "shadow"
    assert row["action"] == "no-op (shadow)"


@pytest.mark.asyncio
async def test_flush_now_writes_rows(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    written = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def add_all(self, rows):
            written.extend(rows)

        commit = AsyncMock()

    class _FakeFactory:
        def __call__(self):
            return _FakeSession()

    import roboco.db.base as db_base

    monkeypatch.setattr(db_base, "get_session_factory", _FakeFactory)
    log_action("parking", PilotMode.ON, "retry_soon", "lane=retry_soon", _result())
    count = await persist.flush_now()
    assert count == 1
    assert written[0].pilot == "parking"
    assert len(persist._pending) == 0


@pytest.mark.asyncio
async def test_flush_failure_drops_batch_without_raising(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)

    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("db down")

    import roboco.db.base as db_base

    monkeypatch.setattr(db_base, "get_session_factory", _BoomFactory())
    log_action("parking", PilotMode.ON, "escalate", "lane=escalate", _result())
    # Must not raise: evidence loss under a DB outage is the right posture.
    await persist.flush_now()
    assert len(persist._pending) == 0


def test_buffer_is_bounded(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    for i in range(persist._BUFFER_MAX + 50):
        persist._pending.append({"pilot": f"p{i}"})
    assert len(persist._pending) == persist._BUFFER_MAX
