"""Supersede umbrella close-on-land guards (orchestration-marker storage).

The supersede marker (``pr={n} review={uuid}`` plus a ``closed=1`` token once
the contributor PR is retired) lives in ``orchestration_markers`` after
migration 041 — isolated from the human ``quick_context``, so CEO escalation /
approval notes can no longer be mistaken for it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.db.tables import TaskTable
from roboco.models.base import TaskStatus
from roboco.services.task import TaskService, supersede_marker_line

_VALUE = "pr=5 review=abc"
_LANDED_PR = 42  # the replacement PR number a landed descendant carries


def _scalars_all(rows: list[object]) -> MagicMock:
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    return res


def _service(execute_returns: object) -> TaskService:
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_returns)
    session.flush = AsyncMock()
    return TaskService(session)


def _bind(svc: TaskService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


def _task(supersede: str | None = None, **kw: Any) -> SimpleNamespace:
    om = {"external_pr_supersede": supersede} if supersede is not None else None
    return SimpleNamespace(orchestration_markers=om, **kw)


def _umbrella(pr_number: int | None) -> TaskTable:
    """A real TaskTable for direct calls into
    ``TaskService._supersede_replacement_landed``, which is typed to take a
    ``TaskTable`` -- only ``id``/``pr_number`` matter for that method, and a
    transient (never flushed) instance is safe here since the session is
    mocked."""
    return TaskTable(id=uuid4(), pr_number=pr_number)


# ---------------------------------------------------------------------------
# supersede_marker_line — reads the marker value
# ---------------------------------------------------------------------------


def test_marker_line_returns_value() -> None:
    assert supersede_marker_line(_task(_VALUE)) == _VALUE


def test_marker_line_empty_when_absent() -> None:
    assert supersede_marker_line(_task()) == ""
    assert supersede_marker_line(SimpleNamespace(orchestration_markers=None)) == ""


# ---------------------------------------------------------------------------
# supersede_umbrellas_pending_close — closed-token + landed-replacement gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_close_excludes_umbrella_with_closed_marker() -> None:
    umbrella = _task(f"{_VALUE} closed=1", id=uuid4())
    svc = _service(_scalars_all([umbrella]))
    _bind(svc, "_supersede_replacement_landed", AsyncMock(return_value=_LANDED_PR))
    assert await svc.supersede_umbrellas_pending_close() == []


@pytest.mark.asyncio
async def test_pending_close_keeps_open_landed_umbrella() -> None:
    umbrella = _task(_VALUE, id=uuid4())
    svc = _service(_scalars_all([umbrella]))
    _bind(svc, "_supersede_replacement_landed", AsyncMock(return_value=_LANDED_PR))
    assert await svc.supersede_umbrellas_pending_close() == [(umbrella, _LANDED_PR)]


@pytest.mark.asyncio
async def test_pending_close_requires_landed_replacement() -> None:
    # COMPLETED + no closed marker, but the replacement never landed (the code
    # subtask was cancelled) — close-on-land must skip it.
    umbrella = _task(_VALUE, id=uuid4())
    svc = _service(_scalars_all([umbrella]))
    _bind(svc, "_supersede_replacement_landed", AsyncMock(return_value=None))
    assert await svc.supersede_umbrellas_pending_close() == []


# ---------------------------------------------------------------------------
# _supersede_replacement_landed: own-pr preference, then subtree walk
# fallback (returns pr_number | None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replacement_landed_prefers_umbrella_own_pr() -> None:
    """The umbrella's OWN pr_number (stamped by submit_root) is the
    canonical replacement PR and must be preferred over the descendant walk
    -- the walk must not even run (no session.execute call)."""
    svc = _service(_scalars_all([]))
    umbrella = _umbrella(_LANDED_PR)
    assert await svc._supersede_replacement_landed(umbrella) == _LANDED_PR
    cast("AsyncMock", svc.session.execute).assert_not_called()


@pytest.mark.asyncio
async def test_replacement_landed_true_for_completed_descendant_with_pr() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.COMPLETED, pr_number=_LANDED_PR)
    svc = _service(_scalars_all([child]))
    umbrella = _umbrella(None)
    assert await svc._supersede_replacement_landed(umbrella) == _LANDED_PR


@pytest.mark.asyncio
async def test_replacement_landed_false_when_descendant_cancelled() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.CANCELLED, pr_number=42)
    svc = _service(_scalars_all([child]))
    umbrella = _umbrella(None)
    assert await svc._supersede_replacement_landed(umbrella) is None


@pytest.mark.asyncio
async def test_replacement_landed_false_when_completed_without_pr() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.COMPLETED, pr_number=None)
    svc = _service(_scalars_all([child]))
    umbrella = _umbrella(None)
    assert await svc._supersede_replacement_landed(umbrella) is None


@pytest.mark.asyncio
async def test_pending_close_uses_umbrella_own_pr_with_no_qualifying_descendant() -> (
    None
):
    """Regression: when no descendant qualifies but the umbrella itself has
    a merged PR of its own, close-on-land must still surface it instead of
    skipping the umbrella forever (this exercises the REAL, unmocked
    _supersede_replacement_landed through the full pending-close path)."""
    umbrella = _task(_VALUE, id=uuid4(), pr_number=_LANDED_PR)
    svc = _service(_scalars_all([umbrella]))
    assert await svc.supersede_umbrellas_pending_close() == [(umbrella, _LANDED_PR)]


# ---------------------------------------------------------------------------
# find_supersede_umbrella — value dedup (pr=N review= prefix match)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_umbrella_matches_by_value() -> None:
    match = _task(_VALUE, id=uuid4())
    other = _task("pr=9 review=z", id=uuid4())  # different PR
    svc = _service(_scalars_all([other, match]))
    found = await svc.find_supersede_umbrella(uuid4(), 5)
    assert found is match


# ---------------------------------------------------------------------------
# mark_supersede_pr_closed — token appended to the marker value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_closed_appends_token() -> None:
    task = _task(_VALUE)
    svc = _service(_scalars_all([]))
    _bind(svc, "get", AsyncMock(return_value=task))
    await svc.mark_supersede_pr_closed(uuid4())
    assert supersede_marker_line(task) == f"{_VALUE} closed=1"


@pytest.mark.asyncio
async def test_mark_closed_is_idempotent() -> None:
    task = _task(f"{_VALUE} closed=1")
    svc = _service(_scalars_all([]))
    _bind(svc, "get", AsyncMock(return_value=task))
    await svc.mark_supersede_pr_closed(uuid4())
    assert supersede_marker_line(task) == f"{_VALUE} closed=1"
