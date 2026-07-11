"""Assembles ``VaultWriter`` dataclasses from live DB state.

Kept separate from ``vault_writer`` (which is a pure, DB-free materializer)
so that module stays trivially unit-testable with tmp_path. Shared by the
Auditor's ``curate_vault`` verb and the ``python -m roboco.vault rebuild`` CLI
— both need "task + parent + subtasks + dependencies + project slug" turned
into a ``TaskNoteData``.

Services are passed in as ``Any`` (duck-typed) rather than imported by type,
so this module never needs ``roboco.services.task`` / ``roboco.services.project``
at import time — avoids a cycle with ``TaskService`` (which itself calls into
the vault seam on status transitions).
"""

from __future__ import annotations

from typing import Any

from roboco.services.vault_writer import TaskLinkRef, TaskNoteData


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


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
    )
