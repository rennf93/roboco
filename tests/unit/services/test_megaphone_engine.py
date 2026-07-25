"""Megaphone engine: originate ONE held exploration cycle, deduped, never
authors content itself, and — unlike PestControlEngine — needs no
per-project opt-in to RUN (org-scoped: it reads the org's own shipped-task/
changelog history, not a repo). ALSO gated on X credentials being configured
(materialized drafts land in the X post queue; drafting for nobody to post
is pointless) — the one guard PeriscopeEngine (a report, no X queue) doesn't
carry.

Mirrors test_periscope_engine.py: like periscope, the exploration task's
``project_id`` still resolves against the RoboCo project (a hard
TaskService._require_target_or_umbrella invariant every non-coordination
task carries) even though the program itself needs no per-project opt-in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
    XCredentialsTable,
)
from roboco.foundation import identity as _foundation
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskType,
    Team,
)
from roboco.models.base import TaskStatus as TS
from roboco.services.megaphone_engine import MegaphoneEngine
from roboco.services.task import (
    MEGAPHONE_SOURCE,
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    TaskCreateRequest,
    get_task_service,
)
from roboco.services.workspace import WorkspaceService
from roboco.services.x_credentials import get_x_credentials_service
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
HOM_UUID = _foundation.AGENTS["head-marketing"].uuid
SLUG = "roboco"
ONE = 1


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """See test_board_program_engine.py's identical fixture: Board Program
    settings-store rows / ledger rows / open exploration tasks are shared,
    cross-test-persistent DB state. Purge before every test in this file.

    Also clears the ``x_credentials`` singleton row (a real committing HTTP
    route elsewhere in the full suite can leave one behind — see
    test_video_routes.py's identical warning): the credentials gate is a
    genuinely global-scope read by design, so a "nothing configured"
    assertion in this file must not depend on collection order elsewhere in
    a 14000+-test run against one shared Postgres. A raw DELETE is safe here
    (a standalone singleton row, no FK dependents) — unlike the completed-
    task digest below, which is scoped via monkeypatch instead of a DELETE
    to avoid touching other tests' TaskTable rows (and their FK-dependent
    audit/journal/work-session rows) at all."""
    await db_session.execute(
        delete(SystemSettingTable).where(SystemSettingTable.key.like("board_program.%"))
    )
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.execute(delete(XCredentialsTable))
    await db_session.execute(
        update(TaskTable)
        .where(
            TaskTable.source.in_(
                [
                    ROADMAP_SOURCE,
                    X_FEATURE_EXPLORATION_SOURCE,
                    PEST_CONTROL_SOURCE,
                    PERISCOPE_SOURCE,
                    MEGAPHONE_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (HOM_UUID, "head-marketing", AgentRole.HEAD_MARKETING, Team.BOARD),
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
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=SLUG,
            git_url="https://github.com/x/roboco.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


async def _grant_credentials(session: AsyncSession) -> None:
    await get_x_credentials_service(session).set_credentials(
        api_key="k",
        api_secret="s",
        access_token="t",
        access_token_secret="ts",
    )


async def _arm(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    session.add(SystemSettingTable(key="board_program.megaphone.enabled", value="true"))
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    await _grant_credentials(session)


@pytest.mark.asyncio
async def test_disabled_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    await _grant_credentials(db_session)
    await db_session.flush()
    engine = MegaphoneEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_megaphone_cycles() == []


@pytest.mark.asyncio
async def test_no_legacy_flag_backdoor(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No settings-store row at all = disabled — there is no legacy env flag
    for megaphone to fall back to (unlike roadmap/x_feature)."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    await _grant_credentials(db_session)
    await db_session.flush()
    engine = MegaphoneEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_no_credentials_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Armed but no X credentials configured — drafting content nobody can
    ever post is pointless (mirrors XEngine's identical spotlight guard)."""
    await _seed(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.megaphone.enabled", value="true")
    )
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    await db_session.flush()
    engine = MegaphoneEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_megaphone_cycles() == []


