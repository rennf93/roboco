"""roboco.api.routes.research — role gate, quota, and error mapping.

Calls the route coroutines directly with a constructed AgentContext (the same
style as test_company_goals_routes) so no app/DB wiring is needed; the service
and quota tracker are patched.
"""

from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.routes import research as research_route
from roboco.api.schemas.research import FetchRequest, SearchRequest
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.research import (
    FetchOutcome,
    ResearchError,
    ResearchUnsupportedError,
    SearchHit,
    SearchOutcome,
)
from roboco.services.research_quota import QuotaStatus


def _agent(role: AgentRole) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=None)


class _FakeService:
    def __init__(
        self,
        *,
        search_outcome: SearchOutcome | None = None,
        fetch_outcome: FetchOutcome | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._search_outcome = search_outcome
        self._fetch_outcome = fetch_outcome
        self._exc = exc
        self.closed = False

    async def search(self, _query: str, _max_results: int | None) -> SearchOutcome:
        if self._exc is not None:
            raise self._exc
        assert self._search_outcome is not None
        return self._search_outcome

    async def fetch(self, _url: str, _max_chars: int | None) -> FetchOutcome:
        if self._exc is not None:
            raise self._exc
        assert self._fetch_outcome is not None
        return self._fetch_outcome

    async def close(self) -> None:
        self.closed = True


def _allow_quota(monkeypatch: pytest.MonkeyPatch, *, allowed: bool = True) -> None:
    async def _check(_agent_id: str, limit: int, **_: object) -> QuotaStatus:
        return QuotaStatus(allowed=allowed, used=1, limit=limit, day="2026-06-15")

    monkeypatch.setattr(research_route._quota_tracker, "check_and_consume", _check)


def _install_service(monkeypatch: pytest.MonkeyPatch, service: _FakeService) -> None:
    monkeypatch.setattr(research_route, "get_research_service", lambda: service)


@pytest.mark.asyncio
async def test_non_research_role_is_forbidden() -> None:
    with pytest.raises(HTTPException) as exc:
        await research_route.research_search(
            SearchRequest(query="x"), _agent(AgentRole.DEVELOPER)
        )
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_search_success_maps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_quota(monkeypatch)
    service = _FakeService(
        search_outcome=SearchOutcome(
            query="q",
            hits=[SearchHit(title="T", url="https://t.test", snippet="s", score=0.7)],
            answer="ans",
            provider="tavily",
        )
    )
    _install_service(monkeypatch, service)
    resp = await research_route.research_search(
        SearchRequest(query="q"), _agent(AgentRole.PRODUCT_OWNER)
    )
    assert resp.provider == "tavily"
    assert resp.answer == "ans"
    assert resp.results[0].url == "https://t.test"
    assert service.closed is True


@pytest.mark.asyncio
async def test_quota_exhausted_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_quota(monkeypatch, allowed=False)
    with pytest.raises(HTTPException) as exc:
        await research_route.research_search(
            SearchRequest(query="q"), _agent(AgentRole.MAIN_PM)
        )
    assert exc.value.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_provider_error_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_quota(monkeypatch)
    _install_service(monkeypatch, _FakeService(exc=ResearchError("boom")))
    with pytest.raises(HTTPException) as exc:
        await research_route.research_search(
            SearchRequest(query="q"), _agent(AgentRole.CELL_PM)
        )
    assert exc.value.status_code == HTTPStatus.BAD_GATEWAY


@pytest.mark.asyncio
async def test_fetch_unsupported_returns_501(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_quota(monkeypatch)
    _install_service(
        monkeypatch, _FakeService(exc=ResearchUnsupportedError("no fetch"))
    )
    with pytest.raises(HTTPException) as exc:
        await research_route.research_fetch(
            FetchRequest(url="https://x.test"), _agent(AgentRole.PRODUCT_OWNER)
        )
    assert exc.value.status_code == HTTPStatus.NOT_IMPLEMENTED


@pytest.mark.asyncio
async def test_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_quota(monkeypatch)
    service = _FakeService(
        fetch_outcome=FetchOutcome(
            url="https://x.test", content="body", truncated=False, provider="exa"
        )
    )
    _install_service(monkeypatch, service)
    resp = await research_route.research_fetch(
        FetchRequest(url="https://x.test"), _agent(AgentRole.HEAD_MARKETING)
    )
    assert resp.content == "body"
    assert resp.provider == "exa"
