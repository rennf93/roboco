"""Scenario 1: a leaf dev task walks claim → work → PR → QA → docs → PM queue.

Every hop goes through the REAL MCP tool functions → real HTTP → real
gateway gates → real services → real git against the local origin, with a
fake GitHub REST layer whose merges are real git merges. No LLM: the arcs
in ``tests/e2e_smoke/arcs.py`` ARE the agent script, and every rejection
envelope prints verbatim so a seam regression names itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from tests.e2e_smoke.arcs import (
    dev_arc,
    doc_arc,
    qa_arc,
    seed_company,
    seed_project,
    seed_task,
    task_state,
)

if TYPE_CHECKING:
    from tests.e2e_smoke.harness import E2EStack


def test_leaf_dev_task_reaches_pm_review(e2e_stack: E2EStack) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    project_id, project_slug = seed_project(stack, company)
    task_id = seed_task(
        stack,
        title="Add the greeting module",
        description=(
            "Create greeting.txt with a friendly greeting so the smoke "
            "harness has a real file change to commit, push, and merge."
        ),
        acceptance_criteria=[
            "greeting.txt exists at the repo root",
            "its content greets the reader",
        ],
        project_id=project_id,
        created_by=company.cell_pm_id,
        # Pool→agent routing is the orchestrator dispatcher's job (not under
        # test); a dev container is always spawned with its task already
        # routed, which give_me_work serves via the pre-assigned lane.
        assigned_to=company.dev_id,
    )

    dev_arc(stack, company, project_slug, task_id)
    qa_arc(stack, company, task_id)
    doc_arc(stack, company, task_id, filename="greeting.txt")

    final = task_state(stack, task_id)
    assert final["status"] == "awaiting_pm_review", final
    assert final["docs_complete"] is True, final


def test_leaf_dev_task_reaches_pm_review_with_fable_mode_on(
    e2e_stack: E2EStack,
) -> None:
    """Non-interference regression check for fable_mode_enabled=True.

    This harness cannot exercise compose_prompt / _generate_agent_settings —
    those live entirely in the orchestrator's spawn-prep path, which the
    harness bypasses by design (see tests/integration/test_fable_mode_spawn_prep.py
    for that half). What it CAN prove is that arming the flag doesn't perturb
    the gateway/lifecycle arc itself: the same scenario must reach the same
    outcome with the flag on as with it off.
    """
    with patch("roboco.config.settings.fable_mode_enabled", True):
        stack = e2e_stack
        company = seed_company(stack)
        project_id, project_slug = seed_project(stack, company)
        task_id = seed_task(
            stack,
            title="Add the greeting module (fable-mode non-interference check)",
            description=(
                "Create greeting.txt with a friendly greeting so the smoke "
                "harness has a real file change to commit, push, and merge."
            ),
            acceptance_criteria=[
                "greeting.txt exists at the repo root",
                "its content greets the reader",
            ],
            project_id=project_id,
            created_by=company.cell_pm_id,
            assigned_to=company.dev_id,
        )

        dev_arc(stack, company, project_slug, task_id)
        qa_arc(stack, company, task_id)
        doc_arc(stack, company, task_id, filename="greeting.txt")

        final = task_state(stack, task_id)
    assert final["status"] == "awaiting_pm_review", final
    assert final["docs_complete"] is True, final
