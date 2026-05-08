"""TaskService coverage — final misc paths to push to 100%.

Targets:
- _append_capped truncation
- _default_claim_statuses for role-specific
- extract_original_developer invalid UUID
- _validate_parent_depth circular + max-depth + missing parent
- activate without project_id
- branch creation methods (with git mocks)
- claim role validations
- TaskLifecycleError catches in unclaim_for_reaper / unclaim_for_agent
- docs_complete branches (no notes / no assigned_to / has documenter)
- completion_notes early-return
- get_completing_agent_role missing agent
- complete main_pm escalation to CEO for root parent
- complete with PR not merged
- list_by_team_or_assignee with no conditions
- escalate_to_ceo_for_agent → inner escalate returns None
- mark_agent_idle missing agent
- qa_fail mismatch logging
- cell_pm_complete missing task
- get_task_service factory
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, WorkSessionTable
from roboco.enforcement import TaskLifecycleError
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus
from roboco.models.permissions import AgentContext
from roboco.models.task import TaskCreateRequest
from roboco.models.work_session import WorkSessionStatus
from roboco.services.base import ValidationError
from roboco.services.task import (
    TaskService,
    _append_capped,
    _default_claim_statuses,
    extract_original_developer,
    get_task_service,
)
from roboco.templates.git.constants import MAX_TASK_DEPTH

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def task_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        default_branch="main",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "project_slug": project.slug,
        "db": db_session,
    }


def _req(setup: dict, **overrides) -> TaskCreateRequest:
    return TaskCreateRequest(
        title=overrides.pop("title", "t"),
        description=overrides.pop("description", "d"),
        acceptance_criteria=overrides.pop("acceptance_criteria", ["ac"]),
        team=overrides.pop("team", Team.BACKEND),
        created_by=setup["agent_id"],
        project_id=setup["project_id"],
        **overrides,
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_append_capped_truncates_when_over_max() -> None:
    """When the joined notes exceed _MAX_NOTES_CHARS, oldest gets dropped."""
    big_existing = "OLD" * 4000  # ~12000 chars
    addition = "newest"
    result = _append_capped(big_existing, addition)
    assert "[...earlier notes truncated for size...]" in result
    assert result.endswith(addition)


def test_append_capped_no_truncation_below_max() -> None:
    out = _append_capped("hello", "world")
    assert out == "hello\n\nworld"


def test_default_claim_statuses_for_qa() -> None:
    """QA role returns the role-specific set."""
    statuses = _default_claim_statuses("qa")
    assert TaskStatus.AWAITING_QA in statuses


def test_default_claim_statuses_for_unknown_role() -> None:
    statuses = _default_claim_statuses("developer")
    assert TaskStatus.PENDING in statuses
    assert TaskStatus.NEEDS_REVISION in statuses


def test_default_claim_statuses_for_none() -> None:
    statuses = _default_claim_statuses(None)
    assert statuses == {TaskStatus.PENDING}


def test_extract_original_developer_invalid_format() -> None:
    """Invalid UUID format returns None even when prefix matches."""
    out = extract_original_developer("original_developer:not-a-uuid")
    assert out is None


def test_extract_original_developer_no_match() -> None:
    out = extract_original_developer("some other context")
    assert out is None


def test_extract_original_developer_empty() -> None:
    assert extract_original_developer(None) is None
    assert extract_original_developer("") is None


def test_extract_original_developer_valid() -> None:
    test_uuid = "12345678-1234-1234-1234-123456789012"
    out = extract_original_developer(f"original_developer:{test_uuid}")
    assert out == test_uuid


# ---------------------------------------------------------------------------
# _validate_parent_depth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_parent_depth_missing_parent_raises(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    with pytest.raises(ValueError, match="not found"):
        await svc._validate_parent_depth(uuid4())


@pytest.mark.asyncio
async def test_validate_parent_depth_circular_reference(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    """A self-referential parent loop raises ValueError."""
    svc = task_setup["svc"]
    a = await svc.create(_req(task_setup))
    b = await svc.create(_req(task_setup, parent_task_id=a.id))
    # Force circular: a.parent_task_id = b.id
    a.parent_task_id = b.id
    await db_session.flush()
    with pytest.raises(ValueError, match="Circular reference"):
        await svc._validate_parent_depth(a.id)


@pytest.mark.asyncio
async def test_validate_parent_depth_exceeds_max(
    task_setup: dict,
) -> None:
    """Adding a child past MAX_TASK_DEPTH raises ValueError."""
    svc = task_setup["svc"]
    # Build a chain of MAX_TASK_DEPTH+1 tasks
    parent = None
    for _ in range(MAX_TASK_DEPTH):
        new = await svc.create(
            _req(task_setup, parent_task_id=parent.id if parent else None)
        )
        parent = new
    assert parent is not None
    with pytest.raises(ValueError, match="MAX_TASK_DEPTH"):
        await svc._validate_parent_depth(parent.id)


# ---------------------------------------------------------------------------
# activate without project_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_without_project_id_raises(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tasks with no project_id can't be activated.

    Tests the defensive guard at line 550-554 by stubbing get() to return
    a task synthetically built with project_id=None. The DB column is
    NOT NULL so this code path can only be reached if the in-memory object
    is mutated post-fetch (which the activate flow does after the session
    check).
    """
    svc = task_setup["svc"]

    fake_task = MagicMock()
    fake_task.id = uuid4()
    fake_task.title = "fake"
    fake_task.status = TaskStatus.BACKLOG
    fake_task.project_id = None

    async def _stub_get(tid):
        del tid
        return fake_task

    monkeypatch.setattr(svc, "get", _stub_get)

    # Stub session.execute to return a session_link so we get past that gate
    fake_link = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = fake_link

    db = task_setup["db"]
    real_execute = db.execute

    async def _exec_stub(stmt, *a, **kw):
        compiled = str(stmt)
        if "session_tasks" in compiled.lower():
            return fake_result
        return await real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db, "execute", _exec_stub)
    with pytest.raises(ValueError, match="no project set"):
        await svc.activate(fake_task.id, agent_role="cell_pm")


