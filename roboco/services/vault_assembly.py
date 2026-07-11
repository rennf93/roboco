"""Assembles ``VaultWriter`` dataclasses from live DB state.

Kept separate from ``vault_writer`` (which is a pure, DB-free materializer)
so that module stays trivially unit-testable with tmp_path. Shared by the
create-on-task seam, the Auditor's ``curate_vault`` verb, the drift janitor,
and the ``python -m roboco.vault rebuild`` CLI — all need "task + parent +
subtasks + dependencies + project slug (+ archive eligibility)" turned into
a ``TaskNoteData``. ``reproject_task`` additionally bundles the "preserve the
existing Auditor narrative" step every re-projection needs.

Services are passed in as ``Any`` (duck-typed) rather than imported by type,
so this module never needs ``roboco.services.task`` / ``roboco.services.project``
at import time — avoids a cycle with ``TaskService`` (which itself calls into
the vault seam on status transitions).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from roboco.config import settings
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.vault_writer import FindingRow, TaskLinkRef, TaskNoteData

logger = structlog.get_logger()

_TERMINAL_STATUS_VALUES = ("completed", "cancelled")


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _archive_year(task: Any) -> int | None:
    """Terminal task older than ``vault_archive_days`` -> its terminal
    timestamp's year (the archival target); else None (stays live).
    ``vault_archive_days=0`` disables archival outright."""
    if settings.vault_archive_days <= 0:
        return None
    if _enum_value(task.status) not in _TERMINAL_STATUS_VALUES:
        return None
    terminal_ts = task.completed_at or task.updated_at or task.created_at
    if terminal_ts is None:
        return None
    cutoff = datetime.now(UTC) - timedelta(days=settings.vault_archive_days)
    return terminal_ts.year if terminal_ts < cutoff else None


async def _resolve_project_slug(project_service: Any, task: Any) -> str:
    if task.project_id is None:
        return "unassigned"
    project = await project_service.get(task.project_id)
    return project.slug if project is not None else "unassigned"


async def _resolve_parent(task_service: Any, task: Any) -> TaskLinkRef | None:
    if task.parent_task_id is None:
        return None
    parent_task = await task_service.get(task.parent_task_id)
    if parent_task is None:
        return None
    return TaskLinkRef(id=str(parent_task.id), title=parent_task.title)


async def _resolve_subtasks(task_service: Any, task: Any) -> tuple[TaskLinkRef, ...]:
    return tuple(
        TaskLinkRef(id=str(t.id), title=t.title)
        for t in await task_service.get_subtasks(task.id)
    )


async def _resolve_dependencies(
    task_service: Any, task: Any
) -> tuple[TaskLinkRef, ...]:
    dependencies: list[TaskLinkRef] = []
    for dep_id in task.dependency_ids or []:
        dep = await task_service.get(dep_id)
        if dep is not None:
            dependencies.append(TaskLinkRef(id=str(dep.id), title=dep.title))
    return tuple(dependencies)


async def _resolve_findings(task_service: Any, task: Any) -> tuple[FindingRow, ...]:
    """Revision-findings ledger rows for the vault ``## Findings`` section.

    Fetched via ``task_service.session`` rather than a threaded repository
    argument — every real caller (the create seam, ``curate_vault``, the
    janitor, ``rebuild``) already carries a ``TaskService`` backed by a real
    session, so this needs no new parameter at any of the four call sites.
    A duck-typed stub without a ``.session`` (some unit tests) yields no
    findings rather than crashing — best-effort, matching the rest of this
    module's DB-optional posture.

    Fail-open: ANY fetch failure (a non-functional session on a stub, a
    transient DB error) degrades to no findings — the vault seams are
    best-effort and swallow exceptions, so a raise here would silently kill
    the whole note materialization, not just the findings section.
    """
    session = getattr(task_service, "session", None)
    if session is None:
        return ()
    try:
        rows = await ReviewFindingsRepository(session).list_for_task(task.id)
    except Exception as exc:
        logger.warning(
            "vault findings fetch failed — rendering note without findings",
            task_id=str(task.id),
            error=str(exc),
        )
        return ()
    return tuple(
        FindingRow(
            id8=str(row.id)[:8],
            severity=row.severity,
            file=row.file,
            line=row.line,
            expected=row.expected,
            actual=row.actual,
            fix=row.fix,
            status=row.status,
            round=row.round,
        )
        for row in rows
    )


async def assemble_task_note_data(
    task_service: Any,
    project_service: Any,
    task: Any,
    *,
    narrative: str | None = None,
) -> TaskNoteData:
    """Build the full ``TaskNoteData`` for one task, resolving its parent,
    subtasks, dependencies, and project slug via the given services."""
    return TaskNoteData(
        id=str(task.id),
        title=task.title,
        project_slug=await _resolve_project_slug(project_service, task),
        description=task.description or "",
        status=_enum_value(task.status),
        team=_enum_value(task.team),
        priority=task.priority,
        task_type=_enum_value(task.task_type),
        acceptance_criteria=tuple(task.acceptance_criteria or ()),
        pr_number=task.pr_number,
        pr_url=task.pr_url,
        parent=await _resolve_parent(task_service, task),
        subtasks=await _resolve_subtasks(task_service, task),
        dependencies=await _resolve_dependencies(task_service, task),
        batch_id=str(task.batch_id) if task.batch_id else None,
        narrative=narrative,
        archive_year=_archive_year(task),
        findings=await _resolve_findings(task_service, task),
    )


async def reproject_task(
    writer: Any, task_service: Any, project_service: Any, task: Any
) -> Any:
    """Re-project one existing task, preserving its Auditor narrative.

    The shared "assemble + preserve narrative + materialize" step used by
    ``rebuild`` and every drift-janitor pass (changed/sample/archival) — one
    code path so they can't diverge on how a task's note gets refreshed.
    """
    project_slug = await _resolve_project_slug(project_service, task)
    narrative = writer.existing_narrative(project_slug, str(task.id))
    data = await assemble_task_note_data(
        task_service, project_service, task, narrative=narrative
    )
    return writer.write_task(data)
