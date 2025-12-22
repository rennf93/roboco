"""
HTTP Error Factories

Centralized error handling for API routes.
Provides factory functions for common HTTP errors and
automatic translation from service errors.
"""

from collections.abc import Awaitable, Callable
from functools import wraps

from fastapi import HTTPException, status

from roboco.services.base import (
    ConflictError,
    NotFoundError,
    ServiceError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
)

# =============================================================================
# ERROR FACTORY FUNCTIONS
# =============================================================================
# Use these to create consistent HTTPExceptions across routes


def not_found(
    resource_type: str,
    resource_id: str | None = None,
) -> HTTPException:
    """
    Create a 404 Not Found exception.

    Usage:
        if not task:
            raise not_found("Task", str(task_id))
    """
    detail = f"{resource_type} not found"
    if resource_id:
        detail = f"{resource_type} not found: {resource_id}"
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail,
    )


def forbidden(
    action: str,
    reason: str | None = None,
) -> HTTPException:
    """
    Create a 403 Forbidden exception.

    Usage:
        if not can_edit:
            raise forbidden("edit task", "not task owner")
    """
    detail = f"Not authorized: {action}"
    if reason:
        detail = f"{detail} ({reason})"
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


def unauthorized(
    message: str = "Authentication required",
) -> HTTPException:
    """
    Create a 401 Unauthorized exception.

    Usage:
        if not agent_id:
            raise unauthorized("Missing X-Agent-ID header")
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
    )


def validation_error(
    message: str,
    field: str | None = None,
) -> HTTPException:
    """
    Create a 400 Bad Request exception for validation errors.

    Usage:
        if not valid_status:
            raise validation_error("Invalid status transition", "status")
    """
    detail = message
    if field:
        detail = f"{field}: {message}"
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=detail,
    )


def conflict(
    message: str,
    resource_type: str | None = None,
) -> HTTPException:
    """
    Create a 409 Conflict exception.

    Usage:
        if duplicate:
            raise conflict("Channel slug already exists", "Channel")
    """
    detail = message
    if resource_type:
        detail = f"{resource_type}: {message}"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


def service_unavailable(
    service_name: str,
    reason: str | None = None,
) -> HTTPException:
    """
    Create a 503 Service Unavailable exception.

    Usage:
        if not initialized:
            raise service_unavailable("Orchestrator", "not initialized")
    """
    detail = f"Service unavailable: {service_name}"
    if reason:
        detail = f"{detail} ({reason})"
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


# =============================================================================
# SERVICE ERROR TRANSLATION
# =============================================================================


def handle_service_error(error: ServiceError) -> HTTPException:
    """
    Translate a ServiceError to an HTTPException.

    Maps service-layer errors to appropriate HTTP status codes:
    - NotFoundError → 404
    - ValidationError → 400
    - ConflictError → 409
    - UnauthorizedError → 403
    - ServiceUnavailableError → 503
    - ServiceError (generic) → 500

    Usage:
        try:
            result = await service.do_something()
        except ServiceError as e:
            raise handle_service_error(e)
    """
    if isinstance(error, NotFoundError):
        return not_found(error.resource_type, error.resource_id)
    if isinstance(error, ValidationError):
        return validation_error(error.message, error.field)
    if isinstance(error, ConflictError):
        return conflict(error.message, error.resource_type)
    if isinstance(error, UnauthorizedError):
        return forbidden(error.action, error.reason)
    if isinstance(error, ServiceUnavailableError):
        return service_unavailable(error.service_name, error.reason)

    # Generic service error → 500
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=error.message,
    )


# =============================================================================
# DECORATOR FOR AUTOMATIC ERROR HANDLING
# =============================================================================


def service_error_handler[**P, R](
    func: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """
    Decorator that automatically translates ServiceErrors to HTTPExceptions.

    Usage:
        @router.post("/tasks")
        @service_error_handler
        async def create_task(req: CreateTaskRequest) -> TaskResponse:
            # Any ServiceError raised here is automatically
            # translated to the appropriate HTTPException
            return await service.create(req)
    """

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except ServiceError as e:
            raise handle_service_error(e) from e

    return wrapper
