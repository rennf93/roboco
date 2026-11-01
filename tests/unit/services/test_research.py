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
    ResearchService,
    SearchOutcome,
    SearchProvider,
    SearXNGProvider,
    TavilyProvider,
    build_provider,
    get_research_service,
)
from roboco.services.research_errors import (
    ResearchProviderError,
    ResearchSiteError,
    ResearchUnsupportedError,
    ResearchUrlRefusedError,
)
from tests.unit.research_fakes import (
    PLAIN_NOT_FOUND_HTML,
    PUBLIC_IP,
    VERCEL_CHECKPOINT_HTML,
    fake_dns,
)

Handler = Callable[[httpx.Request], httpx.Response]

_QUERY = "agentic frameworks"
_N_RESULTS = 3
_TOP_SCORE = 0.9
_TRUNC_CAP = 10
_RESULTS_CAP = 5
_REQ_RESULTS = 2
_FETCH_CAP = 20
_SEARXNG_BASE = "http://searxng.internal:8080"
_PAGE_URL = f"https://{PUBLIC_IP}/page"
_RFC1918_IP = "172.18.0.5"


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _noop(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


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
# SearXNG
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_searxng_search_parses_results_and_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "searxng.internal"
        assert request.url.path == "/search"
        assert request.url.params["format"] == "json"
        assert request.url.params["q"] == _QUERY
        return httpx.Response(
            200,
            json={
                "query": _QUERY,
                "answers": ["42"],
                "results": [
                    {
                        "title": "A",
                        "url": "https://a.test",
                        "content": "sa",
                        "score": 0.9,
                    },
                    {"title": "B", "url": "https://b.test", "content": "sb"},
                ],
            },
        )

    client = _client(handler)
    provider = SearXNGProvider(
        base_url="http://searxng.internal:8080/", timeout=5.0, client=client
    )
    out = await provider.search(_QUERY, _N_RESULTS)
    assert out.provider == "searxng"
    assert out.answer == "42"
    assert [h.url for h in out.hits] == ["https://a.test", "https://b.test"]
    assert out.hits[0].score == _TOP_SCORE
    await client.aclose()


@pytest.mark.asyncio
async def test_searxng_fetch_extracts_readability_text_locally() -> None:
    html = (
        "<html><head><title>t</title></head><body>"
        "<nav>menu menu</nav><article><h1>Head</h1>"
        "<p>Hello readable world.</p></article></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        return httpx.Response(200, text=html)

    client = _client(handler)
    provider = SearXNGProvider(
        base_url="http://searxng.internal", timeout=5.0, client=client
    )
    out = await provider.fetch("https://93.184.216.34/page", 1000)
    assert "Hello readable world." in out.content
    assert out.truncated is False
    await client.aclose()


@pytest.mark.asyncio
async def test_searxng_fetch_truncates_to_cap() -> None:
    html = f"<html><body><p>{'word ' * 200}</p></body></html>"
    client = _client(lambda _r: httpx.Response(200, text=html))
    provider = SearXNGProvider(
        base_url="http://searxng.internal", timeout=5.0, client=client
    )
    out = await provider.fetch("https://93.184.216.34/page", _TRUNC_CAP)
    assert len(out.content) == _TRUNC_CAP
    assert out.truncated is True
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "why"),
    [
        pytest.param(
            "http://127.0.0.1:8000/api",
            "it resolves to a non-public address",
            id="loopback",
        ),
        pytest.param(
            "http://169.254.169.254/latest/meta-data",
            "it resolves to a non-public address",
            id="cloud-metadata",
        ),
        pytest.param(
            "http://roboco-postgres:5432/",
            "it resolves to a non-public address",
            id="internal-name",
        ),
        pytest.param(
            "http://no-such-host.invalid/",
            "its host 'no-such-host.invalid' does not resolve (gaierror: ",
            id="unresolvable",
        ),
        pytest.param("http:///path", "it has no hostname", id="no-hostname"),
        pytest.param(
            "ftp://93.184.216.34/file",
            "only http(s) URLs are fetched",
            id="non-http-scheme",
        ),
    ],
)
async def test_searxng_fetch_refuses_non_public_urls(
    url: str, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard refuses before any request leaves, and says it was RoboCo."""
    fake_dns(
        monkeypatch,
        {
            "127.0.0.1": "127.0.0.1",
            "169.254.169.254": "169.254.169.254",
            "roboco-postgres": _RFC1918_IP,
        },
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="<html><body>x</body></html>")

    provider = SearXNGProvider(
        base_url="http://searxng.internal", timeout=5.0, client=_client(handler)
    )
    with pytest.raises(ResearchUrlRefusedError) as exc:
        await provider.fetch(url, 100)
    assert str(exc.value).startswith(f"RoboCo's URL guard refused {url!r}: {why}")
    assert requests == []


@pytest.mark.asyncio
async def test_searxng_configured_keys_off_base_url() -> None:
    configured = SearXNGProvider(
        base_url="http://searxng.internal", timeout=5.0, client=_client(_noop)
    )
    unconfigured = SearXNGProvider(base_url="", timeout=5.0, client=_client(_noop))
    assert configured.configured is True
    assert unconfigured.configured is False


# --------------------------------------------------------------------------- #
# SearXNG fetch: the target site is the party that refuses
# --------------------------------------------------------------------------- #


def _searxng(handler: Handler) -> SearXNGProvider:
    return SearXNGProvider(base_url=_SEARXNG_BASE, timeout=5.0, client=_client(handler))


@pytest.mark.asyncio
async def test_searxng_fetch_vercel_checkpoint_names_site_title_and_bot_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-10-28 refusal: the site, not SearXNG, answered 403 with Vercel's
    Security Checkpoint page, and the message says so in readable words."""
    fake_dns(monkeypatch, {"app.example.com": PUBLIC_IP})
    provider = _searxng(
        lambda _r: httpx.Response(
            403, text=VERCEL_CHECKPOINT_HTML, headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(ResearchSiteError) as exc:
        await provider.fetch("https://app.example.com/pricing", 100)
    assert str(exc.value) == (
        "Site app.example.com answered HTTP 403 Forbidden to the page fetch of "
        "https://app.example.com/pricing (page title: Vercel Security "
        "Checkpoint). This is Vercel's bot-check (challenge) page: the site "
        "blocks automated fetches, so get this information from another source."
    )
    assert "<" not in str(exc.value)
    assert "searxng" not in str(exc.value).lower()


@pytest.mark.asyncio
async def test_searxng_fetch_plain_404_names_site_and_readable_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"docs.example.org": PUBLIC_IP})
    provider = _searxng(lambda _r: httpx.Response(404, text=PLAIN_NOT_FOUND_HTML))
    with pytest.raises(ResearchSiteError) as exc:
        await provider.fetch("https://docs.example.org/missing", 100)
    assert str(exc.value) == (
        "Site docs.example.org answered HTTP 404 Not Found to the page fetch of "
        "https://docs.example.org/missing (response text: Not Found The "
        "requested URL was not found on this server.)."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "value", "status", "vendor"),
    [
        pytest.param("cf-mitigated", "challenge", 403, "Cloudflare", id="cloudflare"),
        pytest.param("x-amzn-waf-action", "challenge", 202, "AWS WAF", id="aws-202"),
        pytest.param("x-amzn-waf-action", "captcha", 405, "AWS WAF", id="aws-405"),
        pytest.param("x-vercel-mitigated", "challenge", 429, "Vercel", id="vercel"),
    ],
)
async def test_searxng_fetch_challenge_header_is_a_refusal_whatever_the_status(
    header: str, value: str, status: int, vendor: str
) -> None:
    """A challenge page is a refusal even when its status is 2xx (AWS WAF's
    challenge answers 202), and the header names the vendor."""
    provider = _searxng(
        lambda _r: httpx.Response(
            status,
            text="<html><body>Just a moment...</body></html>",
            headers={header: value},
        )
    )
    with pytest.raises(ResearchSiteError) as exc:
        await provider.fetch(_PAGE_URL, 100)
    assert f"answered HTTP {status} " in str(exc.value)
    assert f"This is {vendor}'s bot-check (challenge) page" in str(exc.value)


