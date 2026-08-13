"""The background pool (roboco.db.base), a genuinely separate engine.

Covers item 3 of the DB-pool split: a second create_async_engine/
async_sessionmaker pair reserved for the orchestrator's background engine
loops, sized independently, so a long-held background connection can never
starve an agent-facing FastAPI request queued on the primary pool.

The mock-based tests below mirror tests/integration/test_db_base.py's own
get_engine/get_session_factory coverage, extended to the "background" pool.
The two real-DB tests at the bottom verify, against a live connection, not
just by reading the code, the two claims the fix relies on: (1) each
session_factory() call gets its own Session and identity map (true of ANY
async_sessionmaker, primary or background), and (2) defer_after_commit's
nested-transaction guard is per-Session and engine-agnostic (it reads
session.sync_session state only, never which engine backs it).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.base import (
    _BackgroundDbHolder,
    _DbHolder,
    close_db,
    get_db_context,
    get_engine,
    get_session_factory,
)
from roboco.db.tables import AgentTable
from roboco.models.base import AgentRole, AgentStatus
from roboco.services.notification_delivery import defer_after_commit
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import Generator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

_PRIMARY_POOL_SIZE = 10
_PRIMARY_MAX_OVERFLOW = 20
_BACKGROUND_POOL_SIZE = 4
_BACKGROUND_MAX_OVERFLOW = 4
_TWO = 2


@pytest.fixture(autouse=True)
def _reset_holders() -> Generator[None]:
    """Snapshot/restore BOTH singletons so tests don't poison the live
    engines, mirroring test_db_base.py's _reset_holder, extended to the
    background holder this module adds coverage for."""
    saved_primary = (_DbHolder.engine, _DbHolder.session_factory, _DbHolder.loop)
    saved_background = (
        _BackgroundDbHolder.engine,
        _BackgroundDbHolder.session_factory,
        _BackgroundDbHolder.loop,
    )
    yield
    _DbHolder.engine, _DbHolder.session_factory, _DbHolder.loop = saved_primary
    (
        _BackgroundDbHolder.engine,
        _BackgroundDbHolder.session_factory,
        _BackgroundDbHolder.loop,
    ) = saved_background


# ---------------------------------------------------------------------------
# get_engine(pool="background") / get_session_factory(pool="background")
# ---------------------------------------------------------------------------


def test_background_pool_is_a_separate_engine_from_primary() -> None:
    """Two distinct create_async_engine calls, not the primary engine reused."""
    _DbHolder.engine = None
    _BackgroundDbHolder.engine = None
    engines = [MagicMock(), MagicMock()]
    with patch("roboco.db.base.create_async_engine", side_effect=engines) as ce:
        primary = get_engine("primary")
        background = get_engine("background")
    assert primary is engines[0]
    assert background is engines[1]
    assert primary is not background
    assert ce.call_count == len(engines)


def test_background_pool_sized_from_its_own_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The background engine is built with database_background_pool_size /
    _max_overflow, NOT the primary's database_pool_size / _max_overflow."""
    monkeypatch.setattr(settings, "database_pool_size", 10)
    monkeypatch.setattr(settings, "database_max_overflow", 20)
    monkeypatch.setattr(settings, "database_background_pool_size", 4)
    monkeypatch.setattr(settings, "database_background_max_overflow", 4)
    _DbHolder.engine = None
    _BackgroundDbHolder.engine = None
    with patch("roboco.db.base.create_async_engine", return_value=MagicMock()) as ce:
        get_engine("primary")
        get_engine("background")
    primary_kwargs = ce.call_args_list[0].kwargs
    background_kwargs = ce.call_args_list[1].kwargs
    assert primary_kwargs["pool_size"] == _PRIMARY_POOL_SIZE
    assert primary_kwargs["max_overflow"] == _PRIMARY_MAX_OVERFLOW
    assert background_kwargs["pool_size"] == _BACKGROUND_POOL_SIZE
    assert background_kwargs["max_overflow"] == _BACKGROUND_MAX_OVERFLOW


def test_background_pool_caches_independently_of_primary() -> None:
    _DbHolder.engine = None
    _BackgroundDbHolder.engine = None
    with patch("roboco.db.base.create_async_engine", return_value=MagicMock()) as ce:
        get_engine("primary")
        get_engine("primary")
        get_engine("background")
        get_engine("background")
    assert ce.call_count == _TWO  # one per pool, each cached on repeat access


def test_background_pool_rebinds_on_a_different_event_loop() -> None:
    """Mirrors test_get_engine_rebinds_on_a_different_event_loop for the
    background holder: a second loop must not reuse the first loop's
    background engine (its pooled connections are loop-bound)."""
    _BackgroundDbHolder.engine = None
    _BackgroundDbHolder.session_factory = None
    _BackgroundDbHolder.loop = None
    engines = [MagicMock(), MagicMock()]
    with patch("roboco.db.base.create_async_engine", side_effect=engines):

        async def _grab() -> object:
            return get_engine("background")

        loop_a_engine = asyncio.run(_grab())
        loop_b_engine = asyncio.run(_grab())
    assert loop_a_engine is engines[0]
    assert loop_b_engine is engines[1]


def test_get_session_factory_background_pool_creates_and_caches() -> None:
    _DbHolder.engine = None
    _DbHolder.session_factory = None
    _BackgroundDbHolder.engine = None
    _BackgroundDbHolder.session_factory = None
    fake_factories = [MagicMock(), MagicMock()]
    with (
        patch("roboco.db.base.create_async_engine", return_value=MagicMock()),
        patch("roboco.db.base.async_sessionmaker", side_effect=fake_factories) as sm,
    ):
        primary_factory = get_session_factory("primary")
        background_factory = get_session_factory("background")
        # Repeat access hits the cache, not a third async_sessionmaker call.
        get_session_factory("background")
    assert primary_factory is fake_factories[0]
    assert background_factory is fake_factories[1]
    assert sm.call_count == len(fake_factories)


