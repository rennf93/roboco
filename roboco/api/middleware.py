"""
API Middleware

Request/response middleware for logging, error handling, and correlation IDs.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import Any, cast

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from roboco.api.schemas.common import ErrorCode
from roboco.config import settings
from roboco.exceptions import (
    AuthenticationError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    RobocoError,
    ValidationError,
)
from roboco.foundation.policy.flow_timeouts import SLOW_VERBS as _SLOW_VERBS
from roboco.services.base import (
    ConflictError as ServiceConflictError,
)
from roboco.services.base import (
    NotFoundError as ServiceNotFoundError,
)
from roboco.services.base import (
    ServiceError,
    ServiceUnavailableError,
)
from roboco.services.base import (
    UnauthorizedError as ServiceUnauthorizedError,
)
from roboco.services.base import (
    ValidationError as ServiceValidationError,
)
from roboco.services.exceptions import RateLimitError

logger = structlog.get_logger()


# =============================================================================
# CORRELATION ID MIDDLEWARE
# =============================================================================


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Adds a correlation ID to each request for tracing.

    The correlation ID is:
    - Extracted from X-Correlation-ID header if present
    - Generated if not present
    - Added to response headers
    - Bound to the logger context
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Get or generate correlation ID
        correlation_id = request.headers.get("X-Correlation-ID")
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # Store in request state for access in handlers
        request.state.correlation_id = correlation_id

        # Bind to structlog context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            path=request.url.path,
            method=request.method,
        )

        # Process request
        response = cast("Response", await call_next(request))

        # Add correlation ID to response
        response.headers["X-Correlation-ID"] = correlation_id

        return response


# =============================================================================
# REQUEST LOGGING MIDDLEWARE
# =============================================================================


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs request/response details with timing.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()

        # Log request
        logger.info(
            "Request started",
            path=request.url.path,
            method=request.method,
            query_params=dict(request.query_params),
        )

        try:
            response = cast("Response", await call_next(request))
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log response
            logger.info(
                "Request completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            # Add timing header
            response.headers["X-Response-Time-Ms"] = str(round(duration_ms, 2))

            return response

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "Request failed",
                duration_ms=round(duration_ms, 2),
                error=str(e),
            )
            raise


# =============================================================================
# EXCEPTION HANDLERS
# =============================================================================


def get_status_code(exc: RobocoError) -> int:
    """Map exception type to HTTP status code."""
    status_map = {
        NotFoundError: 404,
        ValidationError: 422,
        InvalidStateError: 409,
        PermissionDeniedError: 403,
        AuthenticationError: 401,
    }

    for exc_type, status in status_map.items():
        if isinstance(exc, exc_type):
            return status

    # Default for other RobocoError subclasses
    return 400


async def roboco_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle RobocoError exceptions."""
    roboco_exc = cast("RobocoError", exc)
    status_code = get_status_code(roboco_exc)

    # Add correlation ID to error details
    correlation_id = getattr(request.state, "correlation_id", None)
    if correlation_id:
        roboco_exc.details["correlation_id"] = correlation_id

    logger.warning(
        "Handled exception",
        error_code=roboco_exc.code,
        error_message=roboco_exc.message,
        status_code=status_code,
    )

    return JSONResponse(
        status_code=status_code,
        content=roboco_exc.to_dict(),
    )


# `roboco.services.base.ServiceError` is a parallel exception hierarchy that
# does NOT inherit from `RobocoError` (it extends `Exception` directly), so
# `roboco_exception_handler` never sees it and the requests fall through to
# `generic_exception_handler` as 500s. Map its subclasses to the same status
# codes used in the RobocoError handler so route-layer try/except blocks can
# surface clean 4xx codes whether the service raises from `roboco.exceptions`
# or `roboco.services.base`.
_SERVICE_ERROR_STATUS: dict[type[ServiceError], int] = {
    ServiceNotFoundError: 404,
    ServiceValidationError: 422,
    ServiceConflictError: 409,
    ServiceUnauthorizedError: 403,
    ServiceUnavailableError: 503,
}