@pytest.mark.asyncio
async def test_searxng_fetch_redirect_names_the_site_that_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"a.example.com": PUBLIC_IP})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example.com":
            return httpx.Response(
                302, headers={"location": "https://b.example.com/landing"}
            )
        return httpx.Response(403, text=PLAIN_NOT_FOUND_HTML)

    with pytest.raises(ResearchSiteError) as exc:
        await _searxng(handler).fetch("https://a.example.com/start", 100)
    assert str(exc.value).startswith(
        "Site b.example.com answered HTTP 403 Forbidden to the page fetch of "
        "https://a.example.com/start after a redirect to "
        "https://b.example.com/landing (response text: "
    )


@pytest.mark.asyncio
async def test_searxng_fetch_unreachable_site_names_site_and_exception_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_dns(monkeypatch, {"down.example.com": PUBLIC_IP})

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    with pytest.raises(ResearchSiteError) as exc:
        await _searxng(handler).fetch("https://down.example.com/page", 100)
    assert str(exc.value) == (
        "No answer from site down.example.com to the page fetch of "
        "https://down.example.com/page: ConnectError: [Errno 111] Connection "
        "refused."
    )
    assert not isinstance(exc.value, ResearchProviderError)


@pytest.mark.asyncio
async def test_searxng_fetch_timeout_names_the_bare_exception_kind() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    with pytest.raises(ResearchSiteError) as exc:
        await _searxng(handler).fetch(_PAGE_URL, 100)
    assert str(exc.value) == (
        f"No answer from site {PUBLIC_IP} to the page fetch of {_PAGE_URL}: "
        "ReadTimeout."
    )


