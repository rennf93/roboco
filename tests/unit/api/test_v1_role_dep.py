"""v1 flow routes reject requests with the wrong X-Agent-Role.

Defense-in-depth check: every v1 flow router declares router-level
dependencies that 403 if `X-Agent-Role` doesn't match the router's role.
We verify that gate by mounting only the dev router on a minimal app
with the choreographer mocked — no DB / lifespan needed because the
role check fires BEFORE any body validation or choreographer call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.agents_config import issue_agent_token
from roboco.api.deps import get_choreographer
from roboco.api.routes.v1.flow_dev import router as flow_dev_router

if TYPE_CHECKING:
    import pytest

_HTTP_200 = 200
_HTTP_401 = 401
_HTTP_403 = 403
_AGENT_ID = "00000000-0000-0000-0000-000000000001"
_SECRET = "test-secret-for-v1-role-dep"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(flow_dev_router)
    mock_chore = MagicMock()
    mock_envelope = MagicMock()
    mock_envelope.as_dict.return_value = {"status": "idle", "next": "..."}
    mock_chore.give_me_work = AsyncMock(return_value=mock_envelope)
    app.dependency_overrides[get_choreographer] = lambda: mock_chore
    return app


def test_dev_route_rejects_qa_role() -> None:
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={
            "X-Agent-ID": "00000000-0000-0000-0000-000000000001",
            "X-Agent-Role": "qa",
        },
    )
    assert r.status_code == _HTTP_403
    assert "role" in r.json()["detail"].lower()


def test_dev_route_accepts_developer_role() -> None:
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={
            "X-Agent-ID": "00000000-0000-0000-0000-000000000001",
            "X-Agent-Role": "developer",
        },
    )
    # Role gate passes through; mocked choreographer returns 200.
    assert r.status_code != _HTTP_403


def test_dev_route_accepts_developer_role_case_insensitive() -> None:
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={
            "X-Agent-ID": "00000000-0000-0000-0000-000000000001",
            "X-Agent-Role": "DEVELOPER",
        },
    )
    assert r.status_code != _HTTP_403


def test_dev_route_rejects_missing_role_header() -> None:
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={"X-Agent-ID": "00000000-0000-0000-0000-000000000001"},
    )
    # Missing X-Agent-Role => FastAPI 422 from header validation.
    # We just need it not to silently pass as 200.
    assert r.status_code != _HTTP_200


def test_dev_route_401_when_auth_required_and_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # In strict mode the role guard must require the token, not just the header.
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "developer"},
    )
    assert r.status_code == _HTTP_401


def test_dev_route_rejects_invalid_token_even_in_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even in header-trust mode, a presented-but-forged token is rejected.
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": "forged-not-a-real-hmac",
        },
    )
    assert r.status_code == _HTTP_401


def test_dev_route_accepts_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # The good path: a valid HMAC token for the allowed role passes the guard.
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(_AGENT_ID, "developer")
    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": token,
        },
    )
    # Verified token + allowed role → guard passes; mocked choreographer 200.
    assert r.status_code == _HTTP_200
