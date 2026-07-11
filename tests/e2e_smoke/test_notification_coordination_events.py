"""Scenario: coordination-event notification producers land real DB rows.

Drives two of the coordination-event notification producers wired at
``TaskService``'s transition chokepoints (``roboco/services/task.py``) —
soft-block (BLOCKER_ESCALATION to the cell PM) and unblock (TASK_ASSIGNMENT
to the original assignee) — through the real REST task surface mounted at
``/api/tasks`` in this harness (the same surface scenario 3's CEO
approve-and-merge call uses). Real in-process API, real
``NotificationDeliveryService``, real ephemeral Postgres — no mocks. Each
assertion reads the persisted ``NotificationTable`` row back out of the DB
via ``E2EStack.run_db``, exactly as the harness's other DB-truth checks do.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx
from tests.e2e_smoke.arcs import seed_company, seed_project, seed_task

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


def _agent_headers(agent_id: Any, role: str) -> dict[str, str]:
    return {"X-Agent-ID": str(agent_id), "X-Agent-Role": role}


def _notifications_for_task(
    stack: E2EStack, task_id: Any, notification_type: Any
) -> list[dict[str, Any]]:
    from roboco.db.tables import NotificationTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    select(NotificationTable).where(
                        NotificationTable.related_task_id == task_id,
                        NotificationTable.type == notification_type,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "type": str(r.type),
                "related_task_id": r.related_task_id,
                "subject": r.subject,
                "priority": str(r.priority),
                "to_agents": list(r.to_agents),
            }
            for r in rows
        ]

    rows: list[dict[str, Any]] = stack.run_db(_run)
    return rows


def test_soft_block_persists_blocker_escalation_notification(
    e2e_stack: E2EStack,
) -> None:
    """soft-block chokepoint: notify_pm_of_block -> BLOCKER_ESCALATION."""
    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)

    from roboco.models.base import TaskStatus

    task_id = seed_task(
        stack,
        title="Investigate flaky upstream API",
        description=(
            "Dev is mid-work and hits an external dependency outage; "
            "soft-blocking so the cell PM is paged for resolution."
        ),
        acceptance_criteria=["the upstream API responds reliably again"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
        claimed_by=company.dev_id,
        active_claimant_id=company.dev_id,
        status=TaskStatus.IN_PROGRESS,
        branch_name="feature/backend/e2e-soft-block-notify",
    )

    resp = httpx.post(
        f"{stack.base_url}/api/tasks/{task_id}/soft-block",
        json={
            "reason": "The upstream payments API is returning 503s.",
            "blocker_type": "external",
            "what_needed": "Wait for the upstream provider to recover.",
            "resolver_type": "agent",
        },
        headers=_agent_headers(company.dev_id, "developer"),
        timeout=30,
    )
    assert resp.status_code == HTTPStatus.OK, (
        f"soft-block: {resp.status_code} {resp.text[:1500]}"
    )

    from roboco.models import NotificationType

    notifications = _notifications_for_task(
        stack, task_id, NotificationType.BLOCKER_ESCALATION
    )
    assert len(notifications) == 1, notifications
    note = notifications[0]
    assert "blocker_escalation" in note["type"].lower(), note
    assert note["related_task_id"] == task_id, note
    assert note["subject"], "subject must be populated"
    assert note["priority"], "priority must be populated"
    assert company.cell_pm_id in note["to_agents"], note


def test_unblock_persists_task_assignment_notification(e2e_stack: E2EStack) -> None:
    """unblock chokepoint: notify_assignee_of_unblock -> TASK_ASSIGNMENT."""
    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)

    from roboco.models.base import TaskStatus

    task_id = seed_task(
        stack,
        title="Rotate the expired staging credential",
        description=(
            "Dev soft-blocked waiting on a credential rotation; the cell "
            "PM resolves it and unblocks the task so the dev resumes."
        ),
        acceptance_criteria=["the staging credential is valid again"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
        claimed_by=company.dev_id,
        active_claimant_id=company.dev_id,
        status=TaskStatus.IN_PROGRESS,
        branch_name="feature/backend/e2e-unblock-notify",
    )

    block_resp = httpx.post(
        f"{stack.base_url}/api/tasks/{task_id}/soft-block",
        json={
            "reason": "The staging DB credential expired overnight.",
            "blocker_type": "external",
            "what_needed": "A rotated staging credential from the cell PM.",
            "resolver_type": "agent",
        },
        headers=_agent_headers(company.dev_id, "developer"),
        timeout=30,
    )
    assert block_resp.status_code == HTTPStatus.OK, (
        f"soft-block: {block_resp.status_code} {block_resp.text[:1500]}"
    )

    unblock_resp = httpx.post(
        f"{stack.base_url}/api/tasks/{task_id}/unblock",
        headers=_agent_headers(company.cell_pm_id, "cell_pm"),
        timeout=30,
    )
    assert unblock_resp.status_code == HTTPStatus.OK, (
        f"unblock: {unblock_resp.status_code} {unblock_resp.text[:1500]}"
    )

    from roboco.models import NotificationType

    notifications = _notifications_for_task(
        stack, task_id, NotificationType.TASK_ASSIGNMENT
    )
    assert len(notifications) == 1, notifications
    note = notifications[0]
    assert "task_assignment" in note["type"].lower(), note
    assert note["related_task_id"] == task_id, note
    assert note["subject"], "subject must be populated"
    assert note["priority"], "priority must be populated"
    assert company.dev_id in note["to_agents"], note
