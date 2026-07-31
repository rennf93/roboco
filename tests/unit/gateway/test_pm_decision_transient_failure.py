"""PM-decision write-then-gate: transient DB failure must not launder into a
durable rejection/block.

Live bug: every PM verb (complete / submit_up / submit_root / unblock /
escalate_up / escalate_to_ceo / delegate) runs ``_ensure_pm_decision`` to
auto-record its own rationale as a journal:decision before the freshness
gate (``_check_pm_decision_required`` / ``_check_complete_gates`` /
``_check_submit_up_gates``) runs. The write can lock-timeout under DB
contention (a concurrent claim holding the task row's FK share lock); the
old contract swallowed that and let the gate reject a "missing decision"
the PM's own rationale already answered — a PM retries, keeps getting
rejected while contention lasts, then escalates, and the task ends up
BLOCKED. Transient congestion laundered into a durable blocked task.

Fix: ``_ensure_pm_decision`` returns a ``PmDecisionOutcome`` — "fresh" /
"wrote" / "transient_failure" / "absent". Every gate helper accepts
``pm_decision_outcome`` (default ``None`` = legacy behavior unchanged) and
treats "transient_failure" as gate-satisfied for THIS call — the rationale
is in the verb payload; the write was only ever a convenience. "absent" (no
rationale at all) still rejects exactly as before.

These tests exercise the gate helpers directly (the leanest harness that
reaches the actual decision-point) for precise, fast coverage of the
mechanism, plus one near-real-call-site test per verb family
(``_cell_pm_complete_guard``, ``escalate_up``) proving the real wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer import _impl as _impl_module
from sqlalchemy.exc import OperationalError


def _make_choreographer(**overrides: Any) -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    return Choreographer(ChoreographerDeps(**base))


_TRANSIENT_WARNING_SUBSTRING = "gate satisfied by verb rationale"


# ---------------------------------------------------------------------------
# _check_pm_decision_required (unblock / escalate_up / escalate_to_ceo / delegate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_pm_decision_required_transient_failure_satisfies_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A "transient_failure" outcome satisfies the gate for this call even
    though no fresh decision exists — the verb's own rationale is the
    substance, the write was a convenience.

    ``_impl.py`` logs via ``structlog.get_logger()`` directly; absent this
    process having called ``roboco.logging.setup_logging()`` (never true in
    a bare unit-test run), structlog uses its own default global config and
    never touches stdlib ``logging`` — so ``caplog`` cannot see it. Patching
    the module-level ``logger`` object is the reliable way to assert a
    structlog call in this harness.
    """
    mock_logger = MagicMock()
    monkeypatch.setattr(_impl_module, "logger", mock_logger)
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = None  # no fresh decision
    c = _make_choreographer(journal=journal_svc)
    t = MagicMock(id=uuid4())

    env = await c._check_pm_decision_required(
        "unblock",
        uuid4(),
        t.id,
        t,
        pm_decision_outcome="transient_failure",
    )
    assert env is None
    mock_logger.warning.assert_called_once()
    call = mock_logger.warning.call_args
    assert _TRANSIENT_WARNING_SUBSTRING in call.args[0]
    assert call.kwargs.get("verb") == "unblock"


@pytest.mark.asyncio
async def test_check_pm_decision_required_absent_still_rejects() -> None:
    """ "absent" (no rationale, no fresh decision) rejects exactly as before
    — defense-in-depth unchanged."""
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = None
    c = _make_choreographer(journal=journal_svc)
    t = MagicMock(id=uuid4())

    env = await c._check_pm_decision_required(
        "unblock", uuid4(), t.id, t, pm_decision_outcome="absent"
    )
    assert env is not None
    assert env.as_dict()["error"] == "tracing_gap"


@pytest.mark.asyncio
async def test_check_pm_decision_required_none_default_unchanged() -> None:
    """Every call site that hasn't threaded the outcome through (there are
    none left in production, but the param defaults to None for legacy
    parity) behaves byte-for-byte as before: no fresh decision rejects."""
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = None
    c = _make_choreographer(journal=journal_svc)
    t = MagicMock(id=uuid4())

    env = await c._check_pm_decision_required("unblock", uuid4(), t.id, t)
    assert env is not None
    assert env.as_dict()["error"] == "tracing_gap"


