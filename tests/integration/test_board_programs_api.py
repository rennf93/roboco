"""Board Programs API route coverage — CEO-only list + run-now."""

from __future__ import annotations

from datetime import UTC, datetime
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
from roboco.foundation.policy.maintenance_pause import PauseScope
from roboco.models import AgentRole, AgentStatus, TaskStatus, Team
from roboco.models.permissions import AgentContext
from roboco.services.maintenance_pause import get_maintenance_pause_service
from roboco.services.task import (
    BARFLY_SOURCE,
    CORONER_SOURCE,
    DOGFOOD_SOURCE,
    LIBRARIAN_SOURCE,
    MEGAPHONE_SOURCE,
    MIRROR_SOURCE,
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    SCALES_SOURCE,
    SENTINEL_SOURCE,
    SPACKLE_SOURCE,
    WAR_ROOM_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
)
from roboco.services.x_credentials import get_x_credentials_service
from sqlalchemy import delete, update

CEO_UUID = _foundation.AGENTS["ceo"].uuid
SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
HOM_UUID = _foundation.AGENTS["head-marketing"].uuid
TWO = 2

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agents(session: AsyncSession) -> None:
    for uuid, slug, role in (
        (CEO_UUID, "ceo", AgentRole.CEO),
        (SYSTEM_UUID, "system", AgentRole.SYSTEM),
        (PO_UUID, "product-owner", AgentRole.PRODUCT_OWNER),
        (HOM_UUID, "head-marketing", AgentRole.HEAD_MARKETING),
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


async def _arm_war_room(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Arms war_room via its settings-store key (no legacy flag exists for
    it — ``program_armed``'s ``_legacy_enabled`` returns False for an
    unregistered legacy alias), seeds the project ``WarRoomEngine.
    _roboco_project`` resolves against (mirrors ``_arm_roadmap``'s unique-
    slug-per-call rationale), and seeds X credentials — ``WarRoomEngine``'s
    own creds gate would otherwise no-op every call, mirroring XEngine's
    release/spotlight guard."""
    key = "board_program.war_room.enabled"
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

    await get_x_credentials_service(session).set_credentials(
        api_key="ak-test",
        api_secret="as-test",
        access_token="at-test",
        access_token_secret="ats-test",
    )


@pytest_asyncio.fixture(autouse=True)
async def _purge_maintenance_pause_rows(
    db_session: AsyncSession,
) -> AsyncIterator[None]:
    """A pause the DEFECT-5 run-now test below writes (through a route that
    commits explicitly) would otherwise outlive it in the shared, cross-
    test-persistent DB and 409 every later run-now call in this file. Purge
    unconditionally -- a no-op for every test here that never paused
    anything."""
    yield
    await db_session.execute(
        delete(SystemSettingTable).where(
            SystemSettingTable.key.like("maintenance_pause.%")
        )
    )
    await db_session.commit()


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
                    MIRROR_SOURCE,
                    MEGAPHONE_SOURCE,
                    LIBRARIAN_SOURCE,
                    WAR_ROOM_SOURCE,
                    BARFLY_SOURCE,
                    DOGFOOD_SOURCE,
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
        "mirror",
        "megaphone",
        "librarian",
        "war_room",
        "barfly",
        "dogfood",
    }
    pest_control = next(p for p in body if p["key"] == "pest_control")
    assert pest_control["role"] == "product_owner"
    assert pest_control["trigger"] == "cron"
    assert pest_control["scope"] == "project"
    spackle = next(p for p in body if p["key"] == "spackle")
    assert spackle["role"] == "product_owner"
    assert spackle["trigger"] == "cron"
    assert spackle["scope"] == "project"
    mirror = next(p for p in body if p["key"] == "mirror")
    assert mirror["role"] == "head_marketing"
    assert mirror["trigger"] == "cron"
    assert mirror["scope"] == "project"
    barfly = next(p for p in body if p["key"] == "barfly")
    assert barfly["role"] == "head_marketing"
    assert barfly["trigger"] == "cron"
    assert barfly["scope"] == "org"
    roadmap = next(p for p in body if p["key"] == "roadmap")
    assert roadmap["role"] == "product_owner"
    assert roadmap["trigger"] == "cron"
    assert roadmap["scope"] == "org"
    coroner = next(p for p in body if p["key"] == "coroner")
    assert coroner["role"] == "auditor"
    assert coroner["trigger"] == "event"
    assert coroner["scope"] == "org"
    war_room = next(p for p in body if p["key"] == "war_room")
    assert war_room["role"] == "head_marketing"
    assert war_room["trigger"] == "event"
    assert war_room["scope"] == "org"
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
    # DEFECT 5: the generic (non-pause) 409 must not claim a pause that
    # isn't happening.
    assert "pause" not in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_run_now_conflict_names_the_maintenance_pause_when_active(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT 5: run-now's 409 must say WHY when the reason is a
    board_programs maintenance pause, not the generic disabled/open-cycle/
    no-project message that gives the CEO nothing actionable."""
    await _arm_roadmap(db_session, monkeypatch)
    await get_maintenance_pause_service(db_session).pause(
        PauseScope.BOARD_PROGRAMS, by="ceo", hours=1
    )
    await db_session.commit()

    resp = await ceo_client.post("/api/board-programs/roadmap/run-now")

    assert resp.status_code == HTTPStatus.CONFLICT
    assert "pause" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_run_now_opens_a_cycle_for_event_program_war_room(
    db_session: AsyncSession,
    ceo_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """War Room is EVENT-triggered same as Coroner (never cron-due — see
    test_list_returns_every_registered_program's assertion), but UNLIKE
    Coroner its ``_ORIGINATORS`` entry is a REAL originator
    (``WarRoomEngine.run_cycle``, not an always-None stub): run-now must
    actually open a cycle through the route, proving the EVENT contract
    holds without needing a stub — ``open_program_cycle`` never checks
    trigger kind, only the cron loop does."""
    await _arm_war_room(db_session, monkeypatch)

    first = await ceo_client.post("/api/board-programs/war_room/run-now")
    assert first.status_code == HTTPStatus.OK
    body = first.json()
    assert body["open_cycle"] is True
    assert body["last_opened_at"] is not None

    second = await ceo_client.post("/api/board-programs/war_room/run-now")
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


# --------------------------------------------------------------------------- #
# Gap 5: the historical-cycles read surface behind the single rendered
# ``last_cycle_summary`` line ``GET /api/board-programs`` returns.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_cycles_returns_historical_decisions(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    db_session.add(
        BoardProgramCycleTable(
            program_key="mirror",
            exploration_task_id=None,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
            items_proposed=1,
            items_approved=1,
            items_rejected=0,
            decisions=[
                {
                    "item_ref": "README claims a dead feature",
                    "verdict": "approved",
                    "reason": None,
                    "item_snapshot": {
                        "title": "README claims a dead feature",
                        "materialized_task_id": str(uuid4()),
                    },
                }
            ],
        )
    )
    await db_session.flush()

    resp = await ceo_client.get("/api/board-programs/mirror/cycles")
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body) == 1
    cycle = body[0]
    assert cycle["items_proposed"] == 1
    assert cycle["items_approved"] == 1
    decision = cycle["decisions"][0]
    assert decision["item_ref"] == "README claims a dead feature"
    assert decision["verdict"] == "approved"
    assert decision["item_snapshot"]["title"] == "README claims a dead feature"


@pytest.mark.asyncio
async def test_list_cycles_respects_limit(
    db_session: AsyncSession, ceo_client: AsyncClient
) -> None:
    for _ in range(3):
        db_session.add(
            BoardProgramCycleTable(
                program_key="sentinel",
                exploration_task_id=None,
                opened_at=datetime.now(UTC),
            )
        )
    await db_session.flush()

    resp = await ceo_client.get(
        "/api/board-programs/sentinel/cycles", params={"limit": 2}
    )
    assert resp.status_code == HTTPStatus.OK
    assert len(resp.json()) == TWO


@pytest.mark.asyncio
async def test_list_cycles_unknown_key_is_404(ceo_client: AsyncClient) -> None:
    resp = await ceo_client.get("/api/board-programs/not-a-real-program/cycles")
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_list_cycles_non_ceo_is_forbidden(db_session: AsyncSession) -> None:
    await _seed_agents(db_session)
    app = _build_app(db_session, AgentRole.DEVELOPER, uuid4())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/board-programs/mirror/cycles")
    assert resp.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()
