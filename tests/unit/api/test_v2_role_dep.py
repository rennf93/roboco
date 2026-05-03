"""v2 flow routes reject requests with the wrong X-Agent-Role.

Defense-in-depth check: every v2 flow router declares router-level
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
from roboco.api.routes.v2.flow_dev import router as flow_dev_router

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
        "/api/v2/flow/dev/give_me_work",
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
        "/api/v2/flow/dev/give_me_work",
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
        "/api/v2/flow/dev/give_me_work",
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
        "/api/v2/flow/dev/give_me_work",
        json={},
        headers={"X-Agent-ID": "00000000-0000-0000-0000-000000000001"},
    )
    # Missing X-Agent-Role => FastAPI 422 from header validation.
    # We just need it not to silently pass as 200.
    assert r.status_code != _HTTP_200
