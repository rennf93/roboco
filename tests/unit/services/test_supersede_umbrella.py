"""Supersede umbrella close-on-land guards.

Covers the parts of the external-PR supersede flow that decide whether — and
which — a landed supersede's contributor PR gets retired:

- ``supersede_marker_line`` anchors marker/state checks to the marker line, so
  free-form CEO escalation/approval notes appended to the same multi-writer
  ``quick_context`` can't be mistaken for the marker.
- ``supersede_umbrellas_pending_close`` only returns umbrellas whose
  replacement work actually landed (a non-cancelled descendant carrying a PR),
  not every COMPLETED umbrella — the CEO can force-complete over a cancelled
  code subtask.
- ``mark_supersede_pr_closed`` writes the ``closed=1`` idempotency token onto
  the marker line, surviving appended notes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.task import TaskService, supersede_marker_line

_MARKER = "external_pr_supersede pr=5 review=abc"


def _scalars_all(rows: list[object]) -> MagicMock:
    """A session.execute return value whose .scalars().all() yields `rows`."""
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


# ---------------------------------------------------------------------------
# supersede_marker_line — line anchoring
# ---------------------------------------------------------------------------


def test_marker_line_returns_marker_ignoring_appended_notes() -> None:
    qc = f"{_MARKER}\nceo_approval_notes: shipped, looks good"
    assert supersede_marker_line(qc) == _MARKER


def test_marker_line_not_fooled_by_closed_token_in_note() -> None:
    qc = f"{_MARKER}\nceo_approval_notes: marked closed=1 in jira"
    # The marker line itself carries no closed=1, so the PR is NOT yet closed.
    assert "closed=1" not in supersede_marker_line(qc).split()


def test_marker_line_empty_when_absent() -> None:
    assert supersede_marker_line("no marker here\nescalation_notes: x") == ""
    assert supersede_marker_line(None) == ""


# ---------------------------------------------------------------------------
# supersede_umbrellas_pending_close — closed-token + landed-replacement gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_close_excludes_umbrella_with_closed_marker() -> None:
    umbrella = MagicMock(id=uuid4(), quick_context=f"{_MARKER} closed=1")
    svc = _service(_scalars_all([umbrella]))
    _bind(svc, "_supersede_replacement_landed", AsyncMock(return_value=True))
    assert await svc.supersede_umbrellas_pending_close() == []


@pytest.mark.asyncio
async def test_pending_close_keeps_umbrella_with_closed_token_only_in_note() -> None:
    # A CEO note containing the literal "closed=1" must NOT retire the PR.
    umbrella = MagicMock(
        id=uuid4(), quick_context=f"{_MARKER}\nceo_approval_notes: closed=1 elsewhere"
    )
    svc = _service(_scalars_all([umbrella]))
    _bind(svc, "_supersede_replacement_landed", AsyncMock(return_value=True))
    out = await svc.supersede_umbrellas_pending_close()
    assert out == [umbrella]


@pytest.mark.asyncio
async def test_pending_close_requires_landed_replacement() -> None:
    # COMPLETED + no closed marker, but the replacement never landed (the code
    # subtask was cancelled) — close-on-land must skip it.
    umbrella = MagicMock(id=uuid4(), quick_context=_MARKER)
    svc = _service(_scalars_all([umbrella]))
    _bind(svc, "_supersede_replacement_landed", AsyncMock(return_value=False))
    assert await svc.supersede_umbrellas_pending_close() == []


# ---------------------------------------------------------------------------
# _supersede_replacement_landed — subtree walk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replacement_landed_true_for_completed_descendant_with_pr() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.COMPLETED, pr_number=42)
    svc = _service(_scalars_all([child]))
    assert await svc._supersede_replacement_landed(uuid4()) is True


@pytest.mark.asyncio
async def test_replacement_landed_false_when_descendant_cancelled() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.CANCELLED, pr_number=42)
    svc = _service(_scalars_all([child]))
    # The `seen` guard terminates the walk even though the mock re-returns child.
    assert await svc._supersede_replacement_landed(uuid4()) is False


@pytest.mark.asyncio
async def test_replacement_landed_false_when_completed_without_pr() -> None:
    child = MagicMock(id=uuid4(), status=TaskStatus.COMPLETED, pr_number=None)
    svc = _service(_scalars_all([child]))
    assert await svc._supersede_replacement_landed(uuid4()) is False


# ---------------------------------------------------------------------------
# find_supersede_umbrella — marker-line dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_umbrella_matches_marker_not_note() -> None:
    match = MagicMock(id=uuid4(), quick_context=f"{_MARKER}\nescalation_notes: x")
    other = MagicMock(
        id=uuid4(),
        # marker for a different PR, but a note mentions "pr=5 review=" text
        quick_context="external_pr_supersede pr=9 review=z\nnote: see pr=5 review= ok",
    )
    svc = _service(_scalars_all([other, match]))
    found = await svc.find_supersede_umbrella(uuid4(), 5)
    assert found is match


# ---------------------------------------------------------------------------
# mark_supersede_pr_closed — token written on the marker line
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_closed_appends_token_to_marker_line() -> None:
    task = MagicMock(quick_context=f"{_MARKER}\nceo_approval_notes: shipped")
    svc = _service(_scalars_all([]))
    _bind(svc, "get", AsyncMock(return_value=task))
    await svc.mark_supersede_pr_closed(uuid4())
    lines = task.quick_context.splitlines()
    assert lines[0] == f"{_MARKER} closed=1"
    assert lines[1] == "ceo_approval_notes: shipped"  # note untouched


@pytest.mark.asyncio
async def test_mark_closed_is_idempotent_on_marker_line() -> None:
    task = MagicMock(quick_context=f"{_MARKER} closed=1\nceo_approval_notes: x")
    svc = _service(_scalars_all([]))
    _bind(svc, "get", AsyncMock(return_value=task))
    await svc.mark_supersede_pr_closed(uuid4())
    # No second closed=1 token appended.
    assert task.quick_context.splitlines()[0] == f"{_MARKER} closed=1"
