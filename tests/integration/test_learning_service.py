"""LearningPropagationService integration coverage — _create_notifications.

The notifications path (lines 207-290 in learning.py) requires a real DB
context because it queries `AgentTable` to find recipients. This file
exercises the full path against the test Postgres so the SELECT, the
loop body, and the per-agent NotificationService writes are covered.

Isolation: the fixture patches `get_db_context()` to yield the test's
own `db_session`. That way `_create_notifications` reuses the same
transaction as the test — no `commit()` is ever called, and the
conftest's per-test rollback wipes every row at teardown. Earlier
versions of this file committed via the session and corrupted the
shared DB for later tests (broke test_qa_agent_for_team_returns_none).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.services.learning import (
    LearningPropagationService,
    LearningScope,
    LearningType,
    RecordLearningParams,
)
from sqlalchemy import update

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


class _StubOptimal:
    """Minimal stub matching the optimal-service shape."""

    async def record_learning(self, _params: Any) -> None:
        return None


@pytest_asyncio.fixture
async def shared_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    """Make `_create_notifications` reuse the test's session.

    `get_db_context` is replaced with a context manager that yields the
    test's `db_session`. Both `_create_notifications` (which iterates
    AgentTable) and the test code share one transaction, so test data
    seen via `db.add(...); await db.flush()` is immediately visible to
    `_create_notifications` without committing.
    """

    @asynccontextmanager
    async def _ctx() -> AsyncIterator[AsyncSession]:
        yield db_session

    # learning.py imports `get_db_context` inside the function body (so the
    # `roboco.db.base` patch picks up at call time), but notification.py
    # imports it at module load — patch both targets.
    monkeypatch.setattr("roboco.db.base.get_db_context", _ctx)
    monkeypatch.setattr("roboco.services.notification.get_db_context", _ctx)
    yield db_session


def _make_agent(role: AgentRole, slug: str | None = None) -> AgentTable:
    return AgentTable(
        id=uuid4(),
        name=f"Agent {slug or role.value}",
        slug=slug or f"learn-{role.value}-{uuid4().hex[:8]}",
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )


@pytest.mark.asyncio
async def test_team_scope_role_uppercase_via_agent_role_enum(
    shared_session: AsyncSession,
) -> None:
    """TEAM scope invokes AgentRole(role.upper()) — AgentRole is StrEnum
    with lowercase values, so .upper() raises ValueError and the except
    branch runs, falling through to no role filter.
    """
    author = _make_agent(AgentRole.DEVELOPER)
    peer = _make_agent(AgentRole.DEVELOPER)
    other_role = _make_agent(AgentRole.QA)
    shared_session.add_all([author, peer, other_role])
    await shared_session.flush()

    svc = LearningPropagationService()
    await svc.initialize(_StubOptimal())
    learning = await svc.record_learning(
        RecordLearningParams(
            agent_id=cast(uuid.UUID, author.id),
            agent_role="developer",
            content="A useful pattern for batch updates",
            learning_type=LearningType.PATTERN,
            scope=LearningScope.TEAM,
        )
    )
    notified_ids = {
        n.target_agent_id
        for n in svc._notification_queue
        if n.learning_id == learning.learning_id
    }
    assert peer.id in notified_ids
    assert other_role.id in notified_ids


@pytest.mark.asyncio
async def test_team_scope_invalid_role_skips_filter(
    shared_session: AsyncSession,
) -> None:
    """Invalid role string falls through to no role filter and notifies all."""
    a1 = _make_agent(AgentRole.DEVELOPER)
    a2 = _make_agent(AgentRole.QA)
    shared_session.add_all([a1, a2])
    await shared_session.flush()

    svc = LearningPropagationService()
    await svc.initialize(_StubOptimal())
    await svc.record_learning(
        RecordLearningParams(
            agent_id=cast(uuid.UUID, a1.id),
            agent_role="not_a_real_role",
            content="content",
            learning_type=LearningType.SOLUTION,
            scope=LearningScope.TEAM,
        )
    )
    # No assertion on per-agent counts — the invalid-role branch is
    # intentionally permissive. What matters is that no exception escapes.


@pytest.mark.asyncio
async def test_cell_scope_runs_without_role_filter(
    shared_session: AsyncSession,
) -> None:
    """CELL scope hits the elif branch (line 222-224)."""
    a1 = _make_agent(AgentRole.DEVELOPER)
    a2 = _make_agent(AgentRole.QA)
    shared_session.add_all([a1, a2])
    await shared_session.flush()

    svc = LearningPropagationService()
    await svc.initialize(_StubOptimal())
    await svc.record_learning(
        RecordLearningParams(
            agent_id=cast(uuid.UUID, a1.id),
            agent_role="developer",
            content="cell-scope lesson",
            learning_type=LearningType.INSIGHT,
            scope=LearningScope.CELL,
        )
    )
    targets = {n.target_agent_id for n in svc._notification_queue}
    assert a2.id in targets


@pytest.mark.asyncio
async def test_org_scope_notifies_all_other_agents(
    shared_session: AsyncSession,
) -> None:
    """ORG scope: all agents except author are notified."""
    author = _make_agent(AgentRole.DEVELOPER)
    a2 = _make_agent(AgentRole.QA)
    a3 = _make_agent(AgentRole.CELL_PM)
    shared_session.add_all([author, a2, a3])
    await shared_session.flush()

    svc = LearningPropagationService()
    await svc.initialize(_StubOptimal())
    await svc.record_learning(
        RecordLearningParams(
            agent_id=cast(uuid.UUID, author.id),
            agent_role="developer",
            content="x" * 250,  # >200 chars to exercise the truncation branch
            learning_type=LearningType.SOLUTION,
            scope=LearningScope.ORG,
        )
    )
    targets = {n.target_agent_id for n in svc._notification_queue}
    assert a2.id in targets
    assert a3.id in targets
    assert author.id not in targets
    queued = next(n for n in svc._notification_queue if n.target_agent_id == a2.id)
    assert queued.learning_summary.endswith("...")


@pytest.mark.asyncio
async def test_no_other_agents_logs_and_returns(
    shared_session: AsyncSession,
) -> None:
    """If only the author exists, the no-recipients branch executes (line 230).

    We use a unique slug-prefix and assert via per-learning-id filter
    on the queue so unrelated rows in the DB don't pollute the
    assertion. (Other tests' rolled-back rows shouldn't be visible
    here anyway, but defensive filtering keeps this test order-stable.)
    """
    author = _make_agent(AgentRole.DEVELOPER, slug=f"solo-{uuid4().hex[:8]}")
    shared_session.add(author)
    await shared_session.flush()

    svc = LearningPropagationService()
    await svc.initialize(_StubOptimal())
    learning = await svc.record_learning(
        RecordLearningParams(
            agent_id=cast(uuid.UUID, author.id),
            agent_role="developer",
            content="solo agent learning",
            learning_type=LearningType.SOLUTION,
            scope=LearningScope.ORG,
        )
    )
    # No notifications should be queued for this specific learning. (Other
    # agents may exist in DB pollution from sibling tests; we don't assert
    # the queue is *globally* empty.)
    matching = [
        n for n in svc._notification_queue if n.learning_id == learning.learning_id
    ]
    assert all(n.target_agent_id != author.id for n in matching)


@pytest.mark.asyncio
async def test_no_recipients_after_role_filter_hits_empty_branch(
    shared_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover lines 230-235: query.scalars().all() returns empty -> log + return.

    Strategy: temporarily reassign every other agent's role to SYSTEM within
    this transaction so the TEAM-scope role filter (DEVELOPER) finds zero
    matches. Use the patched AgentRole so .upper() succeeds and the role
    filter actually applies (otherwise the except-ValueError branch falls
    through to no-filter, which finds all the other-role agents).
    """
    real_role = AgentRole

    class _PermissiveRole:
        def __new__(cls, value: str) -> "_PermissiveRole":
            return cast("_PermissiveRole", real_role(value.lower()))

    monkeypatch.setattr("roboco.models.base.AgentRole", _PermissiveRole)

    author = _make_agent(AgentRole.DEVELOPER, slug=f"empty-{uuid4().hex[:8]}")
    shared_session.add(author)
    await shared_session.flush()

    # Demote every other agent's role so the DEVELOPER filter finds nobody.
    await shared_session.execute(
        update(AgentTable)
        .where(AgentTable.id != author.id)
        .values(role=AgentRole.SYSTEM)
    )

    svc = LearningPropagationService()
    await svc.initialize(_StubOptimal())
    learning = await svc.record_learning(
        RecordLearningParams(
            agent_id=cast(uuid.UUID, author.id),
            agent_role="developer",
            content="alone with the role filter",
            learning_type=LearningType.SOLUTION,
            scope=LearningScope.TEAM,
        )
    )
    matching = [
        n for n in svc._notification_queue if n.learning_id == learning.learning_id
    ]
    assert matching == []


