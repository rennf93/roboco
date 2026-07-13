"""Scenario: coordination-event notification producers land real DB rows.

Drives three of the coordination-event notification producers wired at
``TaskService``'s transition chokepoints (``roboco/services/task.py``) —
soft-block (BLOCKER_ESCALATION to the cell PM), unblock (ALERT to the
restored owner + CEO, ``send_unblock_notification``), and dependency-revival
(ALERT to the revived owner + CEO, ``send_dependency_revival_notification``)
— through the real REST task surface mounted at ``/api/tasks`` in this
harness (the same surface scenario 3's CEO approve-and-merge call uses), plus
one direct ``TaskService`` chokepoint call for the dependency-revival
producer (mirrors ``arcs.wire_dependency``'s pattern of driving
``TaskService`` directly for setup that has no bespoke REST endpoint). Real
in-process API, real ``NotificationService``/``NotificationDeliveryService``,
real ephemeral Postgres — no mocks. Each assertion reads the persisted
``NotificationTable`` row back out of the DB via ``E2EStack.run_db``, exactly
as the harness's other DB-truth checks do.

The unblock route (``POST /api/tasks/{id}/unblock``) used to ALSO send a
second, duplicate TASK_ASSIGNMENT notification from the route handler
itself (``notify_assignee_of_unblock``) on top of the ALERT
``TaskService.unblock()`` already sends. That duplicate route-layer call has
been removed — unblock fires exactly one notification now, the ALERT below.
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


def _seed_system_agent(stack: E2EStack) -> None:
    """Seed the ``system`` sentinel at its fixed foundation UUID.

    Production seeds it via ``initial_data.py``; the e2e harness's
    ``seed_company`` deliberately does NOT (seeding it globally adds
    notification-creation latency to every test, pushing ``i_documented``
    past its 120 s verb timeout). Only the coordination-event tests that
    exercise ``send_unblock_notification`` / ``send_dependency_revival_notification``
    need it — both resolve ``from_agent="system"`` to a UUID via DB lookup.
    """
    from roboco.db.tables import AgentTable
    from roboco.foundation import identity as _foundation
    from roboco.models import AgentRole, AgentStatus

    async def _run(session: AsyncSession) -> None:
        session.add(
            AgentTable(
                id=_foundation.AGENTS["system"].uuid,
                name="system",
                slug="system",
                role=AgentRole.SYSTEM,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="system",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await session.flush()

    stack.run_db(_run)


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


def test_unblock_persists_alert_notification(e2e_stack: E2EStack) -> None:
    """unblock chokepoint: TaskService.unblock -> send_unblock_notification -> ALERT.

    Exactly one notification fires for unblock (the route-layer
    ``notify_assignee_of_unblock`` TASK_ASSIGNMENT duplicate was removed).
    """
    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)
    _seed_system_agent(stack)

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

    notifications = _notifications_for_task(stack, task_id, NotificationType.ALERT)
    assert len(notifications) == 1, notifications
    note = notifications[0]
    assert "alert" in note["type"].lower(), note
    assert note["related_task_id"] == task_id, note
    assert note["subject"] == f"Task {task_id} unblocked", note
    assert note["priority"], "priority must be populated"
    assert company.dev_id in note["to_agents"], note

    # The deleted route-layer TASK_ASSIGNMENT duplicate must not reappear.
    stale = _notifications_for_task(stack, task_id, NotificationType.TASK_ASSIGNMENT)
    assert stale == [], stale


def test_dependency_revival_persists_alert_notification(e2e_stack: E2EStack) -> None:
    """dependency-revival chokepoint: _unblock_dependents ->
    send_dependency_revival_notification -> ALERT.

    No resolver calls unblock here — a dependent task blocked on another
    task auto-resumes the moment that dependency's completion clears the
    last outstanding dependency, at the same ``_unblock_dependents``
    chokepoint ``TaskService.complete``/``ceo_approve`` call in production.
    Driven directly against ``TaskService`` (mirrors
    ``arcs.wire_dependency``'s pattern) since there is no bespoke REST
    endpoint for "a dependency just completed".
    """
    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)
    _seed_system_agent(stack)

    from roboco.models.base import TaskStatus

    dependency_id = seed_task(
        stack,
        title="Ship the shared auth helper",
        description="Upstream task the dependent below is blocked on.",
        acceptance_criteria=["the shared auth helper is merged"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
        status=TaskStatus.IN_PROGRESS,
    )
    dependent_id = seed_task(
        stack,
        title="Wire the new endpoint to the shared auth helper",
        description=(
            "Blocked on the shared auth helper landing; should auto-resume "
            "the moment that dependency completes, with no resolver acting."
        ),
        acceptance_criteria=["the endpoint uses the shared auth helper"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
        claimed_by=company.dev_id,
        status=TaskStatus.BLOCKED,
        dependency_ids=[dependency_id],
        branch_name="feature/backend/e2e-dependency-revival",
    )

    from roboco.services.task import get_task_service

    async def _complete_dependency(session: AsyncSession) -> None:
        await get_task_service(session)._unblock_dependents(dependency_id)

    stack.run_db(_complete_dependency)

    from roboco.models import NotificationType

    notifications = _notifications_for_task(stack, dependent_id, NotificationType.ALERT)
    assert len(notifications) == 1, notifications
    note = notifications[0]
    assert "alert" in note["type"].lower(), note
    assert note["related_task_id"] == dependent_id, note
    assert note["subject"] == f"Task {dependent_id} revived by dependency completion", (
        note
    )
    assert note["priority"], "priority must be populated"
    assert company.dev_id in note["to_agents"], note
