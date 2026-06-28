"""/api/v1/do/* must enforce the same HMAC agent-token gate as
the /api/v1/flow/* routers.

The do router serves every role (content tools are role-uniform), so it has
no single role to assert — but it must still bind the presented X-Agent-ID
to a verified token when ROBOCO_AGENT_AUTH_REQUIRED=true and reject a forged
token even in dev mode. Without this guard the content-tool endpoints were
the one agent-gateway path that accepted a forged X-Agent-ID with no token
check — a weaker gate than the flow routers' role guards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.agents_config import issue_agent_token
from roboco.api.deps import get_content_actions
from roboco.api.routes.v1.do import router
from roboco.services.gateway.content_actions import ContentActions

if TYPE_CHECKING:
    import pytest

_HTTP_200 = 200
_HTTP_401 = 401
_AGENT_ID = "00000000-0000-0000-0000-000000000001"
_SECRET = "test-secret-for-do-auth"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    mock_actions = MagicMock(spec=ContentActions)
    mock_env = MagicMock()
    mock_env.as_dict.return_value = {"status": "ok", "next": "continue"}
    mock_actions.commit = AsyncMock(return_value=mock_env)
    app.dependency_overrides[get_content_actions] = lambda: mock_actions
    return app


def _commit_body() -> dict:
    return {"message": "add user authentication endpoint"}


def test_do_route_401_when_auth_required_and_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict mode: a do endpoint must require the token, not just X-Agent-ID."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/do/commit",
        json=_commit_body(),
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "developer"},
    )
    assert r.status_code == _HTTP_401


def test_do_route_rejects_forged_token_even_in_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even in header-trust mode, a presented-but-forged token is rejected."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/do/commit",
        json=_commit_body(),
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": "forged-not-a-real-hmac",
        },
    )
    assert r.status_code == _HTTP_401


def test_do_route_accepts_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The good path: a valid HMAC token passes the guard and reaches the handler."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(_AGENT_ID, "developer")
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/do/commit",
        json=_commit_body(),
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": token,
        },
    )
    assert r.status_code == _HTTP_200
