"""
API Middleware

Request/response middleware for logging, error handling, and correlation IDs.
"""

import time
import uuid
from collections.abc import Callable
from typing import cast

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from roboco.api.schemas.common import ErrorCode
from roboco.exceptions import (
    AuthenticationError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    RobocoError,
    ValidationError,
)

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


def setup_middleware(app: FastAPI) -> None:
    """
    Setup all middleware for the application.

    Order matters:
    1. CorrelationIdMiddleware - first to set correlation ID
    2. RequestLoggingMiddleware - logs with correlation ID

    Exception handler priority:
    1. HTTPException - most common, converts to string error codes
    2. RobocoError - custom domain exceptions
    3. Exception - catch-all for unexpected errors
    """
    # Exception handlers (order: specific to general)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RobocoError, roboco_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # Middleware (added in reverse order due to LIFO)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