@pytest.mark.asyncio
async def test_get_db_context_background_routes_to_background_session_factory() -> None:
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    class _Cm:
        async def __aenter__(self) -> object:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_factory = MagicMock(return_value=_Cm())
    with patch("roboco.db.base.get_session_factory", return_value=fake_factory) as gsf:
        async with get_db_context(pool="background") as s:
            assert s is fake_session
    gsf.assert_called_once_with("background")


@pytest.mark.asyncio
async def test_close_db_disposes_both_pools() -> None:
    fake_primary = MagicMock()
    fake_primary.dispose = AsyncMock()
    fake_background = MagicMock()
    fake_background.dispose = AsyncMock()
    _DbHolder.engine = fake_primary
    _DbHolder.session_factory = MagicMock()
    _BackgroundDbHolder.engine = fake_background
    _BackgroundDbHolder.session_factory = MagicMock()

    await close_db()

    fake_primary.dispose.assert_awaited_once()
    fake_background.dispose.assert_awaited_once()
    primary_engine_after = cast("AsyncEngine | None", _DbHolder.engine)
    primary_factory_after = cast(
        "async_sessionmaker[AsyncSession] | None", _DbHolder.session_factory
    )
    background_engine_after = cast("AsyncEngine | None", _BackgroundDbHolder.engine)
    background_factory_after = cast(
        "async_sessionmaker[AsyncSession] | None", _BackgroundDbHolder.session_factory
    )
    assert primary_engine_after is None
    assert primary_factory_after is None
    assert background_engine_after is None
    assert background_factory_after is None


# ---------------------------------------------------------------------------
# Real-DB verification of the two claims item 3 relies on.
# ---------------------------------------------------------------------------


@pytest.fixture
def _point_singletons_at_test_db(
    _test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repoint the app's settings-derived database_url at the ephemeral test
    DB (the same one tests/conftest.py already provisioned and migrated), so
    get_engine()/get_session_factory(), the real singleton machinery, talk
    to it instead of a dev/production database. database_url is a
    computed_field re-derived from these plain fields on every access, so
    patching them is enough; the computed property itself can't be assigned
    directly."""
    parsed = urlparse(_test_database_url)
    monkeypatch.setattr(settings, "database_host", parsed.hostname or "localhost")
    monkeypatch.setattr(settings, "database_port", parsed.port or 5432)
    monkeypatch.setattr(settings, "database_user", parsed.username or "roboco")
    monkeypatch.setattr(settings, "database_password", parsed.password or "")
    db_name = (parsed.path or "/roboco").lstrip("/")
    monkeypatch.setattr(settings, "database_name", db_name)


@pytest.mark.asyncio
async def test_primary_and_background_sessions_have_independent_identity_maps(
    _point_singletons_at_test_db: None,
) -> None:
    """Claim 1: async_sessionmaker() gives every session_factory() call its
    own Session + identity map, true of the background pool exactly like
    the primary one. Loading the SAME row through a primary-pool session and
    a background-pool session must yield two distinct Python objects (no
    identity-map sharing across pools), both reflecting the same row."""
    _DbHolder.engine = None
    _BackgroundDbHolder.engine = None
    try:
        agent_id = uuid4()
        async with get_db_context(pool="primary") as db:
            db.add(
                AgentTable(
                    id=agent_id,
                    name="bg-pool-test",
                    slug=f"bg-pool-test-{agent_id.hex[:8]}",
                    role=AgentRole.SYSTEM,
                    team=None,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )

        async with get_db_context(pool="primary") as primary_db:
            primary_row = (
                await primary_db.execute(
                    select(AgentTable).where(AgentTable.id == agent_id)
                )
            ).scalar_one()
            async with get_db_context(pool="background") as background_db:
                assert background_db is not primary_db
                background_row = (
                    await background_db.execute(
                        select(AgentTable).where(AgentTable.id == agent_id)
                    )
                ).scalar_one()
                # Same row content, but NOT the same Python object, proving
                # the two pools' sessions don't share an identity map.
                assert background_row is not primary_row
                assert background_row.id == primary_row.id
                assert background_row.slug == primary_row.slug

        async with get_db_context(pool="primary") as cleanup_db:
            row = await cleanup_db.get(AgentTable, agent_id)
            if row is not None:
                await cleanup_db.delete(row)
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_defer_after_commit_is_engine_agnostic_on_background_pool(
    _point_singletons_at_test_db: None,
) -> None:
    """Claim 2: defer_after_commit reads only session.sync_session state
    (get_nested_transaction/get_transaction), never which engine backs the
    session, so it must behave IDENTICALLY on a background-pool session: a
    savepoint release does not drain, only the real root commit does.
    Mirrors test_defer_after_commit_does_not_drain_at_savepoint_release in
    tests/integration/test_notification_expiry_sweep.py, run here against
    the background pool instead of the default fixture session."""
    _BackgroundDbHolder.engine = None
    try:
        async with get_db_context(pool="background") as db:
            ran: list[str] = []

            async def _work() -> None:
                ran.append("ran")

            await db.execute(select(1))  # force a real root transaction to open
            defer_after_commit(db, _work)

            async with db.begin_nested():
                pass  # savepoint opens and releases; root transaction stays open

            assert ran == []  # not drained at the savepoint boundary

            await db.commit()
            drain_tasks = list(db.info.get("_roboco_drain_tasks", []))
            if drain_tasks:
                await asyncio.gather(*drain_tasks, return_exceptions=True)
            assert ran == ["ran"]  # drained at the real root commit
    finally:
        await close_db()