async def service_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle `roboco.services.base.ServiceError` and subclasses."""
    svc_exc = cast("ServiceError", exc)
    status_code = 500
    for exc_type, mapped_status in _SERVICE_ERROR_STATUS.items():
        if isinstance(svc_exc, exc_type):
            status_code = mapped_status
            break

    correlation_id = getattr(request.state, "correlation_id", None)
    details = dict(svc_exc.details)
    if correlation_id:
        details["correlation_id"] = correlation_id

    logger.warning(
        "Handled exception",
        error_type=type(svc_exc).__name__,
        error_message=svc_exc.message,
        status_code=status_code,
    )

    return JSONResponse(
        status_code=status_code,
        content={
            "error": type(svc_exc).__name__,
            "message": svc_exc.message,
            "details": details,
        },
    )


async def rate_limit_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handle :class:`~roboco.services.exceptions.RateLimitError`.

    Returns HTTP 429 with a ``Retry-After`` response header (when available)
    and a structured JSON body so API consumers can back off gracefully.
    """
    rl_exc = cast("RateLimitError", exc)
    correlation_id = getattr(request.state, "correlation_id", None)

    logger.warning(
        "LLM rate limit exhausted",
        provider=rl_exc.provider,
        retry_after=rl_exc.retry_after,
    )

    content: dict = {
        "error": "rate_limit_exceeded",
        "provider": rl_exc.provider,
        "message": str(rl_exc),
    }
    if correlation_id:
        content["correlation_id"] = correlation_id

    headers: dict[str, str] = {}
    if rl_exc.retry_after is not None:
        headers["Retry-After"] = str(int(rl_exc.retry_after))

    return JSONResponse(
        status_code=429,
        content=content,
        headers=headers,
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    correlation_id = getattr(request.state, "correlation_id", None)

    logger.exception(
        "Unhandled exception",
        error=str(exc),
        error_type=type(exc).__name__,
    )

    return JSONResponse(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": ErrorCode.INTERNAL_ERROR,
                "message": "An internal error occurred",
                "details": {
                    "correlation_id": correlation_id,
                },
            }
        },
    )


