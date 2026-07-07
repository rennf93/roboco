"""roboco.api.schemas.tasks coverage — pure-Python conversion helpers.

The route layer is owned by another agent; here we cover the pure
data-mapping helpers: convert_plan, convert_checkpoints,
convert_progress_updates, convert_commits, parse_uuid_or_none,
_parse_uuid_list, transform_update_data, and task_to_response/
task_list_to_response (with a stub TaskTable to avoid DB).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.api.schemas.docs import DocRefResponse
from roboco.api.schemas.tasks import (
    SubstituteRequest,
    TaskUpdate,
    _parse_uuid_list,
    convert_checkpoints,
    convert_commits,
    convert_documents,
    convert_plan,
    convert_progress_updates,
    enrich_task_with_context,
    parse_uuid_or_none,
    task_list_to_response,
    task_to_response,
    transform_update_data,
)
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.models.product import ProductCellMapping

_ORDER_DEFAULT = 0


# ---------------------------------------------------------------------------
# convert_plan
# ---------------------------------------------------------------------------


def test_convert_plan_returns_none_for_empty() -> None:
    assert convert_plan(None) is None
    assert convert_plan({}) is None


def test_convert_plan_with_full_data() -> None:
    sub_id = uuid4()
    data = {
        "approach": "Implement X using Y",
        "sub_tasks": [
            {
                "id": str(sub_id),
                "title": "Sub 1",
                "description": "do thing",
                "completed": True,
                "order": 1,
                "estimated_hours": 2.5,
                "notes": "n",
            },
        ],
        "technical_considerations": ["c1"],
        "risks": [{"name": "r1"}],
        "open_questions": [{"q": "?"}],
    }
    plan = convert_plan(data)
    assert plan is not None
    assert plan.approach == "Implement X using Y"
    assert plan.sub_tasks[0].id == sub_id
    assert plan.sub_tasks[0].completed is True


def test_convert_plan_coerces_invalid_uuid_to_fresh() -> None:
    """Non-UUID id strings are silently replaced with a new UUID."""
    data: dict[str, Any] = {
        "approach": "x",
        "sub_tasks": [{"id": "not-a-uuid", "title": "t", "order": 0}],
    }
    plan = convert_plan(data)
    assert plan is not None
    assert isinstance(plan.sub_tasks[0].id, UUID)


def test_convert_plan_passes_through_uuid_id() -> None:
    """When id is already a UUID instance, it's preserved."""
    sub_id = uuid4()
    data: dict[str, Any] = {
        "approach": "x",
        "sub_tasks": [{"id": sub_id, "title": "t", "order": 0}],
    }
    plan = convert_plan(data)
    assert plan is not None
    assert plan.sub_tasks[0].id == sub_id


def test_convert_plan_with_non_uuid_non_string_id() -> None:
    """Numeric ids are also coerced to a fresh UUID."""
    data: dict[str, Any] = {
        "approach": "x",
        "sub_tasks": [{"id": 12345, "title": "t", "order": 0}],
    }
    plan = convert_plan(data)
    assert plan is not None
    assert isinstance(plan.sub_tasks[0].id, UUID)


# ---------------------------------------------------------------------------
# convert_checkpoints
# ---------------------------------------------------------------------------


def test_convert_checkpoints_empty() -> None:
    assert convert_checkpoints(None) == []
    assert convert_checkpoints([]) == []


def test_convert_checkpoints_with_data() -> None:
    cp_id = uuid4()
    agent_id = uuid4()
    data = [
        {
            "id": cp_id,
            "timestamp": datetime.now(UTC),
            "agent_id": agent_id,
            "state_summary": "halfway",
            "remaining_work": ["x"],
            "notes": "fine",
        }
    ]
    result = convert_checkpoints(data)
    assert len(result) == 1
    assert result[0].id == cp_id
    assert result[0].state_summary == "halfway"


# ---------------------------------------------------------------------------
# convert_progress_updates
# ---------------------------------------------------------------------------


def test_convert_progress_updates_empty() -> None:
    assert convert_progress_updates(None) == []
    assert convert_progress_updates([]) == []


def test_convert_progress_updates_with_data() -> None:
    agent_id = uuid4()
    data = [
        {
            "timestamp": datetime.now(UTC),
            "agent_id": agent_id,
            "message": "step done",
            "percentage": 50,
        }
    ]
    result = convert_progress_updates(data)
    assert len(result) == 1
    assert result[0].message == "step done"


