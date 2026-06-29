"""#7: assignee-vs-task_type matrix for delegate().

Cell-PM delegation friction: agents burned turns probing the
``_validate_assignee_task_type`` guard by trial and error. Two behaviors
are pinned here:

1. The UX/UI cell's developers ARE its designers — ``task_type='design'``
   is legitimate work for ``ux-dev-1``/``ux-dev-2`` (Role.DEVELOPER on
   Team.UX_UI), so delegate must accept it. Backend/frontend devs are NOT
   design assignees and stay rejected.
2. The rejection ``remediate`` is per-assignee-class: a developer mis-type
   gets a developer hint (not the generic 'pass planning to a Cell PM').
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.choreographer._impl import DelegateInputs


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
        "messaging": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
    for m in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, m).return_value = []
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _parent(parent_id: object, team: str) -> MagicMock:
    return MagicMock(
        id=parent_id,
        project_id=uuid4(),
        team=team,
        status="in_progress",
        task_type="planning",
        sequence=0,
        assigned_to=uuid4(),
    )


def _inputs(assigned_to: str, team: str, task_type: str) -> DelegateInputs:
    return DelegateInputs(
        title="Cell work item",
        description="A real unit of work for the cell to deliver end to end",
        acceptance_criteria=["the deliverable exists", "it is linked to a PR"],
        assigned_to=assigned_to,
        team=team,
        task_type=task_type,
        nature="technical",
        estimated_complexity="medium",
    )


# --- Behavior 1: validator matrix (pure function, no DB) ----------------


# The static-guard rejection for documentation lives in
# `_delegate_static_guards` (Task #163), NOT in `_validate_assignee_task_type`
# — the validator allows `documentation` for any dev role. So
# `documentation` is exercised separately in the static-guard tests below.
_CELL_PM_PLANNING_HINT = "delegating to a Cell PM"


@pytest.mark.parametrize(
    ("assigned_to", "task_type", "allowed"),
    [
        # UX devs are designers — design is legitimate.
        ("ux-dev-1", "design", True),
        ("ux-dev-2", "design", True),
        ("ux-dev-1", "code", True),
        ("ux-dev-1", "research", True),
        # Backend/frontend devs are NOT design assignees.
        ("be-dev-1", "design", False),
        ("fe-dev-2", "design", False),
        # Code is fine for any dev.
        ("be-dev-1", "code", True),
        ("fe-dev-1", "research", True),
    ],
)
def test_validate_assignee_task_type_matrix(
    assigned_to: str, task_type: str, allowed: bool
) -> None:
    err = Choreographer._validate_assignee_task_type(assigned_to, task_type)
    if allowed:
        assert err is None, f"{assigned_to}/{task_type} should be allowed, got {err!r}"
    else:
        assert err is not None, f"{assigned_to}/{task_type} should be rejected"
        assert assigned_to in err and task_type in err


def test_remediate_for_ux_dev_mentions_design() -> None:
    hint = Choreographer._assignee_task_type_remediate("ux-dev-1")
    assert "design" in hint.lower()
    # Not the generic Cell-PM planning hint.
    assert _CELL_PM_PLANNING_HINT not in hint


def test_remediate_for_backend_dev_routes_design_to_ux() -> None:
    hint = Choreographer._assignee_task_type_remediate("be-dev-1")
    assert "ux" in hint.lower()
    assert "code" in hint.lower()
    # Not the generic Cell-PM planning hint.
    assert _CELL_PM_PLANNING_HINT not in hint


def test_remediate_for_cell_pm_keeps_planning_hint() -> None:
    hint = Choreographer._assignee_task_type_remediate("be-pm")
    assert _CELL_PM_PLANNING_HINT in hint


# --- PM/code guard: main_pm coverage (closes the delegate-to-main-pm hole) ---


@pytest.mark.parametrize(
    ("assigned_to", "task_type", "allowed"),
    [
        # A PM — cell OR main — may only be delegated planning.
        ("be-pm", "planning", True),
        ("main-pm", "planning", True),
        ("be-pm", "code", False),
        ("main-pm", "code", False),
        ("fe-pm", "documentation", False),
        ("main-pm", "research", False),
    ],
)
def test_validate_assignee_task_type_pm_planning_only(
    assigned_to: str, task_type: str, allowed: bool
) -> None:
    """Both PM roles (cell + main) accept planning only — main_pm was previously
    omitted (only the cell-PM slug set was checked), leaving a delegate-to-main-pm
    as code hole."""
    err = Choreographer._validate_assignee_task_type(assigned_to, task_type)
    if allowed:
        assert err is None, f"{assigned_to}/{task_type} should be allowed, got {err!r}"
    else:
        assert err is not None, f"{assigned_to}/{task_type} should be rejected"
        assert assigned_to in err


def test_remediate_for_main_pm_routes_code_to_dev() -> None:
    hint = Choreographer._assignee_task_type_remediate("main-pm")
    assert "Main PM" in hint
    assert "planning" in hint.lower()
    # Not the generic Cell-PM planning hint.
    assert _CELL_PM_PLANNING_HINT not in hint


# --- Behavior 2: static-guard envelope wiring ---------------------------


@pytest.mark.asyncio
async def test_static_guards_allow_ux_design_subtask() -> None:
    """ux-pm delegating a design subtask to its designer passes the guard."""
    pm_id = uuid4()
    parent_id = uuid4()
    c = Choreographer(_make_deps())
    env = await c._delegate_static_guards(
        pm_id,
        parent_id,
        _parent(parent_id, "ux_ui"),
        _inputs("ux-dev-1", "ux_ui", "design"),
    )
    assert env is None, f"UX design subtask must pass static guards, got {env}"


@pytest.mark.asyncio
async def test_static_guards_reject_backend_design_with_dev_remediate() -> None:
    """be-pm handing a design subtask to a backend dev is rejected, and the
    remediate is the developer-class hint (not the planning/Cell-PM hint)."""
    pm_id = uuid4()
    parent_id = uuid4()
    c = Choreographer(_make_deps())
    env = await c._delegate_static_guards(
        pm_id,
        parent_id,
        _parent(parent_id, "backend"),
        _inputs("be-dev-1", "backend", "design"),
    )
    assert env is not None, "design subtask for a backend dev must be rejected"
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    remediate = body["remediate"] or ""
    assert "UX" in remediate
    # The developer-class hint, NOT the generic Cell-PM planning hint.
    assert _CELL_PM_PLANNING_HINT not in remediate


@pytest.mark.asyncio
async def test_static_guards_reject_documentation_for_ux_dev() -> None:
    """documentation stays non-delegatable even for a UX dev (Task #163):
    the lifecycle auto-creates the doc phase after the code subtask."""
    pm_id = uuid4()
    parent_id = uuid4()
    c = Choreographer(_make_deps())
    env = await c._delegate_static_guards(
        pm_id,
        parent_id,
        _parent(parent_id, "ux_ui"),
        _inputs("ux-dev-1", "ux_ui", "documentation"),
    )
    assert env is not None, "documentation subtask must be rejected"
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "not PM-" in (body["message"] or "") or "documenter" in (
        body["remediate"] or ""
    )
