"""The startup pass that puts work authors' extra claims back in their queues.

Live 2026-11-01 the dispatcher started with be-dev-1 still holding claimed
106f46e3 next to its own f5d66db6 (left by the 2026-10-28 run, before the
single-active-task rule covered every path), and booted be-dev-1 onto that
claim at 04:56:06; nothing at startup reconciled such rows.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState
from roboco.seeds.initial_data import AGENT_UUIDS

_WORK_AUTHORS = {
    "be-dev-1",
    "be-dev-2",
    "fe-dev-1",
    "fe-dev-2",
    "ux-dev-1",
    "ux-dev-2",
    "devops-1",
}
_CLAIM = "106f46e3-ad66-4a32-a546-3ba53c6ad0f3"


def _orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


@pytest.mark.asyncio
async def test_every_idle_work_author_is_reconciled_and_nobody_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orchestrator()
    orch._instances["be-dev-2"] = AgentInstance(
        agent_id="be-dev-2", state=AgentState.ACTIVE
    )
    per_author = AsyncMock(return_value=1)
    monkeypatch.setattr(orch, "_release_extra_claims_of", per_author)

    released = await orch._release_extra_claims_of_work_authors()

    reconciled = {call.args[0] for call in per_author.await_args_list}
    assert reconciled == _WORK_AUTHORS - {"be-dev-2"}
    assert released == len(reconciled)


def _session_factory(db: Any) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=MagicMock(return_value=ctx))


@pytest.mark.asyncio
async def test_an_authors_releases_are_committed_and_counted() -> None:
    orch = _orchestrator()
    db = MagicMock()
    db.commit = AsyncMock()
    release = AsyncMock(return_value=[SimpleNamespace(id=UUID(_CLAIM))])

    with (
        patch("roboco.db.base.get_session_factory", _session_factory(db)),
        patch(
            "roboco.services.task.TaskService.release_never_started_extra_claims",
            release,
        ),
    ):
        released = await orch._release_extra_claims_of(
            "be-dev-1", AGENT_UUIDS["be-dev-1"]
        )

    assert released == 1
    release.assert_awaited_once_with(UUID(AGENT_UUIDS["be-dev-1"]))
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_author_never_blocks_startup() -> None:
    orch = _orchestrator()
    with patch(
        "roboco.db.base.get_session_factory", side_effect=RuntimeError("db down")
    ):
        released = await orch._release_extra_claims_of(
            "be-dev-1", AGENT_UUIDS["be-dev-1"]
        )

    assert released == 0
