"""Wave C6 (2026-05-12): auditor spawns on escalation / block / cancel events.

The auditor's role is "silent observer" — read every channel, write
reflect notes on patterns worth surfacing. Smoke run 3 never spawned
auditor because no spawn trigger was registered for the events the
auditor cares about.

Handler lives in roboco/events/handlers.py and is registered by
register_default_handlers(). The orchestrator is injected via
set_event_context(orchestrator=...) so the handler can call
_context.orchestrator.spawn_agent("auditor").
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.events.handlers import (
    handle_auditor_spawn,
    register_default_handlers,
    set_event_context,
)
from roboco.models.events import Event, EventContext, EventType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_type: EventType, task_id: str | None = None) -> Event:
    data: dict = {}
    if task_id:
        data["task_id"] = task_id
    return Event(type=event_type, data=data)


def _make_context(spawn_mock: AsyncMock) -> EventContext:
    orchestrator = MagicMock()
    orchestrator.spawn_agent = spawn_mock
    ctx = EventContext(orchestrator=orchestrator)
    return ctx


# ---------------------------------------------------------------------------
# handle_auditor_spawn — unit tests (call the function directly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auditor_spawns_on_task_blocked() -> None:
    """Emitting a task.blocked event triggers an auditor spawn."""
    spawn = AsyncMock()
    ctx = _make_context(spawn)
    set_event_context(orchestrator=ctx.orchestrator)

    task_id = str(uuid4())
    event = _make_event(EventType.TASK_BLOCKED, task_id=task_id)

    await handle_auditor_spawn(event)

    spawn.assert_awaited_once_with(agent_id="auditor")


@pytest.mark.asyncio
async def test_auditor_spawns_on_task_cancelled() -> None:
    """task.cancelled triggers auditor spawn."""
    spawn = AsyncMock()
    ctx = _make_context(spawn)
    set_event_context(orchestrator=ctx.orchestrator)

    task_id = str(uuid4())
    event = _make_event(EventType.TASK_CANCELLED, task_id=task_id)

    await handle_auditor_spawn(event)

    spawn.assert_awaited_once_with(agent_id="auditor")


@pytest.mark.asyncio
async def test_auditor_spawns_on_task_escalated_to_ceo() -> None:
    """task.awaiting_ceo_approval triggers auditor spawn."""
    spawn = AsyncMock()
    ctx = _make_context(spawn)
    set_event_context(orchestrator=ctx.orchestrator)

    task_id = str(uuid4())
    event = _make_event(EventType.TASK_AWAITING_CEO_APPROVAL, task_id=task_id)

    await handle_auditor_spawn(event)

    spawn.assert_awaited_once_with(agent_id="auditor")


@pytest.mark.asyncio
async def test_auditor_does_not_spawn_when_no_orchestrator() -> None:
    """No orchestrator in context = silently skip, no crash."""
    set_event_context(orchestrator=None)

    event = _make_event(EventType.TASK_BLOCKED, task_id=str(uuid4()))
    # Should not raise; nothing to assert except no exception
    await handle_auditor_spawn(event)


@pytest.mark.asyncio
async def test_auditor_spawn_failure_does_not_propagate() -> None:
    """spawn_agent raising an exception must NOT bubble out of the handler.

    Auditor spawn failure must not block the underlying event's processing.
    """
    spawn = AsyncMock(side_effect=RuntimeError("container start failed"))
    ctx = _make_context(spawn)
    set_event_context(orchestrator=ctx.orchestrator)

    event = _make_event(EventType.TASK_BLOCKED, task_id=str(uuid4()))

    # Must not raise
    await handle_auditor_spawn(event)

    spawn.assert_awaited_once_with(agent_id="auditor")


# ---------------------------------------------------------------------------
# register_default_handlers — integration: verify subscriptions are wired
# ---------------------------------------------------------------------------


def test_auditor_handler_registered_for_blocked() -> None:
    """register_default_handlers subscribes handle_auditor_spawn to TASK_BLOCKED."""
    bus = MagicMock()
    bus.subscribe = MagicMock()

    register_default_handlers(bus)

    calls = [(c.args[0], c.args[1]) for c in bus.subscribe.call_args_list]
    assert (EventType.TASK_BLOCKED, handle_auditor_spawn) in calls


def test_auditor_handler_registered_for_cancelled() -> None:
    """register_default_handlers subscribes handle_auditor_spawn to TASK_CANCELLED."""
    bus = MagicMock()
    bus.subscribe = MagicMock()

    register_default_handlers(bus)

    calls = [(c.args[0], c.args[1]) for c in bus.subscribe.call_args_list]
    assert (EventType.TASK_CANCELLED, handle_auditor_spawn) in calls


def test_auditor_handler_registered_for_ceo_escalation() -> None:
    """handle_auditor_spawn is subscribed to TASK_AWAITING_CEO_APPROVAL."""
    bus = MagicMock()
    bus.subscribe = MagicMock()

    register_default_handlers(bus)

    calls = [(c.args[0], c.args[1]) for c in bus.subscribe.call_args_list]
    assert (EventType.TASK_AWAITING_CEO_APPROVAL, handle_auditor_spawn) in calls


def test_auditor_handler_not_registered_for_routine_events() -> None:
    """Routine events (task.claimed, task.started) do NOT trigger auditor.

    Auditor's mandate is exceptional events; routine progress doesn't
    need observation.
    """
    bus = MagicMock()
    bus.subscribe = MagicMock()

    register_default_handlers(bus)

    calls = [(c.args[0], c.args[1]) for c in bus.subscribe.call_args_list]
    routine_events = [
        EventType.TASK_CLAIMED,
        EventType.TASK_STARTED,
        EventType.TASK_CREATED,
    ]
    for routine_event in routine_events:
        assert (routine_event, handle_auditor_spawn) not in calls, (
            f"auditor must not subscribe to routine event {routine_event}"
        )