# --------------------------------------------------------------------------- #
# Provider failures: the research provider is the party
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_searxng_search_500_names_the_provider() -> None:
    provider = _searxng(
        lambda _r: httpx.Response(
            500,
            text=(
                "<html><head><title>500 Internal Server Error</title></head>"
                "<body><h1>Internal Server Error</h1></body></html>"
            ),
        )
    )
    with pytest.raises(ResearchProviderError) as exc:
        await provider.search("q", _RESULTS_CAP)
    assert str(exc.value) == (
        "Research provider searxng answered HTTP 500 Internal Server Error to "
        f"GET {_SEARXNG_BASE}/search (page title: 500 Internal Server Error)."
    )


@pytest.mark.asyncio
async def test_searxng_search_unreachable_names_the_provider() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    with pytest.raises(ResearchProviderError) as exc:
        await _searxng(handler).search("q", _RESULTS_CAP)
    assert str(exc.value) == (
        f"No answer from research provider searxng to GET {_SEARXNG_BASE}/search: "
        "ConnectError: [Errno 111] Connection refused."
    )


@pytest.mark.asyncio
async def test_tavily_search_error_status_names_the_provider() -> None:
    client = _client(lambda _r: httpx.Response(500, text="boom"))
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchProviderError) as exc:
        await provider.search("q", _RESULTS_CAP)
    assert str(exc.value) == (
        "Research provider tavily answered HTTP 500 Internal Server Error to "
        "POST https://api.tavily.com/search (response text: boom)."
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_tavily_unreachable_names_the_provider() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = _client(handler)
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchProviderError) as exc:
        await provider.search("q", _RESULTS_CAP)
    assert str(exc.value) == (
        "No answer from research provider tavily to POST "
        "https://api.tavily.com/search: ConnectError: down."
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_tavily_non_json_body_names_the_provider() -> None:
    client = _client(
        lambda _r: httpx.Response(
            200, text="not json", headers={"content-type": "application/json"}
        )
    )
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchProviderError) as exc:
        await provider.search("q", _RESULTS_CAP)
    assert str(exc.value) == (
        "Research provider tavily answered POST https://api.tavily.com/search "
        "with HTTP 200 OK but a body that is not a JSON object "
        "(application/json; response text: not json)."
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_json_array_body_names_the_provider() -> None:
    """Valid JSON that is not an object is a provider failure, not a crash."""
    client = _client(lambda _r: httpx.Response(200, json=["not", "an", "object"]))
    provider = ExaProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchProviderError) as exc:
        await provider.search("q", _RESULTS_CAP)
    assert str(exc.value).startswith(
        "Research provider exa answered POST https://api.exa.ai/search with "
        "HTTP 200 OK but a body that is not a JSON object"
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_tavily_fetch_error_names_the_provider_not_the_site() -> None:
    """Tavily downloads the page itself, so its own API is the party."""
    client = _client(
        lambda _r: httpx.Response(
            401, json={"detail": {"error": "Unauthorized: missing or invalid API key."}}
        )
    )
    provider = TavilyProvider(api_key="k", timeout=5.0, client=client)
    with pytest.raises(ResearchProviderError) as exc:
        await provider.fetch("https://app.example.com/pricing", 100)
    assert str(exc.value) == (
        "Research provider tavily answered HTTP 401 Unauthorized to POST "
        "https://api.tavily.com/extract (response text: "
        '{"detail":{"error":"Unauthorized: missing or invalid API key."}}).'
    )
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
    assert isinstance(
        build_provider("searxng", None, 5.0, searxng_base_url="http://s:8080"),
        SearXNGProvider,
    )
    assert isinstance(build_provider("null", "k", 5.0), NullProvider)


def test_build_provider_null_when_no_key_or_unknown() -> None:
    assert isinstance(build_provider("tavily", None, 5.0), NullProvider)
    assert isinstance(build_provider("mystery", "k", 5.0), NullProvider)
    # SearXNG degrades like every provider when its base URL is missing.
    assert isinstance(build_provider("searxng", None, 5.0), NullProvider)


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


def test_default_provider_is_self_hosted_searxng() -> None:
    """The stock stack ships keyless: the default provider is the bundled
    SearXNG container, not a paid API. Compose env defaults mirror this."""
    assert settings.research_provider == "searxng"


def test_get_research_service_searxng_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "research_provider", "searxng")
    monkeypatch.setattr(
        settings, "research_searxng_base_url", "http://roboco-searxng:8080"
    )
    service = get_research_service()
    assert service.provider_name == "searxng"
    assert service.configured is True
