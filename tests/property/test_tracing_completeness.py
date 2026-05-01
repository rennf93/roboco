"""Property test: every completed task has full tracing.

Asserts the six tracing-contract requirements from Phase 4 plan §11.1 over
every task in the synthetic `smoke_test_batch` fixture. The same checks will
run against the real org-wide smoke flow in Phase 4 Task 12 (manual NAS run).

For each completed task, the test asserts:
    1. Every `audit_log` row whose `target_id == task.id` has a non-null
       `agent_id`.
    2. Some agent with role=DEVELOPER has at least one journal entry of
       type=TASK_REFLECTION linked to the task.
    3. Some agent with role=QA has at least one journal entry of
       type=LEARNING linked to the task.
    4. Some agent with role in {CELL_PM, MAIN_PM} has at least one journal
       entry of type=DECISION_LOG linked to the task.
    5. `tasks.acceptance_criteria_status` covers every entry in
       `tasks.acceptance_criteria` with a non-null `referencing_artifact_id`.
    6. `tasks.qa_evidence_inspected` is True.

Notes on storage:
    `acceptance_criteria_status` and `qa_evidence_inspected` are added by
    alembic migration 006 but are NOT (yet) mapped on the SQLAlchemy ORM
    `TaskTable` — the test reads them via a raw SQL query against the same
    Postgres test DB, which is the ground truth the production services
    inspect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    JournalEntryTable,
    JournalTable,
)
from roboco.models.base import AgentRole, JournalEntryType
from sqlalchemy import select, text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def _audit_rows_for_task(
    session: AsyncSession, task_id: UUID
) -> list[AuditLogTable]:
    rows = await session.execute(
        select(AuditLogTable).where(AuditLogTable.target_id == task_id)
    )
    return list(rows.scalars().all())


async def _journal_entries_for_task_by_role(
    session: AsyncSession,
    task_id: UUID,
    entry_type: JournalEntryType,
    roles: tuple[AgentRole, ...],
) -> list[JournalEntryTable]:
    rows = await session.execute(
        select(JournalEntryTable)
        .join(JournalTable, JournalEntryTable.journal_id == JournalTable.id)
        .join(AgentTable, JournalTable.agent_id == AgentTable.id)
        .where(
            JournalEntryTable.task_id == task_id,
            JournalEntryTable.type == entry_type,
            AgentTable.role.in_(roles),
        )
    )
    return list(rows.scalars().all())


async def _task_gateway_columns(
    session: AsyncSession, task_id: UUID
) -> tuple[list[str], list[dict], bool]:
    """Read acceptance_criteria, acceptance_criteria_status, qa_evidence_inspected
    via raw SQL — `acceptance_criteria_status` and `qa_evidence_inspected` are
    not (yet) mapped on the ORM TaskTable, so we go straight to the DB.
    """
    row = await session.execute(
        text(
            "SELECT acceptance_criteria, acceptance_criteria_status, "
            "       qa_evidence_inspected "
            "FROM tasks WHERE id = :tid"
        ),
        {"tid": task_id},
    )
    fetched = row.one()
    criteria: list[str] = list(fetched[0] or [])
    status: list[dict] = list(fetched[1] or [])
    inspected: bool = bool(fetched[2])
    return criteria, status, inspected


@pytest.mark.asyncio
async def test_completed_tasks_have_full_tracing(
    db_session: AsyncSession,
    smoke_test_batch: list[UUID],
) -> None:
    """For every task with status=completed in the smoke-test fixture batch:

    - audit_log has >=1 entry per state transition with non-null agent_id
    - DEVELOPER role has >=1 journal:TASK_REFLECTION for the task
    - QA role has >=1 journal:LEARNING for the task
    - CELL_PM or MAIN_PM has >=1 journal:DECISION_LOG for the task
    - acceptance_criteria_status: every criterion has referencing_artifact_id
    - qa_evidence_inspected = true
    """
    assert smoke_test_batch, "smoke_test_batch fixture seeded zero tasks"

    for task_id in smoke_test_batch:
        # ------------------------------------------------------------------
        # 1. audit_log: every transition row for this task has agent_id != NULL.
        # ------------------------------------------------------------------
        audit_rows = await _audit_rows_for_task(db_session, task_id)
        assert audit_rows, f"task {task_id} has no audit_log rows"
        null_agent_rows = [r for r in audit_rows if r.agent_id is None]
        assert not null_agent_rows, (
            f"task {task_id} has audit_log rows with NULL agent_id: "
            f"{[r.event_type for r in null_agent_rows]}"
        )

        # ------------------------------------------------------------------
        # 2. dev TASK_REFLECTION entry exists.
        # ------------------------------------------------------------------
        dev_reflections = await _journal_entries_for_task_by_role(
            db_session,
            task_id,
            JournalEntryType.TASK_REFLECTION,
            (AgentRole.DEVELOPER,),
        )
        assert dev_reflections, (
            f"task {task_id}: no DEVELOPER TASK_REFLECTION journal entry"
        )

        # ------------------------------------------------------------------
        # 3. qa LEARNING entry exists.
        # ------------------------------------------------------------------
        qa_learnings = await _journal_entries_for_task_by_role(
            db_session,
            task_id,
            JournalEntryType.LEARNING,
            (AgentRole.QA,),
        )
        assert qa_learnings, f"task {task_id}: no QA LEARNING journal entry"

        # ------------------------------------------------------------------
        # 4. PM (cell or main) DECISION_LOG entry exists.
        # ------------------------------------------------------------------
        pm_decisions = await _journal_entries_for_task_by_role(
            db_session,
            task_id,
            JournalEntryType.DECISION_LOG,
            (AgentRole.CELL_PM, AgentRole.MAIN_PM),
        )
        assert pm_decisions, (
            f"task {task_id}: no CELL_PM/MAIN_PM DECISION_LOG journal entry"
        )

        # ------------------------------------------------------------------
        # 5. acceptance_criteria_status covers every criterion with a
        #    referencing_artifact_id.
        # ------------------------------------------------------------------
        criteria, status_rows, inspected = await _task_gateway_columns(
            db_session, task_id
        )
        addressed = {
            entry["criterion"]
            for entry in status_rows
            if isinstance(entry, dict) and entry.get("referencing_artifact_id")
        }
        unaddressed = [c for c in criteria if c not in addressed]
        assert not unaddressed, (
            f"task {task_id}: acceptance criteria without "
            f"referencing_artifact_id: {unaddressed}"
        )

        # ------------------------------------------------------------------
        # 6. qa_evidence_inspected is True.
        # ------------------------------------------------------------------
        assert inspected, f"task {task_id}: qa_evidence_inspected is False"
