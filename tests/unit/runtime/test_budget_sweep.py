"""``_fetch_budget_status`` — reading an agent's SDK budget endpoint.

Extracted from the budget kill-switch sweep so the swallow of an unreachable
SDK is observable (logged) rather than a silent ``try/except/continue``. These
tests pin the contract: a dict on 200-JSON, ``None`` on every benign failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from collections.abc import Callable

_URL = "http://roboco-agent-be-dev-1:9000/budget/status"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_returns_dict_on_200_json() -> None:
    client = _client(lambda _r: httpx.Response(200, json={"halt": True, "total": 99}))
    data = await AgentOrchestrator._fetch_budget_status(client, _URL, "be-dev-1")
    assert data == {"halt": True, "total": 99}
    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_when_unreachable() -> None:
    def _boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = _client(_boom)
    data = await AgentOrchestrator._fetch_budget_status(client, _URL, "be-dev-1")
    assert data is None  # benign: container not up yet / gone
    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_on_non_200() -> None:
    client = _client(lambda _r: httpx.Response(503))
    data = await AgentOrchestrator._fetch_budget_status(client, _URL, "be-dev-1")
    assert data is None
    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_on_non_json_body() -> None:
    client = _client(lambda _r: httpx.Response(200, text="not json"))
    data = await AgentOrchestrator._fetch_budget_status(client, _URL, "be-dev-1")
    assert data is None
    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_when_json_is_not_an_object() -> None:
    client = _client(lambda _r: httpx.Response(200, json=[1, 2, 3]))
    data = await AgentOrchestrator._fetch_budget_status(client, _URL, "be-dev-1")
    assert data is None  # a list is not a status object
    await client.aclose()
