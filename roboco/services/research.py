"""Pluggable web-research service — provider-agnostic search + fetch.

The capability is exposed to Board + PM agents through the ``roboco-search``
MCP server, which calls the ``/api/research/*`` routes; those routes call this
service. The provider API key lives only in the server-side process — never in
an agent container — and the agent never egresses: the provider's own API does.

Design:

* ``SearchProvider`` is the abstract adapter. Concrete adapters
  (``TavilyProvider``, ``BraveProvider``, ``ExaProvider``, ``SearXNGProvider``)
  translate a query into the provider's wire format and normalise the response
  into our dataclasses. ``SearXNGProvider`` is the keyless self-hosted default:
  search via the instance's JSON endpoint, fetch via a local trafilatura
  extraction guarded against internal-network SSRF. ``NullProvider`` is the
  graceful-degradation stub returned when no provider is configured; it never
  raises and always yields empty results.
* ``ResearchService`` selects an adapter from settings, clamps result/byte
  caps defensively, and is the single entry point the route uses.
* Every failure is a ``ResearchError`` subclass from
  ``roboco.services.research_errors`` whose message names the party that
  failed the call (the provider, the target site, or RoboCo's URL guard) and
  why, so the route can answer with that party's status.

Swapping providers is a config change (``ROBOCO_RESEARCH_PROVIDER``) only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura

from roboco.config import settings
from roboco.services.research_errors import (
    ResearchUnsupportedError,
    carries_challenge_header,
    provider_body_error,
    provider_status_error,
    provider_unreachable_error,
    site_status_error,
    site_unreachable_error,
    url_refused_error,
)


@dataclass(frozen=True)
class SearchHit:
    """A single normalised search result."""

    title: str
    url: str
    snippet: str
    score: float | None = None


@dataclass(frozen=True)
class SearchOutcome:
    """The normalised result of a ``search`` call."""

    query: str
    hits: list[SearchHit]
    answer: str | None
    provider: str


@dataclass(frozen=True)
class FetchOutcome:
    """The normalised result of a ``fetch`` call."""

    url: str
    content: str
    truncated: bool
    provider: str


# --------------------------------------------------------------------------- #
# Provider adapters
# --------------------------------------------------------------------------- #


class SearchProvider(ABC):
    """Abstract web-search/fetch adapter.

    Subclasses translate to/from a specific provider's API. They may share an
    injected ``httpx.AsyncClient`` (for tests, pass one wrapping a
    ``MockTransport``); otherwise one is created lazily and owned/closed here.
    """

    name: str = "base"

    def __init__(
        self,
        api_key: str | None,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        """True when a key is present (NullProvider overrides to False)."""
        return bool(self._api_key)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the client iff this adapter created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call the provider's API and return its JSON object body.

        Every failure raises a ``ResearchProviderError`` that names this
        provider and the endpoint: no answer, an error status, or a body that
        is not a JSON object.
        """
        endpoint = f"{method} {url}"
        client = await self._http()
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise provider_unreachable_error(self.name, endpoint, exc) from exc
        if not response.is_success:
            raise provider_status_error(self.name, endpoint, response)
        return self._json_object(endpoint, response)

    def _json_object(self, endpoint: str, response: httpx.Response) -> dict[str, Any]:
        """The response body as a JSON object, or a ``ResearchProviderError``."""
        try:
            parsed = response.json()
        except (ValueError, TypeError) as exc:
            raise provider_body_error(self.name, endpoint, response) from exc
        if not isinstance(parsed, dict):
            raise provider_body_error(self.name, endpoint, response)
        return parsed

    @abstractmethod
    async def search(self, query: str, max_results: int) -> SearchOutcome:
        """Run a web search and return normalised hits."""

    async def fetch(self, url: str, max_chars: int) -> FetchOutcome:
        """Extract readable content for ``url`` (override where supported)."""
        _ = (url, max_chars)
        msg = f"{self.name} does not support web_fetch"
        raise ResearchUnsupportedError(msg)


class TavilyProvider(SearchProvider):
    """Tavily — LLM-native search with cited results and an extract endpoint."""

    name = "tavily"
    _SEARCH_URL = "https://api.tavily.com/search"
    _EXTRACT_URL = "https://api.tavily.com/extract"

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        body = await self._request_json(
            "POST",
            self._SEARCH_URL,
            json_body={
                "api_key": self._api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": True,
            },
        )
        hits = [
            SearchHit(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
                score=_as_float(item.get("score")),
            )
            for item in body.get("results", [])
            if isinstance(item, dict)
        ]
        answer = body.get("answer")
        return SearchOutcome(
            query=query,
            hits=hits,
            answer=str(answer) if answer else None,
            provider=self.name,
        )

    async def fetch(self, url: str, max_chars: int) -> FetchOutcome:
        body = await self._request_json(
            "POST",
            self._EXTRACT_URL,
            json_body={"api_key": self._api_key, "urls": [url]},
        )
        results = body.get("results", [])
        content = ""
        if results and isinstance(results[0], dict):
            content = str(results[0].get("raw_content", ""))
        return _truncated_fetch(url, content, max_chars, self.name)


