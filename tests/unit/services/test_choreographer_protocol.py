"""ChoreographerHelpers stub coverage.

The class is a TYPE_CHECKING-only stub used to give mypy a typed view of the
Choreographer's helper methods. The real impls live on `_LegacyChoreographer`
and resolve via MRO — these stubs raise NotImplementedError when called
directly. We instantiate the bare class and confirm each stub raises so the
file shows up in coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer._protocol import ChoreographerHelpers

if TYPE_CHECKING:
    from roboco.services.gateway.envelope import Envelope


def _empty_env() -> Any:
    return {
        "status": "ok",
        "task_id": None,
        "next": None,
        "evidence": None,
        "context_briefing": None,
    }


@pytest.mark.asyncio
async def test_emit_rejection_raises() -> None:
    helpers = ChoreographerHelpers()
    with pytest.raises(NotImplementedError):
        await helpers._emit_rejection(
            _empty_env(), agent_id=uuid4(), task_id=None, verb="x"
        )


@pytest.mark.asyncio
async def test_briefing_for_raises() -> None:
    helpers = ChoreographerHelpers()
    with pytest.raises(NotImplementedError):
        await helpers._briefing_for(uuid4(), None)


def test_with_briefing_raises() -> None:
    with pytest.raises(NotImplementedError):
        ChoreographerHelpers._with_briefing(_empty_env(), {})


@pytest.mark.asyncio
async def test_run_claim_guards_raises() -> None:
    helpers = ChoreographerHelpers()
    with pytest.raises(NotImplementedError):
        await helpers._run_claim_guards(agent_id=uuid4(), task=None)


@pytest.mark.asyncio
async def test_touch_raises() -> None:
    helpers = ChoreographerHelpers()
    with pytest.raises(NotImplementedError):
        await helpers._touch(uuid4())