# Map HTTP status codes to string error codes
_HTTP_TO_ERROR_CODE: dict[int, str] = {
    400: ErrorCode.INVALID_INPUT,
    401: ErrorCode.NOT_AUTHORIZED,
    403: ErrorCode.ACCESS_DENIED,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.INVALID_INPUT,  # Conflict
    422: ErrorCode.INVALID_INPUT,  # Validation error
    500: ErrorCode.INTERNAL_ERROR,
}


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle FastAPI HTTPException with standardized error format.

    Converts HTTP status codes to string error codes for consistency with MCP.
    """
    http_exc = cast("HTTPException", exc)
    correlation_id = getattr(request.state, "correlation_id", None)

    # Map status code to error code
    error_code = _HTTP_TO_ERROR_CODE.get(http_exc.status_code, ErrorCode.INTERNAL_ERROR)

    logger.warning(
        "HTTP exception",
        status_code=http_exc.status_code,
        error_code=error_code,
        detail=http_exc.detail,
    )

    response_content: dict = {
        "error": {
            "code": error_code,
            "message": str(http_exc.detail),
        }
    }

    if correlation_id:
        response_content["error"]["details"] = {"correlation_id": correlation_id}

    return JSONResponse(
        status_code=http_exc.status_code,
        content=response_content,
    )


# =============================================================================
# SETUP FUNCTION
# =============================================================================


def _uuid_field_remediation(errors: Sequence[Any]) -> str | None:
    """Spell out the fix when a truncated id is sent where a UUID is required.

    Agents routinely copy the 8-character task prefix the system shows them
    (e.g. the ``[cee99ecc]`` commit prefix) and send it as ``task_id``, which
    fails UUID validation with an opaque "invalid length" message and wastes a
    call. Detect that case and hand back an actionable remediation instead.
    """
    for err in errors:
        if not isinstance(err, dict):
            continue
        loc = err.get("loc") or ()
        field = loc[-1] if loc else None
        if field == "task_id" and "uuid" in str(err.get("type", "")).lower():
            return (
                "Use the FULL 36-character task UUID, not the 8-character short "
                "form shown in commit prefixes or summaries. The full id is in "
                "the `task_id` field of the envelope returned by give_me_work "
                "or your most recent verb."
            )
    return None


# Credential-bearing request fields. A 422 on a secret-bearing request
# would otherwise dump the plaintext GitHub PAT / provider API key / bearer
# token into structlog output before the route ever encrypts it. The per-field
# ``errors`` carry only field names and types (never values), so they stay
# logged unchanged. Match by exact key name so a renamed secret field is
# caught by the next audit pass rather than silently leaking.
_SECRET_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "git_token",
        "api_key",
        "auth_token",
        "token",
        "password",
        "secret",
        "client_secret",
        "access_token",
        "refresh_token",
    }
)

_REDACTED = "***REDACTED***"


def _scrub_secrets(value: Any) -> Any:
    """Return a deep copy of ``value`` with known secret fields redacted.

    Recurses into nested dicts and lists so a secret inside ``nested: {...}``
    or a list element is also scrubbed. Non-secret fields are preserved so ops
    can still see which field broke. The original ``rve.body`` is not mutated
    (the 422 response body echoes the client's own submission unscrubbed).
    """
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k in _SECRET_FIELD_NAMES else _scrub_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_secrets(v) for v in value]
    return value


async def request_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the rejected body before returning the standard 422 response.

    FastAPI's default 422 returns validation details to the client but
    nothing lands in server logs. During smoke tests this leaves us
    blind to which field actually broke. Log the body + the per-field
    errors so the next 422 is debuggable in one log scan.

    When the failure is a truncated ``task_id`` (the recurring agent mistake),
    add a ``remediate`` hint so the agent knows to retry with the full UUID.

    F022: the log line scrubs known credential-bearing fields
    (``git_token`` / ``api_key`` / ``auth_token`` / …) from the body before
    logging. The 422 *response* body is unchanged — the client sent those
    values, only the server's own log is redacted.
    """
    rve = cast("RequestValidationError", exc)
    body = rve.body if isinstance(rve.body, str | bytes | dict | list) else None
    errors = rve.errors()
    body_for_log = _scrub_secrets(body) if isinstance(body, dict | list) else body
    logger.warning(
        "Request validation failed",
        path=request.url.path,
        method=request.method,
        body=body_for_log,
        errors=errors,
    )
    # jsonable_encoder is mandatory: Pydantic v2 stashes the raw exception
    # object under error['ctx']['error'] for any validator that raises
    # ValueError (e.g. blocker_type), and a raw Exception is not JSON
    # serializable — without this json.dumps crashes the 422 render into a
    # 500. FastAPI's own default validation handler encodes for the same reason.
    content: dict[str, Any] = {
        "detail": jsonable_encoder(errors),
        "body": jsonable_encoder(body),
    }
    remediate = _uuid_field_remediation(errors)
    if remediate is not None:
        content["remediate"] = remediate
    return JSONResponse(
        status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=content,
    )


def setup_middleware(app: FastAPI) -> None:
    """
    Setup all middleware for the application.

    Order matters:
    1. CorrelationIdMiddleware - first to set correlation ID
    2. RequestLoggingMiddleware - logs with correlation ID

    Exception handler priority:
    1. RequestValidationError - 422s; log body + per-field errors
    2. HTTPException - most common, converts to string error codes
    3. RobocoError - custom domain exceptions
    4. Exception - catch-all for unexpected errors
    """
    # Exception handlers (order: specific to general)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RobocoError, roboco_exception_handler)
    app.add_exception_handler(ServiceError, service_exception_handler)
    app.add_exception_handler(RateLimitError, rate_limit_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Middleware (added in reverse order due to LIFO): the LAST add_middleware
    # call is the OUTERMOST. DbCommitMiddleware is added FIRST so it is the
    # INNERMOST of all four — closest to the routes, right next to CORS —
    # and, critically, INSIDE FlowVerbTimeoutMiddleware: a hanging commit on a
    # flow-verb request stays bounded by Flow's asyncio.timeout, and Flow's
    # own synthesized 504 (sent via its own upstream `send`, never re-entering
    # `self.app`) never reaches DbCommitMiddleware at all. FlowVerbTimeoutMiddleware
    # is added next so correlation + logging still wrap the 504 it returns,
    # AND its asyncio.timeout cancels the route coroutine + its get_db
    # dependency directly (same task, reliable cancel).
    app.add_middleware(DbCommitMiddleware)
    app.add_middleware(FlowVerbTimeoutMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIdMiddleware)


class FlowVerbTimeoutMiddleware:
    """Pure-ASGI server-side timeout on gateway intent-verb requests.

    A hung flow verb — e.g. ``claim()`` blocked on a ``SELECT ... FOR
    UPDATE`` row lock held by a prior stuck transaction — would otherwise hold
    its request transaction open indefinitely. uvicorn does not cancel the
    endpoint coroutine on client disconnect, and ``get_db`` only rolled back
    on ``Exception``, so the row lock was never released and every later
    task-row write on that task wedged (the 2026-07-07
    ``kimi-k2.7-code:cloud`` agent on task 79d686f0). This wraps each
    ``/api/v1/flow/*`` request in ``asyncio.timeout``; on expiry the inner
    app is cancelled (CancelledError propagates through ``get_db``, which now
    invalidates the session — releasing the lock and discarding a connection
    that may be mid-protocol rather than reusing it via a rollback, see
    ``get_db``) and a clean retryable 504 envelope is returned. Pure ASGI (not
    BaseHTTPMiddleware) so cancellation propagates into the route coroutine
    without the spawned-task gap.

    Reads (``evidence``) and journal writes (``note``) don't touch the task
    row, so they are unaffected; only task-row writes route through ``claim``.

    ``_SLOW_VERBS`` (from ``roboco.foundation.policy.flow_timeouts`` — git
    push + quality gate, a multi-step PR-create chain, workspace clone, or
    planning writes) get the longer ``flow_verb_slow_timeout_seconds`` budget
    instead of the default — routine calls to those verbs otherwise exceed
    120s. The same set drives the agent-side MCP client's timeout
    (``roboco/mcp/flow_server.py``) so the two walls can't drift apart.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1/flow/"):
            await self.app(scope, receive, send)
            return
        verb = scope["path"].rstrip("/").rsplit("/", 1)[-1]
        timeout = (
            settings.flow_verb_slow_timeout_seconds
            if verb in _SLOW_VERBS
            else settings.flow_verb_timeout_seconds
        )
        started = False

        async def send_wrapper(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            async with asyncio.timeout(timeout):
                await self.app(scope, receive, send_wrapper)
        except TimeoutError:
            # The inner app was cancelled mid-verb; get_db has already
            # invalidated the session (releasing the FOR UPDATE lock and
            # discarding the connection) by the time we get here.
            if started:
                # The route had already begun a response before the timeout
                # fired — the client owns whatever was sent; we cannot start
                # a new one. Rare for a hung verb (it hangs before responding).
                return
            body = json.dumps(
                {
                    "status": None,
                    "task_id": None,
                    "next": None,
                    "evidence": {},
                    "context_briefing": {},
                    "error": "gateway_timeout",
                    "message": (
                        f"verb exceeded the {timeout:.0f}s server-side timeout; "
                        "the request transaction was rolled back"
                    ),
                    "remediate": (
                        "retry the verb; if it persists the underlying task "
                        "may be wedged — escalate"
                    ),
                }
            ).encode()
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ]
            try:
                await send(
                    {"type": "http.response.start", "status": 504, "headers": headers}
                )
                await send({"type": "http.response.body", "body": body})
            except Exception:
                # Client may have already disconnected (the original trigger);
                # the lock is released regardless. Nothing to do.
                pass


class DbCommitMiddleware:
    """Commits the request's DB session before the response reaches the client.

    FastAPI resolves ``Depends(get_db)`` on the request-scoped ``AsyncExitStack``
    and sends the response (``fastapi/routing.py``'s ``request_response``,
    ``await response(scope, receive, send)``) BEFORE that stack unwinds and
    runs ``get_db``'s post-yield ``await session.commit()``. So every write
    endpoint that relies on it returns 200 while its commit is still pending —
    a follow-up request (or a fresh connection) can read pre-commit state —
    and a commit that later FAILS leaves the client told "ok" with nothing
    persisted.

    ``get_db_committed`` (``roboco/db/base.py`` — the ``roboco.api.deps.DbSession``
    target every route depends on) stashes the live session on
    ``request.state.db_session`` before yielding. This middleware wraps
    ``send``: on the FIRST ``http.response.start`` it commits that session
    BEFORE forwarding the event, so the client only ever sees the response
    after the commit lands. A commit failure rolls back and re-raises — the
    response hasn't started, so the surrounding exception-handling machinery
    (FastAPI's handlers / Starlette's ``ServerErrorMiddleware``) turns it into
    a clean 500 instead of the silent post-200 loss. ``session.in_transaction()``
    makes the check idempotent and skips the exception path for free: an
    exception rolls back (and closes) the session inside ``get_db`` before
    its error response is built, so by the time THAT response's
    ``http.response.start`` reaches here there is no open transaction left
    to commit.

    Added INSIDE (closer to the routes than) ``FlowVerbTimeoutMiddleware`` —
    see ``setup_middleware`` — so a hanging commit on a flow-verb request
    stays bounded by Flow's ``asyncio.timeout``. That timeout is scoped to
    the WHOLE ``self.app(...)`` call including this middleware, so its
    deadline can fire while the commit below is itself in flight (not just
    while a route handler hangs before responding) — ``started`` in the
    outer middleware is still ``False`` at that point (its own wrapped
    ``send`` hasn't been called yet), so it sends its 504 normally once the
    ``CancelledError`` below propagates.

    The commit itself (``_commit_shielded``) runs cancellation-safe: a bare
    ``await session.commit()`` cancelled mid-wire abandons the asyncpg
    connection in an undefined protocol state, and the pool's own recovery —
    ``invalidate()`` forcing the driver's ``terminate()`` — is itself
    implicated in a uvloop/asyncpg segfault class observed on CI (uvloop
    0.22 + asyncpg 0.31 + Python 3.13; see ``ROBOCO_UVICORN_LOOP``). So the
    commit runs as its own task, shielded from this request's cancellation,
    and gets a short grace (``settings.db_commit_cancel_grace_seconds``) to
    finish naturally instead of being severed on the spot. Only a commit
    that actually fails, or one still stuck past the grace, gets invalidated
    — and re-raising the original ``CancelledError`` afterward still
    propagates up through FastAPI's dependency ``AsyncExitStack`` (still
    open here — ``response(scope, receive, send)`` is called from inside it,
    see ``get_db_committed``'s docstring) into ``get_db``'s own
    ``except asyncio.CancelledError``, which invalidates the session again —
    a safe no-op (SQLAlchemy's ``Session.invalidate()`` only touches the
    connection once; a session with no open transaction left skips it) that
    keeps the two layers independent rather than coupled.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                session = scope.get("state", {}).get("db_session")
                if session is not None and session.in_transaction():
                    await _commit_shielded(session)
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def _commit_shielded(session: Any) -> None:
    """Commit ``session`` without abandoning it mid-wire on cancellation.

    Runs the commit as an independent task and shields the wait on it from
    this request's own cancellation (a plain ``await session.commit()``
    would otherwise hand the request task's cancel straight to the commit
    task, since awaiting a Task makes it the awaiter's ``_fut_waiter``).
    On cancel, the commit keeps running in the background for up to
    ``settings.db_commit_cancel_grace_seconds``:

    - finishes successfully -> nothing to undo, just re-raise the cancel
      (the client's retry is idempotent-safe; data is already durable).
    - finishes with an error, or is still stuck past the grace -> the
      connection is in a state worth discarding; invalidate() then re-raise.

    A non-cancellation commit failure (shield propagates it unchanged)
    takes the plain rollback path, unchanged from before.
    """
    commit_task = asyncio.ensure_future(session.commit())
    try:
        await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        done, _pending = await asyncio.wait(
            {commit_task}, timeout=settings.db_commit_cancel_grace_seconds
        )
        if commit_task not in done:
            commit_task.cancel()
            with suppress(BaseException):
                await commit_task
        if not commit_task.cancelled() and commit_task.exception() is None:
            raise  # committed despite the cancel — nothing to undo
        await session.invalidate()
        raise
    except Exception:
        await session.rollback()
        raise
