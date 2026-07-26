"""roboco.services.gateway.content_actions.nothing_to_propose — the generic
"this cycle found nothing worth proposing" exit for any Board Program
exploration task. Mirrors test_content_actions_barfly.py's mock-based shape;
registry-driven (not a hardcoded role set), so the role gate is exercised
against more than one program to prove it isn't a single frozenset."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps


class _FakeTask:
    """Minimal stand-in for the ORM TaskTable row — carries just what
    ``nothing_to_propose`` touches."""

    def __init__(
        self,
        *,
        source: str,
        assigned_to: Any,
        status: Any = TaskStatus.PENDING,
    ) -> None:
        self.id = uuid4()
        self.source = source
        self.assigned_to = assigned_to
        self.status = status


def _actions(role: str) -> ContentActions:
    task = MagicMock()
    agent = MagicMock()
    agent.role = role
    task.agent_for = AsyncMock(return_value=agent)
    task.session = MagicMock()
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=MagicMock(),
        notifications=MagicMock(),
        notification_delivery=None,
    )
    return ContentActions(deps)


def _patch_task_lookup(actions: ContentActions, task: _FakeTask | None) -> None:
    """``nothing_to_propose`` now resolves the caller's task by EXPLICIT
    task_id (``self.task.get(task_id)``), mirroring ``curate_vault`` —
    no more guessing at "the caller's open task"."""
    actions.task.get = AsyncMock(return_value=task)


def _patch_board_program_engine(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    engine = MagicMock()
    engine.record_nothing_to_propose = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.board_programs.get_board_program_engine", lambda _s: engine
    )
    return engine


REASON = "Reviewed the last 10 candidates; none were on-topic or worth a reply."


# --------------------------------------------------------------------------- #
# Reason validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_empty_reason() -> None:
    env = await _actions("product_owner").nothing_to_propose(
        agent_id=uuid4(), task_id=uuid4(), reason=""
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_trivial_reason() -> None:
    env = await _actions("product_owner").nothing_to_propose(
        agent_id=uuid4(), task_id=uuid4(), reason="wip"
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_too_short_reason() -> None:
    env = await _actions("product_owner").nothing_to_propose(
        agent_id=uuid4(), task_id=uuid4(), reason="nothing found"[:10]
    )
    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_oversized_reason() -> None:
    env = await _actions("product_owner").nothing_to_propose(
        agent_id=uuid4(), task_id=uuid4(), reason="x" * 801
    )
    assert env.error == "invalid_state"
    assert "801" in (env.message or "")


# --------------------------------------------------------------------------- #
# Task resolution — task_id is REQUIRED and resolved by direct fetch-by-id,
# never guessed at from "the caller's open task" (see DEFECT 1: one role can
# own several open exploration tasks from different programs at once).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_nothing_to_propose_missing_task_is_not_found() -> None:
    actions = _actions("product_owner")
    _patch_task_lookup(actions, None)
    env = await actions.nothing_to_propose(
        agent_id=uuid4(), task_id=uuid4(), reason=REASON
    )
    assert env.error == "not_found"


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_task_not_assigned_to_caller() -> None:
    task = _FakeTask(source="board_barfly", assigned_to=uuid4())
    actions = _actions("head_marketing")
    _patch_task_lookup(actions, task)
    env = await actions.nothing_to_propose(
        agent_id=uuid4(), task_id=task.id, reason=REASON
    )
    assert env.error == "not_authorized"
    assert "not assigned to you" in (env.message or "")


@pytest.mark.asyncio
async def test_nothing_to_propose_rejects_terminal_task() -> None:
    agent_id = uuid4()
    task = _FakeTask(
        source="board_barfly", assigned_to=agent_id, status=TaskStatus.COMPLETED
    )
    actions = _actions("head_marketing")
    _patch_task_lookup(actions, task)
    env = await actions.nothing_to_propose(
        agent_id=agent_id, task_id=task.id, reason=REASON
    )
    assert env.error == "invalid_state"
    assert "completed" in (env.message or "")


# --------------------------------------------------------------------------- #
# Registry-driven role gate — proven against TWO different programs, not one
# hardcoded frozenset.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_nothing_to_propose_wrong_role_rejected_for_barfly() -> None:
    """barfly's declared role is head_marketing — a product_owner resolving
    an (implausibly mis-assigned) barfly task is refused."""
    agent_id = uuid4()
    task = _FakeTask(source="board_barfly", assigned_to=agent_id)
    actions = _actions("product_owner")
    _patch_task_lookup(actions, task)
    env = await actions.nothing_to_propose(
        agent_id=agent_id, task_id=task.id, reason=REASON
    )
    assert env.error == "not_authorized"
    assert "head_marketing" in (env.message or "")


@pytest.mark.asyncio
async def test_nothing_to_propose_wrong_role_rejected_for_pest_control() -> None:
    """pest_control's declared role is product_owner — a head_marketing
    caller is refused, proving the gate is registry-driven per-task, not a
    single fixed role."""
    agent_id = uuid4()
    task = _FakeTask(source="board_pest_control", assigned_to=agent_id)
    actions = _actions("head_marketing")
    _patch_task_lookup(actions, task)
    env = await actions.nothing_to_propose(
        agent_id=agent_id, task_id=task.id, reason=REASON
    )
    assert env.error == "not_authorized"
    assert "product_owner" in (env.message or "")


@pytest.mark.asyncio
async def test_nothing_to_propose_unregistered_source_is_invalid_state() -> None:
    """Defensive: a task whose source isn't in PROGRAMS (unreachable via a
    real spawn, which only ever assigns registered-program sources) fails
    clean rather than crashing on a bare KeyError."""
    agent_id = uuid4()
    task = _FakeTask(source="not_a_real_program", assigned_to=agent_id)
    actions = _actions("product_owner")
    _patch_task_lookup(actions, task)
    env = await actions.nothing_to_propose(
        agent_id=agent_id, task_id=task.id, reason=REASON
    )
    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# Cross-program regression (DEFECT 1): the resolved task is ALWAYS the one
# named by task_id, proven by handing a task_id that belongs to a DIFFERENT
# program than the one a naive "caller's open task" guess would have picked.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_nothing_to_propose_completes_exactly_the_named_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    named = _FakeTask(source="board_megaphone", assigned_to=agent_id)
    actions = _actions("head_marketing")
    _patch_task_lookup(actions, named)
    _patch_board_program_engine(monkeypatch)
    actions.task.session.flush = AsyncMock()

    env = await actions.nothing_to_propose(
        agent_id=agent_id, task_id=named.id, reason=REASON
    )

    assert env.error is None, env.message
    assert env.task_id == str(named.id)
    assert env.context_briefing["program"] == "megaphone"
    actions.task.get.assert_awaited_once_with(named.id)


# --------------------------------------------------------------------------- #
# Happy path — completes the task and records the LEARN reason
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_nothing_to_propose_happy_path_completes_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    task = _FakeTask(source="board_barfly", assigned_to=agent_id)
    actions = _actions("head_marketing")
    _patch_task_lookup(actions, task)
    _patch_board_program_engine(monkeypatch)

    actions.task.session.flush = AsyncMock()

    env = await actions.nothing_to_propose(
        agent_id=agent_id, task_id=task.id, reason=REASON
    )

    assert env.error is None, env.message
    assert env.status == "nothing_to_propose"
    assert env.task_id == str(task.id)
    assert task.status == TaskStatus.COMPLETED
    assert env.context_briefing["program"] == "barfly"
    assert env.context_briefing["reason"] == REASON


@pytest.mark.asyncio
async def test_nothing_to_propose_records_learn_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    task = _FakeTask(source="board_coroner", assigned_to=agent_id)
    actions = _actions("auditor")
    _patch_task_lookup(actions, task)
    engine = _patch_board_program_engine(monkeypatch)

    actions.task.session.flush = AsyncMock()

    await actions.nothing_to_propose(agent_id=agent_id, task_id=task.id, reason=REASON)

    engine.record_nothing_to_propose.assert_awaited_once_with(
        "coroner", task.id, REASON
    )


@pytest.mark.asyncio
async def test_nothing_to_propose_learn_record_failure_does_not_fail_verb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LEARN-ledger write failure is best-effort — the verb still
    completes the task and returns ok, mirroring every other
    record_decision producer's own best-effort wrapping. It is isolated in
    its own savepoint (DEFECT 2), so this proves the failure alone, not
    whether the completion would additionally survive a real commit — that
    "does the outer transaction actually survive" guarantee needs a real DB
    session and is proven in
    test_nothing_to_propose_learn_failure_does_not_poison_completion
    (tests/unit/services/test_board_program_engine.py)."""
    agent_id = uuid4()
    task = _FakeTask(source="board_barfly", assigned_to=agent_id)
    actions = _actions("head_marketing")
    _patch_task_lookup(actions, task)
    engine = MagicMock()
    engine.record_nothing_to_propose = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(
        "roboco.services.board_programs.get_board_program_engine", lambda _s: engine
    )

    actions.task.session.flush = AsyncMock()

    env = await actions.nothing_to_propose(
        agent_id=agent_id, task_id=task.id, reason=REASON
    )
    assert env.error is None, env.message
    assert task.status == TaskStatus.COMPLETED
