"""Pluggable web-research service — provider-agnostic search + fetch.

The capability is exposed to Board + PM agents through the ``roboco-search``
MCP server, which calls the ``/api/research/*`` routes; those routes call this
service. The provider API key lives only in the server-side process — never in
an agent container — and the agent never egresses: the provider's own API does.

Design:

* ``SearchProvider`` is the abstract adapter. Concrete adapters
  (``TavilyProvider``, ``BraveProvider``, ``ExaProvider``) translate a query
  into the provider's wire format and normalise the response into our
  dataclasses. ``NullProvider`` is the graceful-degradation stub returned when
  no key is configured — it never raises and always yields empty results.
* ``ResearchService`` selects an adapter from settings, clamps result/byte
  caps defensively, and is the single entry point the route uses.

Swapping providers is a config change (``ROBOCO_RESEARCH_PROVIDER``) only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from roboco.config import settings


class ResearchError(Exception):
    """A provider call failed (network error, non-2xx, or malformed body)."""


class ResearchUnsupportedError(ResearchError):
    """The active provider does not support the requested operation.

    Raised, for example, when ``web_fetch`` is called while the configured
    provider has no content-extraction endpoint (Brave). Distinct from a
    transient ``ResearchError`` so the route can map it to 501 rather than 502.
    """


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
        """Issue a request and return parsed JSON, or raise ``ResearchError``."""
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
            msg = f"{self.name}: request failed: {exc}"
            raise ResearchError(msg) from exc
        if not response.is_success:
            detail = response.text[:200] if response.text else "no body"
            msg = f"{self.name}: HTTP {response.status_code}: {detail}"
            raise ResearchError(msg)
        try:
            parsed: dict[str, Any] = response.json()
        except (ValueError, TypeError) as exc:
            msg = f"{self.name}: invalid JSON response: {exc}"
            raise ResearchError(msg) from exc
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
) -> SearchProvider:
    """Construct the adapter for ``name`` — NullProvider when unconfigured."""
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


def _truncated_fetch(
    url: str, content: str, max_chars: int, provider: str
) -> FetchOutcome:
    """Build a ``FetchOutcome``, marking truncation when content exceeds cap."""
    if len(content) > max_chars:
        return FetchOutcome(
            url=url, content=content[:max_chars], truncated=True, provider=provider
        )
    return FetchOutcome(url=url, content=content, truncated=False, provider=provider)
