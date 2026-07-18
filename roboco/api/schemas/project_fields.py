"""Shared (project_slug, project_name) response-builder helper.

Both the X and video engine queues surface which project a held draft/video
targets. Extracted here so the X and video routes' five response builders call
ONE implementation instead of five inline copies — the same
``sa_inspect(task).unloaded`` guard convention ``task_to_response`` uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import inspect as sa_inspect

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def task_project_fields(task: TaskTable) -> tuple[str | None, str | None]:
    """(project_slug, project_name), or (None, None).

    A freshly-created task can have an unloaded ``project`` relationship, so a
    sync attribute access would raise MissingGreenlet — checked via
    ``sa_inspect(task).unloaded`` before ever touching ``task.project``.
    """
    if "project" in sa_inspect(task).unloaded or task.project is None:
        return None, None
    return task.project.slug, task.project.name
