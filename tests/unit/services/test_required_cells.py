"""required_cells decomposition gate — marker parse + uncovered-cell coverage.

The Main PM must create a subtask for each cell the brief explicitly names
(recorded as a ``required_cells:`` marker on the parent's quick_context). The
gate is inert when no marker is present, so legacy decompositions never block.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.task import TaskService, extract_required_cells

# ---------------------------------------------------------------------------
# extract_required_cells (marker parser)
# ---------------------------------------------------------------------------


def test_extract_required_cells_absent_is_empty() -> None:
    assert extract_required_cells(None) == []
    assert extract_required_cells("original_developer: abc\ndoc_notes: y") == []


def test_extract_required_cells_parses_and_normalizes() -> None:
    qc = "original_developer: abc\nrequired_cells: Backend, Frontend , UX/UI"
    assert extract_required_cells(qc) == ["backend", "frontend", "ux_ui"]


def test_extract_required_cells_dedups_in_order() -> None:
    out = extract_required_cells("required_cells: backend, backend, frontend")
    assert out == ["backend", "frontend"]


# ---------------------------------------------------------------------------
# uncovered_required_cells (service coverage check)
# ---------------------------------------------------------------------------


def _service(parent_qc: str | None, child_teams: list[str | None]) -> TaskService:
    """A TaskService whose get()/get_subtasks() return a parent + these children."""
    svc = TaskService(MagicMock())
    parent = MagicMock(quick_context=parent_qc)
    children = [MagicMock(team=t) for t in child_teams]
    object.__setattr__(svc, "get", AsyncMock(return_value=parent))
    object.__setattr__(svc, "get_subtasks", AsyncMock(return_value=children))
    return svc


@pytest.mark.asyncio
async def test_uncovered_inert_without_marker() -> None:
    svc = _service("doc_notes: x", ["backend"])
    assert await svc.uncovered_required_cells(uuid4()) == []


@pytest.mark.asyncio
async def test_uncovered_flags_the_dropped_cell() -> None:
    # Brief named backend+frontend+ux_ui; only backend+frontend got subtasks.
    svc = _service("required_cells: backend, frontend, ux_ui", ["backend", "frontend"])
    assert await svc.uncovered_required_cells(uuid4()) == ["ux_ui"]


@pytest.mark.asyncio
async def test_uncovered_empty_when_all_named_cells_covered() -> None:
    svc = _service("required_cells: backend, frontend", ["frontend", "backend"])
    assert await svc.uncovered_required_cells(uuid4()) == []


@pytest.mark.asyncio
async def test_uncovered_normalizes_child_team_form() -> None:
    # Marker uses underscore, child team uses the slash form — they match.
    svc = _service("required_cells: ux_ui", ["UX/UI"])
    assert await svc.uncovered_required_cells(uuid4()) == []
