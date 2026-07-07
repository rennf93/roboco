"""Startup sweep closes orphan agent_spawn_sessions rows.

A spawn session left open (ended_at IS NULL) by an orchestrator crash is
excluded from usage summaries (they filter ended_at IS NOT NULL). The sweep
closes any open session whose agent is no longer running, leaving running
agents' sessions open for their live finalize.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import roboco.db.base as db_base
from roboco.runtime.orchestrator import AgentOrchestrator

# select(orphans) then update(orphans) — two executes when work is done.
_SELECT_THEN_UPDATE_CALLS = 2


class _Rows:
    """Fake execute result holding the rows the sweep reads."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> Any:
        class _S:
            def __init__(self, rows: list[Any]) -> None:
                self._rows = rows

            def all(self) -> list[Any]:
                return self._rows

        return _S(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []
        self.executed: list[Any] = []
        self.committed = False

    def __call__(self) -> _FakeSession:
        return self

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, stmt: Any) -> _Rows:
        self.executed.append(stmt)
        return _Rows(self._rows)

    async def commit(self) -> None:
        self.committed = True


def _open_session(slug: str) -> MagicMock:
    row = MagicMock()
    row.id = f"sess-{slug}"
    row.agent_slug = slug
    row.ended_at = None
    return row


@pytest.mark.asyncio
async def test_sweep_closes_orphans_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    # be-dev-1 still running (re-adopted); be-dev-2 dead -> orphan.
    orch._instances = {"be-dev-1": MagicMock()}
    orphan = _open_session("be-dev-2")
    live = _open_session("be-dev-1")
    fake_session = _FakeSession(rows=[orphan, live])

    factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr(db_base, "get_session_factory", lambda: factory)

    closed = await orch._reconcile_orphan_spawn_sessions()

    assert closed == 1
    assert len(fake_session.executed) == _SELECT_THEN_UPDATE_CALLS
    assert fake_session.committed
    assert live.ended_at is None  # untouched (not in the update's where)


@pytest.mark.asyncio
async def test_sweep_no_op_when_no_open_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    fake_session = _FakeSession(rows=[])
    monkeypatch.setattr(db_base, "get_session_factory", lambda: fake_session)
    assert await orch._reconcile_orphan_spawn_sessions() == 0
    assert not fake_session.committed