# ---------------------------------------------------------------------------
# convert_commits
# ---------------------------------------------------------------------------


def test_convert_commits_empty() -> None:
    assert convert_commits(None) == []
    assert convert_commits([]) == []


def test_convert_commits_with_data() -> None:
    agent_id = uuid4()
    data = [
        {
            "hash": "abc123",
            "message": "fix",
            "timestamp": datetime.now(UTC),
            "author_agent_id": agent_id,
        }
    ]
    result = convert_commits(data)
    assert len(result) == 1
    assert result[0].hash == "abc123"


# ---------------------------------------------------------------------------
# parse_uuid_or_none / _parse_uuid_list
# ---------------------------------------------------------------------------


def test_parse_uuid_or_none_with_valid_uuid() -> None:
    raw = uuid4()
    assert parse_uuid_or_none(str(raw)) == raw


def test_parse_uuid_or_none_with_empty() -> None:
    assert parse_uuid_or_none("") is None
    assert parse_uuid_or_none(None) is None


def test_parse_uuid_or_none_with_invalid_returns_none() -> None:
    assert parse_uuid_or_none("not-a-uuid") is None


def test_parse_uuid_list_with_valid() -> None:
    a, b = uuid4(), uuid4()
    out = _parse_uuid_list([str(a), str(b)])
    assert a in out
    assert b in out


def test_parse_uuid_list_skips_empty_strings() -> None:
    raw = uuid4()
    vals: list[Any] = [str(raw), "", None]
    out = _parse_uuid_list(cast("list[str]", vals))
    assert raw in out
    assert len(out) == 1


def test_parse_uuid_list_with_none() -> None:
    assert _parse_uuid_list(None) == []
    assert _parse_uuid_list([]) == []


# ---------------------------------------------------------------------------
# transform_update_data
# ---------------------------------------------------------------------------


def test_transform_update_data_converts_assigned_to() -> None:
    raw = uuid4()
    update = TaskUpdate(assigned_to=str(raw))
    out = transform_update_data(update)
    assert out["assigned_to"] == raw


def test_transform_update_data_converts_dependency_ids() -> None:
    a, b = uuid4(), uuid4()
    update = TaskUpdate(dependency_ids=[str(a), str(b)])
    out = transform_update_data(update)
    assert a in out["dependency_ids"]
    assert b in out["dependency_ids"]


def test_transform_update_data_skips_unset_fields() -> None:
    """Empty TaskUpdate produces an empty dict (model_dump exclude_unset)."""
    update = TaskUpdate()
    out = transform_update_data(update)
    assert out == {}


def test_transform_update_data_handles_null_unassign() -> None:
    """Empty string assigned_to → None (parse_uuid_or_none)."""
    update = TaskUpdate(assigned_to="")
    out = transform_update_data(update)
    assert out["assigned_to"] is None


def test_transform_update_data_passes_sequence() -> None:
    """sequence is editable via PATCH (panel task-details sequence editor)."""
    new_order = 3
    update = TaskUpdate(sequence=new_order)
    out = transform_update_data(update)
    assert out["sequence"] == new_order
    # Unset sequence is omitted (exclude_unset), so a partial PATCH never
    # clobbers the existing order.
    assert "sequence" not in transform_update_data(TaskUpdate(priority=1))


def test_task_update_sequence_rejects_negative() -> None:
    """sequence has ge=0 — a negative order is a validation error, not stored."""
    with pytest.raises(ValueError, match="sequence"):
        TaskUpdate(sequence=-1)


# ---------------------------------------------------------------------------
# task_to_response / task_list_to_response
# ---------------------------------------------------------------------------


def _stub_task(*, with_project: bool = False) -> Any:
    """Build a TaskTable stand-in that matches task_to_response's reads."""
    return SimpleNamespace(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["a"],
        status=TaskStatus.PENDING,
        priority=1,
        sequence=0,
        nature=TaskNature.TECHNICAL,
        task_type=TaskType.CODE,
        project_id=uuid4(),
        product_id=None,
        cell_projects=[],
        project=(SimpleNamespace(slug="proj-1") if with_project else None),
        docs_complete=False,
        pr_created=False,
        board_review_complete=False,
        team=Team.BACKEND,
        created_by=uuid4(),
        assigned_to=None,
        parent_task_id=None,
        batch_id=None,
        dependency_ids=[],
        blocker_ids=[],
        created_at=datetime.now(UTC),
        updated_at=None,
        claimed_at=None,
        claimed_by=None,
        started_at=None,
        completed_at=None,
        target_date=None,
        last_heartbeat_at=None,
        estimated_complexity=Complexity.LOW,
        plan=None,
        checkpoints=[],
        progress_updates=[],
        commits=[],
        documents=[],
        dev_notes=None,
        qa_notes=None,
        auditor_notes=None,
        pr_reviewer_notes=None,
        doc_notes=None,
        quick_context=None,
        notes_structured=None,
        self_verified=False,
        qa_verified=None,
        branch_name=None,
        pr_number=None,
        pr_url=None,
    )


