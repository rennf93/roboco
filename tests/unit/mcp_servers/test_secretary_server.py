"""roboco-secretary MCP server — tools wrap the shared backend helpers as JSON.

The backend-calling logic (``secretary_driver._do_*``) is covered by the secretary
driver tests; here we only assert the MCP wrappers forward the right args and
return the backend result as a JSON string the model reads back.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from roboco.mcp import secretary_server


@pytest.mark.asyncio
async def test_read_company_state_returns_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _state() -> dict[str, Any]:
        return {"charter": "ship it", "tasks": {"pending": 3}}

    monkeypatch.setattr(secretary_server, "_do_read_state", _state)
    out = await secretary_server.read_company_state()
    assert json.loads(out) == {"charter": "ship it", "tasks": {"pending": 3}}


@pytest.mark.asyncio
async def test_read_task_forwards_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _task(task_id: str) -> dict[str, Any]:
        seen["id"] = task_id
        return {"id": task_id, "title": "T"}

    monkeypatch.setattr(secretary_server, "_do_read_task", _task)
    out = await secretary_server.read_task("task-9")
    assert seen["id"] == "task-9"
    assert json.loads(out)["title"] == "T"


@pytest.mark.asyncio
async def test_search_tasks_forwards_query_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _search(q: str, limit: int = 20) -> dict[str, Any]:
        seen["q"] = q
        seen["limit"] = limit
        return {"tasks": [{"id": "t1"}]}

    monkeypatch.setattr(secretary_server, "_do_search_tasks", _search)
    out = await secretary_server.search_tasks("x account", 5)
    assert seen == {"q": "x account", "limit": 5}
    assert json.loads(out) == {"tasks": [{"id": "t1"}]}


@pytest.mark.asyncio
async def test_submit_directive_forwards_kind_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _submit(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen["kind"] = kind
        seen["payload"] = payload
        return {"queued": True}

    monkeypatch.setattr(secretary_server, "_do_submit_directive", _submit)
    out = await secretary_server.submit_directive(
        "relay_message", {"channel": "announcements", "text": "hi"}
    )
    assert seen["kind"] == "relay_message"
    assert seen["payload"] == {"channel": "announcements", "text": "hi"}
    assert json.loads(out) == {"queued": True}


@pytest.mark.asyncio
async def test_submit_directive_tolerates_missing_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _submit(_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(secretary_server, "_do_submit_directive", _submit)
    await secretary_server.submit_directive("announce", None)
    assert seen["payload"] == {}
