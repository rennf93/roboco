"""Board Programs API route coverage — CEO-only list + run-now."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.board_programs import router as board_programs_router
from roboco.config import settings as cfg
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.models import AgentRole, AgentStatus, TaskStatus, Team
from roboco.models.permissions import AgentContext
from roboco.services.task import (
    CORONER_SOURCE,
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SCALES_SOURCE,
    SENTINEL_SOURCE,
    SPACKLE_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
)
from sqlalchemy import delete, update

CEO_UUID = _foundation.AGENTS["ceo"].uuid
SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agents(session: AsyncSession) -> None:
    for uuid, slug, role in (
        (CEO_UUID, "ceo", AgentRole.CEO),
        (SYSTEM_UUID, "system", AgentRole.SYSTEM),
        (PO_UUID, "product-owner", AgentRole.PRODUCT_OWNER),
    ):
        if await session.get(AgentTable, uuid) is not None:
            continue
        session.add(
            AgentTable(
                id=uuid,
                name=slug,
                slug=slug,
                role=role,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="x",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
    await session.flush()


async def _arm_roadmap(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Arms roadmap two ways: the new per-program settings-store key (what
    Task 7 makes writable, consulted by ``BoardProgramEngine.enabled``) AND
    the legacy ``roadmap_engine_enabled`` config flag — ``RoadmapEngine.
    run_cycle`` (the migrated originator itself, unchanged since before the
    registry existed) independently checks the legacy flag and no-ops
    without it, regardless of the registry-level settings-store override.

    Also seeds the project ``RoadmapEngine._roboco_project`` resolves against
    (a unique slug per call + a matching ``self_heal_project_slug`` override —
    ``db_session`` is a real, cross-test-persistent database within one
    pytest run, so a fixed slug like "roboco-api" would collide the second
    a sibling test also arms roadmap)."""
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    key = "board_program.roadmap.enabled"
    existing = await session.get(SystemSettingTable, key)
    if existing is None:
        session.add(SystemSettingTable(key=key, value="true"))
    else:
        existing.value = "true"

    slug = f"roboco-api-{uuid4().hex[:8]}"
    monkeypatch.setattr(cfg, "self_heal_project_slug", slug)
    session.add(
        ProjectTable(
            id=uuid4(),
            name="RoboCo",
            slug=slug,
            git_url="https://example.com/roboco.git",
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
        )
    )
    await session.flush()


def _build_app(db_session: AsyncSession, role: AgentRole, agent_id: UUID) -> FastAPI:
    app = FastAPI()
    app.include_router(board_programs_router, prefix="/api/board-programs")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent_id, role=role, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def ceo_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await _seed_agents(db_session)
    app = _build_app(db_session, AgentRole.CEO, CEO_UUID)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    # run-now's route handler commits explicitly (write-route convention), so
    # anything a test wrote through it (settings-store overrides, an opened
    # ledger row, the board_roadmap/x_feature_exploration/board_pest_control
    # task it originates) would otherwise outlive this test in the shared,
    # cross-test-persistent DB and poison every later real-DB board-program
    # unit test (dedup checks, settings-store PK collisions, ledger
    # scalar_one() lookups) — not just roadmap's, so every registered
    # program's exploration source is swept here, not only the one this
    # module's own tests happen to exercise today. Purge unconditionally — a
    # no-op for the tests here that never wrote anything.
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
                    PERISCOPE_SOURCE,
                    CORONER_SOURCE,
                    SENTINEL_SOURCE,
                    SPACKLE_SOURCE,
                    SCALES_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
        .values(status=TaskStatus.CANCELLED)
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_list_returns_every_registered_program(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/board-programs")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert {p["key"] for p in body} == {
        "roadmap",
        "x_feature",
        "pest_control",
        "periscope",
        "coroner",
        "sentinel",
        "spackle",
        "scales",
    }
    pest_control = next(p for p in body if p["key"] == "pest_control")
    assert pest_control["role"] == "product_owner"
    assert pest_control["trigger"] == "cron"
    assert pest_control["scope"] == "project"
    spackle = next(p for p in body if p["key"] == "spackle")
    assert spackle["role"] == "product_owner"
    assert spackle["trigger"] == "cron"
    assert spackle["scope"] == "project"
    roadmap = next(p for p in body if p["key"] == "roadmap")
    assert roadmap["role"] == "product_owner"
    assert roadmap["trigger"] == "cron"
    assert roadmap["scope"] == "org"
    coroner = next(p for p in body if p["key"] == "coroner")
    assert coroner["role"] == "auditor"
    assert coroner["trigger"] == "event"
    assert coroner["scope"] == "org"
    assert roadmap["open_cycle"] is False
    assert roadmap["last_opened_at"] is None
    # Not asserted == [] — org-scoped "eligible" means every active project
    # (default-eligible, opt-out only), and db_session is a real,
    # cross-test-persistent database within one pytest run: sibling suites
    # seed their own projects that legitimately show up here too.
    assert isinstance(roadmap["opted_in_project_slugs"], list)


@pytest.mark.asyncio
async def test_run_now_opens_a_cycle_then_conflicts_on_retry(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One test, not two — ``RoadmapEngine.run_cycle``'s own dedup
    (``list_open_roadmap_cycles``) is system-wide (any open ``board_roadmap``
    task, not scoped to this test's project), and ``db_session`` is a real,
    cross-test-persistent database within one pytest run: a leftover open
    cycle from a sibling test would make the FIRST call here 409 too."""
    await _arm_roadmap(db_session, monkeypatch)

    first = await ceo_client.post("/api/board-programs/roadmap/run-now")
    assert first.status_code == HTTPStatus.OK
    body = first.json()
    assert body["open_cycle"] is True
    assert body["last_opened_at"] is not None

    second = await ceo_client.post("/api/board-programs/roadmap/run-now")
    assert second.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_run_now_unknown_key_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.post("/api/board-programs/not-a-real-program/run-now")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_agents(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/board-programs")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()
