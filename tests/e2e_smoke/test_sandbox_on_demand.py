"""E2E smoke: on-demand sandbox provisioning wiring (2026-07-08 spec, Phase 3).

Two seams:

1. The spawn manifest carries ``request_sandbox`` for developer/qa roles only
   (``role_config.py`` -> ``spawn_manifest.build_for_role``) — the class of bug
   that silently strands an agent with no way to ask for a sandbox, or
   silently over-grants the verb to a role never scoped to it.
2. The verb's HTTP wiring (schema -> route -> ``ContentActions`` ->
   envelope), driven end to end by a scripted dev agent against the REAL
   API. The e2e harness wires no orchestrator (``_ServiceHolder.orchestrator``
   stays unset — no docker here), so the happy-path provision itself is
   covered by the mocked-orchestrator unit suite
   (``tests/unit/gateway/test_request_sandbox_verb.py``); this test proves
   the guard chain up to and including the clean, retryable "orchestrator
   unavailable" envelope every caller sees before docker is ever touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from roboco.config import settings
from roboco.models.base import TaskStatus
from tests.e2e_smoke.arcs import seed_company, seed_project, seed_task
from tests.e2e_smoke.harness import ScriptedAgent, expect_error

if TYPE_CHECKING:
    import pytest
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.e2e_smoke.harness import E2EStack


def _set_sandbox_services(
    stack: E2EStack, project_id: object, services: list[str]
) -> None:
    """System-side project opt-in write (mirrors ``arcs.set_branch_name``)."""
    from roboco.db.tables import ProjectTable
    from sqlalchemy import select

    async def _run(session: AsyncSession) -> None:
        row = (
            await session.execute(
                select(ProjectTable).where(ProjectTable.id == project_id)
            )
        ).scalar_one()
        row.sandbox_services = services

    stack.run_db(_run)


def test_manifest_grants_request_sandbox_to_dev_and_qa_only() -> None:
    """role_config -> spawn_manifest wiring: the verb reaches dev/qa manifests
    and no other role — the exact scope e418a4ca added it under."""
    from roboco.runtime.spawn_manifest import SpawnInputs, build_for_role

    def do_tools(role: str) -> list[str]:
        manifest = build_for_role(
            SpawnInputs(
                agent_id=uuid4(),
                role=role,
                team="backend",
                workspace_path=Path("/tmp/x"),
                agent_model="sonnet",
            )
        )
        return manifest.do_tools

    assert "request_sandbox" in do_tools("developer")
    assert "request_sandbox" in do_tools("qa")
    assert "request_sandbox" not in do_tools("documenter")


def test_request_sandbox_guard_chain_over_real_api(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full HTTP round-trip proving the guard order the spec's §1 promises:
    flag off refuses before any DB lookup; a requested service outside the
    project's opted set names the allowed set; a request within the opted
    set clears every guard and reaches the (here, absent) orchestrator,
    returning the clean retryable envelope rather than ever refusing a spawn
    or crashing."""
    stack = e2e_stack
    company = seed_company(stack)
    dev = ScriptedAgent(stack, company.dev_id, "be-dev-1", "developer")

    # --- flag off: refused before any task/project lookup -------------------
    monkeypatch.setattr(settings, "sandbox_db_enabled", False)
    env = expect_error(dev.do("request_sandbox"), "invalid_state", "flag off")
    assert "ROBOCO_SANDBOX_DB_ENABLED" in (env.get("remediate") or "")

    # --- flag on, project opted into a subset, request outside it -----------
    monkeypatch.setattr(settings, "sandbox_db_enabled", True)
    project_id, _project_slug = seed_project(stack, company)
    _set_sandbox_services(stack, project_id, ["redis"])
    seed_task(
        stack,
        title="Sandbox smoke task",
        description="Active project-bound task for request_sandbox to scope to.",
        project_id=project_id,
        created_by=company.cell_pm_id,
        assigned_to=company.dev_id,
        status=TaskStatus.IN_PROGRESS,
    )
    env = expect_error(
        dev.do("request_sandbox", services=["postgres"]),
        "invalid_state",
        "requested service outside opted set",
    )
    assert "redis" in (env.get("remediate") or "")

    # --- same project/task, request within the opted set --------------------
    # Every guard up to provisioning passes; the harness wires no
    # orchestrator, so the verb must return the clean, retryable
    # "unavailable" envelope rather than raising.
    env = expect_error(
        dev.do("request_sandbox"), "invalid_state", "orchestrator unavailable"
    )
    assert "retry" in (env.get("remediate") or "").lower()