def test_task_to_response_omits_slug_when_project_not_loaded() -> None:
    stub = _stub_task(with_project=False)
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"project"}
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(stub)
    assert resp.project_slug is None


def test_task_to_response_includes_slug_when_project_loaded() -> None:
    stub = _stub_task(with_project=True)
    fake_inspector = MagicMock()
    fake_inspector.unloaded = set()  # project IS loaded
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(stub)
    assert resp.project_slug == "proj-1"


def test_task_to_response_serializes_cell_projects_when_loaded() -> None:
    """An ad-hoc per-cell map (a multi-cell MegaTask root-subtask) round-trips
    into the response when the relationship is loaded."""
    be_proj, fe_proj = uuid4(), uuid4()
    stub = _stub_task()
    stub.project_id = None
    stub.product_id = None
    stub.cell_projects = [
        SimpleNamespace(team=Team.BACKEND, project_id=be_proj),
        SimpleNamespace(team=Team.FRONTEND, project_id=fe_proj),
    ]
    fake_inspector = MagicMock()
    fake_inspector.unloaded = set()  # cell_projects IS loaded
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(stub)
    assert resp.cell_projects == [
        ProductCellMapping(team=Team.BACKEND, project_id=be_proj),
        ProductCellMapping(team=Team.FRONTEND, project_id=fe_proj),
    ]


def test_task_to_response_omits_cell_projects_when_unloaded() -> None:
    """A freshly-created task whose cell_projects relationship is unloaded
    serializes to [] rather than triggering a lazy load."""
    stub = _stub_task()
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"cell_projects"}
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(stub)
    assert resp.cell_projects == []


def test_task_to_response_serializes_all_note_sections() -> None:
    """Regression: pr_reviewer_notes / doc_notes / notes_structured MUST be in the
    response. The builder previously omitted them, so the panel showed them blank
    even when the DB had them (the recurring "notes invisible" bug)."""
    stub = _stub_task()
    stub.pr_reviewer_notes = "## Findings\n- looks good"
    stub.doc_notes = "Updated the README"
    stub.notes_structured = {"pr_review": {"verdict": "passed"}}
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"project"}
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(stub)
    assert resp.pr_reviewer_notes == "## Findings\n- looks good"
    assert resp.doc_notes == "Updated the README"
    assert resp.notes_structured == {"pr_review": {"verdict": "passed"}}


def test_task_to_response_serializes_documents() -> None:
    """TaskTable.documents (a JSON list of DocRef.model_dump() dicts) is
    serialized into TaskResponse.documents as DocRefResponse instances."""
    stub = _stub_task()
    stub.documents = [
        {
            "path": "docs/x.md",
            "title": "X",
            "doc_type": "api",
            "version": None,
            "created_by": "be-dev-1",
            "created_at": None,
            "updated_by": None,
            "updated_at": None,
            "commit_status": "committed",
        }
    ]
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"project"}
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(stub)
    assert resp.documents == [
        DocRefResponse(
            path="docs/x.md",
            title="X",
            doc_type="api",
            version=None,
            created_by="be-dev-1",
            created_at=None,
            updated_by=None,
            updated_at=None,
            commit_status="committed",
        )
    ]


def test_task_to_response_documents_defaults_empty() -> None:
    """A task with no documents serializes to an empty list, not None."""
    stub = _stub_task()
    stub.documents = []
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"project"}
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(stub)
    assert resp.documents == []


def test_convert_documents_defensive_on_malformed_row() -> None:
    """A malformed legacy dict missing required fields still yields a
    DocRefResponse (with empty-string defaults), never a 500."""
    out = convert_documents([{"path": "docs/y.md"}])
    assert out == [DocRefResponse(path="docs/y.md", title="", doc_type="")]


def test_task_list_to_response_returns_list() -> None:
    stubs = [_stub_task(), _stub_task()]
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"project"}
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        out = task_list_to_response(stubs)
    assert len(out) == len(stubs)


