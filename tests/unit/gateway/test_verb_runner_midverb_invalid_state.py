"""#102: a concurrent mid-verb state change surfaces as a clean INVALID_STATE.

The verb runner wraps the composed atomic actions in a savepoint and detects a
mid-sequence ``None``: a composed action returns ``None`` when its source-status
check fails after a CONCURRENT transition moved the row between the verb's
precondition gate and execution (e.g. ``i_will_plan``'s gate saw
``needs_revision`` but a racing ``i_am_blocked`` moved it to ``blocked``, so
``claim()`` found no valid transition and returned ``None``). The runner raises
``INVALID_STATE`` with a re-fetch instruction instead of crashing on
``None.id`` — so the choreographer surfaces an actionable rejection and the agent
re-fetches + re-issues, never respawn-loops. This pins that protection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer._verb_runner import VerbRunner


def _runner(task_service: MagicMock) -> VerbRunner:
    return VerbRunner(task_service=task_service, git_service=MagicMock())


def _session_with_savepoint() -> MagicMock:
    session = MagicMock()
    session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return session


@pytest.mark.asyncio
async def test_mid_verb_none_raises_invalid_state() -> None:
    """i_will_plan composes (claim, set_plan, start). If a concurrent
    transition makes ``set_plan`` return None mid-sequence, the runner raises
    INVALID_STATE before dispatching ``start`` — not a cryptic None.id crash."""
    task = MagicMock(id=uuid4())
    task_service = MagicMock()
    task_service.session = _session_with_savepoint()
    task_service.claim = AsyncMock(return_value=task)
    task_service.set_plan = AsyncMock(return_value=None)  # mid-verb source-status fail
    task_service.start = AsyncMock(return_value=task)

    agent = MagicMock(id=uuid4())
    ctx = MagicMock()
    runner = _runner(task_service)

    with pytest.raises(ValueError, match="INVALID_STATE"):
        await runner.run_intent("i_will_plan", task, agent, ctx)

    # start must NOT run after the None — the runner fails loud before it.
    task_service.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_last_composed_none_flows_out_as_result() -> None:
    """A None from the LAST composed action is the verb's own result (e.g. a
    board verb's decline), not a mid-verb crash — it flows out so the caller's
    ``if task is None`` handler surfaces the verb-specific message. The
    INVALID_STATE guard fires only on an INTERMEDIATE None."""
    task = MagicMock(id=uuid4())
    task_service = MagicMock()
    task_service.session = _session_with_savepoint()
    task_service.claim = AsyncMock(return_value=task)
    task_service.set_plan = AsyncMock(return_value=task)
    task_service.start = AsyncMock(return_value=None)  # last action declines

    agent = MagicMock(id=uuid4())
    ctx = MagicMock()
    runner = _runner(task_service)

    result = await runner.run_intent("i_will_plan", task, agent, ctx)
    assert result is None  # the verb's own None result, not a raised INVALID_STATE
