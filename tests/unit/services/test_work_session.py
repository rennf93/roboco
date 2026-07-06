"""Unit tests for WorkSessionService gateway-backfill methods."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable, WorkSessionTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.work_session import WorkSessionCreate, WorkSessionStatus
from roboco.services.base import ConflictError
from roboco.services.work_session import WorkSessionService, get_work_session_service


def _service() -> WorkSessionService:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return WorkSessionService(session)


def _bind(svc: WorkSessionService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


@pytest.mark.asyncio
async def test_files_changed_returns_files_modified_list() -> None:
    svc = _service()
    fake_session = MagicMock(files_modified=["roboco/api/app.py", "README.md"])
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    out = await svc.files_changed(uuid4())
    assert out == ["roboco/api/app.py", "README.md"]


@pytest.mark.asyncio
async def test_files_changed_returns_empty_list_when_session_missing() -> None:
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=None))
    assert await svc.files_changed(uuid4()) == []


@pytest.mark.asyncio
async def test_files_changed_handles_none_files_modified() -> None:
    svc = _service()
    fake_session = MagicMock(files_modified=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.files_changed(uuid4()) == []


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_no_commits() -> None:
    svc = _service()
    fake_session = MagicMock(commits=[], pr_number=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is False


@pytest.mark.asyncio
async def test_has_unpushed_commits_true_when_commits_but_no_pr() -> None:
    svc = _service()
    fake_session = MagicMock(commits=["abc", "def"], pr_number=None)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is True


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_pr_exists() -> None:
    svc = _service()
    fake_session = MagicMock(commits=["abc"], pr_number=42)
    _bind(svc, "get", AsyncMock(return_value=fake_session))
    assert await svc.has_unpushed_commits(uuid4()) is False


@pytest.mark.asyncio
async def test_has_unpushed_commits_false_when_session_missing() -> None:
    svc = _service()
    _bind(svc, "get", AsyncMock(return_value=None))
    assert await svc.has_unpushed_commits(uuid4()) is False


# ---------------------------------------------------------------------------
# merge_pr must be idempotent + active-guarded like close()/complete()
# ---------------------------------------------------------------------------


def _merge_service() -> tuple[WorkSessionService, MagicMock]:
    """A service whose session flush is observable. Returns ``(svc, session)`` so
    the tests can assert that a guarded no-op never reached ``flush``."""
    session = MagicMock()
    session.flush = AsyncMock()
    svc = WorkSessionService(session)
    return svc, session


@pytest.mark.asyncio
async def test_merge_pr_completes_active_session() -> None:
    """Happy path: an ACTIVE session is recorded as merged + COMPLETED."""

    original_merger = uuid4()
    ws = MagicMock(
        status=WorkSessionStatus.ACTIVE, pr_number=42, branch_name="feature/x"
    )
    svc, session = _merge_service()
    _bind(svc, "get", AsyncMock(return_value=ws))

    result = await svc.merge_pr(uuid4(), original_merger)

    assert result is ws
    assert ws.pr_status == "merged"
    assert ws.merged_by == original_merger
    assert ws.pr_merged_at is not None
    assert ws.status == WorkSessionStatus.COMPLETED
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_pr_idempotent_on_already_completed_preserves_audit_trail() -> None:
    """A retried merge after a successful-but-unconfirmed GitHub merge must NOT
    overwrite the original ``merged_by`` / ``pr_merged_at`` — the merge audit
    trail is preserved. Mirrors close()'s idempotency guard."""

    original_merger = uuid4()
    original_ts = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    retry_merger = uuid4()
    ws = MagicMock(
        status=WorkSessionStatus.COMPLETED,
        pr_number=42,
        merged_by=original_merger,
        pr_merged_at=original_ts,
        pr_status="merged",
    )
    svc, session = _merge_service()
    _bind(svc, "get", AsyncMock(return_value=ws))

    result = await svc.merge_pr(uuid4(), retry_merger)

    assert result is ws
    # Audit trail untouched — retry did not overwrite the original merger/ts.
    assert ws.merged_by == original_merger
    assert ws.pr_merged_at == original_ts
    assert ws.status == WorkSessionStatus.COMPLETED
    # No mutation happened, so flush must not have run.
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_pr_does_not_resurrect_abandoned_session() -> None:
    """``merge_pr`` on an ABANDONED session must NOT flip it to COMPLETED — that
    would silently undo the single-active invariant's abandonment and make
    discarded work look like a successful merge."""

    ws = MagicMock(status=WorkSessionStatus.ABANDONED, pr_number=42, merged_by=None)
    svc, session = _merge_service()
    _bind(svc, "get", AsyncMock(return_value=ws))

    result = await svc.merge_pr(uuid4(), uuid4())

    assert result is ws
    # Not resurrected — stays abandoned, no merger recorded.
    assert ws.status == WorkSessionStatus.ABANDONED
    assert ws.merged_by is None
    session.flush.assert_not_awaited()


# ---------------------------------------------------------------------------
# H10: create() must translate IntegrityError -> ConflictError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_translates_integrity_error_to_conflict(
    db_session, monkeypatch
) -> None:
    """Two agents racing a same-task active-session create: the 047 partial-
    unique index fires IntegrityError on the loser's flush. The service must
    re-raise as ConflictError so the choreographer's try/except lands a clean
    invalid_state envelope instead of a 500."""
    agent = AgentTable(
        id=uuid4(),
        name="A",
        slug=f"a-{uuid4().hex[:8]}",
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
        name="p",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="git@x:y/z.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    tid = uuid4()
    db_session.add(
        TaskTable(
            id=tid,
            title="t",
            description="d",
            acceptance_criteria=["done"],
            status=TaskStatus.IN_PROGRESS,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            team=Team.BACKEND,
            confirmed_by_human=True,
            project_id=project.id,
            created_by=agent.id,
            branch_name="feature/x",
        )
    )
    await db_session.flush()

    existing = WorkSessionTable(
        id=uuid4(),
        project_id=project.id,
        task_id=tid,
        agent_id=agent.id,
        branch_name="feature/x",
        base_branch="master",
        target_branch="master",
        status=WorkSessionStatus.ACTIVE,
    )
    db_session.add(existing)
    await db_session.flush()

    svc = get_work_session_service(db_session)

    async def _none(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "get_active_for_task_and_agent", _none)

    with pytest.raises(ConflictError):
        await svc.create(
            WorkSessionCreate(
                project_id=project.id,
                task_id=tid,
                agent_id=agent.id,
                branch_name="feature/x",
                base_branch="master",
                target_branch="master",
            )
        )
