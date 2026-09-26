"""Tests for the decision_log persistence layer: buffered fire-and-forget
writes from the log_action chokepoint, drop-on-failure degradation, and the
master-flag gate."""

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import roboco.config as cfg
from roboco.services.decisions import persist
from roboco.services.decisions.pilots import PilotMode, log_action
from roboco.services.decisions.schemas import (
    DecisionResult,
    parse_decisions_payload,
)


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    persist.reset_persist_state()
    yield
    persist.reset_persist_state()


def _result(noul: float = 0.9) -> DecisionResult:
    payload = {
        "model": "convaiinnovations/laya",
        "answers": {"gate": {"type": "noul", "noul": noul, "confidence": noul}},
        "usage": {"input_tokens": 100, "output_tokens": 5, "cost": 0.0001},
    }
    return parse_decisions_payload(payload, tier="laya", session_id="selfheal:run-1")


def test_log_action_buffers_a_row_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_log_action_off_flag_buffers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    log_action("self_heal", PilotMode.ON, "originate", "action", _result())
    assert len(persist._pending) == 0


def test_shadow_mode_is_recorded_with_its_mode_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    log_action(
        "parking", PilotMode.SHADOW, "retry_soon", "no-op (shadow)", _result(0.7)
    )
    row = persist._pending[0]
    assert row["mode"] == "shadow"
    assert row["action"] == "no-op (shadow)"


@pytest.mark.asyncio
async def test_flush_now_writes_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    written: list[Any] = []

    class _FakeSession:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def add_all(self, rows: list[Any]) -> None:
            written.extend(rows)

        commit = AsyncMock()

    class _FakeFactory:
        def __call__(self) -> _FakeSession:
            return _FakeSession()

    import roboco.db.base as db_base

    monkeypatch.setattr(db_base, "get_session_factory", _FakeFactory)
    log_action("parking", PilotMode.ON, "retry_soon", "lane=retry_soon", _result())
    count = await persist.flush_now()
    assert count == 1
    assert written[0].pilot == "parking"
    assert len(persist._pending) == 0


@pytest.mark.asyncio
async def test_flush_failure_drops_batch_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)

    class _BoomFactory:
        def __call__(self) -> Any:
            raise RuntimeError("db down")

    import roboco.db.base as db_base

    monkeypatch.setattr(db_base, "get_session_factory", _BoomFactory())
    log_action("parking", PilotMode.ON, "escalate", "lane=escalate", _result())
    # Must not raise: evidence loss under a DB outage is the right posture.
    await persist.flush_now()
    assert len(persist._pending) == 0


def test_buffer_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    for i in range(persist._BUFFER_MAX + 50):
        persist._pending.append({"pilot": f"p{i}"})
    assert len(persist._pending) == persist._BUFFER_MAX


# ---------------------------------------------------------------------------
# Training-corpus inputs (migration 106)
# ---------------------------------------------------------------------------


def _stamped_result(
    noul: float = 0.9,
    state: Any = None,
    questions: Any = None,
) -> DecisionResult:
    """A result shaped like one the client returns: wire state + questions
    stamped on (what the model actually saw)."""
    result = _result(noul)
    result.state = state if state is not None else {"repo": "roboco"}
    result.questions = questions if questions is not None else {
        "gate": {"type": "noul", "instructions": "Transient?"}
    }
    return result


def test_row_captures_question_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    log_action("self_heal", PilotMode.ON, "originate", "allow", _stamped_result())
    row = persist._pending[0]
    assert row["state"] == {"repo": "roboco"}
    assert row["questions"] == {
        "gate": {"type": "noul", "instructions": "Transient?"}
    }


def test_row_without_inputs_stays_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A result built outside the client (no stamps) still logs; the
    corpus inputs are simply absent and the exporter skips the row."""
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    log_action("parking", PilotMode.SHADOW, "retry_soon", "no-op", _result())
    row = persist._pending[0]
    assert row["state"] is None
    assert row["questions"] is None


def test_oversize_input_becomes_marked_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client caps inputs, so the cap here only guards a future path
    that skips the client: the oversize payload is dropped with a marker,
    the under-cap payload next to it survives untouched."""
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    oversized = {"diff": "x" * (persist._CORPUS_INPUT_CAP_CHARS + 10)}
    log_action(
        "findings_mapping",
        PilotMode.ON,
        "map",
        "suggested map",
        _stamped_result(state=oversized),
    )
    row = persist._pending[0]
    assert row["state"] == {
        "_dropped": f"oversize>{persist._CORPUS_INPUT_CAP_CHARS}"
    }
    assert row["questions"]["gate"]["type"] == "noul"


def test_unserializable_input_becomes_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The corpus pass mirrors the client's ``json.dumps(default=str)``;
    only a value whose str() itself raises defeats it."""

    class _Unstrable:
        def __str__(self) -> str:
            raise RuntimeError("no string form")

    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    log_action(
        "parking",
        PilotMode.ON,
        "retry_soon",
        "lane",
        _stamped_result(state={"bad": _Unstrable()}),
    )
    assert persist._pending[0]["state"] == {"_dropped": "unserializable"}


# ---------------------------------------------------------------------------
# record_outcome: ground-truth labeling for corpus rows
# ---------------------------------------------------------------------------


class _FakeNested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeOutcomeSession:
    """Records the UPDATE statement record_outcome issues."""

    def __init__(self, rowcount: int = 2, boom: bool = False) -> None:
        self.rowcount = rowcount
        self.boom = boom
        self.executed: list[Any] = []

    def begin_nested(self) -> _FakeNested:
        return _FakeNested()

    async def execute(self, stmt: Any) -> Any:
        if self.boom:
            raise RuntimeError("db down")
        self.executed.append(stmt)
        return SimpleNamespace(rowcount=self.rowcount)


@pytest.mark.asyncio
async def test_record_outcome_labels_unlabeled_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    session = _FakeOutcomeSession(rowcount=3)
    labeled = await persist.record_outcome(
        session,
        pilot="self_heal",
        session_id="selfheal:abc123",
        outcome="cleared_after_gate",
    )
    assert labeled == 3
    stmt = session.executed[0]
    compiled = str(stmt.compile())
    assert "decision_log" in compiled
    assert "outcome" in compiled
    params = stmt.compile().params
    assert params["outcome"] == "cleared_after_gate"
    assert isinstance(params["outcome_at"], datetime)
    assert params["outcome_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_record_outcome_truncates_long_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    session = _FakeOutcomeSession()
    await persist.record_outcome(
        session,
        pilot="self_heal",
        session_id="selfheal:abc123",
        outcome="x" * 100,
    )
    assert session.executed[0].compile().params["outcome"] == "x" * 60


@pytest.mark.asyncio
async def test_record_outcome_rejects_empty_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    session = _FakeOutcomeSession()
    labeled = await persist.record_outcome(
        session, pilot="self_heal", session_id="selfheal:abc", outcome="  "
    )
    assert labeled == 0
    assert session.executed == []


@pytest.mark.asyncio
async def test_record_outcome_noop_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    session = _FakeOutcomeSession()
    labeled = await persist.record_outcome(
        session, pilot="self_heal", session_id="selfheal:abc", outcome="x"
    )
    assert labeled == 0
    assert session.executed == []


@pytest.mark.asyncio
async def test_record_outcome_failure_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    session = _FakeOutcomeSession(boom=True)
    labeled = await persist.record_outcome(
        session, pilot="self_heal", session_id="selfheal:abc", outcome="x"
    )
    assert labeled == 0  # evidence, not a gate: never raises
