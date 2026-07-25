"""Scenario: the Coroner (Board Program) loop end to end.

Arm coroner via the settings-store key, force a real task's 3rd bounce into
needs_revision through the actual TaskService chokepoint, and verify the
event hook opens exactly one held autopsy task assigned to the auditor —
then drive ``propose_postmortem`` for real (via ContentActions) with a
playbook-kind process change and verify the autopsy completes AND a DRAFT
playbook exists, riding the normal pending-playbook curation queue.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.foundation import identity as _foundation
from tests.e2e_smoke.arcs import seed_company, seed_project

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack

ONE = 1
ZERO = 0
BOUNCE_THRESHOLD = 3


def _seed_system_and_auditor(stack: E2EStack) -> None:
    """Seed ``system`` + ``auditor`` at their FIXED foundation UUIDs —
    CoronerEngine._originate assigns the autopsy task to
    _foundation.AGENTS["auditor"].uuid, so the seeded row must exist there."""
    from roboco.db.tables import AgentTable
    from roboco.models import AgentRole, AgentStatus, Team

    async def _run(session: AsyncSession) -> None:
        for agent_uuid, slug, role, team in (
            (_foundation.AGENTS["system"].uuid, "system", AgentRole.SYSTEM, None),
            (
                _foundation.AGENTS["auditor"].uuid,
                "auditor",
                AgentRole.AUDITOR,
                Team.BOARD,
            ),
        ):
            if await session.get(AgentTable, agent_uuid) is not None:
                continue
            session.add(
                AgentTable(
                    id=agent_uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt=slug,
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )

    stack.run_db(_run)


def _arm_coroner(stack: E2EStack) -> None:
    from roboco.db.tables import SystemSettingTable

    async def _run(session: AsyncSession) -> None:
        session.add(
            SystemSettingTable(key="board_program.coroner.enabled", value="true")
        )

    stack.run_db(_run)


def _seed_incident_task(stack: E2EStack, project_id: Any, company: Any) -> Any:
    from roboco.models.base import (
        Complexity,
        TaskNature,
        TaskStatus,
        TaskType,
        Team,
    )
    from roboco.services.task import TaskCreateRequest, get_task_service

    async def _run(session: AsyncSession) -> Any:
        task = await get_task_service(session).create(
            TaskCreateRequest(
                title="Chronic task",
                description="Keeps bouncing on QA review",
                acceptance_criteria=["it works"],
                team=Team.BACKEND,
                created_by=company.ceo_id,
                assigned_to=company.dev_id,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.IN_PROGRESS,
            )
        )
        return task.id

    task_id: Any = stack.run_db(_run)
    return task_id


def _force_third_bounce(stack: E2EStack, task_id: Any) -> None:
    """Drive the REAL chokepoint (TaskService._validate_and_set_status ->
    _emit_status_transition_audit) three times, awaiting the scheduled
    fire-and-forget coroner hook inline (same event loop as ``run_db``'s
    own ``asyncio.run`` — a real ``create_task`` left unawaited would be
    abandoned when that loop closes)."""
    import asyncio

    from roboco.models.base import TaskStatus
    from roboco.services.task import TaskService
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> None:
        from roboco.db.tables import TaskTable

        scheduled: list[Any] = []
        real_create_task = asyncio.create_task

        def _capture(coro: Any, *a: Any, **kw: Any) -> Any:
            scheduled.append(coro)
            return real_create_task(_noop())

        async def _noop() -> None:
            return None

        import roboco.services.task as task_module

        original = task_module.asyncio.create_task
        task_module.asyncio.create_task = _capture
        try:
            svc = TaskService(session)
            task = (
                await session.execute(select(TaskTable).where(TaskTable.id == task_id))
            ).scalar_one()
            for _ in range(BOUNCE_THRESHOLD):
                if task.status != TaskStatus.IN_PROGRESS:
                    task.status = TaskStatus.IN_PROGRESS
                svc._validate_and_set_status(task, TaskStatus.NEEDS_REVISION, "qa")
        finally:
            task_module.asyncio.create_task = original

        for coro in scheduled:
            await coro

    stack.run_db(_run)


def _find_coroner_task(stack: E2EStack) -> dict[str, Any]:
    from roboco.db.tables import TaskTable
    from roboco.services.task import CORONER_SOURCE
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any]:
        rows = (
            (
                await session.execute(
                    select(TaskTable).where(TaskTable.source == CORONER_SOURCE)
                )
            )
            .scalars()
            .all()
        )
        return {
            "count": len(rows),
            "rows": [
                {
                    "id": r.id,
                    "status": str(r.status),
                    "assigned_to": r.assigned_to,
                    "confirmed_by_human": r.confirmed_by_human,
                }
                for r in rows
            ],
        }

    state: dict[str, Any] = stack.run_db(_run)
    return state


def _propose_postmortem_for_real(stack: E2EStack, coroner_task_id: Any) -> Any:
    """Mirrors ``roboco.api.deps.get_content_actions``'s wiring exactly, minus
    the orchestrator handle (unused by propose_postmortem)."""

    async def _run(session: AsyncSession) -> Any:
        from roboco.services.a2a import A2AService
        from roboco.services.gateway.content_actions import (
            ContentActions,
            ContentActionsDeps,
        )
        from roboco.services.git import GitService
        from roboco.services.journal import JournalService
        from roboco.services.notification import NotificationService
        from roboco.services.notification_delivery import (
            NotificationDeliveryService,
        )
        from roboco.services.task import TaskService
        from roboco.services.workspace import WorkspaceService

        deps = ContentActionsDeps(
            task=TaskService(session),
            git=GitService(session),
            a2a=A2AService(session),
            journal=JournalService(session),
            workspace=WorkspaceService(session),
            notifications=NotificationService(),
            notification_delivery=NotificationDeliveryService(session),
        )
        actions = ContentActions(deps)
        env = await actions.propose_postmortem(
            agent_id=_foundation.AGENTS["auditor"].uuid,
            incident_summary="the task bounced 3 times over a stale QA signal",
            root_cause="the gate never re-checked the flaky dependency version",
            failed_stage="awaiting_qa",
            process_change={
                "kind": "playbook",
                "description": "pin the flaky dependency before QA review",
            },
            playbook={
                "title": f"Pin flaky dep before QA {coroner_task_id}",
                "body": "Before claim_review, pin the flaky dep's exact version.",
            },
        )
        return env

    return stack.run_db(_run)


def _find_playbook_draft(
    stack: E2EStack, title_substring: str
) -> dict[str, Any] | None:
    from roboco.db.tables import PlaybookTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> dict[str, Any] | None:
        rows = (
            (
                await session.execute(
                    select(PlaybookTable).where(
                        PlaybookTable.title.like(f"%{title_substring}%")
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None
        return {"status": rows[0].status, "id": str(rows[0].id)}

    result: dict[str, Any] | None = stack.run_db(_run)
    return result


def test_coroner_loop_bounce_opens_autopsy_and_completes_with_playbook(
    e2e_stack: E2EStack,
) -> None:
    stack = e2e_stack
    company = seed_company(stack)
    _seed_system_and_auditor(stack)
    project_id, _project_slug = seed_project(stack, company)
    _arm_coroner(stack)

    incident_id = _seed_incident_task(stack, project_id, company)
    _force_third_bounce(stack, incident_id)

    state = _find_coroner_task(stack)
    assert state["count"] == ONE, state
    row = state["rows"][0]
    assert row["status"] == "pending"
    assert row["assigned_to"] == _foundation.AGENTS["auditor"].uuid
    assert row["confirmed_by_human"] is False

    # The dispatcher's own dev-work skip recognizes this exact task shape.
    from roboco.runtime.orchestrator import _is_non_dev_dispatch_source

    assert _is_non_dev_dispatch_source({"source": "board_coroner"}) is True

    env = _propose_postmortem_for_real(stack, row["id"])
    assert env.status == "postmortem_proposed"

    state_after = _find_coroner_task(stack)
    assert state_after["rows"][0]["status"] == "completed"

    draft = _find_playbook_draft(stack, f"Pin flaky dep before QA {row['id']}")
    assert draft is not None
    assert draft["status"] == "draft"