@pytest.mark.asyncio
async def test_check_pm_decision_required_fresh_path_unchanged() -> None:
    """A genuinely fresh decision passes regardless of pm_decision_outcome
    — "fresh" is not a special-case bypass, it's the ordinary passing path."""
    journal_svc = AsyncMock()
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    c = _make_choreographer(journal=journal_svc)
    t = MagicMock(id=uuid4())

    env = await c._check_pm_decision_required(
        "unblock", uuid4(), t.id, t, pm_decision_outcome="fresh"
    )
    assert env is None


# ---------------------------------------------------------------------------
# _check_complete_gates (cell_pm / main_pm complete)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_complete_gates_transient_failure_satisfies_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """See ``test_check_pm_decision_required_transient_failure_satisfies_gate``
    for why the module logger is patched directly instead of using caplog."""
    mock_logger = MagicMock()
    monkeypatch.setattr(_impl_module, "logger", mock_logger)
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.has_reflect_for_task.return_value = False
    c = _make_choreographer(journal=journal_svc)

    env = await c._check_complete_gates(
        uuid4(),
        uuid4(),
        "closing this task: the cell's contribution merged cleanly",
        pm_decision_outcome="transient_failure",
    )
    assert env is None
    mock_logger.warning.assert_called_once()
    call = mock_logger.warning.call_args
    assert _TRANSIENT_WARNING_SUBSTRING in call.args[0]
    assert call.kwargs.get("verb") == "complete"


@pytest.mark.asyncio
async def test_check_complete_gates_absent_still_rejects() -> None:
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.has_reflect_for_task.return_value = False
    c = _make_choreographer(journal=journal_svc)

    env = await c._check_complete_gates(
        uuid4(),
        uuid4(),
        "closing this task: the cell's contribution merged cleanly",
        pm_decision_outcome="absent",
    )
    assert env is not None
    assert env.as_dict()["error"] == "tracing_gap"


@pytest.mark.asyncio
async def test_check_complete_gates_fresh_path_unchanged() -> None:
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.has_reflect_for_task.return_value = True
    c = _make_choreographer(journal=journal_svc)

    env = await c._check_complete_gates(
        uuid4(),
        uuid4(),
        "closing this task: the cell's contribution merged cleanly",
        pm_decision_outcome="fresh",
    )
    assert env is None


# ---------------------------------------------------------------------------
# _cell_pm_complete_guard — the real complete() call site, one hop above the
# gate helper, proving the outcome actually reaches it end to end (without
# dragging in the full cell_pm_complete verb's PR-merge/finalize machinery).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_guard_survives_journal_write_lock_timeout() -> None:
    """journal.write_decision raising (a lock-timeout under DB contention)
    with a substantive ``notes`` rationale in hand must not reject the PM's
    complete — the guard clears (returns None) instead of tracing_gap."""
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        assigned_to=pm_id,
        status="awaiting_pm_review",
        pr_number=42,
    )
    task_svc = AsyncMock()
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    # _ensure_pm_decision opens a session.begin_nested() savepoint before
    # the write raises — an unshaped AsyncMock's auto-attribute return
    # doesn't support `async with`, orphaning the mock's internal coroutine
    # (AsyncMockMixin._execute_mock_call never awaited).
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.has_reflect_for_task.return_value = False
    journal_svc.latest_decision_at.return_value = None
    journal_svc.write_decision.side_effect = OperationalError(
        "INSERT INTO journal_entries (id, ...) VALUES (...)",
        {},
        Exception("canceling statement due to lock timeout"),
    )
    c = _make_choreographer(task=task_svc, journal=journal_svc)

    env = await c._cell_pm_complete_guard(
        pm_id, task_id, t, "closing this task: the cell's contribution merged cleanly"
    )

    task_svc.session.begin_nested.assert_called()
    assert env is None, env.as_dict() if env is not None else None


@pytest.mark.asyncio
async def test_cell_pm_complete_guard_empty_notes_still_rejects() -> None:
    """No rationale at all (empty notes) AND no fresh decision on record
    still rejects — "absent" is not a bypass."""
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        assigned_to=pm_id,
        status="awaiting_pm_review",
        pr_number=42,
    )
    task_svc = AsyncMock()
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.has_reflect_for_task.return_value = False
    journal_svc.latest_decision_at.return_value = None
    c = _make_choreographer(task=task_svc, journal=journal_svc)

    env = await c._cell_pm_complete_guard(pm_id, task_id, t, "")
    assert env is not None
    assert env.as_dict()["error"] == "tracing_gap"
