"""e2e smoke test for auditor triggers.

Exercises both the scheduled audit trigger path and the reactive alert producer
path end-to-end, verifying they result in auditor-targeted work.

- Scheduled path: an in-process orchestrator instance polls the real e2e API,
  sees recent delivery activity, and calls ``spawn_agent(agent_id="auditor")``
  with the scheduled sweep prompt.
- Reactive path: a real QA-fail POST creates an ``ALERT`` notification addressed
  to the auditor in the DB; the orchestrator's audit dispatcher fetches that
  alert and calls ``spawn_agent(agent_id="auditor")`` with the quality-alert
  prompt.

No real auditor container is spawned — ``spawn_agent`` is stubbed so the test
asserts on the dispatch decision, not the LLM runtime.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from roboco.config import settings
from roboco.models.base import TaskStatus
from roboco.runtime.orchestrator import AgentOrchestrator
from tests.e2e_smoke.arcs import seed_company, seed_project, seed_task

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


def _agent_headers(agent_id: Any, role: str) -> dict[str, str]:
    return {"X-Agent-ID": str(agent_id), "X-Agent-Role": role}


def _seed_auditor_agent(stack: E2EStack) -> UUID:
    """Seed the canonical auditor agent at its fixed foundation UUID.

    ``_resolve_agent_slug`` maps this UUID to ``"auditor"`` so the orchestrator
    recognises auditor-targeted notifications and spawns the right role.
    """
    from roboco.db.tables import AgentTable
    from roboco.foundation import identity as _foundation
    from roboco.models import AgentRole, AgentStatus

    async def _run(session: AsyncSession) -> UUID:
        auditor_id = _foundation.AGENTS["auditor"].uuid
        session.add(
            AgentTable(
                id=auditor_id,
                name="auditor",
                slug="auditor",
                role=AgentRole.AUDITOR,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="auditor",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await session.flush()
        return auditor_id

    auditor_id: UUID = stack.run_db(_run)
    return auditor_id


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


def _fresh_orchestrator(stack: E2EStack, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return a bare orchestrator whose internal API points at the e2e app."""
    monkeypatch.setattr(settings, "internal_api_url", f"{stack.base_url}/api")
    orch: Any = AgentOrchestrator.__new__(AgentOrchestrator)
    # __new__ bypasses __init__, so the instance attributes that
    # _is_agent_active and _dispatch_audit_work read must be initialized here.
    orch._instances = {}
    orch._last_audit_spawn_at = None
    orch.spawn_agent = AsyncMock()
    return orch


@pytest.mark.asyncio
async def test_scheduled_audit_trigger_spawns_auditor(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheduled sweep spawns the auditor when delivery activity is recent."""
    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)
    auditor_id = _seed_auditor_agent(stack)

    # Any active delivery task counts as "recent activity" for the sweep gate.
    task_id = seed_task(
        stack,
        title="Scheduled audit smoke task",
        description="An in-progress task so the scheduled sweep has work to audit.",
        acceptance_criteria=["the scheduled path sees recent activity"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
        claimed_by=company.dev_id,
        active_claimant_id=company.dev_id,
        status=TaskStatus.IN_PROGRESS,
        branch_name="feature/backend/e2e-scheduled-audit",
    )

    orch = _fresh_orchestrator(stack, monkeypatch)
    monkeypatch.setattr(settings, "audit_interval_seconds", 60)

    async with httpx.AsyncClient(timeout=5.0) as client:
        await orch._dispatch_audit_work(client)

    orch.spawn_agent.assert_awaited_once()
    call = orch.spawn_agent.await_args
    assert call is not None
    assert call.kwargs["agent_id"] == "auditor"
    assert call.kwargs["spawned_by"] == "_dispatch_audit_work"
    prompt = call.kwargs["initial_prompt"]
    assert "SCHEDULED AUDIT SWEEP" in prompt

    # No reactive alert should have been created for this path.
    assert _notifications_for_task(stack, task_id, "ALERT") == []
    assert auditor_id is not None  # auditor was seeded and resolved


@pytest.mark.asyncio
async def test_reactive_alert_producer_spawns_auditor(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA-fail emits an auditor-targeted ALERT; the dispatcher spawns the auditor."""
    stack = e2e_stack
    company = seed_company(stack)
    project_id, _project_slug = seed_project(stack, company)
    auditor_id = _seed_auditor_agent(stack)

    task_id = seed_task(
        stack,
        title="Reactive alert smoke task",
        description="A task awaiting QA so fail_qa can emit an auditor alert.",
        acceptance_criteria=["the reactive path emits an auditor alert"],
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
        claimed_by=company.dev_id,
        active_claimant_id=company.dev_id,
        status=TaskStatus.AWAITING_QA,
        branch_name="feature/backend/e2e-reactive-alert",
    )

    resp = httpx.post(
        f"{stack.base_url}/api/tasks/{task_id}/fail-qa",
        json={"notes": "missing edge-case coverage"},
        headers=_agent_headers(company.qa_id, "qa"),
        timeout=30,
    )
    assert resp.status_code == HTTPStatus.OK, (
        f"fail-qa: {resp.status_code} {resp.text[:1500]}"
    )

    alerts = _notifications_for_task(stack, task_id, "ALERT")
    assert len(alerts) == 1, alerts
    alert = alerts[0]
    assert "rework alert" in alert["subject"].lower(), alert
    assert auditor_id in alert["to_agents"], alert

    orch = _fresh_orchestrator(stack, monkeypatch)
    monkeypatch.setattr(settings, "audit_interval_seconds", 60)

    async with httpx.AsyncClient(timeout=5.0) as client:
        await orch._dispatch_audit_work(client)

    orch.spawn_agent.assert_awaited_once()
    call = orch.spawn_agent.await_args
    assert call is not None
    assert call.kwargs["agent_id"] == "auditor"
    assert call.kwargs["spawned_by"] == "_dispatch_audit_work"
    prompt = call.kwargs["initial_prompt"]
    assert "QUALITY ALERT" in prompt
    assert "missing edge-case coverage" in prompt
