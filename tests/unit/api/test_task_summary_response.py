"""Task list summary mode — trimmed payloads for panel list views.

The panel fetched /api/tasks unbounded and full-fat (2MB measured live,
2026-07-02): every list row shipped description, plan, progress_updates,
commits, notes. TaskSummaryResponse existed but was dead code. These tests
pin the wired-up summary path: the converter carries exactly the fields
list views render (tree, kanban card, git badge), excludes the fat columns,
and the /summary route is registered before /{task_id} so it can't be
swallowed by the UUID path match.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.api.routes import tasks as routes_mod
from roboco.api.routes.tasks import router
from roboco.api.schemas.tasks import (
    _SUMMARY_SNIPPET_LEN,
    task_list_to_summary_response,
    task_to_summary_response,
)
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable

_LIMIT = 2


def _stub_task(**overrides: Any) -> TaskTable:
    base: dict[str, Any] = {
        "id": uuid4(),
        "title": "t",
        "description": "d" * (_SUMMARY_SNIPPET_LEN * 2 + 100),
        "status": TaskStatus.PENDING,
        "priority": 3,
        "sequence": 1,
        "nature": TaskNature.TECHNICAL,
        "task_type": TaskType.CODE,
        "team": Team.BACKEND,
        "assigned_to": uuid4(),
        "parent_task_id": uuid4(),
        "batch_id": None,
        "project_id": uuid4(),
        "product_id": None,
        "branch_name": "feature/backend/x",
        "pr_number": 42,
        "pr_url": "https://github.com/x/y/pull/42",
        "pr_created": True,
        "docs_complete": False,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "estimated_complexity": Complexity.MEDIUM,
    }
    base.update(overrides)
    return cast("TaskTable", SimpleNamespace(**base))


def test_summary_carries_every_list_view_field() -> None:
    t = _stub_task()
    s = task_to_summary_response(t)
    assert (s.id, s.title, s.status) == (t.id, "t", TaskStatus.PENDING)
    assert s.parent_task_id == t.parent_task_id  # tree build
    assert s.sequence == 1 and s.task_type is TaskType.CODE  # kanban card
    assert (s.pr_number, s.pr_created, s.docs_complete) == (
        42,
        True,
        False,
    )  # git badge
    assert s.branch_name == "feature/backend/x"
    assert s.project_id == t.project_id and s.product_id is None


def test_summary_excludes_fat_fields_and_truncates_snippet() -> None:
    s = task_to_summary_response(_stub_task())
    dump = s.model_dump()
    for fat in (
        "description",
        "plan",
        "progress_updates",
        "commits",
        "quick_context",
        "checkpoints",
        "notes_structured",
        "dev_notes",
        "acceptance_criteria",
    ):
        assert fat not in dump, f"summary must not carry {fat}"
    assert len(s.description_snippet or "") == _SUMMARY_SNIPPET_LEN


def test_summary_snippet_none_safe() -> None:
    assert (
        task_to_summary_response(_stub_task(description=None)).description_snippet
        is None
    )
    assert (
        task_to_summary_response(_stub_task(description="")).description_snippet is None
    )


def test_summary_list_converter() -> None:
    stubs = [_stub_task() for _ in range(_LIMIT)]
    assert len(task_list_to_summary_response(stubs)) == len(stubs)


def test_summary_route_registered_before_task_id_route() -> None:
    """/tasks/summary must not be swallowed by /tasks/{task_id} UUID parsing."""
    paths = [getattr(r, "path", "") for r in router.routes]
    assert "/summary" in paths
    assert paths.index("/summary") < paths.index("/{task_id}")


@pytest.mark.asyncio
async def test_summary_route_status_branch_respects_limit() -> None:
    service = AsyncMock()
    service.list_by_status.return_value = [_stub_task() for _ in range(_LIMIT * 3)]
    permissions = MagicMock()
    permissions.can_perform_task_action.return_value = True
    agent = MagicMock(team=Team.BACKEND)
    with (
        patch.object(routes_mod, "get_task_service", return_value=service),
        patch.object(routes_mod, "get_permission_service", return_value=permissions),
    ):
        out = await routes_mod.list_tasks_summary(
            db=MagicMock(),
            agent=agent,
            team=None,
            status=TaskStatus.PENDING,
            limit=_LIMIT,
        )
    assert len(out) == _LIMIT
