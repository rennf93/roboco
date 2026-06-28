"""SSE ``subscribe_to_task`` is authenticated like the rest of the a2a
message surface and opens a SHORT-LIVED DB session per poll iteration
(via ``get_session_factory()``) instead of holding the request-scoped
``db: DbSession`` for the full SSE lifetime, which exhausted the asyncpg
pool one connection per connected client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_agent_token
from roboco.api.routes import a2a as a2a_module
from roboco.api.routes.a2a import router as a2a_router
from roboco.db.base import get_db

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi.routing import APIRoute

_SECRET = "test-secret-for-a2a-subscribe"
_AGENT_ID = "00000000-0000-0000-0000-000000000003"
_HTTP_200 = 200
_HTTP_401 = 401
_HTTP_404 = 404


@pytest.fixture
async def a2a_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(a2a_router, prefix="/api/a2a")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth gate (F023 parity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_rejects_missing_token_when_required(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode + no X-Agent-Token => 401, never reaches the generator."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    r = await a2a_client.get(
        "/api/a2a/tasks/some-task/subscribe",
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "developer"},
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_subscribe_rejects_forged_token_even_in_dev(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A presented-but-forged token is rejected even in header-trust mode."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await a2a_client.get(
        "/api/a2a/tasks/some-task/subscribe",
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": "forged-not-a-real-hmac",
        },
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_subscribe_accepts_valid_token_then_404s_unknown_task(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid token passes the gate; the route then 404s on the initial
    task-existence check (no DB seeded). 404 (not 401) proves the gate let
    the request through."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(_AGENT_ID, "developer")
    # get_task returns None -> 404. Patch A2AService.get_task to return None
    # so the route doesn't need a real DB.
    monkeypatch.setattr(a2a_module.A2AService, "get_task", AsyncMock(return_value=None))
    r = await a2a_client.get(
        "/api/a2a/tasks/some-task/subscribe",
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": token,
        },
    )
    assert r.status_code == _HTTP_404


# ---------------------------------------------------------------------------
# Session-per-query: structural + behavioral
# ---------------------------------------------------------------------------


def test_subscribe_route_does_not_hold_request_scoped_db() -> None:
    """The route must NOT depend on ``get_db`` — the request-scoped session
    would be held for the full SSE lifetime (up to 1 hour). Each poll opens
    its own short-lived session via ``get_session_factory``.
    """
    subscribe_route = cast(
        "APIRoute",
        next(
            r
            for r in a2a_router.routes
            if getattr(r, "path", "") == "/tasks/{task_id}/subscribe"
        ),
    )
    # Walk the route's dependency tree; get_db must not appear anywhere.
    deps = [subscribe_route.dependant]
    seen: set[int] = set()
    found_get_db = False
    while deps:
        d = deps.pop()
        if id(d) in seen:
            continue
        seen.add(id(d))
        if d.call is get_db:
            found_get_db = True
        deps.extend(d.dependencies)
    assert not found_get_db, (
        "subscribe_to_task still depends on get_db — the request-scoped "
        "session is held for the full SSE lifetime (pool-exhaustion vector)."
    )


@pytest.mark.asyncio
async def test_subscribe_opens_a_short_lived_session_per_poll(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each poll iteration opens its own session and closes it before the next
    ``asyncio.sleep`` — never holding one connection across the full SSE
    lifetime. Asserts more than one session open (one per poll, not one for
    the lifetime)."""

    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(_AGENT_ID, "developer")

    # Count session opens across the SSE lifetime.
    open_count = {"n": 0}

    def _factory() -> Any:
        open_count["n"] += 1

        class _Ctx:
            async def __aenter__(self) -> MagicMock:
                return MagicMock()

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()

    monkeypatch.setattr(a2a_module, "get_session_factory", lambda: _factory)

    # Non-terminal fake task so the loop keeps polling.
    fake_task = MagicMock()
    fake_task.status.state = "in_progress"
    fake_task.model_dump_json = MagicMock(return_value="{}")
    monkeypatch.setattr(
        a2a_module.A2AService, "get_task", AsyncMock(return_value=fake_task)
    )

    # No sleeping — drain the generator as fast as possible.
    monkeypatch.setattr(a2a_module.asyncio, "sleep", AsyncMock(return_value=None))

    # Disconnect after 3 polls so the stream terminates.
    disconnect_after = {"remaining": 3}

    async def _fake_is_disconnected() -> bool:
        if disconnect_after["remaining"] <= 0:
            return True
        disconnect_after["remaining"] -= 1
        return False

    # The route reads request.is_disconnected(); patch it on the request via
    # the Starlette request. We patch the Request.is_disconnected property.
    monkeypatch.setattr(
        "fastapi.Request.is_disconnected",
        lambda _self: _fake_is_disconnected(),
    )

    r = await a2a_client.get(
        "/api/a2a/tasks/some-task/subscribe",
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": token,
        },
    )
    # Drain the SSE stream so the generator runs to completion.
    assert r.status_code == _HTTP_200
    # Consume the body (the SSE stream finishes once is_disconnected returns
    # True on the 4th check).
    _ = await r.aread()

    # 3 polls + 1 initial validation = 4 session opens (one per query, none
    # held across the lifetime). The key assertion: more than one session
    # was opened — proving the request-scoped session is gone.
    assert open_count["n"] > 1, (
        f"only {open_count['n']} session open(s) — the route is holding a "
        "single request-scoped session for the full SSE lifetime (pool "
        "exhaustion vector)."
    )
