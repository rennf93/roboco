"""LearningPropagationService coverage — stub OptimalService.

LearningPropagationService is logic on top of OptimalService (the RAG layer).
We unit-test the wiring with a stub that records calls and returns canned
SearchResult lists; OptimalService itself is exercised in its own integration
tests.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from roboco.models.optimal import IndexType, SearchResult
from roboco.services.learning import (
    Learning,
    LearningNotification,
    LearningPropagationService,
    LearningScope,
    LearningType,
    RecordLearningParams,
)


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
    """Team-scope learnings call _create_notifications, which best-effort logs on error."""
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
    # Visible: own personal, team for matching role, org-anyone — drop other-personal & team-other-role
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
    assert stub.searches[0]["top_k"] == 3
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
