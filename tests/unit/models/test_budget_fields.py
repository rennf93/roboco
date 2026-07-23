"""Task.budget_usd / Project.monthly_budget_usd validation (#654).

The task-budgets feature's own design says "0 rejected — a zero budget
silently blocks everything" (every claim is refused from the first tick),
so every schema that can set these fields must reject 0 and negative values
at the pydantic boundary — a 422, never a stored self-DoS. Null ("no cap")
stays valid throughout. Mirrors test_project_sandbox_services.py's style
(domain-model `pytest.raises(ValidationError)` coverage).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.models.base import Team
from roboco.models.project import Project, ProjectCreate, ProjectUpdate
from roboco.models.task import Task, TaskUpdate


def _task(budget_usd: float | None = None) -> Task:
    return Task(
        title="Add user lookup endpoint",
        description="Add GET /v1/users/{id} returning user JSON.",
        acceptance_criteria=["returns 404 for unknown user"],
        created_by=uuid4(),
        team=Team.BACKEND,
        budget_usd=budget_usd,
    )


def _project(monthly_budget_usd: float | None = None) -> Project:
    return Project(
        name="P",
        slug="p",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=uuid4(),
        monthly_budget_usd=monthly_budget_usd,
    )


# ---------------------------------------------------------------------------
# Task.budget_usd
# ---------------------------------------------------------------------------


def test_task_defaults_budget_usd_to_none() -> None:
    assert _task().budget_usd is None


def test_task_accepts_positive_budget_usd() -> None:
    budget = 12.5
    assert _task(budget_usd=budget).budget_usd == budget


def test_task_rejects_zero_budget_usd() -> None:
    with pytest.raises(ValidationError, match="budget_usd"):
        _task(budget_usd=0)


def test_task_rejects_negative_budget_usd() -> None:
    with pytest.raises(ValidationError, match="budget_usd"):
        _task(budget_usd=-5)


# ---------------------------------------------------------------------------
# roboco.models.task.TaskUpdate.budget_usd (domain update model)
# ---------------------------------------------------------------------------


def test_task_update_accepts_null_budget_usd() -> None:
    assert TaskUpdate(budget_usd=None).budget_usd is None


def test_task_update_accepts_positive_budget_usd() -> None:
    budget = 3.0
    assert TaskUpdate(budget_usd=budget).budget_usd == budget


def test_task_update_rejects_zero_budget_usd() -> None:
    with pytest.raises(ValidationError, match="budget_usd"):
        TaskUpdate(budget_usd=0)


def test_task_update_rejects_negative_budget_usd() -> None:
    with pytest.raises(ValidationError, match="budget_usd"):
        TaskUpdate(budget_usd=-1)


# ---------------------------------------------------------------------------
# Project.monthly_budget_usd
# ---------------------------------------------------------------------------


def test_project_defaults_monthly_budget_usd_to_none() -> None:
    assert _project().monthly_budget_usd is None


def test_project_accepts_positive_monthly_budget_usd() -> None:
    cap = 100.0
    assert _project(monthly_budget_usd=cap).monthly_budget_usd == cap


def test_project_rejects_zero_monthly_budget_usd() -> None:
    with pytest.raises(ValidationError, match="monthly_budget_usd"):
        _project(monthly_budget_usd=0)


def test_project_rejects_negative_monthly_budget_usd() -> None:
    with pytest.raises(ValidationError, match="monthly_budget_usd"):
        _project(monthly_budget_usd=-5)


# ---------------------------------------------------------------------------
# ProjectCreate.monthly_budget_usd
# ---------------------------------------------------------------------------


def test_project_create_accepts_null_monthly_budget_usd() -> None:
    assert (
        ProjectCreate(
            name="P",
            slug="p",
            git_url="https://example.com/r.git",
            assigned_cell=Team.BACKEND,
        ).monthly_budget_usd
        is None
    )


def test_project_create_rejects_zero_monthly_budget_usd() -> None:
    with pytest.raises(ValidationError, match="monthly_budget_usd"):
        ProjectCreate(
            name="P",
            slug="p",
            git_url="https://example.com/r.git",
            assigned_cell=Team.BACKEND,
            monthly_budget_usd=0,
        )


# ---------------------------------------------------------------------------
# ProjectUpdate.monthly_budget_usd
# ---------------------------------------------------------------------------


def test_project_update_accepts_null_monthly_budget_usd() -> None:
    assert ProjectUpdate(monthly_budget_usd=None).monthly_budget_usd is None


def test_project_update_accepts_positive_monthly_budget_usd() -> None:
    cap = 50.0
    assert ProjectUpdate(monthly_budget_usd=cap).monthly_budget_usd == cap


def test_project_update_rejects_zero_monthly_budget_usd() -> None:
    with pytest.raises(ValidationError, match="monthly_budget_usd"):
        ProjectUpdate(monthly_budget_usd=0)


def test_project_update_rejects_negative_monthly_budget_usd() -> None:
    with pytest.raises(ValidationError, match="monthly_budget_usd"):
        ProjectUpdate(monthly_budget_usd=-5)
