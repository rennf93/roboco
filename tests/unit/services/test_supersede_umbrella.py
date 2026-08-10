"""Supersede umbrella close-on-land guards (orchestration-marker storage).

The supersede marker (``pr={n} review={uuid}`` plus a ``closed=1`` token once
the contributor PR is retired) lives in ``orchestration_markers`` after
migration 041 — isolated from the human ``quick_context``, so CEO escalation /
approval notes can no longer be mistaken for it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
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
# _supersede_replacement_landed: subtree walk (returns pr_number | None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replacement_landed_true_for_completed_descendant_with_pr() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.COMPLETED, pr_number=_LANDED_PR)
    svc = _service(_scalars_all([child]))
    assert await svc._supersede_replacement_landed(uuid4()) == _LANDED_PR


@pytest.mark.asyncio
async def test_replacement_landed_false_when_descendant_cancelled() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.CANCELLED, pr_number=42)
    svc = _service(_scalars_all([child]))
    assert await svc._supersede_replacement_landed(uuid4()) is None


@pytest.mark.asyncio
async def test_replacement_landed_false_when_completed_without_pr() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.COMPLETED, pr_number=None)
    svc = _service(_scalars_all([child]))
    assert await svc._supersede_replacement_landed(uuid4()) is None


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
