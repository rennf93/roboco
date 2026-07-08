"""api.middleware coverage — pure-function status mapping + handlers."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Annotated, Any

# UUID annotates a Pydantic model field below, so it must stay a runtime import
# (Pydantic resolves the annotation when building the model) despite `from
# __future__ import annotations` making it look type-checking-only to ruff.
from uuid import UUID  # noqa: TC003

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator
from roboco.api.middleware import (
    DbCommitMiddleware,
    _uuid_field_remediation,
    get_status_code,
    setup_middleware,
)
from roboco.config import settings
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


class _EnumFieldBody(BaseModel):
    """Mirrors IAmBlockedRequest: a field_validator that raises ValueError.

    Pydantic v2 stashes the raw ValueError object in error['ctx']['error'],
    which is not JSON-serializable — the handler must encode it (jsonable_encoder)
    or json.dumps crashes the 422 render into a 500."""

    kind: str

    @field_validator("kind")
    @classmethod
    def _one_of(cls, v: str) -> str:
        if v not in {"a", "b"}:
            raise ValueError(f"kind must be one of: a | b. Got {v!r}.")
        return v


def test_validator_valueerror_returns_422_not_500() -> None:
    """A field_validator ValueError (raw exc in ctx) must render a clean 422,
    not crash the handler into a 500. Reproduces the live i_am_blocked
    blocker_type='task_complete' crash."""
    app = FastAPI()
    setup_middleware(app)

    @app.post("/enum")
    async def _e(_data: _EnumFieldBody) -> Any:
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/enum", json={"kind": "task_complete"})
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert "detail" in body
    # The human-readable validator message must survive serialization.
    assert "kind must be one of" in str(body["detail"])


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


# ---------------------------------------------------------------------------
# FlowVerbTimeoutMiddleware — per-verb budget selection
# ---------------------------------------------------------------------------


def _make_flow_app() -> FastAPI:
    """Two /api/v1/flow/* routes that each sleep past the fast budget but
    under the slow one, so the picked timeout is observable by outcome."""
    app = FastAPI()

    @app.post("/api/v1/flow/developer/give_me_work")
    async def _normal_verb() -> Any:
        await asyncio.sleep(0.3)
        return {"status": "ok"}

    @app.post("/api/v1/flow/developer/i_am_done")
    async def _slow_verb() -> Any:
        await asyncio.sleep(0.3)
        return {"status": "ok"}

    setup_middleware(app)
    return app


def test_flow_verb_timeout_normal_verb_uses_default_budget(
    monkeypatch: Any,
) -> None:
    """A verb outside _SLOW_VERBS keeps the short default budget — a 0.3s
    handler exceeds a 0.05s budget and comes back as a 504."""
    monkeypatch.setattr(settings, "flow_verb_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "flow_verb_slow_timeout_seconds", 5)

    client = TestClient(_make_flow_app())
    response = client.post("/api/v1/flow/developer/give_me_work")
    assert response.status_code == HTTPStatus.GATEWAY_TIMEOUT
    assert response.json()["error"] == "gateway_timeout"


def test_flow_verb_timeout_slow_verb_uses_slow_budget(monkeypatch: Any) -> None:
    """A _SLOW_VERBS verb gets the longer budget — the same 0.3s handler that
    times out on the default budget completes fine under the slow one."""
    monkeypatch.setattr(settings, "flow_verb_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "flow_verb_slow_timeout_seconds", 5)

    client = TestClient(_make_flow_app())
    response = client.post("/api/v1/flow/developer/i_am_done")
    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# DbCommitMiddleware — commits the request's DB session before the response
# reaches the client. Reproduces the race: FastAPI sends the response before
# a Depends(get_db)-with-yield dependency's post-yield commit runs.
# ---------------------------------------------------------------------------


class _OrderedSession:
    """get_db-style fake session for ordering assertions.

    Exposes ``in_transaction()`` / ``commit()`` / ``rollback()`` like the real
    ``AsyncSession`` the middleware drives, recording call order in a shared
    list so a test can assert the commit happens before the wire send.
    """

    def __init__(self, order: list[str], fail_commit: bool = False) -> None:
        self._order = order
        self._fail_commit = fail_commit
        self._txn = True

    def in_transaction(self) -> bool:
        return self._txn

    async def commit(self) -> None:
        self._order.append("commit")
        if self._fail_commit:
            raise RuntimeError("commit failed")
        self._txn = False

    async def rollback(self) -> None:
        self._order.append("rollback")
        self._txn = False


async def _fake_get_db(request: Request) -> Any:
    """Module-level get_db-style dependency: stash the session on
    request.state, yield, commit post-yield as the fallback — the exact
    shape ``roboco.db.base.get_db`` uses and ``DbCommitMiddleware`` targets.

    Reads its order-list/fail-flag from ``request.app.state`` rather than a
    closure: ``Annotated[Any, Depends(...)]`` is stringified by this file's
    ``from __future__ import annotations``, and ``typing.get_type_hints``
    only resolves names from the function's module globals — a local
    closure name would raise, silently downgrading the parameter to a plain
    query param instead of a dependency.
    """
    order: list[str] = request.app.state.db_commit_order
    session = _OrderedSession(order, fail_commit=request.app.state.db_commit_fail)
    request.state.db_session = session
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise


def _make_db_commit_app(order: list[str], fail_commit: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.db_commit_order = order
    app.state.db_commit_fail = fail_commit

    @app.post("/write")
    async def _write(_db: Annotated[Any, Depends(_fake_get_db)]) -> Any:
        order.append("route_body")
        return {"ok": True}

    setup_middleware(app)
    return app


def _instrumented_transport(app: FastAPI, order: list[str]) -> httpx.ASGITransport:
    """Wraps ``app`` so 'wire_response_start' marks the instant bytes would
    leave the server — the outermost observation point, past every
    middleware including DbCommitMiddleware. ``raise_app_exceptions=False``
    lets the failing-commit test inspect the resulting 5xx response instead
    of the exception ServerErrorMiddleware always re-raises after sending it."""

    async def outer(scope: Any, receive: Any, send: Any) -> None:
        async def capture(message: Any) -> None:
            if message["type"] == "http.response.start":
                order.append("wire_response_start")
            await send(message)

        await app(scope, receive, capture)

    return httpx.ASGITransport(app=outer, raise_app_exceptions=False)


async def test_db_commit_middleware_commits_before_response_reaches_client() -> None:
    """The client only sees the response after the session commits — proving
    the middleware, not get_db's post-yield fallback (which FastAPI's own
    routing runs AFTER the response is already on the wire), commits in time."""
    order: list[str] = []
    app = _make_db_commit_app(order)
    transport = _instrumented_transport(app, order)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/write")
    assert response.status_code == HTTPStatus.OK
    assert "commit" in order
    assert "wire_response_start" in order
    assert order.index("commit") < order.index("wire_response_start"), order


async def test_db_commit_middleware_failing_commit_returns_5xx_not_200() -> None:
    """A commit that fails after the route succeeded must not report 200 —
    the response hasn't reached the wire yet, so it comes back as a 5xx."""
    order: list[str] = []
    app = _make_db_commit_app(order, fail_commit=True)
    transport = _instrumented_transport(app, order)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/write")
    assert response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
    assert "commit" in order


async def test_db_commit_middleware_skips_non_http_scope() -> None:
    """Websocket (and any non-http) scope passes straight through untouched —
    no send wrapping, no state lookup."""
    calls: list[dict[str, Any]] = []

    async def inner_app(scope: Any, _receive: Any, _send: Any) -> None:
        calls.append(scope)

    middleware = DbCommitMiddleware(inner_app)
    scope = {"type": "websocket"}

    async def receive() -> dict[str, Any]:
        return {}

    async def send(_message: Any) -> None:
        raise AssertionError("send should not be called for a websocket scope")

    await middleware(scope, receive, send)
    assert calls == [scope]


def test_db_commit_middleware_passes_through_session_less_request() -> None:
    """A request whose route never installs a get_db-style dependency (no
    request.state.db_session stashed) reaches the client unmodified."""
    app = FastAPI()

    @app.get("/plain")
    async def _plain() -> Any:
        return {"ok": True}

    setup_middleware(app)
    client = TestClient(app)
    response = client.get("/plain")
    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"ok": True}
