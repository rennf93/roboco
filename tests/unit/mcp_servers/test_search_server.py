"""roboco.mcp.search_server — handler shaping + server construction."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from roboco.mcp.search_server import (
    _NOT_CONFIGURED,
    _handle_fetch,
    _handle_search,
    create_search_mcp_server,
)
from roboco.mcp.utils import ApiClient


class _FakeClient(ApiClient):
    """ApiClient stand-in that records calls and returns canned responses.

    Subclasses ApiClient (so it type-checks where one is expected) but skips
    the real ``__init__`` — only ``post_or_error`` is exercised here.
    """

    def __init__(
        self,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def post_or_error(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        error_code: str = "API_ERROR",
        error_message: str = "Request failed",
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        self.calls.append((endpoint, json))
        return self._result, self._error


@pytest.mark.asyncio
async def test_search_success_includes_cite_guidance() -> None:
    client = _FakeClient(
        result={
            "query": "q",
            "provider": "tavily",
            "answer": "a",
            "results": [{"title": "T", "url": "https://t.test", "snippet": "s"}],
        }
    )
    out = await _handle_search("q", 3, client)
    assert out["provider"] == "tavily"
    assert "cite" in out["guidance"].lower()
    assert client.calls == [("/research/search", {"query": "q", "max_results": 3})]


@pytest.mark.asyncio
async def test_search_null_provider_signals_not_configured() -> None:
    client = _FakeClient(
        result={"query": "q", "provider": "null", "answer": None, "results": []}
    )
    out = await _handle_search("q", None, client)
    assert out["guidance"] == _NOT_CONFIGURED
    # No max_results key when not supplied.
    assert client.calls == [("/research/search", {"query": "q"})]


@pytest.mark.asyncio
async def test_search_propagates_error() -> None:
    err = {"status": "error", "error": {"code": "SEARCH_FAILED"}}
    client = _FakeClient(error=err)
    out = await _handle_search("q", None, client)
    assert out == err


@pytest.mark.asyncio
async def test_fetch_success_shapes_payload() -> None:
    client = _FakeClient(
        result={
            "url": "https://x.test",
            "provider": "exa",
            "content": "body",
            "truncated": True,
        }
    )
    out = await _handle_fetch("https://x.test", 500, client)
    assert out["content"] == "body"
    assert out["truncated"] is True
    assert client.calls == [
        ("/research/fetch", {"url": "https://x.test", "max_chars": 500})
    ]


def test_create_search_mcp_server_builds() -> None:
    server = create_search_mcp_server("be-pm")
    assert isinstance(server, FastMCP)