# ---------------------------------------------------------------------------
# _ensure_branch_for_task / _auto_create_branch / _resolve_parent_branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_branch_returns_existing(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.branch_name = "feature/backend/EXISTING"
    out = await svc._ensure_branch_for_task(task, task_setup["agent_id"])
    assert out == "feature/backend/EXISTING"


@pytest.mark.asyncio
async def test_ensure_branch_no_project_id_raises(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # Cleared project_id via raw SQL won't work here — instead, mock task.project_id
    task.project_id = None
    with pytest.raises(ValueError, match="project_id"):
        await svc._ensure_branch_for_task(task, task_setup["agent_id"])


@pytest.mark.asyncio
async def test_auto_create_branch_no_project_raises(
    task_setup: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    fake_project_svc = MagicMock()
    fake_project_svc.get = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "roboco.services.project.get_project_service",
        lambda _s: fake_project_svc,
    )
    monkeypatch.setattr(
        "roboco.services.git.get_git_service",
        lambda _s: MagicMock(),
    )
    with pytest.raises(ValueError, match="not found"):
        await svc._auto_create_branch(task, task_setup["agent_id"])


@pytest.mark.asyncio
async def test_auto_create_branch_succeeds(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full happy path of _auto_create_branch."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()

    fake_project = MagicMock()
    fake_project.id = task_setup["project_id"]
    fake_project.slug = task_setup["project_slug"]
    fake_project.default_branch = "main"
    fake_project.assigned_cell = Team.BACKEND
    fake_project_svc = MagicMock()
    fake_project_svc.get = AsyncMock(return_value=fake_project)

    fake_git = MagicMock()
    fake_git.get_workspace = AsyncMock(return_value="/workspace")
    fake_git.create_branch = AsyncMock(return_value=("feature/backend/AAAAAAA", None))

    monkeypatch.setattr(
        "roboco.services.project.get_project_service",
        lambda _s: fake_project_svc,
    )
    monkeypatch.setattr("roboco.services.git.get_git_service", lambda _s: fake_git)
    out = await svc._auto_create_branch(task, task_setup["agent_id"])
    assert out == "feature/backend/AAAAAAA"
    assert task.branch_name == "feature/backend/AAAAAAA"


@pytest.mark.asyncio
async def test_find_ancestor_branch_walks_up(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    grand = await svc.create(_req(task_setup))
    grand.branch_name = "feature/backend/GRAND"
    parent = await svc.create(_req(task_setup, parent_task_id=grand.id))
    # Parent has no branch_name
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    out = await svc._find_ancestor_branch(child)
    assert out == "feature/backend/GRAND"


@pytest.mark.asyncio
async def test_find_ancestor_branch_handles_circular(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Circular reference returns None gracefully (just logs warning)."""
    svc = task_setup["svc"]
    a = await svc.create(_req(task_setup))
    b = await svc.create(_req(task_setup, parent_task_id=a.id))
    a.parent_task_id = b.id
    await db_session.flush()
    out = await svc._find_ancestor_branch(b)
    assert out is None


@pytest.mark.asyncio
async def test_find_ancestor_branch_returns_none_when_no_branches(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """When parent chain has no branches anywhere, returns None."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    out = await svc._find_ancestor_branch(child)
    assert out is None


@pytest.mark.asyncio
async def test_resolve_parent_branch_uses_default_when_no_ancestor(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    fake_project = MagicMock()
    fake_project.default_branch = "main"
    out = await svc._resolve_parent_branch(task, fake_project)
    assert out == "main"


@pytest.mark.asyncio
async def test_resolve_parent_branch_uses_ancestor(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    parent.branch_name = "feature/backend/PARENT"
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()
    fake_project = MagicMock()
    fake_project.default_branch = "main"
    out = await svc._resolve_parent_branch(child, fake_project)
    assert out == "feature/backend/PARENT"


# ---------------------------------------------------------------------------
# _resolve_team_dir branches
# ---------------------------------------------------------------------------


def test_resolve_team_dir_fullstack(task_setup: dict) -> None:
    svc = task_setup["svc"]
    fake_project = MagicMock()
    fake_project.assigned_cell = Team.FULLSTACK
    fake_project.slug = "p"
    task = MagicMock()
    task.team = Team.BACKEND
    out = svc._resolve_team_dir(fake_project, task)
    assert "p/backend" in out


def test_resolve_team_dir_fullstack_no_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    fake_project = MagicMock()
    fake_project.assigned_cell = Team.FULLSTACK
    fake_project.slug = "p"
    task = MagicMock()
    task.team = None
    out = svc._resolve_team_dir(fake_project, task)
    assert "p/cross" in out


def test_resolve_team_dir_task_team(task_setup: dict) -> None:
    svc = task_setup["svc"]
    fake_project = MagicMock()
    fake_project.assigned_cell = Team.BACKEND
    task = MagicMock()
    task.team = Team.FRONTEND
    out = svc._resolve_team_dir(fake_project, task)
    assert out == "frontend"


def test_resolve_team_dir_falls_back_to_project_cell(task_setup: dict) -> None:
    svc = task_setup["svc"]
    fake_project = MagicMock()
    fake_project.assigned_cell = Team.BACKEND
    task = MagicMock()
    task.team = None
    out = svc._resolve_team_dir(fake_project, task)
    assert out == "backend"


def test_resolve_team_dir_cross_when_nothing(task_setup: dict) -> None:
    svc = task_setup["svc"]
    fake_project = MagicMock()
    fake_project.assigned_cell = None
    task = MagicMock()
    task.team = None
    out = svc._resolve_team_dir(fake_project, task)
    assert out == "cross"


# ---------------------------------------------------------------------------
# _validate_claim_status — role-specific status missing agent
# ---------------------------------------------------------------------------


def test_validate_claim_status_no_agent_role_specific(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.status = TaskStatus.AWAITING_QA
    err = svc._validate_claim_status(task, agent=None, valid_statuses=set())
    assert err is not None
    assert "role required" in err


def test_validate_claim_status_invalid(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.status = TaskStatus.COMPLETED
    err = svc._validate_claim_status(
        task, agent=None, valid_statuses={TaskStatus.PENDING}
    )
    assert err == "invalid status for role"


def test_validate_claim_status_ok(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.status = TaskStatus.PENDING
    err = svc._validate_claim_status(
        task, agent=None, valid_statuses={TaskStatus.PENDING}
    )
    assert err is None


def test_validate_not_self_review_no_agent(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    out = svc._validate_not_self_review(task, agent=None, agent_id=uuid4())
    assert out is None


def test_validate_not_self_review_no_role(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    agent = MagicMock(role=None)
    out = svc._validate_not_self_review(task, agent, agent_id=uuid4())
    assert out is None


def test_validate_not_self_review_dev_role(task_setup: dict) -> None:
    """Dev role isn't QA/documenter so passes (returns None)."""
    svc = task_setup["svc"]
    task = MagicMock()
    agent = MagicMock(role=AgentRole.DEVELOPER)
    out = svc._validate_not_self_review(task, agent, agent_id=uuid4())
    assert out is None


def test_validate_not_self_review_qa_self(task_setup: dict) -> None:
    svc = task_setup["svc"]
    aid = uuid4()
    task = MagicMock()
    task.quick_context = f"original_developer:{aid}"
    agent = MagicMock(role=AgentRole.QA)
    out = svc._validate_not_self_review(task, agent, agent_id=aid)
    assert "self-review" in (out or "")


def test_set_original_developer_skips_when_already_set(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = "original_developer:already-set"
    task.assigned_to = uuid4()
    agent = MagicMock(role=AgentRole.QA, id=uuid4())
    # Should not change quick_context
    before = task.quick_context
    svc._set_original_developer_context(task, agent)
    assert task.quick_context == before


def test_set_original_developer_skips_when_no_role(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    agent = MagicMock(role=None)
    svc._set_original_developer_context(task, agent)


def test_set_original_developer_skips_when_dev_role(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = ""
    task.assigned_to = uuid4()
    agent = MagicMock(role=AgentRole.DEVELOPER, id=uuid4())
    svc._set_original_developer_context(task, agent)
    # dev role → early return, quick_context unchanged
    assert task.quick_context == ""


def test_set_original_developer_skips_when_self(task_setup: dict) -> None:
    svc = task_setup["svc"]
    aid = uuid4()
    task = MagicMock()
    task.quick_context = ""
    task.assigned_to = aid
    agent = MagicMock(role=AgentRole.QA, id=aid)
    svc._set_original_developer_context(task, agent)
    # Same agent → don't set
    assert task.quick_context == ""


# ---------------------------------------------------------------------------
# unclaim_for_reaper / unclaim_for_agent — TaskLifecycleError catches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unclaim_for_reaper_lifecycle_error_returns(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the lifecycle validator to raise TaskLifecycleError → return."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    def _raise(*_args, **_kwargs) -> None:
        raise TaskLifecycleError(
            current_status="claimed",
            target_status="pending",
            valid_transitions=[],
        )

    monkeypatch.setattr(svc, "_validate_and_set_status", _raise)
    # Should not raise — silently returns
    await svc.unclaim_for_reaper(task.id)


@pytest.mark.asyncio
async def test_unclaim_for_agent_lifecycle_error_returns_none(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    def _raise(*_args, **_kwargs) -> None:
        raise TaskLifecycleError(
            current_status="claimed",
            target_status="pending",
            valid_transitions=[],
        )

    monkeypatch.setattr(svc, "_validate_and_set_status", _raise)
    out = await svc.unclaim_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert out is None


@pytest.mark.asyncio
async def test_resume_for_agent_lifecycle_error_returns_none(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    async def _bad_resume(*_a, **_kw) -> None:
        raise TaskLifecycleError(
            current_status="paused",
            target_status="in_progress",
            valid_transitions=[],
        )

    monkeypatch.setattr(svc, "resume", _bad_resume)
    out = await svc.resume_for_agent(task.id, agent_id=task_setup["agent_id"])
    assert out is None


# ---------------------------------------------------------------------------
# docs_complete edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_complete_indexes_when_documents_present(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path: docs_complete spawns _index_docs_background when docs present."""
    svc = task_setup["svc"]
    doc = AgentTable(
        id=uuid4(),
        name="Doc",
        slug=f"be-doc-{uuid4().hex[:8]}",
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="d",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(doc)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_DOCUMENTATION
    task.assigned_to = doc.id
    task.pr_number = 1
    task.pr_url = "u"
    task.documents = [{"path": "doc1.md"}]
    await db_session.flush()
    fake_optimal = MagicMock()
    fake_optimal.index_documentation = AsyncMock(return_value=1)

    async def _get_optimal() -> Any:
        return fake_optimal

    monkeypatch.setattr("roboco.services.optimal.get_optimal_service", _get_optimal)
    out = await svc.docs_complete(task.id, doc_notes="docs done")
    assert out is not None
    await asyncio.sleep(0.05)


def test_record_doc_notes_skips_empty(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = "before"
    svc._record_doc_notes(task, None)
    assert task.quick_context == "before"
    svc._record_doc_notes(task, "")
    assert task.quick_context == "before"


def test_record_documenter_context_skips_no_assignee(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.assigned_to = None
    task.quick_context = "x"
    svc._record_documenter_context(task)
    assert task.quick_context == "x"


def test_record_documenter_context_skips_when_already(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.assigned_to = uuid4()
    task.quick_context = "documenter:something"
    before = task.quick_context
    svc._record_documenter_context(task)
    assert task.quick_context == before


def test_record_documenter_context_appends(task_setup: dict) -> None:
    svc = task_setup["svc"]
    aid = uuid4()
    task = MagicMock()
    task.assigned_to = aid
    task.quick_context = "existing"
    svc._record_documenter_context(task)
    assert "documenter:" in task.quick_context
    assert "existing" in task.quick_context


def test_record_documenter_context_first_entry(task_setup: dict) -> None:
    svc = task_setup["svc"]
    aid = uuid4()
    task = MagicMock()
    task.assigned_to = aid
    task.quick_context = None
    svc._record_documenter_context(task)
    assert "documenter:" in task.quick_context


def test_record_completion_notes_skips_empty(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = "before"
    svc._record_completion_notes(task, None)
    assert task.quick_context == "before"


def test_record_completion_notes_with_existing_context(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = "existing"
    svc._record_completion_notes(task, "merged successfully")
    assert "completion_notes:merged successfully" in task.quick_context


def test_record_completion_notes_no_existing_context(task_setup: dict) -> None:
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = None
    svc._record_completion_notes(task, "merged")
    assert task.quick_context.startswith("completion_notes:")


# ---------------------------------------------------------------------------
# _resolve_pm_for_review — direct candidate found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_pm_for_review_finds_first_assignee(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    pm_agent = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm_agent)
    await db_session.flush()
    parent = await svc.create(_req(task_setup, assigned_to=pm_agent.id))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    pm_id = await svc._resolve_pm_for_review(child)
    assert pm_id == pm_agent.id


# ---------------------------------------------------------------------------
# _get_completing_agent_role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_completing_agent_role_none_for_missing_agent_id(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc._get_completing_agent_role(None)
    assert out is None


@pytest.mark.asyncio
async def test_get_completing_agent_role_none_for_unknown(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc._get_completing_agent_role(uuid4())
    assert out is None


@pytest.mark.asyncio
async def test_get_completing_agent_role_returns_role(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc._get_completing_agent_role(task_setup["agent_id"])
    assert out == "developer"


# ---------------------------------------------------------------------------
# complete: main_pm root parent escalates to CEO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_main_pm_root_parent_escalates_ceo(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    main_pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(main_pm)
    await db_session.flush()
    parent = await svc.create(_req(task_setup))
    parent.status = TaskStatus.AWAITING_PM_REVIEW
    parent.pr_number = 1
    parent.pr_url = "u"
    parent.pr_created = True
    parent.docs_complete = True
    await db_session.flush()
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    child.status = TaskStatus.COMPLETED
    await db_session.flush()
    out = await svc.complete(parent.id, agent_id=main_pm.id)
    assert out is not None
    # Root parent + main_pm + descendants → escalate to CEO
    assert out.status == TaskStatus.AWAITING_CEO_APPROVAL


# ---------------------------------------------------------------------------
# complete: PR not merged returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_none_when_pr_not_merged(
    task_setup: dict, db_session: AsyncSession
) -> None:
    """Cell PM completing → escalate-to-Main PM is short-circuited so the
    PR-not-merged guard is reached. Strip leaked Main PMs from earlier tests
    that committed and survived rollback isolation.
    """
    svc = task_setup["svc"]
    await db_session.execute(
        AgentTable.__table__.update()
        .where(AgentTable.role == AgentRole.MAIN_PM)
        .values(role=AgentRole.SYSTEM)
    )
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"be-pm-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    ws = WorkSessionTable(
        id=uuid4(),
        project_id=task_setup["project_id"],
        task_id=task.id,
        agent_id=task_setup["agent_id"],
        branch_name="feature/backend/x",
        base_branch="main",
        target_branch="main",
        status=WorkSessionStatus.ACTIVE,
        pr_status="open",  # NOT merged
    )
    db_session.add(ws)
    await db_session.flush()
    task.work_session_id = ws.id
    await db_session.flush()
    out = await svc.complete(task.id, agent_id=pm.id)
    assert out is None


# ---------------------------------------------------------------------------
# list_by_team_or_assignee — no conditions returns []
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_team_or_assignee_no_conditions_empty(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_by_team_or_assignee(team=None, agent_id=None)
    assert rows == []


@pytest.mark.asyncio
async def test_list_by_team_or_assignee_with_status(task_setup: dict) -> None:
    svc = task_setup["svc"]
    rows = await svc.list_by_team_or_assignee(
        team=Team.BACKEND, agent_id=None, status=TaskStatus.PENDING
    )
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# escalate_to_ceo_for_agent — inner escalate returns None → ValidationError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalate_to_ceo_for_agent_inner_returns_none(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_PM_REVIEW
    task.pr_number = 1
    task.pr_url = "u"
    task.pr_created = True
    task.docs_complete = True
    await db_session.flush()
    agent_ctx = AgentContext(
        agent_id=task_setup["agent_id"],
        role=AgentRole.MAIN_PM,
        team=Team.MAIN_PM,
        slug="x",
    )

    class _P:
        def can_perform_task_action(self, *a, **kw) -> bool:
            del a, kw
            return True

    # Force the inner escalate_to_ceo to return None
    object.__setattr__(svc, "escalate_to_ceo", AsyncMock(return_value=None))
    with pytest.raises(ValidationError, match="awaiting_pm_review"):
        await svc.escalate_to_ceo_for_agent(
            task.id,
            agent_ctx,
            _P(),
            "Substantial reasons for CEO review of breaking changes",
        )


# ---------------------------------------------------------------------------
# mark_agent_idle — missing agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_agent_idle_missing_agent_no_op(task_setup: dict) -> None:
    svc = task_setup["svc"]
    # Should not raise
    await svc.mark_agent_idle(uuid4())


# ---------------------------------------------------------------------------
# qa_fail — actor mismatch logs warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_fail_actor_mismatch_warning(
    task_setup: dict, db_session: AsyncSession
) -> None:
    svc = task_setup["svc"]
    qa_a = AgentTable(
        id=uuid4(),
        name="QA-A",
        slug=f"qa-a-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    qa_b = AgentTable(
        id=uuid4(),
        name="QA-B",
        slug=f"qa-b-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="qa",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add_all([qa_a, qa_b])
    await db_session.flush()
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.AWAITING_QA
    task.claimed_by = qa_a.id  # Different from qa_b
    await db_session.flush()
    out = await svc.qa_fail(qa_b.id, task.id, "needs work", issues=["x"])
    assert out is not None  # warning logged but flow continues


# ---------------------------------------------------------------------------
# cell_pm_complete — missing task returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_missing_task_returns_none(
    task_setup: dict,
) -> None:
    svc = task_setup["svc"]
    out = await svc.cell_pm_complete(
        task_setup["agent_id"], uuid4(), "merged", merge_commit="x"
    )
    assert out is None


# ---------------------------------------------------------------------------
# get_task_service factory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_service_returns_instance(
    db_session: AsyncSession,
) -> None:
    out = get_task_service(db_session)
    assert isinstance(out, TaskService)


# ---------------------------------------------------------------------------
# Final coverage gaps (line-specific)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_branch_calls_auto_create(
    task_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover line 595: _ensure_branch_for_task → _auto_create_branch."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    # No branch_name set → falls through to auto-create

    async def _stub(_t, _a) -> str:
        return "feature/backend/MOCK"

    monkeypatch.setattr(svc, "_auto_create_branch", _stub)
    out = await svc._ensure_branch_for_task(task, task_setup["agent_id"])
    assert out == "feature/backend/MOCK"


@pytest.mark.asyncio
async def test_find_ancestor_branch_break_when_parent_missing(
    task_setup: dict, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover line 622: break when parent lookup returns None."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()

    real_get = svc.get

    async def _stub_get(tid):
        # Return None when looking up the parent
        if tid == parent.id:
            return None
        return await real_get(tid)

    monkeypatch.setattr(svc, "get", _stub_get)
    out = await svc._find_ancestor_branch(child)
    assert out is None


def test_validate_claim_team_no_agent(task_setup: dict) -> None:
    """Cover line 838: _validate_claim_team early return when no agent."""
    svc = task_setup["svc"]
    task = MagicMock()
    task.team = Team.BACKEND
    out = svc._validate_claim_team(task, agent=None)
    assert out is None


def test_validate_not_self_review_qa_with_different_dev(task_setup: dict) -> None:
    """Cover line 866: _validate_not_self_review returns None when QA differs."""
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = f"original_developer:{uuid4()}"
    agent = MagicMock(role=AgentRole.QA)
    out = svc._validate_not_self_review(task, agent, agent_id=uuid4())
    assert out is None


def test_set_original_developer_records_when_different(task_setup: dict) -> None:
    """Cover line 889: sets quick_context when assigned_to != agent.id."""
    svc = task_setup["svc"]
    task = MagicMock()
    task.quick_context = ""
    other_id = uuid4()
    task.assigned_to = other_id
    agent = MagicMock(role=AgentRole.QA, id=uuid4())
    svc._set_original_developer_context(task, agent)
    assert "original_developer:" in task.quick_context
    assert str(other_id) in task.quick_context


@pytest.mark.asyncio
async def test_finalize_claim_refreshes_after_branch_creation(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover line 998: session.refresh after _ensure_branch_for_task succeeds."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    await db_session.flush()

    async def _ensure_branch(_t, _a) -> str:
        _t.branch_name = "feature/backend/X"
        return "feature/backend/X"

    monkeypatch.setattr(svc, "_ensure_branch_for_task", _ensure_branch)
    refresh_mock = AsyncMock()
    monkeypatch.setattr(svc.session, "refresh", refresh_mock)
    claimed = await svc.claim(task.id, task_setup["agent_id"])
    assert claimed is not None
    refresh_mock.assert_awaited()


@pytest.mark.asyncio
async def test_resolve_pm_for_review_returns_none_when_chain_broken(
    task_setup: dict,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover line 2650: parent lookup returns None mid-chain."""
    svc = task_setup["svc"]
    parent = await svc.create(_req(task_setup))
    child = await svc.create(_req(task_setup, parent_task_id=parent.id))
    await db_session.flush()

    real_get = svc.get

    async def _stub_get(tid):
        # Return None when looking up the parent
        if tid == parent.id:
            return None
        return await real_get(tid)

    monkeypatch.setattr(svc, "get", _stub_get)
    out = await svc._resolve_pm_for_review(child)
    assert out is None


@pytest.mark.asyncio
async def test_unclaim_for_agent_works_with_uuid_round_trip(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    """Regression for 2026-05-08: main-pm got 'not your claim' on its OWN
    claim. Pin the UUID-comparator behavior: same agent_id in (whether
    fresh UUID or string-coerced UUID round-trip), unclaim succeeds.
    """
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.CLAIMED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    # Round-trip the UUID through string → UUID to mirror what an HTTP
    # request body / header would do via Pydantic. The comparator must
    # treat both the freshly-constructed and the round-tripped UUID as
    # equal (which Python's UUID class does — pinning so a future move
    # to a custom comparator can't silently break it).
    same_uuid_via_str = UUID(str(task_setup["agent_id"]))
    out = await svc.unclaim_for_agent(task.id, agent_id=same_uuid_via_str)
    assert out is not None, "round-tripped UUID rejected — comparator broken"
    assert out.assigned_to is None
    assert out.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_resume_for_agent_works_with_uuid_round_trip(
    task_setup: dict,
    db_session: AsyncSession,
) -> None:
    """Mirror of the unclaim regression for resume_for_agent."""
    svc = task_setup["svc"]
    task = await svc.create(_req(task_setup))
    task.status = TaskStatus.PAUSED
    task.assigned_to = task_setup["agent_id"]
    await db_session.flush()

    same_uuid_via_str = UUID(str(task_setup["agent_id"]))
    out = await svc.resume_for_agent(task.id, agent_id=same_uuid_via_str)
    assert out is not None, "round-tripped UUID rejected — comparator broken"
    assert out.status == TaskStatus.IN_PROGRESS
