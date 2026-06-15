"""roboco.api.routes.secretary_live — live-bridge endpoints (mocked deps)."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from roboco.api.routes import secretary_live as sl
from roboco.api.routes.secretary_live import (
    AgentEvent,
    LiveMessageRequest,
    StartSecretaryRequest,
)


@pytest.mark.asyncio
async def test_start_spawns_session(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = MagicMock()
    orch.start_secretary_session = AsyncMock()
    monkeypatch.setattr(sl, "get_orchestrator", lambda: orch)
    resp = await sl.start_live(StartSecretaryRequest(initial_message="hi"))
    assert resp.session_id
    orch.start_secretary_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_messages_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = MagicMock()
    reg.deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(sl, "get_live_registry", lambda: reg)
    out = await sl.send_message("sid", LiveMessageRequest(text="hi"))
    assert out == {"delivered": True}


@pytest.mark.asyncio
async def test_messages_404_when_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = MagicMock()
    reg.deliver = AsyncMock(return_value=False)
    monkeypatch.setattr(sl, "get_live_registry", lambda: reg)
    with pytest.raises(HTTPException) as exc:
        await sl.send_message("sid", LiveMessageRequest(text="hi"))
    assert exc.value.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_stop_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = MagicMock()
    orch.reap_secretary_session = AsyncMock()
    monkeypatch.setattr(sl, "get_orchestrator", lambda: orch)
    out = await sl.stop_live("sid")
    assert out == {"stopped": True}


@pytest.mark.asyncio
async def test_relay_event_pushes(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = MagicMock()
    reg.push = MagicMock(return_value=True)
    monkeypatch.setattr(sl, "get_live_registry", lambda: reg)
    out = await sl.relay_event("sid", AgentEvent(kind="text", text="hello"))
    assert out == {"pushed": True}


@pytest.mark.asyncio
async def test_status(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = MagicMock()
    reg.is_alive = MagicMock(return_value=True)
    monkeypatch.setattr(sl, "get_live_registry", lambda: reg)
    out = await sl.session_status("sid")
    assert out == {"alive": True}
