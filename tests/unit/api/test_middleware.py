"""api.middleware coverage — pure-function status mapping + handlers."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from roboco.api.middleware import (
    get_status_code,
    setup_middleware,
)
from roboco.exceptions import (
    AuthenticationError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    RobocoError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# get_status_code
# ---------------------------------------------------------------------------


def test_get_status_code_for_not_found() -> None:
    assert get_status_code(NotFoundError("Task", "abc")) == HTTPStatus.NOT_FOUND


def test_get_status_code_for_validation() -> None:
    assert get_status_code(ValidationError("x")) == HTTPStatus.UNPROCESSABLE_ENTITY


def test_get_status_code_for_invalid_state() -> None:
    assert (
        get_status_code(InvalidStateError("pending", "complete")) == HTTPStatus.CONFLICT
    )


def test_get_status_code_for_permission() -> None:
    assert get_status_code(PermissionDeniedError("x")) == HTTPStatus.FORBIDDEN


def test_get_status_code_for_auth() -> None:
    assert get_status_code(AuthenticationError("x")) == HTTPStatus.UNAUTHORIZED


def test_get_status_code_for_generic() -> None:
    """Unknown RobocoError subclass defaults to 400."""
    assert get_status_code(RobocoError("x", code="other")) == HTTPStatus.BAD_REQUEST


# ---------------------------------------------------------------------------
# Middleware integration via TestClient
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    async def _ok():
        return {"status": "ok"}

    @app.get("/raise")
    async def _raise():
        raise RuntimeError("boom")

    @app.get("/notfound")
    async def _nf():
        raise NotFoundError("Resource", "abc")

    @app.get("/http-error")
    async def _he():
        raise HTTPException(status_code=403, detail="nope")

    setup_middleware(app)
    return app


def test_middleware_adds_correlation_id_header() -> None:
    client = TestClient(_make_app())
    response = client.get("/ok")
    assert response.status_code == HTTPStatus.OK
    assert "X-Correlation-ID" in response.headers


def test_middleware_uses_provided_correlation_id() -> None:
    client = TestClient(_make_app())
    cid = "test-correlation-12345"
    response = client.get("/ok", headers={"X-Correlation-ID": cid})
    assert response.headers["X-Correlation-ID"] == cid


def test_middleware_adds_response_time_header() -> None:
    client = TestClient(_make_app())
    response = client.get("/ok")
    assert "X-Response-Time-Ms" in response.headers


def test_roboco_exception_translates_to_404() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/notfound")
    assert response.status_code == HTTPStatus.NOT_FOUND


def test_http_exception_handler_returns_standardized_format() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/http-error")
    assert response.status_code == HTTPStatus.FORBIDDEN
    body = response.json()
    assert "error" in body


def test_generic_exception_returns_500() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/raise")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "error" in body


def test_request_validation_handler_returns_422_with_details() -> None:
    """request_validation_handler logs + returns 422 with errors+body (251-260)."""

    class _Body(BaseModel):
        name: str

    app = FastAPI()
    setup_middleware(app)

    @app.post("/validate")
    async def _v(_data: _Body):
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/validate", json={"wrong_field": "x"})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert "detail" in body
    assert "body" in body
