from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from roboco.api import deps as d
from roboco.api.deps import get_current_agent_slug


async def _run(dep, headers, settings_on, monkeypatch):
    monkeypatch.setattr(d.settings, "cloud_auth_enabled", settings_on)
    db = AsyncMock()
    response = AsyncMock()
    # _cloud_auth_agent_context is the gate; stub it to assert it's reached
    with patch.object(d, "_cloud_auth_agent_context", new=AsyncMock()) as m:
        m.return_value = type(
            "Ctx",
            (),
            {"agent_id": "00000000-0000-0000-0000-000000000000", "slug": "be-dev-1"},
        )()
        return await dep(
            db=db,
            response=response,
            x_agent_id=headers.get("X-Agent-ID"),
            x_agent_role=headers.get("X-Agent-Role"),
            x_agent_team=headers.get("X-Agent-Team"),
            x_agent_token=headers.get("X-Agent-Token"),
            roboco_session=headers.get("roboco_session"),
        ), m.called


@pytest.mark.asyncio
async def test_cloud_auth_spoof_bare_agent_id_rejected(monkeypatch):
    # A bare X-Agent-ID with no token/cookie must not reach the gate body.
    monkeypatch.setattr(d.settings, "cloud_auth_enabled", True)
    db = AsyncMock()
    response = AsyncMock()
    with patch.object(d, "_cloud_auth_agent_context", new=AsyncMock()) as m:
        m.side_effect = HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Cloud auth is enabled — agent requests require a valid token.",
        )
        with pytest.raises(HTTPException) as exc:
            await d.get_current_agent_slug(
                db=db,
                response=response,
                x_agent_id="be-dev-1",
                x_agent_role=None,
                x_agent_team=None,
                x_agent_token=None,
                roboco_session=None,
            )
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_cloud_auth_routes_through_dual_path(monkeypatch):
    _, called = await _run(
        get_current_agent_slug, {"X-Agent-ID": "be-dev-1"}, True, monkeypatch
    )
    assert called  # cloud mode delegates to _cloud_auth_agent_context


@pytest.mark.asyncio
async def test_dev_mode_unchanged_slug_returns_header(monkeypatch):
    monkeypatch.setattr(d.settings, "cloud_auth_enabled", False)
    db = AsyncMock()
    response = AsyncMock()
    with patch.object(d, "_cloud_auth_agent_context", new=AsyncMock()) as m:
        slug = await d.get_current_agent_slug(
            db=db,
            response=response,
            x_agent_id="be-dev-1",
            x_agent_role=None,
            x_agent_team=None,
            x_agent_token=None,
            roboco_session=None,
        )
    assert slug == "be-dev-1"
    assert not m.called  # dev path does not invoke the cloud gate
