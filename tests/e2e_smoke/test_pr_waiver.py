"""Zero-diff PR-waiver: a report-only cell task completes with no PR.

Reproduces the live incident (Sentinel root 6e2af177's audit cell tasks,
done and re-verified, stuck purely on PR creation): a project-bound cell
task with a real branch whose only child resolved with zero commits (report-
only work — nothing to change) must still reach `completed` end to end,
with PR creation waived instead of 422ing GitHub's "No commits between".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.e2e_smoke.arcs import (
    seed_company,
    seed_hierarchy,
    seed_project,
    task_state,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_ok

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


def _set_task_status_directly(stack: E2EStack, task_id: Any, status: Any) -> None:
    """Directly set a task's status — a data field, not a lifecycle
    transition (mirrors ``arcs.set_branch_name``'s style: standing in for a
    resolution a PM would otherwise reach via an admin/out-of-band action
    that has no scripted flow verb, e.g. a "report-only, nothing to change"
    leaf reaching a terminal status with zero commits). Kept local to this
    test file rather than the shared ``arcs.py`` module — ``arcs.wire_dependency``
    documents "a real dependency edge ... not a direct status write" as the
    shared-module contract, so a general-purpose lifecycle bypass has no
    business living there."""
    from roboco.db.tables import TaskTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> None:
        row = (
            await session.execute(select(TaskTable).where(TaskTable.id == task_id))
        ).scalar_one()
        row.status = status

    stack.run_db(_run)


def test_zero_commit_cell_task_completes_without_pr(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)
    h = seed_hierarchy(stack, company, project_id)

    # The child resolves as report-only (an audit found nothing to change) —
    # terminal with zero commits, so the cell branch never receives a merge.
    # This is the "assembled from report-only children" shape the live
    # incident hit; the leaf-level zero-diff resolution mechanism is out of
    # scope here (the existing open_pr dev steer), so the terminal state is
    # set directly, standing in for whatever out-of-band action produces it.
    from roboco.models.base import TaskStatus

    _set_task_status_directly(stack, h["child_id"], TaskStatus.COMPLETED)

    pm = ScriptedAgent(stack, company.cell_pm_id, "be-pm", "cell_pm")

    # --- submit_up on the zero-commit cell branch: no PR, no error ---------
    env = expect_ok(
        pm.flow(
            "submit_up",
            task_id=str(h["cell_id"]),
            notes=(
                "The only child resolved report-only with zero commits — a "
                "code audit found nothing to change. The cell branch carries "
                "no diff against the root branch, so there is nothing for a "
                "reviewer to check; bubbling straight to PM review."
            ),
        ),
        "pm submit_up on zero-commit cell branch",
    )
    assert env["status"] == "awaiting_pm_review", env
    cell = task_state(stack, h["cell_id"])
    assert cell["status"] == "awaiting_pm_review", cell
    assert cell["pr_number"] is None, (
        f"PR should have been waived on a zero-commit branch, got {cell}"
    )
    # `stack.github` is session-scoped (shared across every test in the run),
    # so assert on the absence of a PR for THIS cell branch specifically
    # rather than the whole fake-GitHub PR table being empty.
    assert not any(
        pr["head"]["ref"] == h["cell_branch"] for pr in stack.github.prs.values()
    ), (
        f"no PR should have been opened for the zero-commit cell branch "
        f"{h['cell_branch']!r}: {stack.github.prs}"
    )

    # --- complete: no PR to merge, reaches `completed` with no human -------
    # status surgery — the same `complete` verb every other assembled task
    # uses.
    expect_ok(
        pm.flow(
            "complete",
            task_id=str(h["cell_id"]),
            notes=(
                "PR-waived report-only cell task: nothing to merge, closing "
                "it out as completed."
            ),
        ),
        "pm complete PR-waived cell task",
    )
    final = task_state(stack, h["cell_id"])
    assert final["status"] == "completed", final
    assert final["pr_number"] is None, final
