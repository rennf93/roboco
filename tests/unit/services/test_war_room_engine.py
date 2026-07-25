"""WarRoomEngine coverage: EVENT-triggered campaign planning — armed,
X-creds gated, one-open-campaign dedup shared across BOTH entry points
(``run_cycle`` for the "run now" seam, ``open_for_release`` for the
release-publish hook), and the release-marker vs blank-brief distinction.

Mirrors test_x_engine.py's feature-spotlight-exploration block (same
_FakeClient/_NullClient injection pattern) and test_coroner_engine.py's
open_for_incident-style dual-path dedup coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from roboco.config import settings as cfg
from roboco.db.tables import AgentTable, BoardProgramCycleTable, ProjectTable
from roboco.db.tables import SystemSettingTable as SST
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services.task import WAR_ROOM_SOURCE, get_task_service
from roboco.services.war_room_engine import WarRoomEngine, get_war_room_engine
from roboco.services.x_client import XClient, XMention, XPostResult
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
HOM_UUID = _foundation.AGENTS["head-marketing"].uuid
SLUG = "roboco"
ONE = 1
FIVE = 5


class _FakeClient(XClient):
    """A configured stub — never touches the network."""

    @property
    def configured(self) -> bool:
        return True

    async def post_tweet(
        self, text: str, *, in_reply_to_tweet_id: str | None = None
    ) -> XPostResult:
        _ = (text, in_reply_to_tweet_id)
        return XPostResult(posted=True, tweet_id="1", detail="ok")

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        _ = (since_id, max_results)
        return []

    async def search_recent(self, query: str, max_results: int) -> list[XMention]:
        _ = (query, max_results)
        return []


class _NullClient(XClient):
    @property
    def configured(self) -> bool:
        return False

    async def post_tweet(
        self, text: str, *, in_reply_to_tweet_id: str | None = None
    ) -> XPostResult:
        _ = (text, in_reply_to_tweet_id)
        return XPostResult(posted=False, tweet_id=None, detail="no creds")

    async def fetch_mentions(
        self, since_id: str | None, max_results: int
    ) -> list[XMention]:
        _ = (since_id, max_results)
        return []

    async def search_recent(self, query: str, max_results: int) -> list[XMention]:
        _ = (query, max_results)
        return []


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
    existing = await session.execute(
        select(ProjectTable).where(ProjectTable.slug == SLUG)
    )
    if existing.scalar_one_or_none() is None:
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


def _arm(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "self_heal_project_slug", SLUG)
    session.add(SST(key="board_program.war_room.enabled", value="true"))


@pytest.mark.asyncio
async def test_run_cycle_disabled_creates_no_exploration(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    engine = WarRoomEngine(db_session, client=_FakeClient())
    task = await engine.run_cycle()
    assert task is None
    assert await get_task_service(db_session).list_open_war_room_cycles() == []


@pytest.mark.asyncio
async def test_run_cycle_no_credentials_creates_no_exploration(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = WarRoomEngine(db_session, client=_NullClient())
    task = await engine.run_cycle()
    assert task is None


@pytest.mark.asyncio
async def test_run_cycle_opens_blank_brief_when_armed_and_credentialed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = WarRoomEngine(db_session, client=_FakeClient())
    task = await engine.run_cycle()
    assert task is not None
    assert task.source == WAR_ROOM_SOURCE
    assert task.assigned_to == HOM_UUID
    assert markers.get_war_room_brief(task) == {}


@pytest.mark.asyncio
async def test_open_for_release_carries_version_and_highlights(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = WarRoomEngine(db_session, client=_FakeClient())
    task = await engine.open_for_release(version="0.30.0", highlights=["a", "b"])
    assert task is not None
    assert markers.get_war_room_brief(task) == {
        "version": "0.30.0",
        "highlights": ["a", "b"],
    }


@pytest.mark.asyncio
async def test_open_for_release_caps_highlights_to_five(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = WarRoomEngine(db_session, client=_FakeClient())
    task = await engine.open_for_release(
        version="0.30.0", highlights=[f"h{i}" for i in range(8)]
    )
    assert task is not None
    brief = markers.get_war_room_brief(task)
    assert brief is not None
    assert len(cast("list[str]", brief["highlights"])) == FIVE


@pytest.mark.asyncio
async def test_open_for_release_disabled_is_a_noop(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    engine = WarRoomEngine(db_session, client=_FakeClient())
    task = await engine.open_for_release(version="0.30.0", highlights=[])
    assert task is None


@pytest.mark.asyncio
async def test_one_open_campaign_blocks_both_entry_points(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_cycle`` (run-now) and ``open_for_release`` (release hook) share
    the SAME one-open-campaign dedup — an open cycle from either path blocks
    the other, not just itself."""
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = WarRoomEngine(db_session, client=_FakeClient())
    first = await engine.run_cycle()
    assert first is not None
    second = await engine.open_for_release(version="0.30.0", highlights=[])
    assert second is None


@pytest.mark.asyncio
async def test_closed_campaign_allows_reorigination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = WarRoomEngine(db_session, client=_FakeClient())
    first = await engine.run_cycle()
    assert first is not None
    first.status = TS.COMPLETED
    await db_session.flush()
    second = await engine.run_cycle()
    assert second is not None


@pytest.mark.asyncio
async def test_open_for_release_records_its_own_learn_ledger_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike ``run_cycle`` (whose ledger row is recorded externally by
    ``BoardProgramEngine._originate_and_record`` when driven through
    ``_ORIGINATORS``), ``open_for_release`` bypasses that dict entirely and
    must record its own row — mirrors ``CoronerEngine._record_cycle``."""
    await _seed(db_session)
    _arm(db_session, monkeypatch)
    await db_session.flush()
    engine = WarRoomEngine(db_session, client=_FakeClient())
    task = await engine.open_for_release(version="0.30.0", highlights=[])
    assert task is not None
    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable).where(
                    BoardProgramCycleTable.program_key == "war_room"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == ONE
    assert rows[0].exploration_task_id == task.id


def test_get_war_room_engine_builder() -> None:
    engine = get_war_room_engine(cast("AsyncSession", None), client=_FakeClient())
    assert isinstance(engine, WarRoomEngine)