# ---------------------------------------------------------------------------
# enrich_task_with_context — covers the work_session + project lookup branches.
# ---------------------------------------------------------------------------


def _stub_response() -> Any:
    """Build a TaskResponse-like object that supports model_dump."""
    fake_inspector = MagicMock()
    fake_inspector.unloaded = {"project"}
    with patch("roboco.api.schemas.tasks.sa_inspect", return_value=fake_inspector):
        resp = task_to_response(_stub_task())
    return resp


@pytest.mark.asyncio
async def test_enrich_task_with_context_attaches_workssession_and_project() -> None:
    """Both work_session and project rows present → both keys attached."""
    resp = _stub_response()

    work_session = MagicMock()
    work_session.id = uuid4()
    work_session.branch_name = "feature/backend/x"
    work_session.status = MagicMock()
    work_session.status.value = "active"
    work_session.commits = ["abc123"]
    work_session.files_modified = ["foo.py"]
    work_session.pr_number = 42
    work_session.pr_url = "https://github.com/x/y/pull/42"
    work_session.pr_status = "open"
    work_session.project_id = uuid4()

    project = MagicMock()
    project.id = work_session.project_id
    project.name = "Proj"
    project.slug = "proj"
    project.git_url = "https://github.com/example/repo.git"
    project.default_branch = "main"

    db = MagicMock()
    db.execute = AsyncMock()
    # First execute returns work_session; second returns project.
    ws_result = MagicMock()
    ws_result.scalar_one_or_none.return_value = work_session
    proj_result = MagicMock()
    proj_result.scalar_one_or_none.return_value = project
    db.execute.side_effect = [ws_result, proj_result]

    enriched = await enrich_task_with_context(resp, db)
    assert enriched.work_session is not None
    assert enriched.project is not None


@pytest.mark.asyncio
async def test_enrich_task_with_context_no_work_session() -> None:
    """No work_session row → enrichment passes through with no work_session set."""
    resp = _stub_response()
    db = MagicMock()
    db.execute = AsyncMock()
    ws_result = MagicMock()
    ws_result.scalar_one_or_none.return_value = None
    db.execute.return_value = ws_result
    enriched = await enrich_task_with_context(resp, db)
    # Returned object — work_session stays as default (None) since pull was empty.
    assert enriched.work_session is None


@pytest.mark.asyncio
async def test_enrich_task_with_context_status_unknown_when_status_falsy() -> None:
    """work_session.status is None → 'unknown' fallback string (line 656)."""
    resp = _stub_response()

    work_session = MagicMock()
    work_session.id = uuid4()
    work_session.branch_name = "feature/x"
    work_session.status = None
    work_session.commits = []
    work_session.files_modified = []
    work_session.pr_number = None
    work_session.pr_url = None
    work_session.pr_status = None
    work_session.project_id = None

    db = MagicMock()
    db.execute = AsyncMock()
    ws_result = MagicMock()
    ws_result.scalar_one_or_none.return_value = work_session
    db.execute.return_value = ws_result
    enriched = await enrich_task_with_context(resp, db)
    assert enriched.work_session is not None
    assert enriched.work_session.status == "unknown"


@pytest.mark.asyncio
async def test_enrich_task_with_context_skips_project_when_not_requested() -> None:
    """include_project=False bypasses project enrichment."""
    resp = _stub_response()

    work_session = MagicMock()
    work_session.id = uuid4()
    work_session.branch_name = "feature/x"
    work_session.status = MagicMock()
    work_session.status.value = "active"
    work_session.commits = []
    work_session.files_modified = []
    work_session.pr_number = None
    work_session.pr_url = None
    work_session.pr_status = None
    work_session.project_id = uuid4()

    db = MagicMock()
    db.execute = AsyncMock()
    ws_result = MagicMock()
    ws_result.scalar_one_or_none.return_value = work_session
    db.execute.return_value = ws_result
    enriched = await enrich_task_with_context(resp, db, include_project=False)
    assert enriched.work_session is not None
    assert enriched.project is None


def test_substitute_request_has_no_suggested_role_or_team() -> None:
    fields = SubstituteRequest.model_fields
    assert "suggested_role" not in fields
    assert "suggested_team" not in fields


def test_substitute_request_keeps_reason_and_details() -> None:
    fields = SubstituteRequest.model_fields
    assert "reason" in fields
    assert "details" in fields
