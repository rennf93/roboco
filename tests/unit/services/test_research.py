"""roboco.services.research — provider adapters + service coverage.

Provider HTTP is exercised against ``httpx.MockTransport`` (no network, no
extra dependency); the service-level tests use a recording fake to assert the
result/byte clamps.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from roboco.config import settings
from roboco.services.research import (
    BraveProvider,
    ExaProvider,
    FetchOutcome,
    NullProvider,
    ResearchError,
    ResearchService,
    ResearchUnsupportedError,
    SearchOutcome,
    SearchProvider,
    TavilyProvider,
    build_provider,
    get_research_service,
)

Handler = Callable[[httpx.Request], httpx.Response]

_QUERY = "agentic frameworks"
_N_RESULTS = 3
_TOP_SCORE = 0.9
_TRUNC_CAP = 10
_RESULTS_CAP = 5
_REQ_RESULTS = 2
_FETCH_CAP = 20


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Tavily
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tavily_search_parses_results_and_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.tavily.com"
        body = json.loads(request.content)
        assert body["query"] == _QUERY
        assert body["max_results"] == _N_RESULTS
        return httpx.Response(
            200,
            json={
                "query": _QUERY,
                "answer": "Several exist.",
                "results": [
                    {
                        "title": "A",
                        "url": "https://a.test",
                        "content": "sa",
                        "score": 0.9,
                    },
                    {
                        "title": "B",
                        "url": "https://b.test",
                        "content": "sb",
                        "score": 0.5,
                    },
                ],
            },
        )

    client = _client(handler)
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    out = await provider.search(_QUERY, _N_RESULTS)
    assert out.provider == "tavily"
    assert out.answer == "Several exist."
    assert [h.url for h in out.hits] == ["https://a.test", "https://b.test"]
    assert out.hits[0].score == _TOP_SCORE
    await client.aclose()


@pytest.mark.asyncio
async def test_tavily_fetch_extracts_raw_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/extract"
        return httpx.Response(
            200, json={"results": [{"url": "https://a.test", "raw_content": "hello"}]}
        )

    client = _client(handler)
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    out = await provider.fetch("https://a.test", 1000)
    assert out.content == "hello"
    assert out.truncated is False
    await client.aclose()


@pytest.mark.asyncio
async def test_tavily_fetch_truncates_to_cap() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"url": "u", "raw_content": "x" * 100}]}
        )

    client = _client(handler)
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    out = await provider.fetch("u", _TRUNC_CAP)
    assert len(out.content) == _TRUNC_CAP
    assert out.truncated is True
    await client.aclose()


# --------------------------------------------------------------------------- #
# Brave
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_brave_search_parses_web_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.search.brave.com"
        assert request.headers["X-Subscription-Token"] == "k"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"title": "T", "url": "https://t.test", "description": "d"}
                    ]
                }
            },
        )

    client = _client(handler)
    provider = BraveProvider(api_key="k", timeout=5.0, client=client)
    out = await provider.search("q", _RESULTS_CAP)
    assert out.provider == "brave"
    assert out.answer is None
    assert out.hits[0].snippet == "d"
    await client.aclose()


@pytest.mark.asyncio
async def test_brave_fetch_is_unsupported() -> None:
    provider = BraveProvider(
        api_key="k", timeout=5.0, client=_client(lambda _r: httpx.Response(200))
    )
    with pytest.raises(ResearchUnsupportedError):
        await provider.fetch("https://x.test", 100)


# --------------------------------------------------------------------------- #
# Exa
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_exa_search_and_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "results": [{"title": "E", "url": "https://e.test", "text": "snip"}]
                },
            )
        return httpx.Response(
            200, json={"results": [{"url": "https://e.test", "text": "full"}]}
        )

    client = _client(handler)
    provider = ExaProvider(api_key="k", timeout=5.0, client=client)
    out = await provider.search("q", _RESULTS_CAP)
    assert out.hits[0].snippet == "snip"
    fetched = await provider.fetch("https://e.test", 1000)
    assert fetched.content == "full"
    await client.aclose()


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_non_2xx_raises_research_error() -> None:
    client = _client(lambda _r: httpx.Response(500, text="boom"))
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchError):
        await provider.search("q", _RESULTS_CAP)
    await client.aclose()


@pytest.mark.asyncio
async def test_network_error_raises_research_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = _client(handler)
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchError):
        await provider.search("q", _RESULTS_CAP)
    await client.aclose()


@pytest.mark.asyncio
async def test_malformed_json_raises_research_error() -> None:
    client = _client(
        lambda _r: httpx.Response(
            200, text="not json", headers={"content-type": "application/json"}
        )
    )
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchError):
        await provider.search("q", _RESULTS_CAP)
    await client.aclose()


# --------------------------------------------------------------------------- #
# NullProvider + build_provider
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_null_provider_degrades_gracefully() -> None:
    provider = NullProvider(api_key=None, timeout=5.0)
    assert provider.configured is False
    search = await provider.search("q", _RESULTS_CAP)
    assert search.hits == []
    assert search.provider == "null"
    fetched = await provider.fetch("u", _RESULTS_CAP)
    assert fetched.content == ""


def test_build_provider_selects_by_name() -> None:
    assert isinstance(build_provider("tavily", "k", 5.0), TavilyProvider)
    assert isinstance(build_provider("brave", "k", 5.0), BraveProvider)
    assert isinstance(build_provider("exa", "k", 5.0), ExaProvider)
    assert isinstance(build_provider("null", "k", 5.0), NullProvider)


def test_build_provider_null_when_no_key_or_unknown() -> None:
    assert isinstance(build_provider("tavily", None, 5.0), NullProvider)
    assert isinstance(build_provider("mystery", "k", 5.0), NullProvider)


# --------------------------------------------------------------------------- #
# ResearchService — clamps
# --------------------------------------------------------------------------- #


class _RecordingProvider(SearchProvider):
    name = "rec"

    def __init__(self) -> None:
        super().__init__(api_key="k", timeout=5.0)
        self.last_max_results: int | None = None
        self.last_max_chars: int | None = None
        self.fetch_content = "z" * 50

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        self.last_max_results = max_results
        return SearchOutcome(query=query, hits=[], answer=None, provider=self.name)

    async def fetch(self, url: str, max_chars: int) -> FetchOutcome:
        self.last_max_chars = max_chars
        return FetchOutcome(
            url=url, content=self.fetch_content, truncated=False, provider=self.name
        )


@pytest.mark.asyncio
async def test_service_clamps_max_results() -> None:
    provider = _RecordingProvider()
    service = ResearchService(
        provider, max_results_cap=_RESULTS_CAP, fetch_max_chars_cap=100
    )
    await service.search("q", 100)
    assert provider.last_max_results == _RESULTS_CAP
    await service.search("q", None)
    assert provider.last_max_results == _RESULTS_CAP
    await service.search("q", _REQ_RESULTS)
    assert provider.last_max_results == _REQ_RESULTS
    await service.search("q", 0)
    assert provider.last_max_results == 1


@pytest.mark.asyncio
async def test_service_clamps_and_truncates_fetch() -> None:
    provider = _RecordingProvider()
    provider.fetch_content = "y" * 80
    service = ResearchService(
        provider, max_results_cap=_RESULTS_CAP, fetch_max_chars_cap=_FETCH_CAP
    )
    out = await service.fetch("u", 1000)
    assert provider.last_max_chars == _FETCH_CAP
    assert len(out.content) == _FETCH_CAP
    assert out.truncated is True


def test_get_research_service_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "research_provider", "null")
    monkeypatch.setattr(settings, "research_api_key", None)
    service = get_research_service()
    assert service.provider_name == "null"
    assert service.configured is False
