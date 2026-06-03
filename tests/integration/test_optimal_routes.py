"""Optimal API route coverage — RAG, KB, mentor, errors, decisions."""

from __future__ import annotations

import json
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context
from roboco.api.routes.optimal import check_staleness
from roboco.api.routes.optimal import router as optimal_router
from roboco.models import AgentRole, Team
from roboco.models.optimal import IndexType, SearchResult
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def optimal_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(optimal_router, prefix="/api/optimal")

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=AgentRole.CEO, team=None)

    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def dev_optimal_client() -> AsyncIterator[AsyncClient]:
    """Optimal client as SYSTEM — has no KB permissions."""
    app = FastAPI()
    app.include_router(optimal_router, prefix="/api/optimal")

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=AgentRole.SYSTEM, team=None)

    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "ceo"}
_DEV_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "system"}


def _search_result(idx: IndexType = IndexType.DOCUMENTATION) -> SearchResult:
    return SearchResult(
        content="some content",
        source="src",
        score=0.9,
        index_type=idx,
    )


# ---------------------------------------------------------------------------
# Index endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_code_forbidden(dev_optimal_client: AsyncClient) -> None:
    response = await dev_optimal_client.post(
        "/api/optimal/kb/index/code",
        json={"sources": ["a.py"]},
        headers=_DEV_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_index_code_forbidden_envelope_remediate(
    dev_optimal_client: AsyncClient,
) -> None:
    """A denied KB call returns an Envelope-shaped body with a real remediate.

    Agents go through the gateway Envelope contract: a denial must carry
    error="not_authorized" plus a non-null remediate hint so the agent can
    recover, not a bare {"detail": ...} HTTPException body.
    """
    response = await dev_optimal_client.post(
        "/api/optimal/kb/index/code",
        json={"sources": ["a.py"]},
        headers=_DEV_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    body = response.json()
    assert body["error"] == "not_authorized"
    assert body["message"]
    assert body["remediate"] is not None
    assert body["remediate"].strip()


@pytest.mark.asyncio
async def test_index_code_success(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.index_code = AsyncMock(return_value=3)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/kb/index/code",
            json={"sources": ["a.py"], "project": "p"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.CREATED
    _CODE_INDEXED = 3
    assert response.json()["indexed"] == _CODE_INDEXED


@pytest.mark.asyncio
async def test_index_docs_forbidden(dev_optimal_client: AsyncClient) -> None:
    response = await dev_optimal_client.post(
        "/api/optimal/kb/index/docs",
        json={"sources": ["a.md"]},
        headers=_DEV_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_index_docs_success(optimal_client: AsyncClient) -> None:
    _DOCS_INDEXED = 5
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.index_documentation = AsyncMock(return_value=_DOCS_INDEXED)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/kb/index/docs",
            json={"sources": ["a.md"]},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.CREATED
    assert response.json()["indexed"] == _DOCS_INDEXED


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_invalid_index_type(optimal_client: AsyncClient) -> None:
    response = await optimal_client.post(
        "/api/optimal/kb/search",
        json={"query": "q", "index_types": ["BOGUS"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_search_success(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search = AsyncMock(return_value=[_search_result()])
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/kb/search",
            json={"query": "find x", "top_k": 5},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_search_with_index_types(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search = AsyncMock(return_value=[])
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/kb/search",
            json={"query": "q", "index_types": ["documentation"]},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_find_similar(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search = AsyncMock(return_value=[_search_result()])
        mock_get.return_value = mock_service
        response = await optimal_client.get(
            "/api/optimal/kb/similar?source=foo.py", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_query_invalid_index_type(optimal_client: AsyncClient) -> None:
    response = await optimal_client.post(
        "/api/optimal/rag/query",
        json={"query": "q", "index_types": ["bogus"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_rag_query_success(optimal_client: AsyncClient) -> None:
    rag_response = SimpleNamespace(
        answer="Here is the answer",
        citations=[_search_result()],
        query="q",
        context_used=1,
        search_stats={"docs": 1},
        search_errors=None,
    )
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.query = AsyncMock(return_value=rag_response)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/rag/query",
            json={"query": "q"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_rag_query_runtime_error(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.query = AsyncMock(side_effect=RuntimeError("not init"))
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/rag/query",
            json={"query": "q"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_rag_query_unexpected_error(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.query = AsyncMock(side_effect=ValueError("oops"))
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/rag/query",
            json={"query": "q"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_rag_context_invalid_index_type(optimal_client: AsyncClient) -> None:
    response = await optimal_client.post(
        "/api/optimal/rag/context",
        json={"query": "q", "index_types": ["bogus"]},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_rag_context_success(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search = AsyncMock(return_value=[_search_result()])
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/rag/context",
            json={"query": "q"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stats_forbidden(dev_optimal_client: AsyncClient) -> None:
    response = await dev_optimal_client.get("/api/optimal/stats", headers=_DEV_HDR)
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_get_stats_success(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.get_all_index_stats = AsyncMock(
            return_value={"initialized": True, "indexes": {}}
        )
        mock_get.return_value = mock_service
        response = await optimal_client.get("/api/optimal/stats", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_single_stats_forbidden(
    dev_optimal_client: AsyncClient,
) -> None:
    response = await dev_optimal_client.get(
        "/api/optimal/stats/documentation", headers=_DEV_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_get_single_stats_invalid_type(optimal_client: AsyncClient) -> None:
    response = await optimal_client.get("/api/optimal/stats/bogus", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_get_single_stats_success(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.get_index_stats = AsyncMock(
            return_value={
                "index_type": "documentation",
                "document_count": 1,
                "chunk_count": 5,
                "last_updated": None,
            }
        )
        mock_get.return_value = mock_service
        response = await optimal_client.get(
            "/api/optimal/stats/documentation", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_check_staleness_via_http(optimal_client: AsyncClient) -> None:
    """`/stats/staleness` is now declared before `/stats/{index_type}`, so it
    routes correctly to `check_staleness` instead of being matched as
    `index_type=staleness` (which would 400 as invalid IndexType)."""
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.check_index_staleness = AsyncMock(
            return_value={"stale": False, "indexes": {}}
        )
        mock_get.return_value = mock_service
        response = await optimal_client.get(
            "/api/optimal/stats/staleness", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"stale": False, "indexes": {}}


@pytest.mark.asyncio
async def test_check_staleness_helper_authorized() -> None:
    """Direct invocation of the check_staleness coroutine (bypass route order)."""

    agent = AgentContext(agent_id=uuid4(), role=AgentRole.CEO, team=None)

    fake_perm = MagicMock()
    fake_perm.can_perform_kb_action.return_value = True
    with patch(
        "roboco.api.routes.optimal.get_optimal_service",
        AsyncMock(
            return_value=AsyncMock(
                check_index_staleness=AsyncMock(return_value={"stale": False})
            )
        ),
    ):
        result = await check_staleness(agent=agent, permissions=fake_perm)
    assert result == {"stale": False}


@pytest.mark.asyncio
async def test_check_staleness_helper_unauthorized() -> None:
    """KB VIEW_STATS denied → gateway Envelope JSONResponse (HTTP 403).

    The authorization decision now lives in the gateway; the route returns
    the Envelope wire-dict (with a non-null remediate) at top level instead
    of raising a bare HTTPException.
    """

    agent = AgentContext(agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND)

    fake_perm = MagicMock()
    fake_perm.can_perform_kb_action.return_value = False
    result = await check_staleness(agent=agent, permissions=fake_perm)
    assert isinstance(result, JSONResponse)
    assert result.status_code == HTTPStatus.FORBIDDEN
    body = json.loads(bytes(result.body))
    assert body["error"] == "not_authorized"
    assert body["remediate"] is not None
    assert body["remediate"].strip()


@pytest.mark.asyncio
async def test_health_check(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.check_health = AsyncMock(
            return_value=(True, True, True, {"version": "1"})
        )
        mock_get.return_value = mock_service
        response = await optimal_client.get("/api/optimal/health", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    assert response.json()["healthy"] is True


# ---------------------------------------------------------------------------
# Clear/refresh/reindex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_index_forbidden(dev_optimal_client: AsyncClient) -> None:
    response = await dev_optimal_client.delete(
        "/api/optimal/kb/documentation", headers=_DEV_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_clear_index_invalid_type(optimal_client: AsyncClient) -> None:
    response = await optimal_client.delete("/api/optimal/kb/bogus", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_clear_index_success(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.clear_index = AsyncMock(return_value=None)
        mock_get.return_value = mock_service
        response = await optimal_client.delete(
            "/api/optimal/kb/documentation", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_documents_forbidden(
    dev_optimal_client: AsyncClient,
) -> None:
    response = await dev_optimal_client.get(
        "/api/optimal/kb/documentation/documents", headers=_DEV_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_list_documents_invalid_type(optimal_client: AsyncClient) -> None:
    response = await optimal_client.get("/api/optimal/kb/bogus/documents", headers=_HDR)
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_list_documents_success(optimal_client: AsyncClient) -> None:
    docs = [
        {
            "id": "1",
            "source": "x.md",
            "indexed_at": "2026-01-01T00:00:00Z",
            "title": "T",
            "preview": "p",
            "chunk_count": 1,
            "extra_data": {},
        }
    ]
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.list_indexed_documents = AsyncMock(return_value=(docs, 1))
        mock_get.return_value = mock_service
        response = await optimal_client.get(
            "/api/optimal/kb/documentation/documents", headers=_HDR
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_refresh_index_forbidden(
    dev_optimal_client: AsyncClient,
) -> None:
    response = await dev_optimal_client.post(
        "/api/optimal/kb/refresh",
        json={"index_type": "documentation"},
        headers=_DEV_HDR,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_refresh_index_invalid_type(optimal_client: AsyncClient) -> None:
    response = await optimal_client.post(
        "/api/optimal/kb/refresh",
        json={"index_type": "bogus"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_refresh_index_with_sources(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.refresh_index = AsyncMock(return_value=None)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/kb/refresh",
            json={"index_type": "documentation", "sources": ["a.md"]},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_refresh_index_empty_sources(optimal_client: AsyncClient) -> None:
    """Empty sources -> service.get_indexed_sources_for is called."""
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.get_indexed_sources_for = AsyncMock(return_value=["x.md"])
        mock_service.refresh_index = AsyncMock(return_value=None)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/kb/refresh",
            json={"index_type": "documentation"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_reindex_all_forbidden(dev_optimal_client: AsyncClient) -> None:
    response = await dev_optimal_client.post(
        "/api/optimal/kb/reindex", headers=_DEV_HDR
    )
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_reindex_all_success(optimal_client: AsyncClient) -> None:
    report = MagicMock()
    report.to_dict = MagicMock(return_value={"summary": "done"})
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service._auto_index_on_startup = AsyncMock(return_value=report)
        mock_get.return_value = mock_service
        response = await optimal_client.post("/api/optimal/kb/reindex", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_prompt_template(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = MagicMock()
        mock_service.create_prompt_template = MagicMock(
            return_value={
                "id": "1",
                "name": "T",
                "template": "Hello {name}",
                "description": None,
                "variables": ["name"],
                "category": None,
                "created_at": "2026-01-01T00:00:00Z",
            }
        )

        # get_optimal_service is async — wrap in async return
        async def _ret():
            return mock_service

        mock_get.side_effect = _ret
        response = await optimal_client.post(
            "/api/optimal/prompts",
            json={"name": "T", "template": "Hello {name}", "variables": ["name"]},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_list_prompt_templates(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = MagicMock()
        mock_service.list_prompt_templates = MagicMock(return_value=[])

        async def _ret():
            return mock_service

        mock_get.side_effect = _ret
        response = await optimal_client.get("/api/optimal/prompts", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Mentor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mentor_ask_init_failure(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_mentor_service") as mock_mentor:
        mock_mentor.side_effect = RuntimeError("boom")
        response = await optimal_client.post(
            "/api/optimal/mentor/ask",
            json={"question": "What"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_mentor_ask_success(optimal_client: AsyncClient) -> None:
    mentor = AsyncMock()
    mentor._optimal_service = None
    mentor.initialize = AsyncMock()
    mentor.ask = AsyncMock(
        return_value=SimpleNamespace(
            answer="42",
            sources=[_search_result()],
            conversation_id="conv-1",
            suggested_followups=[],
            search_stats=None,
            search_errors=None,
        )
    )
    with (
        patch("roboco.api.routes.optimal.get_mentor_service") as mock_mentor,
        patch("roboco.api.routes.optimal.get_optimal_service") as mock_get,
    ):
        mock_mentor.return_value = mentor
        mock_get.return_value = AsyncMock()
        response = await optimal_client.post(
            "/api/optimal/mentor/ask",
            json={"question": "What"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_errors_success(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search_errors = AsyncMock(
            return_value=[_search_result(IndexType.ERRORS)]
        )
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/errors/search",
            json={"error_message": "TypeError"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_record_error(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.index_error = AsyncMock(return_value=None)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/errors/record",
            json={
                "error_message": "Boom",
                "context": "ctx",
                "solution": "fix",
                "worked": True,
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "recorded"


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_decision_with_objects(optimal_client: AsyncClient) -> None:
    decisions = [
        SimpleNamespace(
            topic="x",
            decision="use Y",
            rationale="best fit",
            context="ctx",
        ),
    ]
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.check_decision = AsyncMock(return_value=decisions)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/decisions/check",
            json={"topic": "x"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["has_precedent"] is True


@pytest.mark.asyncio
async def test_check_decision_with_dicts(optimal_client: AsyncClient) -> None:
    decisions = [{"topic": "x", "decision": "Y"}]
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.check_decision = AsyncMock(return_value=decisions)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/decisions/check",
            json={"topic": "x"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_check_decision_empty(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.check_decision = AsyncMock(return_value=[])
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/decisions/check",
            json={"topic": "x"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["has_precedent"] is False


@pytest.mark.asyncio
async def test_record_decision(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.index_decision = AsyncMock(return_value=None)
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/decisions/record",
            json={
                "topic": "Use Y",
                "decision": "go with Y",
                "rationale": "best",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Standards / validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_standards(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.get_standards = AsyncMock(
            return_value=[_search_result(IndexType.STANDARDS)]
        )
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/standards/get",
            json={"domain": "security"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_validate_action_python(optimal_client: AsyncClient) -> None:
    """Action context with `def ` triggers python detection."""
    validator = AsyncMock()
    validator._optimal_service = None
    validator.initialize = AsyncMock()
    validator.validate = AsyncMock(
        return_value=SimpleNamespace(
            allowed=True,
            violations=[],
            warnings=[],
            relevant_standards=[],
        )
    )
    with (
        patch("roboco.api.routes.optimal.get_validator_service") as mock_validator,
        patch("roboco.api.routes.optimal.get_optimal_service") as mock_optimal,
    ):
        mock_validator.return_value = validator
        mock_optimal.return_value = AsyncMock()
        response = await optimal_client.post(
            "/api/optimal/standards/validate",
            json={"action_type": "commit", "context": "def hello(): pass"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_validate_action_typescript(optimal_client: AsyncClient) -> None:
    """Action context with `function ` triggers typescript detection."""
    validator = AsyncMock()
    validator._optimal_service = AsyncMock()  # already initialized
    validator.validate = AsyncMock(
        return_value=SimpleNamespace(
            allowed=False,
            violations=[
                {
                    "rule_id": "R1",
                    "rule_title": "title",
                    "message": "msg",
                    "severity": "error",
                    "line_number": 1,
                    "suggestion": "fix it",
                }
            ],
            warnings=[
                {
                    "rule_id": "W1",
                    "rule_title": "warn",
                    "message": "wmsg",
                    "severity": "warning",
                    "line_number": None,
                    "suggestion": None,
                }
            ],
            relevant_standards=[_search_result(IndexType.STANDARDS)],
        )
    )
    with (
        patch("roboco.api.routes.optimal.get_validator_service") as mock_validator,
        patch("roboco.api.routes.optimal.get_optimal_service") as mock_optimal,
    ):
        mock_validator.return_value = validator
        mock_optimal.return_value = AsyncMock()
        response = await optimal_client.post(
            "/api/optimal/standards/validate",
            json={"action_type": "commit", "context": "function foo() {}"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["allowed"] is False


# ---------------------------------------------------------------------------
# Code review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_code(optimal_client: AsyncClient) -> None:
    reviewer = AsyncMock()
    reviewer._optimal_service = None
    reviewer.initialize = AsyncMock()
    reviewer.review_code = AsyncMock(
        return_value=SimpleNamespace(
            file_path="a.py",
            approved=True,
            score=95.0,
            comments=[],
            standards_checked=[],
            similar_reviews=[],
        )
    )
    with (
        patch("roboco.api.routes.optimal.get_reviewer_service") as mock_rev,
        patch("roboco.api.routes.optimal.get_optimal_service") as mock_optimal,
    ):
        mock_rev.return_value = reviewer
        mock_optimal.return_value = AsyncMock()
        response = await optimal_client.post(
            "/api/optimal/review/code",
            json={
                "code": "def f(): pass",
                "file_path": "a.py",
                "change_type": "modify",
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_estimate_tokens(optimal_client: AsyncClient) -> None:
    response = await optimal_client.post(
        "/api/optimal/tokens/estimate",
        json={"content": "Hello world", "model": "claude-opus-4-7"},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["token_count"] >= 1


# ---------------------------------------------------------------------------
# Learnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_learning(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.record_learning = AsyncMock(return_value="learn-1")
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/learnings/record",
            json={
                "content": "Something useful",
                "category": "patterns",
                "shareable": True,
            },
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_search_learnings(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search_learnings = AsyncMock(
            return_value=[_search_result(IndexType.LEARNINGS)]
        )
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/learnings/search",
            json={"query": "patterns"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# Proactive context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_timeout(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search = AsyncMock(side_effect=TimeoutError("timeout"))
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/kb/search",
            json={"query": "q"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT


@pytest.mark.asyncio
async def test_rag_query_timeout(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.query = AsyncMock(side_effect=TimeoutError("timeout"))
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/rag/query",
            json={"query": "q"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT


@pytest.mark.asyncio
async def test_rag_context_timeout(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service.search = AsyncMock(side_effect=TimeoutError("timeout"))
        mock_get.return_value = mock_service
        response = await optimal_client.post(
            "/api/optimal/rag/context",
            json={"query": "q"},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT


@pytest.mark.asyncio
async def test_reindex_all_timeout(optimal_client: AsyncClient) -> None:
    with patch("roboco.api.routes.optimal.get_optimal_service") as mock_get:
        mock_service = AsyncMock()
        mock_service._auto_index_on_startup = AsyncMock(
            side_effect=TimeoutError("timeout")
        )
        mock_get.return_value = mock_service
        response = await optimal_client.post("/api/optimal/kb/reindex", headers=_HDR)
    assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT


@pytest.mark.asyncio
async def test_get_proactive_context(optimal_client: AsyncClient) -> None:
    proactive = AsyncMock()
    proactive.get_context_for_task = AsyncMock(
        return_value=SimpleNamespace(
            task_id=uuid4(),
            agent_id=uuid4(),
            similar_tasks=[],
            relevant_learnings=[_search_result()],
            code_patterns=[],
            applicable_standards=[],
            recent_decisions=[],
            known_issues=[],
            summary="all good",
        )
    )
    with patch("roboco.services.proactive.get_proactive_service") as mock_get:
        mock_get.return_value = proactive
        response = await optimal_client.post(
            "/api/optimal/context/proactive",
            json={"task_id": str(uuid4())},
            headers=_HDR,
        )
    assert response.status_code == HTTPStatus.OK
