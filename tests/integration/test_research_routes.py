"""roboco.api.routes.research — role gate, quota, and error mapping.

Most tests call the route coroutines directly with a constructed AgentContext
(the same style as test_company_goals_routes) so no app/DB wiring is needed;
the service and quota tracker are patched. The refusal tests at the end go end
to end instead: a real SearXNG adapter over ``httpx.MockTransport``, the route,
and the app's HTTPException handler, so they pin the status and the JSON body
an agent's ``web_search`` / ``web_fetch`` call gets back.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from roboco.api.deps import get_agent_context
from roboco.api.middleware import http_exception_handler
from roboco.api.routes import research as research_route
from roboco.api.schemas.research import FetchRequest, SearchRequest
from roboco.api.utils import research as research_utils
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.research import (
    FetchOutcome,
    ResearchService,
    SearchHit,
    SearchOutcome,
    SearXNGProvider,
)
from roboco.services.research_errors import (
    ResearchError,
    ResearchProviderError,
    ResearchSiteError,
    ResearchUnsupportedError,
    ResearchUrlRefusedError,
)
from roboco.services.research_quota import QuotaStatus

from tests.unit.research_fakes import (
    PLAIN_NOT_FOUND_HTML,
    PUBLIC_IP,
    READABLE_PAGE_HTML,
    VERCEL_CHECKPOINT_HTML,
    fake_dns,
    page,
    redirect,
    site_routes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_SEARXNG_BASE = "http://searxng.internal:8080"
_START = "https://a.example.com/start"


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

    monkeypatch.setattr(research_utils._quota_tracker, "check_and_consume", _check)


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


@pytest.mark.parametrize(
    ("error", "status"),
    [
        pytest.param(
            ResearchUnsupportedError("u"), HTTPStatus.NOT_IMPLEMENTED, id="501"
        ),
        pytest.param(ResearchUrlRefusedError("r"), HTTPStatus.BAD_REQUEST, id="400"),
        pytest.param(ResearchSiteError("s"), HTTPStatus.FAILED_DEPENDENCY, id="424"),
        pytest.param(ResearchProviderError("p"), HTTPStatus.BAD_GATEWAY, id="502"),
        pytest.param(ResearchError("e"), HTTPStatus.BAD_GATEWAY, id="unclassified"),
    ],
)
def test_research_errors_answer_the_status_of_the_party_that_failed(
    error: ResearchError, status: HTTPStatus
) -> None:
    """The detail is the error's own message: no "provider error" prefix."""
    http_error = research_utils.research_error_to_http(error)
    assert http_error.status_code == status
    assert http_error.detail == str(error)


# --------------------------------------------------------------------------- #
# End to end: SearXNG adapter -> route -> HTTPException handler -> JSON body
# --------------------------------------------------------------------------- #


