"""Real-DB integration test for ``AuditService.has_recent_tracing_gap``.

Why this test exists
--------------------
Task 13 (commit 887d073) added the PM-respawn rule-following retry
detection. The query at ``roboco/services/audit.py:489`` filters
``details->>'reason' == 'tracing_gap'`` via SQLAlchemy's ``.astext``
accessor — but ``.astext`` only exists on ``JSONB.Comparator``, NOT on
the generic ``JSON.Comparator``. The ORM declared the column as ``JSON``,
so the access raised ``AttributeError`` at query construction time and
the choreographer's exception handler swallowed it.

The six existing unit tests in
``tests/unit/runtime/test_pm_respawn_reset.py`` all mocked
``audit.has_recent_tracing_gap`` directly, never exercising the SQL —
that's why they passed despite the production code being inert.

This test issues the real query against a real Postgres backend (the
session-scoped test DB from ``tests/conftest.py``). It seeds an audit
row with the production shape and asserts the True / False outcomes.
With the ORM correctly declaring ``JSONB``, this passes; with the prior
``JSON`` declaration it would raise the same ``AttributeError`` the
production code raises.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db import base as db_base
from roboco.db import base as roboco_db_base
from roboco.db.tables import AgentTable
from roboco.models.base import AgentRole, AgentStatus
from roboco.seeds import initial_data as seeds
from roboco.services.audit import AuditService
from sqlalchemy.ext.asyncio import async_sessionmaker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agent(session: AsyncSession) -> UUID:
    """Insert one minimal AgentTable row and return its id.

    ``audit_log.agent_id`` has a SET NULL FK to ``agents.id``. Without an
    actual agent row the persist silently fails (best-effort) and the
    test would assert against zero rows for the wrong reason.
    """
    agent = AgentTable(
        id=uuid4(),
        name="Audit Test Agent",
        slug=f"audit-test-{uuid4().hex[:8]}",
        role=AgentRole.CELL_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="audit test",
        capabilities=[],
        permissions={},
        metrics={},
    )
    session.add(agent)
    await session.commit()
    return UUID(str(agent.id))


@pytest_asyncio.fixture
async def patched_session_factory(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    """Point ``get_session_factory`` at the test-DB engine for the run.

    ``AuditService.log_event`` and ``has_recent_tracing_gap`` both open
    their own sessions via ``roboco.db.base.get_session_factory()`` —
    which without intervention binds to the production database URL
    from ``settings``. We hijack the factory to bind to the same engine
    the test fixture is using so the inserts and SELECT see the same
    rows.

    Yields ``db_session`` so callers can also seed FK targets directly.
    """
    test_engine = db_session.bind
    test_factory = async_sessionmaker(
        bind=test_engine, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(roboco_db_base, "get_session_factory", lambda: test_factory)
    yield db_session


@pytest.mark.asyncio
async def test_has_recent_tracing_gap_finds_seeded_row(
    patched_session_factory: AsyncSession,
) -> None:
    """A seeded gateway.rejected row with reason=tracing_gap is detected.

    Exercises the real SQL path including ``details->>'reason'``. With
    the ORM declaring ``JSON`` (the bug), this raises AttributeError at
    query construction. With ``JSONB`` (the fix), it executes and
    returns True.
    """
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()
    task_id = uuid4()
    since = datetime.now(UTC) - timedelta(seconds=60)

    await audit.log_event(
        event_type="gateway.rejected",
        agent_id=agent_id,
        task_id=task_id,
        details={"verb": "delegate", "reason": "tracing_gap", "missing": ["plan"]},
    )

    result = await audit.has_recent_tracing_gap(
        agent_id=agent_id,
        task_id=task_id,
        since=since,
    )
    assert result is True


@pytest.mark.asyncio
async def test_has_recent_tracing_gap_returns_false_for_other_task(
    patched_session_factory: AsyncSession,
) -> None:
    """Same agent, different task id — must NOT match."""
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()
    seeded_task_id = uuid4()
    other_task_id = uuid4()
    since = datetime.now(UTC) - timedelta(seconds=60)

    await audit.log_event(
        event_type="gateway.rejected",
        agent_id=agent_id,
        task_id=seeded_task_id,
        details={"reason": "tracing_gap"},
    )

    result = await audit.has_recent_tracing_gap(
        agent_id=agent_id,
        task_id=other_task_id,
        since=since,
    )
    assert result is False


@pytest.mark.asyncio
async def test_has_recent_tracing_gap_returns_false_for_other_reason(
    patched_session_factory: AsyncSession,
) -> None:
    """Reason other than 'tracing_gap' on the same (agent, task) — no match.

    This is the most critical assertion: it proves the JSONB
    ``details->>'reason' == 'tracing_gap'`` predicate is actually
    evaluated by Postgres, not silently dropped because ``.astext``
    raised before the SQL was ever issued.
    """
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()
    task_id = uuid4()
    since = datetime.now(UTC) - timedelta(seconds=60)

    await audit.log_event(
        event_type="gateway.rejected",
        agent_id=agent_id,
        task_id=task_id,
        details={"reason": "permission_denied"},
    )

    result = await audit.has_recent_tracing_gap(
        agent_id=agent_id,
        task_id=task_id,
        since=since,
    )
    assert result is False


@pytest.mark.asyncio
async def test_has_recent_tracing_gap_respects_since_window(
    patched_session_factory: AsyncSession,
) -> None:
    """Rows older than ``since`` must not match.

    ``log_event`` always stamps `timestamp = now()`. We pass a `since`
    one hour in the future to force every existing row to fall outside
    the window.
    """
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()
    task_id = uuid4()
    future_since = datetime.now(UTC) + timedelta(hours=1)

    await audit.log_event(
        event_type="gateway.rejected",
        agent_id=agent_id,
        task_id=task_id,
        details={"reason": "tracing_gap"},
    )

    result = await audit.has_recent_tracing_gap(
        agent_id=agent_id,
        task_id=task_id,
        since=future_since,
    )
    assert result is False


# ---------------------------------------------------------------------------
# get_recent_events — query method exercises severity filters and ordering.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recent_events_returns_logged_rows(
    patched_session_factory: AsyncSession,
) -> None:
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()
    task_id = uuid4()

    await audit.log_event(
        event_type="gateway.rejected",
        agent_id=agent_id,
        task_id=task_id,
        details={"reason": "tracing_gap"},
    )

    rows = await audit.get_recent_events(limit=10)
    assert len(rows) >= 1
    assert any(r["event_type"] == "gateway.rejected" for r in rows)


@pytest.mark.asyncio
async def test_get_recent_events_filters_event_type(
    patched_session_factory: AsyncSession,
) -> None:
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()

    await audit.log_event(
        event_type="task.created",
        agent_id=agent_id,
        task_id=uuid4(),
    )
    await audit.log_event(
        event_type="task.completed",
        agent_id=agent_id,
        task_id=uuid4(),
    )

    rows = await audit.get_recent_events(limit=10, event_type="task.created")
    assert all(r["event_type"] == "task.created" for r in rows)


@pytest.mark.asyncio
async def test_get_recent_events_filters_agent_id(
    patched_session_factory: AsyncSession,
) -> None:
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()

    await audit.log_event(
        event_type="task.created",
        agent_id=agent_id,
        task_id=uuid4(),
    )

    rows = await audit.get_recent_events(limit=10, agent_id=agent_id)
    assert all(r["agent_id"] == str(agent_id) for r in rows)


@pytest.mark.asyncio
async def test_get_recent_events_filters_min_severity_warning(
    patched_session_factory: AsyncSession,
) -> None:
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()

    await audit.log_event(
        event_type="some.warn",
        agent_id=agent_id,
        task_id=uuid4(),
        severity="warning",
    )
    await audit.log_event(
        event_type="some.info",
        agent_id=agent_id,
        task_id=uuid4(),
        severity="info",
    )

    rows = await audit.get_recent_events(limit=10, min_severity="warning")
    assert all(r["severity"] in {"warning", "error"} for r in rows)


@pytest.mark.asyncio
async def test_get_recent_events_filters_min_severity_error(
    patched_session_factory: AsyncSession,
) -> None:
    agent_id = await _seed_agent(patched_session_factory)
    audit = AuditService()

    await audit.log_event(
        event_type="error.x",
        agent_id=agent_id,
        task_id=uuid4(),
        severity="error",
    )
    await audit.log_event(
        event_type="warn.x",
        agent_id=agent_id,
        task_id=uuid4(),
        severity="warning",
    )

    rows = await audit.get_recent_events(limit=10, min_severity="error")
    assert all(r["severity"] == "error" for r in rows)


# ---------------------------------------------------------------------------
# _resolve_agent_id_by_slug — covers static and DB lookup paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_id_by_slug_db_lookup(
    patched_session_factory: AsyncSession,
) -> None:
    """An agent NOT in AGENT_UUIDS hits the DB lookup path."""
    agent = AgentTable(
        id=uuid4(),
        name="Runtime Agent",
        slug=f"runtime-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    patched_session_factory.add(agent)
    await patched_session_factory.commit()

    audit = AuditService()
    resolved = await audit._resolve_agent_id_by_slug(agent.slug)
    assert resolved == agent.id


@pytest.mark.asyncio
@pytest.mark.usefixtures("patched_session_factory")
async def test_resolve_agent_id_by_slug_unknown_returns_none() -> None:
    audit = AuditService()
    resolved = await audit._resolve_agent_id_by_slug("nonexistent-slug-xyz123")
    assert resolved is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("patched_session_factory")
async def test_resolve_agent_id_by_slug_static_lookup_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the AGENT_UUIDS import path to raise to cover lines 465-470."""

    # Replace AGENT_UUIDS with an object that raises on .get
    class _BadMap:
        def get(self, *_a: object, **_k: object) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(seeds, "AGENT_UUIDS", _BadMap())
    audit = AuditService()
    # Falls back to DB lookup — returns None since slug isn't in DB.
    resolved = await audit._resolve_agent_id_by_slug("missing-fallback-slug")
    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_agent_id_by_slug_db_lookup_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force DB lookup to raise so the outer except runs (lines 488-494)."""

    def _explode() -> object:
        raise RuntimeError("session factory broken")

    monkeypatch.setattr(db_base, "get_session_factory", _explode)
    audit = AuditService()
    resolved = await audit._resolve_agent_id_by_slug("nope-not-real")
    assert resolved is None
