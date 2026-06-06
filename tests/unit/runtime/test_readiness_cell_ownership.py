"""A cell implementation task may only be worked by its own cell.

Board (product-owner / head-marketing) and Main-PM agents must never be
spawned onto a backend / frontend / ux_ui task. Live failure: a board role
took ownership of cell work it cannot drive (dev -> QA -> docs), so the task
deadlocked paused under an owner that could not progress it. The auditor is
exempt — a silent observer with read access to every task.
"""

from __future__ import annotations

from roboco.runtime.orchestrator import AgentOrchestrator

_check = AgentOrchestrator._readiness_check_cell_ownership


def test_cell_task_refuses_product_owner() -> None:
    reason = _check("product-owner", {"team": "backend"})
    assert reason is not None
    assert "backend" in reason


def test_cell_task_refuses_head_marketing() -> None:
    assert _check("head-marketing", {"team": "frontend"}) is not None


def test_cell_task_refuses_main_pm() -> None:
    assert _check("main-pm", {"team": "ux_ui"}) is not None


def test_cell_task_allows_own_cell_dev() -> None:
    assert _check("be-dev-1", {"team": "backend"}) is None


def test_cell_task_allows_cell_pm() -> None:
    assert _check("be-pm", {"team": "backend"}) is None


def test_cell_task_allows_qa_and_doc() -> None:
    assert _check("fe-qa", {"team": "frontend"}) is None
    assert _check("ux-doc", {"team": "ux_ui"}) is None


def test_cell_task_exempts_auditor() -> None:
    # Silent observer with read access to every task — never the owner, but may
    # be spawned with any task in context.
    assert _check("auditor", {"team": "backend"}) is None


def test_coordination_task_not_cell_gated() -> None:
    # A main_pm / board / coordination task is not a cell implementation task,
    # so the cell-ownership gate does not apply.
    assert _check("product-owner", {"team": "main_pm"}) is None
    assert _check("main-pm", {"team": "board"}) is None


def test_missing_team_passes() -> None:
    assert _check("product-owner", {}) is None
