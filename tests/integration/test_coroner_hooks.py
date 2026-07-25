"""Coroner (Board Program) event hooks fired through the REAL service
transition paths — bounce >=3x (TaskService._emit_status_transition_audit,
via a real _validate_and_set_status transition) and cancel-after-work-started
(TaskService.cancel). Every hook must be best-effort: a transition succeeds
even when the hook itself raises."""

from __future__ import annotations

import asyncio
import contextlib
import types
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Complexity, Team
from roboco.models.base import TaskNature as TN
from roboco.models.base import TaskStatus as TS
from roboco.models.base import TaskType as TT
from roboco.services import coroner_engine as coroner_engine_module
from roboco.services import task as task_module
from roboco.services.task import (
    CORONER_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    TaskCreateRequest,
    TaskService,
    _fire_coroner_bounce_hook,
    get_task_service,
)
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid
ONE = 1
THIRD_BOUNCE = 3


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    await db_session.execute(
        delete(SystemSettingTable).where(SystemSettingTable.key.like("board_program.%"))
    )
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.execute(
        update(TaskTable)
        .where(
            TaskTable.source.in_(
                [
                    ROADMAP_SOURCE,
                    X_FEATURE_EXPLORATION_SOURCE,
                    PEST_CONTROL_SOURCE,
                    CORONER_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> ProjectTable:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (AUDITOR_UUID, "auditor", AgentRole.AUDITOR, Team.BOARD),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    project = ProjectTable(
        name="Backend Service",
        slug=f"backend-svc-{uuid4().hex[:6]}",
        git_url=f"https://github.com/x/{uuid4().hex[:6]}.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
    )
    session.add(project)
    await session.flush()
    return project


def _arm(session: AsyncSession) -> None:
    session.add(SystemSettingTable(key="board_program.coroner.enabled", value="true"))


async def _make_task(
    session: AsyncSession, project: ProjectTable, *, status: TS = TS.PENDING
) -> TaskTable:
    """A real dev task, with its ``cell_projects`` relationship eagerly
    (re)loaded before returning: ``_validate_and_set_status`` reads it
    synchronously (``bool(task.cell_projects)``), and a task fresh off
    ``create()`` can otherwise leave it in a state that needs a lazy DB
    load outside any active greenlet the next time it's touched — a
    ``MissingGreenlet`` error, not a real business-logic failure."""
    task = await get_task_service(session).create(
        TaskCreateRequest(
            title="A real dev task",
            description="Something a dev worked on",
            acceptance_criteria=["it works"],
            team=Team.BACKEND,
            created_by=SYSTEM_UUID,
            task_type=TT.CODE,
            nature=TN.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=cast("UUID", project.id),
            status=TS.PENDING,
        )
    )
    if status != TS.PENDING:
        task.status = status
    await session.flush()
    return (
        await session.execute(
            select(TaskTable)
            .options(selectinload(TaskTable.cell_projects))
            .where(TaskTable.id == task.id)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_bounce_hook_schedules_only_at_the_third_bounce(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chokepoint (_emit_status_transition_audit) schedules
    ``_fire_coroner_bounce_hook`` via ``asyncio.create_task`` — mock the
    module-level hook function itself (a narrow, single-name patch that
    leaves the real ``asyncio`` module and every other caller of
    ``create_task`` — including session-teardown internals — untouched) and
    let the REAL ``create_task`` schedule the mock's coroutine normally."""
    calls: list[Any] = []

    async def _fake_hook(task_id: Any) -> None:
        calls.append(task_id)

    monkeypatch.setattr(task_module, "_fire_coroner_bounce_hook", _fake_hook)

    project = await _seed(db_session)
    _arm(db_session)
    task = await _make_task(db_session, project, status=TS.IN_PROGRESS)
    await db_session.flush()

    svc = TaskService(db_session)
    # 1st and 2nd bounce: no autopsy yet.
    svc._validate_and_set_status(task, TS.NEEDS_REVISION, "qa")
    task.status = TS.IN_PROGRESS  # re-enter from a different status
    svc._validate_and_set_status(task, TS.NEEDS_REVISION, "qa")
    task.status = TS.IN_PROGRESS
    await asyncio.sleep(0)
    assert calls == []

    # 3rd bounce: schedules the hook.
    svc._validate_and_set_status(task, TS.NEEDS_REVISION, "qa")
    await db_session.flush()
    assert task.revision_count == THIRD_BOUNCE
    await asyncio.sleep(0)  # let the scheduled task actually run

    assert calls == [task.id]


@pytest.mark.asyncio
async def test_bounce_hook_opens_its_own_session_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_fire_coroner_bounce_hook``'s own responsibility — open a FRESH
    session via ``get_db_context()``, delegate to ``CoronerEngine.
    open_for_incident``, commit. Verified with mocks (never a real
    ``get_db_context`` redirect to the shared ``db_session``): the hook's own
    ``await db.commit()`` would otherwise durably commit whatever this test
    session had pending — including the OTHER real rows this file's other
    tests create — permanently polluting the shared, cross-test-persistent
    test DB for every test that runs after this one in the same session.
    ``CoronerEngine.open_for_incident``'s actual behavior is covered by
    test_coroner_engine.py's dedicated real-DB tests; this is pure wiring."""
    fake_session = AsyncMock()

    @contextlib.asynccontextmanager
    async def _fake_db_context() -> Any:
        yield fake_session

    monkeypatch.setattr("roboco.db.base.get_db_context", _fake_db_context)

    engine = AsyncMock()
    engine.open_for_incident = AsyncMock(return_value=None)
    monkeypatch.setattr(
        coroner_engine_module, "get_coroner_engine", lambda _session: engine
    )

    task_id = uuid4()
    await _fire_coroner_bounce_hook(task_id)

    engine.open_for_incident.assert_awaited_once_with(task_id, kind="bounced")
    fake_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_bounce_hook_never_fails_the_transition_even_if_it_raises(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort: a hook scheduling failure never breaks the real bounce
    transition that triggered it. Swaps out the ``asyncio`` NAME BINDING
    ``roboco.services.task`` sees (not the shared ``asyncio`` module's own
    ``create_task`` attribute) so this stays scoped to that one module and
    can't break session-teardown machinery elsewhere in the same process."""

    def _raise_create_task(coro: Any, *_a: object, **_kw: object) -> object:
        coro.close()  # avoid a "coroutine was never awaited" warning
        raise RuntimeError("event loop trouble")

    fake_asyncio = types.SimpleNamespace(create_task=_raise_create_task)
    monkeypatch.setattr(task_module, "asyncio", fake_asyncio)

    project = await _seed(db_session)
    _arm(db_session)
    task = await _make_task(db_session, project, status=TS.IN_PROGRESS)
    task.revision_count = 2
    await db_session.flush()

    svc = TaskService(db_session)
    # Must NOT raise even though scheduling the hook blows up — the real
    # transition (status + revision_count bump + audit row) still lands.
    svc._validate_and_set_status(task, TS.NEEDS_REVISION, "qa")
    assert task.status == TS.NEEDS_REVISION
    assert task.revision_count == THIRD_BOUNCE


@pytest.mark.asyncio
async def test_cancel_after_work_started_opens_autopsy(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    _arm(db_session)
    task = await _make_task(db_session, project, status=TS.IN_PROGRESS)
    task.commits = [{"sha": "abc123", "message": "did work"}]
    await db_session.flush()

    svc = TaskService(db_session)
    await svc.cancel(cast("UUID", task.id), agent_role="cell_pm")
    await db_session.flush()

    cycles = (
        (
            await db_session.execute(
                select(TaskTable).where(
                    TaskTable.source == CORONER_SOURCE, TaskTable.status == TS.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(cycles) == ONE


@pytest.mark.asyncio
async def test_cancel_with_no_work_started_opens_no_autopsy(
    db_session: AsyncSession,
) -> None:
    project = await _seed(db_session)
    _arm(db_session)
    task = await _make_task(db_session, project, status=TS.PENDING)
    await db_session.flush()

    svc = TaskService(db_session)
    await svc.cancel(cast("UUID", task.id), agent_role="cell_pm")
    await db_session.flush()

    cycles = (
        (
            await db_session.execute(
                select(TaskTable).where(
                    TaskTable.source == CORONER_SOURCE, TaskTable.status == TS.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert cycles == []


@pytest.mark.asyncio
async def test_cancel_hook_never_fails_the_cancel_when_coroner_engine_raises(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _seed(db_session)
    _arm(db_session)
    task = await _make_task(db_session, project, status=TS.IN_PROGRESS)
    task.commits = [{"sha": "abc123", "message": "did work"}]
    await db_session.flush()

    def _boom(_session: AsyncSession) -> object:
        raise RuntimeError("coroner engine trouble")

    monkeypatch.setattr(coroner_engine_module, "get_coroner_engine", _boom)

    svc = TaskService(db_session)
    result = await svc.cancel(cast("UUID", task.id), agent_role="cell_pm")
    assert result is not None
    assert result.status == TS.CANCELLED
