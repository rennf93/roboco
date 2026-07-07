"""Unit tests for WorkSessionService gateway-backfill methods."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable, WorkSessionTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType
from roboco.models.work_session import WorkSessionCreate, WorkSessionStatus
from roboco.services.base import ConflictError
from roboco.services.work_session import WorkSessionService, get_work_session_service
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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


def _merge_service(
    ws: object | None = MagicMock(),
) -> tuple[WorkSessionService, MagicMock, AsyncMock]:
    """A service whose session.execute returns ``ws`` via scalar_one_or_none and
    whose flush is observable. Returns ``(svc, session, execute)`` so the tests
    can assert on the lock SELECT and that a guarded no-op never reached flush.
    """
    session = MagicMock()
    execute = AsyncMock()
    if ws is not None:
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        execute.return_value = result
    session.execute = execute
    session.flush = AsyncMock()
    svc = WorkSessionService(session)
    return svc, session, execute


@pytest.mark.asyncio
async def test_merge_pr_completes_active_session() -> None:
    """Happy path: an ACTIVE session is recorded as merged + COMPLETED."""

    original_merger = uuid4()
    ws = MagicMock(
        status=WorkSessionStatus.ACTIVE, pr_number=42, branch_name="feature/x"
    )
    svc, session, _execute = _merge_service(ws)

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
    svc, session, _execute = _merge_service(ws)

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
    svc, session, _execute = _merge_service(ws)

    result = await svc.merge_pr(uuid4(), uuid4())

    assert result is ws
    # Not resurrected — stays abandoned, no merger recorded.
    assert ws.status == WorkSessionStatus.ABANDONED
    assert ws.merged_by is None
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_pr_locks_work_session_row_with_for_update() -> None:
    """The work_session SELECT must carry FOR UPDATE so concurrent merge_pr
    calls serialize. Mirrors the pr_merge parent-lock assertion in
    test_pr_merge_concurrency.py."""
    ws = MagicMock(
        status=WorkSessionStatus.ACTIVE, pr_number=42, branch_name="feature/x"
    )
    svc, _session, execute = _merge_service(ws)

    await svc.merge_pr(uuid4(), uuid4())

    stmt = execute.await_args.args[0]
    assert getattr(stmt, "_for_update_arg", None) is not None


# ---------------------------------------------------------------------------
# M37: two concurrent merge_pr calls on the same ACTIVE session must serialize
# — exactly one writes merged_by / pr_merged_at. Exercises real Postgres row
# locking with two separate sessions on asyncio.gather.
# ---------------------------------------------------------------------------


async def _seed_active_session(
    db_session: AsyncSession,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    """Seed project + 3 agents + task + ACTIVE work_session; commit and return
    (ws_id, worker_agent_id, merger_a_id, merger_b_id, project_id). The two
    merger agents exist in the agents table so the merged_by FK is satisfied."""
    worker = AgentTable(
        id=uuid4(),
        name="worker",
        slug=f"worker-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    merger_a = AgentTable(
        id=uuid4(),
        name="merger-a",
        slug=f"merger-a-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    merger_b = AgentTable(
        id=uuid4(),
        name="merger-b",
        slug=f"merger-b-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    for a in (worker, merger_a, merger_b):
        db_session.add(a)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="p",
        slug=f"p-{uuid4().hex[:6]}",
        git_url="git@x:y/z.git",
        assigned_cell=Team.BACKEND,
        created_by=worker.id,
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
            created_by=worker.id,
            branch_name="feature/x",
        )
    )
    await db_session.flush()
    ws_id = uuid4()
    db_session.add(
        WorkSessionTable(
            id=ws_id,
            project_id=project.id,
            task_id=tid,
            agent_id=worker.id,
            branch_name="feature/x",
            base_branch="master",
            target_branch="master",
            status=WorkSessionStatus.ACTIVE,
        )
    )
    await db_session.flush()
    await db_session.commit()
    return ws_id, worker.id, merger_a.id, merger_b.id, project.id


@pytest.mark.asyncio
async def test_merge_pr_concurrent_calls_serialize_one_write(
    db_session: AsyncSession, _test_database_url: str
) -> None:
    """Two concurrent merge_pr calls on the same ACTIVE session: only one
    writes merged_by / pr_merged_at; the other blocks on the row lock, then
    sees status=COMPLETED and no-ops."""
    (
        ws_id,
        _worker_id,
        merger_a_id,
        merger_b_id,
        _project_id,
    ) = await _seed_active_session(db_session)

    engine = create_async_engine(_test_database_url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    merger_a = merger_a_id
    merger_b = merger_b_id

    async def _call(merger: UUID) -> tuple[object, UUID]:
        async with factory() as sess:
            svc = get_work_session_service(sess)
            result = await svc.merge_pr(ws_id, merger)
            await sess.commit()
            return result, merger

    # Both fire together: without FOR UPDATE, each session's SELECT sees the
    # last committed (ACTIVE) row and both write their own merger. With FOR
    # UPDATE, one caller's SELECT blocks on the other's row lock until it
    # commits, then reads COMPLETED and no-ops — exactly one merger is
    # recorded. Which caller wins the lock is non-deterministic, so the
    # assertions below check the invariant, not the winner.
    res_a, res_b = await asyncio.gather(_call(merger_a), _call(merger_b))
    await engine.dispose()

    a_row, a_merger = res_a
    b_row, b_merger = res_b

    # Both resolved COMPLETED; both report the same committed merger.
    assert a_row is not None
    assert b_row is not None
    assert a_row.status == b_row.status == WorkSessionStatus.COMPLETED
    winner = a_row.merged_by
    assert winner in (a_merger, b_merger)
    assert b_row.merged_by == winner


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
