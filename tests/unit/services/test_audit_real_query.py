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
from roboco.db import base as roboco_db_base
from roboco.db.tables import AgentTable
from roboco.models.base import AgentRole, AgentStatus
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
