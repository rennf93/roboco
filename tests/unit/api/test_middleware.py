"""api.middleware coverage — pure-function status mapping + handlers."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

# UUID annotates a Pydantic model field below, so it must stay a runtime import
# (Pydantic resolves the annotation when building the model) despite `from
# __future__ import annotations` making it look type-checking-only to ruff.
from uuid import UUID  # noqa: TC003

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from roboco.api.middleware import (
    _uuid_field_remediation,
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
from roboco.services.base import (
    ConflictError as ServiceConflictError,
)
from roboco.services.base import (
    NotFoundError as ServiceNotFoundError,
)
from roboco.services.base import (
    UnauthorizedError as ServiceUnauthorizedError,
)
from roboco.services.base import (
    ValidationError as ServiceValidationError,
)
from structlog.testing import capture_logs

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
    async def _ok() -> Any:
        return {"status": "ok"}

    @app.get("/raise")
    async def _raise() -> Any:
        raise RuntimeError("boom")

    @app.get("/notfound")
    async def _nf() -> Any:
        raise NotFoundError("Resource", "abc")

    @app.get("/http-error")
    async def _he() -> Any:
        raise HTTPException(status_code=403, detail="nope")

    # service-layer errors (parallel hierarchy from roboco.services.base)
    @app.get("/svc-notfound")
    async def _svc_nf() -> Any:
        raise ServiceNotFoundError("Channel", "main-pm")

    @app.get("/svc-validation")
    async def _svc_v() -> Any:
        raise ServiceValidationError("invalid input", field="title")

    @app.get("/svc-conflict")
    async def _svc_c() -> Any:
        raise ServiceConflictError("duplicate", resource_type="task")

    @app.get("/svc-unauth")
    async def _svc_u() -> Any:
        raise ServiceUnauthorizedError("merge_pr", reason="not your PR")

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


# `roboco.services.base.ServiceError` is a parallel exception hierarchy
# (it does NOT inherit from RobocoError), so a separate handler maps it
# to clean 4xx codes instead of letting the generic 500 handler eat it.


def test_service_notfound_translates_to_404() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/svc-notfound")
    assert response.status_code == HTTPStatus.NOT_FOUND
    body = response.json()
    assert body["error"] == "NotFoundError"
    assert "main-pm" in body["message"]


def test_service_validation_translates_to_422() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/svc-validation")
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert body["error"] == "ValidationError"


def test_service_conflict_translates_to_409() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/svc-conflict")
    assert response.status_code == HTTPStatus.CONFLICT


def test_service_unauthorized_translates_to_403() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/svc-unauth")
    assert response.status_code == HTTPStatus.FORBIDDEN


def test_service_handler_carries_correlation_id() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    cid = "test-svc-correlation-987"
    response = client.get("/svc-notfound", headers={"X-Correlation-ID": cid})
    body = response.json()
    assert body["details"]["correlation_id"] == cid


def test_generic_exception_returns_500() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.get("/raise")
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert "error" in body


# ---------------------------------------------------------------------------
# _uuid_field_remediation + truncated-task_id 422 remediation
# ---------------------------------------------------------------------------


def test_uuid_field_remediation_hits_truncated_task_id() -> None:
    errors = [{"loc": ("body", "task_id"), "type": "uuid_parsing", "msg": "bad"}]
    hint = _uuid_field_remediation(errors)
    assert hint is not None
    assert "full" in hint.lower()
    assert "uuid" in hint.lower()


def test_uuid_field_remediation_ignores_other_field_errors() -> None:
    errors = [{"loc": ("body", "title"), "type": "string_too_short", "msg": "x"}]
    assert _uuid_field_remediation(errors) is None


def test_uuid_field_remediation_ignores_non_uuid_task_id_errors() -> None:
    errors = [{"loc": ("body", "task_id"), "type": "missing", "msg": "required"}]
    assert _uuid_field_remediation(errors) is None


class _TaskIdBody(BaseModel):
    task_id: UUID


def _make_uuid_app() -> FastAPI:
    app = FastAPI()

    @app.post("/needs-uuid")
    async def _need(body: _TaskIdBody) -> dict:
        return {"task_id": str(body.task_id)}

    setup_middleware(app)
    return app


def test_truncated_task_id_422_carries_remediation() -> None:
    """An 8-char task_id (the recurring agent mistake) returns 422 + remediate."""
    client = TestClient(_make_uuid_app(), raise_server_exceptions=False)
    response = client.post("/needs-uuid", json={"task_id": "cee99ecc"})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert "remediate" in body
    assert "full" in body["remediate"].lower()


def test_other_validation_422_omits_remediation() -> None:
    """A non-task_id validation error keeps the standard 422 shape (no remediate)."""
    client = TestClient(_make_uuid_app(), raise_server_exceptions=False)
    response = client.post("/needs-uuid", json={})  # missing task_id entirely
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "remediate" not in response.json()


def test_request_validation_handler_returns_422_with_details() -> None:
    """request_validation_handler logs + returns 422 with errors+body (251-260)."""

    class _Body(BaseModel):
        name: str

    app = FastAPI()
    setup_middleware(app)

    @app.post("/validate")
    async def _v(_data: _Body) -> Any:
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/validate", json={"wrong_field": "x"})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert "detail" in body
    assert "body" in body


# ---------------------------------------------------------------------------
# secret scrubbing in the 422 log line
# ---------------------------------------------------------------------------


class _SecretBody(BaseModel):
    """Module-level model so FastAPI can resolve the annotation under
    `from __future__ import annotations` (function-local classes with complex
    field types aren't resolvable from the function's module globals)."""

    name: str
    git_token: str | None = None
    api_key: str | None = None
    auth_token: str | None = None
    nested: dict[str, Any] | None = None


def test_request_validation_handler_scrubs_secrets_from_log() -> None:
    """A 422 on a secret-bearing request must not dump the plaintext secret
    into the log line — only the redacted placeholder. The 422 response body
    is unchanged (the client sent those values; the server only redacts its
    own log)."""

    app = FastAPI()
    setup_middleware(app)

    @app.post("/project")
    async def _create(_data: _SecretBody) -> Any:
        return {"ok": True}

    secret_pat = "ghp_livesecret_123456"
    secret_key = "ollama-key-do-not-log"
    secret_token = "bearer-should-not-leak"
    payload = {
        # Missing required `name` -> 422, but the secret fields are still
        # parsed into rve.body and would be logged verbatim without the scrub.
        "git_token": secret_pat,
        "api_key": secret_key,
        "auth_token": secret_token,
        "nested": {"git_token": "nested-secret-abc", "safe": "keep"},
    }

    client = TestClient(app, raise_server_exceptions=False)
    with capture_logs() as logs:
        response = client.post("/project", json=payload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    # The response body is NOT scrubbed (the client sent these values).
    resp_body = response.json()
    assert resp_body["body"]["git_token"] == secret_pat
    assert resp_body["body"]["api_key"] == secret_key

    # Exactly one "Request validation failed" warning was emitted.
    fails = [e for e in logs if e["event"] == "Request validation failed"]
    assert len(fails) == 1
    logged_body = fails[0]["body"]

    # The log line must not contain any of the plaintext secrets.
    assert secret_pat not in str(logged_body)
    assert secret_key not in str(logged_body)
    assert secret_token not in str(logged_body)
    assert "nested-secret-abc" not in str(logged_body)

    # The redaction placeholder appears for each secret field (so ops can see
    # WHICH secret field was present), and the per-field errors are still
    # logged (they don't carry secrets).
    assert logged_body["git_token"] == "***REDACTED***"
    assert logged_body["api_key"] == "***REDACTED***"
    assert logged_body["auth_token"] == "***REDACTED***"
    assert logged_body["nested"]["git_token"] == "***REDACTED***"
    assert logged_body["nested"]["safe"] == "keep"  # non-secret preserved
    assert "errors" in fails[0]


def test_request_validation_handler_log_preserves_non_secret_fields() -> None:
    """Non-secret fields in the body are still logged in full — only the
    known credential-looking field names are redacted."""

    app = FastAPI()
    setup_middleware(app)

    @app.post("/project")
    async def _create(_data: _SecretBody) -> Any:
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    # `title` is not a field on _SecretBody -> 422, and `title` is non-secret
    # so it should still appear in the log; `git_token` is secret and must be
    # redacted.
    with capture_logs() as logs:
        response = client.post(
            "/project",
            json={"title": "visible-title", "git_token": "ghp_secret_xyz"},
        )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    fails = [e for e in logs if e["event"] == "Request validation failed"]
    assert len(fails) == 1
    logged_body = fails[0]["body"]
    assert logged_body["title"] == "visible-title"  # non-secret preserved
    assert logged_body["git_token"] == "***REDACTED***"  # secret redacted
    assert "ghp_secret_xyz" not in str(logged_body)
