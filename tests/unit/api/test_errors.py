"""api.utils.errors coverage."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from roboco.api.utils.errors import (
    conflict,
    forbidden,
    handle_service_error,
    not_found,
    service_error_handler,
    service_unavailable,
    unauthorized,
    validation_error,
)
from roboco.services.base import (
    ConflictError,
    NotFoundError,
    ServiceError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def test_not_found_with_id() -> None:
    e = not_found("Task", "abc-123")
    assert e.status_code == 404
    assert "abc-123" in e.detail


def test_not_found_without_id() -> None:
    e = not_found("Task")
    assert e.status_code == 404
    assert e.detail == "Task not found"


def test_forbidden_basic() -> None:
    e = forbidden("edit task")
    assert e.status_code == 403
    assert "edit task" in e.detail


def test_forbidden_with_reason() -> None:
    e = forbidden("edit", reason="not owner")
    assert e.status_code == 403
    assert "not owner" in e.detail


def test_unauthorized_default() -> None:
    e = unauthorized()
    assert e.status_code == 401


def test_unauthorized_custom() -> None:
    e = unauthorized("Missing token")
    assert e.detail == "Missing token"


def test_validation_error_basic() -> None:
    e = validation_error("bad input")
    assert e.status_code == 400


def test_validation_error_with_field() -> None:
    e = validation_error("required", field="title")
    assert "title" in e.detail


def test_conflict_basic() -> None:
    e = conflict("duplicate")
    assert e.status_code == 409


def test_conflict_with_resource() -> None:
    e = conflict("duplicate", resource_type="Channel")
    assert "Channel" in e.detail


def test_service_unavailable_basic() -> None:
    e = service_unavailable("Orchestrator")
    assert e.status_code == 503


def test_service_unavailable_with_reason() -> None:
    e = service_unavailable("Orchestrator", reason="not init")
    assert "not init" in e.detail


# ---------------------------------------------------------------------------
# handle_service_error translation
# ---------------------------------------------------------------------------


def test_handle_not_found() -> None:
    e = handle_service_error(NotFoundError(resource_type="Task", resource_id="abc"))
    assert e.status_code == 404


def test_handle_validation_error() -> None:
    e = handle_service_error(ValidationError("bad", field="x"))
    assert e.status_code == 400


def test_handle_conflict() -> None:
    e = handle_service_error(ConflictError("dup", resource_type="Channel"))
    assert e.status_code == 409


def test_handle_unauthorized() -> None:
    e = handle_service_error(UnauthorizedError(action="edit", reason="x"))
    assert e.status_code == 403


def test_handle_service_unavailable() -> None:
    e = handle_service_error(ServiceUnavailableError(service_name="X", reason="r"))
    assert e.status_code == 503


def test_handle_generic_service_error() -> None:
    e = handle_service_error(ServiceError("oops"))
    assert e.status_code == 500


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_error_handler_translates() -> None:
    @service_error_handler
    async def my_route() -> str:
        raise NotFoundError(resource_type="X", resource_id="1")

    with pytest.raises(HTTPException) as exc:
        await my_route()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_service_error_handler_passes_through_value() -> None:
    @service_error_handler
    async def my_route() -> str:
        return "ok"

    result = await my_route()
    assert result == "ok"


@pytest.mark.asyncio
async def test_service_error_handler_does_not_catch_other_exceptions() -> None:
    @service_error_handler
    async def my_route() -> str:
        raise ValueError("not a service error")

    with pytest.raises(ValueError):
        await my_route()
