"""Per-project monthly-budget claim guard (ROBOCO_TASK_BUDGETS_ENABLED).

Two layers: the pure predicate ``project_budget_exceeded_guard`` (over/under/
null cap) and the Choreographer's ``_project_budget_claim_guard`` wiring
(flag-off inert, no-cap inert, spend query only reached when both are set).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.claim_guards import project_budget_exceeded_guard

# ---------------------------------------------------------------------------
# Pure predicate: project_budget_exceeded_guard
# ---------------------------------------------------------------------------


def test_predicate_null_cap_is_inert() -> None:
    task = MagicMock(id=uuid4())
    assert project_budget_exceeded_guard(task, None, 999.0) is None


def test_predicate_under_cap_passes() -> None:
    task = MagicMock(id=uuid4())
    assert project_budget_exceeded_guard(task, 10.0, 5.0) is None


def test_predicate_over_cap_refuses() -> None:
    task = MagicMock(id=uuid4())
    env = project_budget_exceeded_guard(task, 10.0, 15.0)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "10.00" in body["message"]
    assert "15.00" in body["message"]
    assert "project settings" in body["remediate"]


def test_predicate_at_cap_refuses() -> None:
    """At-or-over refuses — the cap itself is not headroom."""
    task = MagicMock(id=uuid4())
    assert project_budget_exceeded_guard(task, 10.0, 10.0) is not None


# ---------------------------------------------------------------------------
# Choreographer wiring: _project_budget_claim_guard
# ---------------------------------------------------------------------------


def _make_choreographer(*, project_month_spend_usd: float = 0.0) -> Choreographer:
    task_svc = AsyncMock()
    task_svc.project_month_spend_usd.return_value = project_month_spend_usd
    base = {
        "task": task_svc,
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    return Choreographer(ChoreographerDeps(**base))


def _task_with_project(monthly_budget_usd: float | None) -> MagicMock:
    project = MagicMock(id=uuid4(), monthly_budget_usd=monthly_budget_usd)
    return MagicMock(id=uuid4(), project=project)


@pytest.mark.asyncio
async def test_flag_off_is_inert_even_over_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_budgets_enabled", False)
    c = _make_choreographer(project_month_spend_usd=999.0)
    task = _task_with_project(monthly_budget_usd=10.0)
    assert await c._project_budget_claim_guard(task) is None
    c.task.project_month_spend_usd.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_project_cap_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_budgets_enabled", True)
    c = _make_choreographer(project_month_spend_usd=999.0)
    task = _task_with_project(monthly_budget_usd=None)
    assert await c._project_budget_claim_guard(task) is None
    c.task.project_month_spend_usd.assert_not_awaited()


@pytest.mark.asyncio
async def test_under_cap_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "task_budgets_enabled", True)
    c = _make_choreographer(project_month_spend_usd=4.0)
    task = _task_with_project(monthly_budget_usd=10.0)
    assert await c._project_budget_claim_guard(task) is None


@pytest.mark.asyncio
async def test_over_cap_refuses_naming_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "task_budgets_enabled", True)
    c = _make_choreographer(project_month_spend_usd=12.0)
    task = _task_with_project(monthly_budget_usd=10.0)
    env = await c._project_budget_claim_guard(task)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "Monthly Budget" in body["remediate"]
