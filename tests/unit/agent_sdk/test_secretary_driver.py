"""roboco.agent_sdk.secretary_driver — the backend-calling tool helpers."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from roboco.agent_sdk import secretary_driver as sd

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_API_URL", "http://x:8000")
    monkeypatch.setenv("ROBOCO_AGENT_ID", "secretary-uuid")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "secretary")
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "tok")


@pytest.mark.asyncio
async def test_read_state_calls_backend_with_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/secretary/state"
        assert request.headers["X-Agent-Token"] == "tok"
        assert request.headers["X-Agent-Role"] == "secretary"
        return httpx.Response(200, json={"goals": {}})

    out = await sd._do_read_state(client=_client(handler))
    assert out == {"goals": {}}


@pytest.mark.asyncio
async def test_read_task_calls_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/secretary/tasks/abc"
        return httpx.Response(200, json={"id": "abc"})

    out = await sd._do_read_task("abc", client=_client(handler))
    assert out["id"] == "abc"


@pytest.mark.asyncio
async def test_submit_directive_posts_kind_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/secretary/directives"
        body = json.loads(request.content)
        assert body == {"kind": "announce", "payload": {"text": "hi"}}
        return httpx.Response(201, json={"status": "pending"})

    out = await sd._do_submit_directive(
        "announce", {"text": "hi"}, client=_client(handler)
    )
    assert out["status"] == "pending"


@pytest.mark.asyncio
async def test_non_2xx_returns_error_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    out = await sd._do_read_state(
        client=_client(lambda _r: httpx.Response(500, text="boom"))
    )
    assert "error" in out


@pytest.mark.asyncio
async def test_network_error_returns_error_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    out = await sd._do_read_state(client=_client(handler))
    assert out["error"] == "request_failed"


def test_text_result_shape() -> None:
    result = sd._text_result({"a": 1})
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == {"a": 1}