class BraveProvider(SearchProvider):
    """Brave Search API — independent index. No content-extraction endpoint."""

    name = "brave"
    _SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        body = await self._request_json(
            "GET",
            self._SEARCH_URL,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key or "",
            },
            params={"q": query, "count": max_results},
        )
        web = body.get("web", {})
        results = web.get("results", []) if isinstance(web, dict) else []
        hits = [
            SearchHit(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", "")),
            )
            for item in results
            if isinstance(item, dict)
        ]
        return SearchOutcome(query=query, hits=hits, answer=None, provider=self.name)


class ExaProvider(SearchProvider):
    """Exa — neural/semantic search with a contents endpoint."""

    name = "exa"
    _SEARCH_URL = "https://api.exa.ai/search"
    _CONTENTS_URL = "https://api.exa.ai/contents"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key or "",
        }

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        body = await self._request_json(
            "POST",
            self._SEARCH_URL,
            headers=self._headers(),
            json_body={"query": query, "numResults": max_results},
        )
        hits = [
            SearchHit(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("text", "") or item.get("snippet", "")),
                score=_as_float(item.get("score")),
            )
            for item in body.get("results", [])
            if isinstance(item, dict)
        ]
        return SearchOutcome(query=query, hits=hits, answer=None, provider=self.name)

    async def fetch(self, url: str, max_chars: int) -> FetchOutcome:
        body = await self._request_json(
            "POST",
            self._CONTENTS_URL,
            headers=self._headers(),
            json_body={"urls": [url], "text": True},
        )
        results = body.get("results", [])
        content = ""
        if results and isinstance(results[0], dict):
            content = str(results[0].get("text", ""))
        return _truncated_fetch(url, content, max_chars, self.name)


class SearXNGProvider(SearchProvider):
    """SearXNG: self-hosted metasearch aggregator, no API key.

    Search talks to a private instance's JSON endpoint
    (``/search?format=json``), which must list ``json`` in the
    instance's ``search.formats``. Configuration is the base URL only;
    ``configured`` keys off the URL, not an API key.

    SearXNG has no content-extraction endpoint, so ``fetch`` downloads the
    page directly and extracts readable text locally with trafilatura. The
    fetch targets a URL chosen by an agent, so it refuses anything that does
    not resolve to a public address: the orchestrator is multi-homed and an
    agent-supplied URL must never become a probe of the internal network
    (DB, Redis, cloud metadata).
    """

    name = "searxng"

    def __init__(
        self,
        base_url: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key=None, timeout=timeout, client=client)
        self._base_url = base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        body = await self._request_json(
            "GET",
            f"{self._base_url}/search",
            params={"q": query, "format": "json"},
        )
        hits = [
            SearchHit(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "") or item.get("snippet", "")),
                score=_as_float(item.get("score")),
            )
            for item in body.get("results", [])
            if isinstance(item, dict)
        ]
        answers = body.get("answers")
        answer = (
            next((str(a) for a in answers if a), None)
            if isinstance(answers, list)
            else None
        )
        # SearXNG has no result-count request parameter, so the cap is
        # applied client-side after the fetch.
        return SearchOutcome(
            query=query, hits=hits[:max_results], answer=answer, provider=self.name
        )

    async def fetch(self, url: str, max_chars: int) -> FetchOutcome:
        await _assert_public_http_url(url)
        response = await self._get_page(url)
        extracted = await asyncio.to_thread(_extract_readable_text, response.text)
        return _truncated_fetch(url, extracted or "", max_chars, self.name)

    async def _get_page(self, url: str) -> httpx.Response:
        """GET the target page itself; a refusal or no answer names the site.

        The site, not SearXNG, is the party here: SearXNG only searches. A
        response a bot-mitigation layer marked as its challenge page is a
        refusal even with a 2xx status (AWS WAF challenges answer 202).
        """
        client = await self._http()
        try:
            response = await client.get(
                url, timeout=self._timeout, follow_redirects=True
            )
        except httpx.HTTPError as exc:
            raise site_unreachable_error(url, exc) from exc
        if not response.is_success or carries_challenge_header(response):
            raise site_status_error(url, response)
        return response


class NullProvider(SearchProvider):
    """Graceful stub used when no provider key is configured.

    Never raises and never makes a network call — returns empty results so the
    capability degrades softly on an unconfigured deployment.
    """

    name = "null"

    @property
    def configured(self) -> bool:
        return False

    async def search(self, query: str, max_results: int) -> SearchOutcome:
        _ = max_results
        return SearchOutcome(query=query, hits=[], answer=None, provider=self.name)

    async def fetch(self, url: str, max_chars: int) -> FetchOutcome:
        _ = max_chars
        return FetchOutcome(url=url, content="", truncated=False, provider=self.name)


