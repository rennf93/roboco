"""Coordination/board tasks (product_id set, project_id NULL) are not git-gated.

Regression: after tasks.project_id became nullable, a board/fan-out task that
carries a product but no repo of its own was refused at spawn
("task has no project") and auto-blocked as stuck ("Task missing branch_name"),
because the orchestrator's readiness and stuck checks assumed every task does
git work. `_is_coordination_task` marks these tasks so the project/branch/
git-token gates are skipped for them while still gating genuinely unroutable
tasks (neither project nor product) and ordinary code tasks.
"""

from __future__ import annotations

from typing import Any

from roboco.runtime.orchestrator import AgentOrchestrator, _is_coordination_task


def _bare_orchestrator() -> AgentOrchestrator:
    """An instance without the heavy __init__ — these methods need no deps."""
    return object.__new__(AgentOrchestrator)


# ---------------------------------------------------------------------------
# _is_coordination_task
# ---------------------------------------------------------------------------


def test_coordination_task_when_product_without_project() -> None:
    assert _is_coordination_task({"project_id": None, "product_id": "p1"}) is True


def test_not_coordination_when_project_set() -> None:
    # A cell subtask carries both a resolved project and the product lineage.
    assert _is_coordination_task({"project_id": "r1", "product_id": "p1"}) is False


def test_not_coordination_when_only_project() -> None:
    assert _is_coordination_task({"project_id": "r1", "product_id": None}) is False


def test_not_coordination_when_neither() -> None:
    # Genuinely unroutable — stays gated so it is auto-blocked, not spawned.
    assert _is_coordination_task({"project_id": None, "product_id": None}) is False


# ---------------------------------------------------------------------------
# _readiness_check_task
# ---------------------------------------------------------------------------


def _task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "acceptance_criteria": ["does the thing"],
        "status": "pending",
        "project_id": None,
        "product_id": None,
        "project_slug": None,
        "branch_name": None,
    }
    base.update(over)
    return base


def test_readiness_allows_coordination_task_without_project() -> None:
    orch = _bare_orchestrator()
    reason = orch._readiness_check_task(
        "product-owner", _task(product_id="p1", status="pending")
    )
    assert reason is None


def test_readiness_blocks_task_with_neither_project_nor_product() -> None:
    orch = _bare_orchestrator()
    reason = orch._readiness_check_task("product-owner", _task())
    assert reason == "task has no project"


def test_readiness_skips_branch_gate_for_coordination_task() -> None:
    # in_progress + no branch would trip the branch gate for a code task, but a
    # coordination task does no git so it must not be branch-gated.
    orch = _bare_orchestrator()
    reason = orch._readiness_check_task(
        "main-pm", _task(product_id="p1", status="in_progress", branch_name=None)
    )
    assert reason is None


def test_readiness_still_gates_code_task_without_project() -> None:
    orch = _bare_orchestrator()
    reason = orch._readiness_check_task("be-dev-1", _task(status="pending"))
    assert reason == "task has no project"


def test_readiness_still_branch_gates_code_task_in_progress() -> None:
    orch = _bare_orchestrator()
    reason = orch._readiness_check_task(
        "be-dev-1",
        _task(
            project_id="r1",
            project_slug="roboco",
            status="in_progress",
            branch_name=None,
        ),
    )
    assert reason is not None
    assert "branch_name" in reason


# ---------------------------------------------------------------------------
# _check_stuck_conditions
# ---------------------------------------------------------------------------

_GOOD_DESC = "A real coordination task description"


def test_stuck_check_ignores_missing_branch_for_coordination_task() -> None:
    orch = _bare_orchestrator()
    issues = orch._check_stuck_conditions(
        {
            "project_id": None,
            "product_id": "p1",
            "branch_name": None,
            "description": _GOOD_DESC,
        }
    )
    assert "Task missing branch_name" not in issues
    assert issues == []


def test_stuck_check_flags_missing_branch_for_code_task() -> None:
    orch = _bare_orchestrator()
    issues = orch._check_stuck_conditions(
        {
            "project_id": "r1",
            "product_id": None,
            "branch_name": None,
            "description": _GOOD_DESC,
        }
    )
    assert "Task missing branch_name" in issues
