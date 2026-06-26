"""Choreographer._resolve_subtask_project — fan-out resolution coverage.

The method resolves which project a delegated subtask lands in. It has four
priority tiers, exercised here against a stub ``self`` (the cell-map match
path never touches ``self``, and the raise path only reads ``self.product``):

1. explicit ``inputs.project_id`` wins outright.
2. the parent's ad-hoc ``cell_projects`` map → the mapping for ``inputs.team``.
3. the parent's Product map (delegated to ``self.product.project_for``).
4. the parent's own ``project_id``.
5. otherwise ``TaskCompletenessError`` (no repo to land in).

The ad-hoc cell-map tier (2) is the multi-cell MegaTask root-subtask seam; the
other tiers are unchanged product-root / single-project behavior.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from roboco.foundation.policy.task_completeness import TaskCompletenessError
from roboco.models.base import Team
from roboco.services.gateway.choreographer._impl import (
    Choreographer,
    DelegateInputs,
)


def _inputs(*, team: Team, project_id: UUID | None = None) -> DelegateInputs:
    return DelegateInputs(
        title="t",
        description="d",
        assigned_to="be-dev-1",
        team=team.value,
        task_type="code",
        nature="technical",
        acceptance_criteria=["a"],
        project_id=project_id,
    )


def _mapping(team: Team, project_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(team=team, project_id=project_id)


@pytest.mark.asyncio
async def test_explicit_inputs_project_id_wins_over_cell_map() -> None:
    """Tier 1: an explicit project_id on delegate short-circuits the map."""
    be_proj, explicit = uuid4(), uuid4()
    parent = SimpleNamespace(
        cell_projects=[_mapping(Team.BACKEND, be_proj)],
        product_id=None,
        project_id=None,
    )
    self_stub = SimpleNamespace(product=None)
    resolved = await Choreographer._resolve_subtask_project(
        self_stub, parent, _inputs(team=Team.BACKEND, project_id=explicit)
    )
    assert resolved == explicit


@pytest.mark.asyncio
async def test_cell_map_resolves_project_for_matching_team() -> None:
    """Tier 2: the parent's cell map yields the project for the delegated cell."""
    be_proj, fe_proj = uuid4(), uuid4()
    parent = SimpleNamespace(
        cell_projects=[
            _mapping(Team.FRONTEND, fe_proj),
            _mapping(Team.BACKEND, be_proj),
        ],
        product_id=None,
        project_id=None,
    )
    self_stub = SimpleNamespace(product=None)
    resolved = await Choreographer._resolve_subtask_project(
        self_stub, parent, _inputs(team=Team.BACKEND)
    )
    assert resolved == be_proj


@pytest.mark.asyncio
async def test_cell_map_missing_team_falls_through_to_parent_project() -> None:
    """No mapping for the requested cell → fall through to the parent's own
    project_id (tier 4), not raise — the parent may still carry a project."""
    be_proj, own = uuid4(), uuid4()
    parent = SimpleNamespace(
        cell_projects=[_mapping(Team.BACKEND, be_proj)],
        product_id=None,
        project_id=own,
    )
    self_stub = SimpleNamespace(product=None)
    # Frontend subtask but the map only covers backend → fall to parent.project_id.
    resolved = await Choreographer._resolve_subtask_project(
        self_stub, parent, _inputs(team=Team.FRONTEND)
    )
    assert resolved == own


@pytest.mark.asyncio
async def test_cell_map_only_parent_with_no_match_raises_completeness() -> None:
    """A fan-out parent (cell map, no own project, no product) with no mapping
    for the requested cell raises TaskCompletenessError — the subtask has no
    repo to land in."""
    be_proj = uuid4()
    parent = SimpleNamespace(
        cell_projects=[_mapping(Team.BACKEND, be_proj)],
        product_id=None,
        project_id=None,
    )
    self_stub = SimpleNamespace(product=None)
    with pytest.raises(TaskCompletenessError) as exc:
        await Choreographer._resolve_subtask_project(
            self_stub, parent, _inputs(team=Team.FRONTEND)
        )
    assert "project_id" in exc.value.missing