def _app_over_searxng(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    """An app serving the research routes over a SearXNG adapter whose HTTP
    goes to ``handler``; the caller is a Product Owner with quota left."""
    _allow_quota(monkeypatch)
    provider_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = SearXNGProvider(
        base_url=_SEARXNG_BASE, timeout=5.0, client=provider_client
    )
    monkeypatch.setattr(
        research_route,
        "get_research_service",
        lambda: ResearchService(provider, max_results_cap=5, fetch_max_chars_cap=100),
    )
    app = FastAPI()
    app.include_router(research_route.router, prefix="/api/research")
    app.add_exception_handler(HTTPException, http_exception_handler)

    async def _product_owner() -> AgentContext:
        return _agent(AgentRole.PRODUCT_OWNER)

    app.dependency_overrides[get_agent_context] = _product_owner
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _post(
    client: httpx.AsyncClient, path: str, body: dict[str, Any]
) -> httpx.Response:
    async with client:
        return await client.post(f"/api/research/{path}", json=body)


def _connection_refused(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 111] Connection refused")


@pytest.mark.asyncio
async def test_fetch_vercel_checkpoint_answers_424_naming_the_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-10-28 refusal, end to end: a 424 that names the site, the URL,
    the 403, the page title and the bot check - not a SearXNG "provider error"."""
    fake_dns(monkeypatch, {"app.example.com": PUBLIC_IP})
    client = _app_over_searxng(
        monkeypatch, lambda _r: httpx.Response(403, text=VERCEL_CHECKPOINT_HTML)
    )
    response = await _post(client, "fetch", {"url": "https://app.example.com/pricing"})
    assert response.status_code == HTTPStatus.FAILED_DEPENDENCY
    assert response.json() == {
        "error": {
            "code": "DEPENDENCY_FAILED",
            "message": (
                "Site app.example.com answered HTTP 403 Forbidden to the page "
                "fetch of https://app.example.com/pricing (page title: Vercel "
                "Security Checkpoint). This is Vercel's bot-check (challenge) "
                "page: the site blocks automated fetches, so get this "
                "information from another source."
            ),
        }
    }


@pytest.mark.asyncio
async def test_fetch_plain_404_answers_424_naming_the_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"docs.example.org": PUBLIC_IP})
    client = _app_over_searxng(
        monkeypatch, lambda _r: httpx.Response(404, text=PLAIN_NOT_FOUND_HTML)
    )
    response = await _post(client, "fetch", {"url": "https://docs.example.org/x"})
    assert response.status_code == HTTPStatus.FAILED_DEPENDENCY
    assert response.json()["error"] == {
        "code": "DEPENDENCY_FAILED",
        "message": (
            "Site docs.example.org answered HTTP 404 Not Found to the page fetch "
            "of https://docs.example.org/x (response text: Not Found The "
            "requested URL was not found on this server.)."
        ),
    }


@pytest.mark.asyncio
async def test_fetch_unreachable_site_answers_424_naming_the_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"down.example.com": PUBLIC_IP})
    client = _app_over_searxng(monkeypatch, _connection_refused)
    response = await _post(client, "fetch", {"url": "https://down.example.com/p"})
    assert response.status_code == HTTPStatus.FAILED_DEPENDENCY
    assert response.json()["error"]["message"] == (
        "No answer from site down.example.com to the page fetch of "
        "https://down.example.com/p: ConnectError: [Errno 111] Connection refused."
    )


@pytest.mark.asyncio
async def test_search_provider_500_answers_502_naming_searxng(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _app_over_searxng(
        monkeypatch,
        lambda _r: httpx.Response(
            500, text="<html><head><title>500 Internal Server Error</title></head>"
        ),
    )
    response = await _post(client, "search", {"query": "agentic frameworks"})
    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json()["error"]["message"] == (
        "Research provider searxng answered HTTP 500 Internal Server Error to "
        f"GET {_SEARXNG_BASE}/search (page title: 500 Internal Server Error)."
    )


@pytest.mark.asyncio
async def test_search_unreachable_provider_answers_502_naming_searxng(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _app_over_searxng(monkeypatch, _connection_refused)
    response = await _post(client, "search", {"query": "agentic frameworks"})
    assert response.status_code == HTTPStatus.BAD_GATEWAY
    assert response.json()["error"]["message"] == (
        f"No answer from research provider searxng to GET {_SEARXNG_BASE}/search: "
        "ConnectError: [Errno 111] Connection refused."
    )


@pytest.mark.asyncio
async def test_fetch_url_guard_refusal_answers_400_naming_roboco(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same 400 the route-level internal_ssrf_validator answers."""
    client = _app_over_searxng(monkeypatch, _connection_refused)
    response = await _post(client, "fetch", {"url": f"ftp://{PUBLIC_IP}/file"})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["error"] == {
        "code": "INVALID_INPUT",
        "message": (
            f"RoboCo's URL guard refused 'ftp://{PUBLIC_IP}/file': only http(s) "
            "URLs are fetched."
        ),
    }


@pytest.mark.asyncio
async def test_fetch_redirect_to_a_non_public_address_answers_400_naming_the_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard checks every redirect hop: the internal hop is never requested."""
    fake_dns(
        monkeypatch,
        {"a.example.com": PUBLIC_IP, "169.254.169.254": "169.254.169.254"},
    )
    target = "http://169.254.169.254/latest/meta-data/"
    requested: list[str] = []
    client = _app_over_searxng(
        monkeypatch, site_routes({_START: redirect(target)}, requested)
    )
    response = await _post(client, "fetch", {"url": _START})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["error"] == {
        "code": "INVALID_INPUT",
        "message": (
            f"RoboCo's URL guard refused the redirect from {_START} to "
            f"{target!r}: it resolves to a non-public address."
        ),
    }
    assert requested == [_START]


@pytest.mark.asyncio
async def test_fetch_redirect_to_a_public_page_answers_200_with_the_final_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"a.example.com": PUBLIC_IP, "b.example.com": PUBLIC_IP})
    landing = "https://b.example.com/landing"
    requested: list[str] = []
    client = _app_over_searxng(
        monkeypatch,
        site_routes(
            {_START: redirect(landing), landing: page(READABLE_PAGE_HTML)}, requested
        ),
    )
    response = await _post(client, "fetch", {"url": _START})
    assert response.status_code == HTTPStatus.OK
    assert response.json()["url"] == landing
    assert "Hello readable world." in response.json()["content"]


@pytest.mark.asyncio
async def test_fetch_relative_redirect_answers_200_with_the_resolved_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"a.example.com": PUBLIC_IP})
    resolved = "https://a.example.com/landing"
    requested: list[str] = []
    client = _app_over_searxng(
        monkeypatch,
        site_routes(
            {_START: redirect("/landing"), resolved: page(READABLE_PAGE_HTML)},
            requested,
        ),
    )
    response = await _post(client, "fetch", {"url": _START})
    assert response.status_code == HTTPStatus.OK
    assert response.json()["url"] == resolved


@pytest.mark.asyncio
async def test_fetch_redirect_loop_answers_424_saying_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"a.example.com": PUBLIC_IP})
    loop = "https://a.example.com/loop"
    requested: list[str] = []
    client = _app_over_searxng(
        monkeypatch,
        site_routes({_START: redirect(loop), loop: redirect(_START)}, requested),
    )
    response = await _post(client, "fetch", {"url": _START})
    assert response.status_code == HTTPStatus.FAILED_DEPENDENCY
    assert response.json()["error"]["code"] == "DEPENDENCY_FAILED"
    assert response.json()["error"]["message"].startswith(
        f"Site a.example.com redirected the page fetch of {_START} more than 5 times"
    )