_PROVIDERS: dict[str, type[SearchProvider]] = {
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "exa": ExaProvider,
}


def build_provider(
    name: str,
    api_key: str | None,
    timeout: float,
    client: httpx.AsyncClient | None = None,
    searxng_base_url: str | None = None,
) -> SearchProvider:
    """Construct the adapter for ``name``: NullProvider when unconfigured.

    SearXNG is the keyless exception: it needs a base URL instead of an API
    key, so it is constructed (and gated on that URL) before the shared
    ``api_key`` path below.
    """
    if name == "searxng":
        if searxng_base_url:
            return SearXNGProvider(
                base_url=searxng_base_url, timeout=timeout, client=client
            )
        return NullProvider(api_key=None, timeout=timeout, client=client)
    if name == "null" or not api_key:
        return NullProvider(api_key=None, timeout=timeout, client=client)
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        return NullProvider(api_key=None, timeout=timeout, client=client)
    return provider_cls(api_key=api_key, timeout=timeout, client=client)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class ResearchService:
    """Provider-agnostic entry point used by the research route.

    Clamps result count and fetched-content size to the configured caps so a
    misbehaving (or generous) provider can't blow past the operator's limits.
    """

    def __init__(
        self,
        provider: SearchProvider,
        max_results_cap: int,
        fetch_max_chars_cap: int,
    ) -> None:
        self._provider = provider
        self._max_results_cap = max_results_cap
        self._fetch_max_chars_cap = fetch_max_chars_cap

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def configured(self) -> bool:
        return self._provider.configured

    def _clamp_results(self, requested: int | None) -> int:
        if requested is None:
            return self._max_results_cap
        return max(1, min(requested, self._max_results_cap))

    def _clamp_chars(self, requested: int | None) -> int:
        if requested is None:
            return self._fetch_max_chars_cap
        return max(1, min(requested, self._fetch_max_chars_cap))

    async def search(self, query: str, max_results: int | None = None) -> SearchOutcome:
        return await self._provider.search(query, self._clamp_results(max_results))

    async def fetch(self, url: str, max_chars: int | None = None) -> FetchOutcome:
        cap = self._clamp_chars(max_chars)
        outcome = await self._provider.fetch(url, cap)
        if len(outcome.content) > cap:
            return FetchOutcome(
                url=outcome.url,
                content=outcome.content[:cap],
                truncated=True,
                provider=outcome.provider,
            )
        return outcome

    async def close(self) -> None:
        await self._provider.close()


def get_research_service(
    client: httpx.AsyncClient | None = None,
) -> ResearchService:
    """Build a ``ResearchService`` from current settings."""
    provider = build_provider(
        settings.research_provider,
        settings.research_api_key,
        settings.research_timeout_seconds,
        client=client,
        searxng_base_url=settings.research_searxng_base_url,
    )
    return ResearchService(
        provider,
        settings.research_max_results,
        settings.research_fetch_max_chars,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _as_float(value: Any) -> float | None:
    """Coerce a provider score to float, or None when absent/invalid."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_readable_text(html: str) -> str | None:
    """Extract the readable main content of a page (sync, thread-offloaded)."""
    # A malformed page must degrade to empty content, never raise.
    try:
        return trafilatura.extract(
            html, include_links=False, include_comments=False, include_tables=True
        )
    except Exception:
        return None


async def _assert_public_http_url(url: str) -> None:
    """Refuse URLs that are not plain http(s) pointing at a public host.

    ``web_fetch`` fetches agent-supplied URLs from the orchestrator process,
    which is multi-homed and can reach the DB, Redis, and every internal
    service. An agent-supplied URL must never become a probe of that network,
    so the hostname must resolve to a globally-routable address (covers
    loopback, RFC1918, link-local incl. cloud metadata, and ULA).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise url_refused_error(url, "only http(s) URLs are fetched")
    host = parsed.hostname
    if not host:
        raise url_refused_error(url, "it has no hostname")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as exc:
        why = f"its host {host!r} does not resolve ({type(exc).__name__}: {exc})"
        raise url_refused_error(url, why) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise url_refused_error(url, "it resolves to a non-public address")


def _truncated_fetch(
    url: str, content: str, max_chars: int, provider: str
) -> FetchOutcome:
    """Build a ``FetchOutcome``, marking truncation when content exceeds cap."""
    if len(content) > max_chars:
        return FetchOutcome(
            url=url, content=content[:max_chars], truncated=True, provider=provider
        )
    return FetchOutcome(url=url, content=content, truncated=False, provider=provider)
