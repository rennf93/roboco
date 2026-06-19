"""v1 flow routes reject requests with the wrong X-Agent-Role.

Defense-in-depth check: every v1 flow router declares router-level
dependencies that 403 if `X-Agent-Role` doesn't match the router's role.
We verify that gate by mounting only the dev router on a minimal app
with the choreographer mocked — no DB / lifespan needed because the
role check fires BEFORE any body validation or choreographer call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from roboco.api.deps import get_choreographer
from roboco.api.routes.v1.flow_dev import router as flow_dev_router

_HTTP_200 = 200
_HTTP_403 = 403


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


_HTTP_401 = 401


def test_dev_route_requires_token_when_auth_enabled(monkeypatch: object) -> None:
    """When ROBOCO_AGENT_AUTH_REQUIRED=true, the role header alone is not
    enough — a caller cannot pick an allowed role with just X-Agent-Role.
    The HMAC token must be presented and verified against X-Agent-ID +
    role + team. This is the regression for the auth bypass where the v1
    role dep trusted the role header without authenticating the token."""
    from pytest import MonkeyPatch  # noqa: PLC0415 — type-only import

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")

    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={
            "X-Agent-ID": "00000000-0000-0000-0000-000000000001",
            "X-Agent-Role": "developer",
        },
    )
    # Missing X-Agent-Token + auth required => 401, not 200.
    assert r.status_code == _HTTP_401


def test_dev_route_rejects_invalid_token_even_in_dev(monkeypatch: object) -> None:
    """Even with auth not strictly required, a presented token that does
    not verify must be rejected — you can't bypass HMAC by supplying an
    arbitrary string in the header."""
    from pytest import MonkeyPatch  # noqa: PLC0415

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "test-secret-not-used")

    client = TestClient(_build_app())
    r = client.post(
        "/api/v1/flow/developer/give_me_work",
        json={},
        headers={
            "X-Agent-ID": "00000000-0000-0000-0000-000000000001",
            "X-Agent-Role": "developer",
            "X-Agent-Token": "obviously-not-a-valid-hmac",
        },
    )
    assert r.status_code == _HTTP_401