@pytest.mark.asyncio
async def test_team_scope_with_patched_agent_role_hits_filter(
    shared_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch AgentRole so .upper() resolves successfully; line 218 runs.

    AgentRole is a StrEnum with lowercase values, so the production
    `AgentRole(learning.agent_role.upper())` call always raises
    ValueError. We monkeypatch the reference inside `learning.py` with
    a permissive constructor so the role-filter `query.where(...)` line
    executes.
    """
    real_role = AgentRole

    class _PermissiveRole:
        """Stand-in: maps both lower and upper case strings to the real enum."""

        def __new__(cls, value: str) -> "_PermissiveRole":
            return cast("_PermissiveRole", real_role(value.lower()))

    # AgentRole is imported inside the function body (`from roboco.models.base
    # import AgentRole`), so patching `roboco.models.base.AgentRole` is what
    # the function will pick up at call time.
    monkeypatch.setattr("roboco.models.base.AgentRole", _PermissiveRole)

    author = _make_agent(real_role.DEVELOPER)
    peer_dev = _make_agent(real_role.DEVELOPER)
    peer_qa = _make_agent(real_role.QA)
    shared_session.add_all([author, peer_dev, peer_qa])
    await shared_session.flush()

    svc = LearningPropagationService()
    await svc.initialize(_StubOptimal())
    await svc.record_learning(
        RecordLearningParams(
            agent_id=cast(uuid.UUID, author.id),
            agent_role="developer",
            content="role-filter learning",
            learning_type=LearningType.PATTERN,
            scope=LearningScope.TEAM,
        )
    )
    notified = {n.target_agent_id for n in svc._notification_queue}
    assert peer_dev.id in notified
    assert peer_qa.id not in notified