@pytest.mark.asyncio
async def test_enabled_originates_held_exploration_task(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    await _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = MegaphoneEngine(db_session)
    task = await engine.run_cycle()
    assert task is not None

    open_cycles = await get_task_service(db_session).list_open_megaphone_cycles()
    assert len(open_cycles) == ONE
    cycle = open_cycles[0]
    assert cycle.status == TS.PENDING
    assert cycle.confirmed_by_human is False  # HELD; board-dispatched only
    assert cycle.assigned_to == HOM_UUID
    assert cycle.team == Team.BOARD
    assert cycle.source == MEGAPHONE_SOURCE
    assert "Megaphone" in cycle.title


@pytest.mark.asyncio
async def test_dedupe_one_open_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    await _arm(db_session, monkeypatch)
    await db_session.flush()
    await MegaphoneEngine(db_session).run_cycle()
    second = await MegaphoneEngine(db_session).run_cycle()
    assert second is None
    assert len(await get_task_service(db_session).list_open_megaphone_cycles()) == ONE


@pytest.mark.asyncio
async def test_settings_store_false_creates_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    await _grant_credentials(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.megaphone.enabled", value="false")
    )
    await db_session.flush()
    engine = MegaphoneEngine(db_session)
    assert await engine.run_cycle() is None


@pytest.mark.asyncio
async def test_unresolvable_project_no_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    db_session.add(
        SystemSettingTable(key="board_program.megaphone.enabled", value="true")
    )
    monkeypatch.setattr(cfg, "self_heal_project_slug", "no-such-project")
    await _grant_credentials(db_session)
    await db_session.flush()
    engine = MegaphoneEngine(db_session)
    assert await engine.run_cycle() is None
    assert await get_task_service(db_session).list_open_megaphone_cycles() == []


@pytest.mark.asyncio
async def test_a_completed_cycle_unblocks_the_next_one(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    await _arm(db_session, monkeypatch)
    await db_session.flush()
    first = await MegaphoneEngine(db_session).run_cycle()
    assert first is not None
    first.status = TS.COMPLETED
    await db_session.flush()

    second = await MegaphoneEngine(db_session).run_cycle()
    assert second is not None
    assert second.id != first.id


# --------------------------------------------------------------------------- #
# digest_context — server-assembled shipped-this-week + CHANGELOG digest
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_digest_context_empty_shipped_says_so(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_shipped_this_week`` is monkeypatched rather than relying on a truly
    empty DB — this suite runs inside a 14000+-test full-suite pass against
    one shared Postgres, where a completed task from an unrelated test file
    can legitimately land inside the real query's 7-day window (a raw DELETE
    across all TaskTable rows would be needed to rule that out, and that
    risks FK failures against other tests' audit/journal/work-session rows —
    see the purge fixture's docstring). This isolates the render logic under
    test from that query."""
    monkeypatch.setattr(
        MegaphoneEngine, "_shipped_this_week", AsyncMock(return_value=[])
    )
    context = await MegaphoneEngine(db_session).digest_context()
    assert "nothing completed" in context
    assert "not available this cycle" in context


@pytest.mark.asyncio
async def test_digest_context_lists_completed_tasks_this_week(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    task = await get_task_service(db_session).create(
        TaskCreateRequest(
            title="Ship the thing",
            description="x",
            acceptance_criteria=["done"],
            team=Team.BACKEND,
            assigned_to=SYSTEM_UUID,
            created_by=SYSTEM_UUID,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=cast("UUID", project.id),
            status=TS.PENDING,
        )
    )
    task.status = TS.COMPLETED
    task.completed_at = datetime.now(UTC)
    await db_session.flush()

    context = await MegaphoneEngine(db_session).digest_context()
    assert "Ship the thing" in context
    assert "RoboCo" in context


@pytest.mark.asyncio
async def test_digest_context_excludes_tasks_completed_over_a_week_ago(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    task = await get_task_service(db_session).create(
        TaskCreateRequest(
            title="Ancient shipped thing",
            description="x",
            acceptance_criteria=["done"],
            team=Team.BACKEND,
            assigned_to=SYSTEM_UUID,
            created_by=SYSTEM_UUID,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=cast("UUID", project.id),
            status=TS.PENDING,
        )
    )
    task.status = TS.COMPLETED
    task.completed_at = datetime.now(UTC) - timedelta(days=30)
    await db_session.flush()

    context = await MegaphoneEngine(db_session).digest_context()
    assert "Ancient shipped thing" not in context


@pytest.mark.asyncio
async def test_digest_context_changelog_read_failure_is_best_effort(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace/changelog read failure never raises — the section just
    degrades to the explicit 'not available' line."""

    async def _boom(_self: WorkspaceService, _slug: str) -> str:
        raise RuntimeError("no clone available in this test process")

    monkeypatch.setattr(WorkspaceService, "ensure_read_clone", _boom)
    context = await MegaphoneEngine(db_session).digest_context()
    assert "not available this cycle" in context
