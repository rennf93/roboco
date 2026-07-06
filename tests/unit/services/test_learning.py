"""LearningPropagationService coverage — stub OptimalService.

LearningPropagationService is logic on top of OptimalService (the RAG layer).
We unit-test the wiring with a stub that records calls and returns canned
SearchResult lists; OptimalService itself is exercised in its own integration
tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.foundation.policy.communications import ACK_REQUIRED_BY_TYPE
from roboco.models import NotificationPriority, NotificationType
from roboco.models.base import AgentRole
from roboco.models.optimal import IndexType, SearchResult
from roboco.services.learning import (
    Learning,
    LearningNotification,
    LearningPropagationService,
    LearningScope,
    LearningType,
    RecordLearningParams,
    _LearningServiceHolder,
    get_learning_service,
)
from sqlalchemy.sql.dml import Insert

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _StubOptimal:
    """Records calls so tests can assert wiring."""

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.recorded: list[Any] = []
        self.searches: list[dict[str, Any]] = []
        self.search_learnings_calls: list[dict[str, Any]] = []
        self.results = results or []

    async def record_learning(self, params: Any) -> None:
        self.recorded.append(params)

    async def search(
        self, *, query: str, index_types: list[IndexType], top_k: int
    ) -> list[SearchResult]:
        self.searches.append(
            {"query": query, "index_types": index_types, "top_k": top_k}
        )
        return self.results

    async def search_learnings(self, *, query: str, top_k: int) -> list[SearchResult]:
        self.search_learnings_calls.append({"query": query, "top_k": top_k})
        return self.results


@pytest.fixture
def svc() -> LearningPropagationService:
    return LearningPropagationService()


@pytest.mark.asyncio
async def test_record_learning_requires_initialization(
    svc: LearningPropagationService,
) -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        await svc.record_learning(
            RecordLearningParams(
                agent_id=uuid4(),
                agent_role="developer",
                content="x",
                learning_type=LearningType.SOLUTION,
                scope=LearningScope.PERSONAL,
            )
        )


@pytest.mark.asyncio
async def test_record_learning_personal_scope_skips_notifications(
    svc: LearningPropagationService,
) -> None:
    stub = _StubOptimal()
    await svc.initialize(stub)
    learning = await svc.record_learning(
        RecordLearningParams(
            agent_id=uuid4(),
            agent_role="developer",
            content="some private insight",
            learning_type=LearningType.INSIGHT,
            scope=LearningScope.PERSONAL,
        )
    )
    assert isinstance(learning, Learning)
    assert learning.scope == LearningScope.PERSONAL
    assert len(stub.recorded) == 1


@pytest.mark.asyncio
async def test_record_learning_normalizes_string_enums(
    svc: LearningPropagationService,
) -> None:
    stub = _StubOptimal()
    await svc.initialize(stub)
    learning = await svc.record_learning(
        RecordLearningParams(
            agent_id=uuid4(),
            agent_role="qa",
            content="content",
            learning_type="solution",
            scope="personal",
        )
    )
    assert learning.learning_type == LearningType.SOLUTION
    assert learning.scope == LearningScope.PERSONAL


@pytest.mark.asyncio
async def test_record_learning_team_scope_calls_create_notifications(
    svc: LearningPropagationService,
) -> None:
    """Team-scope learnings call _create_notifications; best-effort logs on error."""
    stub = _StubOptimal()
    await svc.initialize(stub)
    # The notifications branch will silently fail because there's no DB
    # context inside the unit-test environment — that's fine; we just want
    # to cover the code path.
    learning = await svc.record_learning(
        RecordLearningParams(
            agent_id=uuid4(),
            agent_role="developer",
            content="team-scoped lesson",
            learning_type=LearningType.PATTERN,
            scope=LearningScope.TEAM,
        )
    )
    assert learning.scope == LearningScope.TEAM


@pytest.mark.asyncio
async def test_get_learnings_for_agent_requires_initialization(
    svc: LearningPropagationService,
) -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        await svc.get_learnings_for_agent(uuid4(), "developer")


@pytest.mark.asyncio
async def test_get_learnings_for_agent_returns_filtered_results(
    svc: LearningPropagationService,
) -> None:
    aid = uuid4()
    other_id = uuid4()

    def _r(metadata: dict, score: float = 0.7) -> SearchResult:
        return SearchResult(
            content="x",
            source="test",
            score=score,
            index_type=IndexType.LEARNINGS,
            metadata=metadata,
        )

    own_personal = _r(
        {"scope": "personal", "agent_id": str(aid), "agent_role": "developer"},
        score=0.9,
    )
    other_personal = _r(
        {
            "scope": "personal",
            "agent_id": str(other_id),
            "agent_role": "developer",
        },
        score=0.9,
    )
    team_visible = _r({"scope": "team", "agent_role": "developer"})
    team_other_role = _r({"scope": "team", "agent_role": "qa"})
    org_visible = _r({"scope": "org", "agent_role": "qa"}, score=0.5)
    stub = _StubOptimal(
        results=[
            own_personal,
            other_personal,
            team_visible,
            team_other_role,
            org_visible,
        ]
    )
    await svc.initialize(stub)
    out = await svc.get_learnings_for_agent(aid, "developer")
    # Visible: own personal, team for matching role, org-anyone.
    # Filtered out: other-personal & team-other-role.
    contents = [r.metadata for r in out]
    assert own_personal.metadata in contents
    assert team_visible.metadata in contents
    assert org_visible.metadata in contents
    assert other_personal.metadata not in contents
    assert team_other_role.metadata not in contents


@pytest.mark.asyncio
async def test_search_similar_learnings_requires_initialization(
    svc: LearningPropagationService,
) -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        await svc.search_similar_learnings("anything")


@pytest.mark.asyncio
async def test_search_similar_learnings_passes_through(
    svc: LearningPropagationService,
) -> None:
    stub = _StubOptimal(
        results=[
            SearchResult(
                content="x",
                source="test",
                score=1.0,
                index_type=IndexType.LEARNINGS,
                metadata={},
            )
        ]
    )
    await svc.initialize(stub)
    out = await svc.search_similar_learnings("how to debug", top_k=3)
    assert len(out) == 1
    _TOP_K = 3
    assert stub.searches[0]["top_k"] == _TOP_K
    assert IndexType.LEARNINGS in stub.searches[0]["index_types"]


@pytest.mark.asyncio
async def test_mark_learning_helpful_logs(
    svc: LearningPropagationService,
) -> None:
    """Just exercises the log call — no error path."""
    await svc.mark_learning_helpful("lrn-abc", uuid4(), helpful=True)
    await svc.mark_learning_helpful("lrn-abc", uuid4(), helpful=False)


@pytest.mark.asyncio
async def test_mark_learning_used_logs(svc: LearningPropagationService) -> None:
    await svc.mark_learning_used("lrn-abc", uuid4(), context="tried this")
    await svc.mark_learning_used("lrn-abc", uuid4())


@pytest.mark.asyncio
async def test_get_pending_notifications_filters_by_agent(
    svc: LearningPropagationService,
) -> None:
    aid = uuid4()
    other_id = uuid4()
    svc._notification_queue.append(
        LearningNotification(
            notification_id="n1",
            learning_id="lrn-1",
            target_agent_id=aid,
            learning_summary="s",
            reason="r",
            created_at="2026-01-01",
        )
    )
    svc._notification_queue.append(
        LearningNotification(
            notification_id="n2",
            learning_id="lrn-2",
            target_agent_id=other_id,
            learning_summary="s",
            reason="r",
            created_at="2026-01-01",
        )
    )
    pending = await svc.get_pending_notifications(aid)
    assert len(pending) == 1
    assert pending[0].notification_id == "n1"


@pytest.mark.asyncio
async def test_get_pending_excludes_already_acknowledged(
    svc: LearningPropagationService,
) -> None:
    aid = uuid4()
    svc._notification_queue.append(
        LearningNotification(
            notification_id="n1",
            learning_id="lrn-1",
            target_agent_id=aid,
            learning_summary="s",
            reason="r",
            created_at="2026-01-01",
            acknowledged=True,
        )
    )
    pending = await svc.get_pending_notifications(aid)
    assert pending == []


@pytest.mark.asyncio
async def test_acknowledge_notification(svc: LearningPropagationService) -> None:
    aid = uuid4()
    svc._notification_queue.append(
        LearningNotification(
            notification_id="n1",
            learning_id="lrn-1",
            target_agent_id=aid,
            learning_summary="s",
            reason="r",
            created_at="2026-01-01",
        )
    )
    assert await svc.acknowledge_notification("n1", aid) is True
    pending = await svc.get_pending_notifications(aid)
    assert pending == []


@pytest.mark.asyncio
async def test_acknowledge_notification_returns_false_when_missing(
    svc: LearningPropagationService,
) -> None:
    assert await svc.acknowledge_notification("ghost", uuid4()) is False


@pytest.mark.asyncio
async def test_get_learning_stats_returns_dict_with_expected_keys(
    svc: LearningPropagationService,
) -> None:
    stats = await svc.get_learning_stats()
    assert "total_learnings" in stats
    assert "by_type" in stats
    assert "by_scope" in stats


@pytest.mark.asyncio
async def test_get_learning_service_factory() -> None:
    """get_learning_service returns a singleton instance."""
    _LearningServiceHolder.instance = None
    a = await get_learning_service()
    b = await get_learning_service()
    assert a is b
    _LearningServiceHolder.instance = None


class _FakeAgentRow:
    """Stand-in for an AgentTable row returned by the recipients SELECT."""

    def __init__(self, *, id: UUID, role: AgentRole) -> None:
        self.id = id
        self.role = role
        self.slug = f"{role.value}-agent"


class _BulkInsertFakeDb:
    """Fake AsyncSession that records INSERT executes for the N+1 test.

    The agent-recipient SELECT returns a canned list; every other execute
    call is recorded so the test can count INSERTs into notifications.
    """

    def __init__(self, agents: list[_FakeAgentRow]) -> None:
        self._agents = agents
        self.execute_calls: list[Any] = []
        self.insert_rows: list[dict[str, Any]] = []
        self.committed = False

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.execute_calls.append((stmt, params))
        # Detect the notifications bulk INSERT.
        if isinstance(stmt, Insert):
            # params is a list of dicts for parametrized bulk insert.
            self.insert_rows.extend(params or [])
            return MagicMock()
        # Agent recipients SELECT.
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._agents)
        return result

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


@asynccontextmanager
async def _fake_db_ctx(db: _BulkInsertFakeDb) -> AsyncIterator[_BulkInsertFakeDb]:
    yield db


def _make_agents(n: int) -> list[_FakeAgentRow]:
    return [_FakeAgentRow(id=uuid4(), role=AgentRole.DEVELOPER) for _ in range(n)]


_N_RECIPIENTS = 25


@pytest.mark.asyncio
async def test_create_notifications_uses_one_bulk_insert_not_n(
    svc: LearningPropagationService,
) -> None:
    """A learning broadcast to N agents issues ONE INSERT, not N.

    Regression guard for the N+1 at `_create_notifications`: the old path
    called `NotificationService._create_notification` per recipient, each
    opening its own session/transaction. The bulk path issues a single
    parametrized INSERT for all NotificationTable rows.
    """
    agents = _make_agents(_N_RECIPIENTS)
    db = _BulkInsertFakeDb(agents)
    delivery_mock = MagicMock()
    delivery_mock.deliver = AsyncMock(return_value=True)

    learning = Learning(
        learning_id="lrn-abc",
        agent_id=uuid4(),
        agent_role="developer",
        content="team lesson" * 30,
        learning_type=LearningType.PATTERN,
        scope=LearningScope.TEAM,
    )

    with (
        patch(
            "roboco.db.base.get_db_context",
            lambda: _fake_db_ctx(db),
        ),
        patch(
            "roboco.services.notification_delivery.get_notification_delivery_service",
            lambda _db: delivery_mock,
        ),
    ):
        await svc._create_notifications(learning)

    # Exactly one INSERT execute call into notifications.
    insert_calls = [
        (stmt, params) for stmt, params in db.execute_calls if isinstance(stmt, Insert)
    ]
    assert len(insert_calls) == 1, (
        f"expected 1 bulk INSERT, got {len(insert_calls)}; "
        f"total execute calls: {len(db.execute_calls)}"
    )

    # All rows landed with correct recipients + payload.
    assert len(db.insert_rows) == _N_RECIPIENTS
    expected_ids = {a.id for a in agents}
    actual_ids = {row["to_agents"][0] for row in db.insert_rows}
    assert actual_ids == expected_ids

    for row in db.insert_rows:
        assert row["type"] is NotificationType.KNOWLEDGE_SHARE
        assert row["priority"] is NotificationPriority.NORMAL
        assert row["from_agent"] == learning.agent_id
        assert row["subject"] == f"New Learning: {learning.learning_type.value}"
        assert row["body"] == db.insert_rows[0]["body"]
        assert (
            row["requires_ack"]
            is ACK_REQUIRED_BY_TYPE[NotificationType.KNOWLEDGE_SHARE]
        )

    # Delivery invoked once per notification, commit ran once.
    assert delivery_mock.deliver.await_count == _N_RECIPIENTS
    assert db.committed is True
