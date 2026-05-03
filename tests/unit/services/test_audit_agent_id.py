"""Real-DB integration test for ``agent_id`` population on audit rows.

Why this test exists
--------------------
Two audit-row defects, both producing ``audit_log.agent_id = NULL``:

1. ``task.awaiting_qa`` is fired from inside ``_validate_and_set_status``
   AFTER ``submit_for_qa`` has already cleared ``task.claimed_by = None``
   (see ``roboco/services/task.py``). The audit writer reads
   ``task.claimed_by`` at fire time and gets ``None`` — even though a real
   developer just submitted the work. The Auditor agent then has no way
   to ask "who submitted this for QA?" from the audit table alone.

2. ``log_agent_event`` (called by the orchestrator on ``agent.spawned``,
   ``agent.spawn_failed``, ``agent.stopped``, etc.) only knows the agent's
   slug, not its UUID. It always passes ``agent_id=None`` to ``_persist``
   and stuffs the slug into ``details``. Same NULL problem — the
   ``audit_log.agent_id`` column is FK to ``agents.id``, so we want the
   real UUID stored, not just a sidecar string.

Both fixes are validated here against a real Postgres DB:

* Test 1 seeds an agent, simulates the submit_for_qa "capture before
  mutate" by passing the dev's UUID through the explicit
  ``audit_agent_id`` parameter, and asserts the row written to
  ``audit_log`` has ``agent_id`` set to the dev's UUID (NOT NULL).

* Test 2 seeds an agent with a known slug, calls ``log_agent_event``
  with that slug, and asserts the row written to ``audit_log`` has
  ``agent_id`` resolved to the seeded agent's UUID via the new
  resolver path.

Both tests would FAIL on the pre-fix code: the first because the audit
row's agent_id would be NULL (claimed_by cleared before fire), and the
second because ``log_agent_event`` hard-codes ``agent_id=None``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db import base as roboco_db_base
from roboco.db.tables import AgentTable, AuditLogTable, ProjectTable, TaskTable
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.audit import AuditService
from roboco.services.task import TaskService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agent_with_slug(
    session: AsyncSession, slug: str | None = None
) -> tuple[UUID, str]:
    """Insert a minimal AgentTable row and return (id, slug).

    Provides a real FK target so ``audit_log.agent_id`` writes succeed
    instead of silently failing the best-effort persist.
    """
    actual_slug = slug or f"audit-agent-test-{uuid4().hex[:8]}"
    agent = AgentTable(
        id=uuid4(),
        name="Audit Agent ID Test",
        slug=actual_slug,
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="audit agent_id test",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.commit()
    return UUID(str(agent.id)), actual_slug


@pytest_asyncio.fixture
async def patched_session_factory(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    """Point ``get_session_factory`` at the test-DB engine for the run.

    ``AuditService._persist`` and ``log_agent_event`` open their own
    sessions via ``roboco.db.base.get_session_factory()`` — without this
    they bind to the production DB URL.
    """
    test_engine = db_session.bind
    test_factory = async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(roboco_db_base, "get_session_factory", lambda: test_factory)
    yield db_session


@pytest.mark.asyncio
async def test_log_task_event_persists_explicit_agent_id_when_claimed_by_was_cleared(
    patched_session_factory: AsyncSession,
) -> None:
    """``log_task_event`` writes the explicit ``agent_id`` even after caller mutation.

    Simulates the ``submit_for_qa`` race: the caller captures the dev's
    UUID before clearing ``task.claimed_by``, then passes the captured
    value into the audit write. The audit row must have ``agent_id``
    populated — that's the whole point of capture-before-mutate.

    On the pre-fix code, ``submit_for_qa`` would clear ``claimed_by``
    first and ``_validate_and_set_status`` would read the now-None value,
    producing ``audit_log.agent_id = NULL``.
    """
    dev_uuid, _ = await _seed_agent_with_slug(patched_session_factory)
    audit = AuditService()
    task_id = uuid4()

    # Caller pattern: capture-before-mutate. The dev's UUID is captured
    # and passed explicitly even though `task.claimed_by` was set to None.
    await audit.log_task_event(
        event_type="task.awaiting_qa",
        task_id=task_id,
        agent_id=dev_uuid,
        details={
            "from_status": "verifying",
            "to_status": "awaiting_qa",
            "agent_role": "developer",
            "team": "backend",
        },
    )

    # Read back the row from audit_log.
    result = await patched_session_factory.execute(
        select(AuditLogTable)
        .where(AuditLogTable.event_type == "task.awaiting_qa")
        .where(AuditLogTable.target_id == task_id)
    )
    rows = list(result.scalars().all())

    assert len(rows) == 1, "Expected exactly one task.awaiting_qa row"
    row = rows[0]
    assert row.agent_id is not None, (
        "agent_id must be populated when caller passes a captured UUID — "
        "this is the capture-before-mutate fix for submit_for_qa"
    )
    assert row.agent_id == dev_uuid


@pytest.mark.asyncio
async def test_log_agent_event_resolves_slug_to_uuid(
    patched_session_factory: AsyncSession,
) -> None:
    """``log_agent_event`` resolves the agent slug to its UUID.

    The orchestrator only sees agent slugs at spawn time. The audit row's
    ``agent_id`` column is FK to ``agents.id`` — we want the real UUID
    stored, not just a slug stuffed into ``details``.

    On the pre-fix code, ``log_agent_event`` always passed
    ``agent_id=None`` to ``_persist``, producing ``audit_log.agent_id = NULL``.
    """
    expected_uuid, slug = await _seed_agent_with_slug(patched_session_factory)
    audit = AuditService()
    task_id = uuid4()

    # Orchestrator-style call — passes slug only.
    await audit.log_agent_event(
        event_type="agent.spawned",
        agent_slug=slug,
        task_id=task_id,
        details={"container_id": "abc123def456", "model": "claude-opus-4-6"},
    )

    # Read back the row.
    result = await patched_session_factory.execute(
        select(AuditLogTable)
        .where(AuditLogTable.event_type == "agent.spawned")
        .where(AuditLogTable.target_id == task_id)
    )
    rows = list(result.scalars().all())

    assert len(rows) == 1, "Expected exactly one agent.spawned row"
    row = rows[0]
    assert row.agent_id is not None, (
        "agent_id must be resolved from the slug — this is the slug-to-UUID "
        "fix for log_agent_event so audit rows have a real FK to agents.id"
    )
    assert row.agent_id == expected_uuid
    # Slug should still be present in details for redundancy.
    assert row.details.get("agent_slug") == slug


@pytest.mark.asyncio
async def test_submit_for_qa_writes_audit_with_dev_agent_id(
    patched_session_factory: AsyncSession,
) -> None:
    """End-to-end: ``submit_for_qa`` -> ``task.awaiting_qa`` row with dev's UUID.

    This is the integration test for the capture-before-mutate fix.
    Pre-fix flow:
        1. submit_for_qa sets ``task.claimed_by = None``
        2. submit_for_qa calls ``_validate_and_set_status``
        3. ``_validate_and_set_status`` reads ``task.claimed_by`` -> None
        4. audit row written with ``agent_id = NULL``

    Post-fix flow:
        1. submit_for_qa captures ``original_dev = task.claimed_by`` first
        2. submit_for_qa sets ``task.claimed_by = None``
        3. submit_for_qa passes ``audit_agent_id=original_dev`` to
           ``_validate_and_set_status``
        4. audit row written with ``agent_id = <dev's UUID>``
    """
    # Seed dev + system + project + a VERIFYING task claimed by dev.
    dev_uuid, _ = await _seed_agent_with_slug(patched_session_factory)
    system_uuid, _ = await _seed_agent_with_slug(patched_session_factory)

    project = ProjectTable(
        id=uuid4(),
        name="Audit Test Project",
        slug=f"audit-test-{uuid4().hex[:8]}",
        git_url="https://github.com/example/audit-test.git",
        default_branch="main",
        protected_branches=["main"],
        assigned_cell=Team.BACKEND,
        created_by=system_uuid,
        is_active=True,
    )
    patched_session_factory.add(project)
    await patched_session_factory.flush()

    task = TaskTable(
        id=uuid4(),
        title="Audit-id capture test",
        description=(
            "Verifies submit_for_qa captures dev UUID before clearing claimed_by."
        ),
        acceptance_criteria=["Audit row has agent_id populated"],
        status=TaskStatus.VERIFYING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        branch_name="feature/backend/AUDIT001",
        pr_number=99,
        pr_url="https://github.com/example/audit-test/pull/99",
        docs_complete=False,
        pr_created=True,
        pm_approvals={},
        created_by=system_uuid,
        assigned_to=dev_uuid,
        claimed_by=dev_uuid,
        team=Team.BACKEND,
        dependency_ids=[],
        blocker_ids=[],
        sequence=0,
        plan={"steps": ["impl"]},
        estimated_complexity=Complexity.MEDIUM,
        execution_log={},
        checkpoints=[],
        progress_updates=[{"at": "t0", "note": "started"}],
        commits=[{"sha": "abc123", "message": "[AUDIT001] init"}],
        documents=[],
        outputs=[],
        dev_notes="impl complete",
        self_verified=True,
    )
    patched_session_factory.add(task)
    await patched_session_factory.commit()

    # Exercise: dev role submitting their own verified task to QA.
    service = TaskService(patched_session_factory)
    captured_task_id = UUID(str(task.id))
    result = await service.submit_for_qa(captured_task_id, agent_role="developer")
    assert result is not None
    # Sanity: claimed_by IS cleared (this is intentional — QA needs to claim).
    assert result.claimed_by is None

    # Wait for the fire-and-forget audit task to complete. The `_validate_
    # and_set_status` write goes onto a strong-ref'd background task; we
    # join the same task set the service uses by sleeping briefly. (The
    # production code uses asyncio.create_task; in tests there's no other
    # scheduler so a short sleep + manual await is the cleanest path.)
    pending = [bg for bg in service._background_tasks if not bg.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # Also drain any audit-internal tasks scheduled via get_running_loop().
    for _ in range(5):
        await asyncio.sleep(0.05)

    # Verify the audit row.
    result_rows = await patched_session_factory.execute(
        select(AuditLogTable)
        .where(AuditLogTable.event_type == "task.awaiting_qa")
        .where(AuditLogTable.target_id == captured_task_id)
    )
    rows = list(result_rows.scalars().all())
    assert len(rows) == 1, (
        f"Expected exactly one task.awaiting_qa audit row, got {len(rows)}"
    )
    row = rows[0]
    assert row.agent_id == dev_uuid, (
        f"audit_log.agent_id must be the dev's UUID ({dev_uuid}), got "
        f"{row.agent_id} — capture-before-mutate regressed"
    )


@pytest.mark.asyncio
async def test_log_agent_event_unknown_slug_writes_null_agent_id(
    patched_session_factory: AsyncSession,
) -> None:
    """An unknown slug must NOT block the audit write.

    The audit subsystem is best-effort — observability must never fail
    the operation being observed. If the slug doesn't resolve, the row
    is still written with ``agent_id=NULL`` and the slug preserved in
    ``details`` for forensic recovery.
    """
    audit = AuditService()
    task_id = uuid4()
    unknown_slug = f"never-seeded-{uuid4().hex[:8]}"

    await audit.log_agent_event(
        event_type="agent.spawn_failed",
        agent_slug=unknown_slug,
        task_id=task_id,
        details={"error": "container died"},
        severity="error",
    )

    result = await patched_session_factory.execute(
        select(AuditLogTable)
        .where(AuditLogTable.event_type == "agent.spawn_failed")
        .where(AuditLogTable.target_id == task_id)
    )
    rows = list(result.scalars().all())

    assert len(rows) == 1
    # agent_id is NULL because slug didn't resolve, but the write succeeded
    # and the slug is preserved in details for forensic lookup.
    assert rows[0].agent_id is None
    assert rows[0].details.get("agent_slug") == unknown_slug
