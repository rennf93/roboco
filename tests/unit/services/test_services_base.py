"""Coverage for roboco.services.base — SingletonHolder pattern."""

from __future__ import annotations

from typing import Any, cast

import pytest
from roboco.services.base import (
    BaseService,
    ConflictError,
    NotFoundError,
    ServiceError,
    ServiceUnavailableError,
    SingletonHolder,
    SingletonService,
    UnauthorizedError,
    ValidationError,
)


class _DummyService:
    def __init__(self, value: str = "default") -> None:
        self.value = value


class _Holder(SingletonHolder[_DummyService]):
    def create_instance(self) -> _DummyService:
        return _DummyService("first")


def test_singleton_holder_get_creates_instance() -> None:
    holder = _Holder()
    assert holder.is_initialized is False
    inst = holder.get()
    assert inst.value == "first"
    assert holder.is_initialized is True


def test_singleton_holder_get_returns_same_instance() -> None:
    holder = _Holder()
    a = holder.get()
    b = holder.get()
    assert a is b


def test_singleton_holder_set_overrides_instance() -> None:
    holder = _Holder()
    custom = _DummyService("custom")
    holder.set(custom)
    assert holder.get() is custom


def test_singleton_holder_clear_resets_instance() -> None:
    holder = _Holder()
    holder.get()
    holder.clear()
    assert holder.is_initialized is False
    # Subsequent get() rebuilds.
    new = holder.get()
    assert new.value == "first"


def test_singleton_holder_create_instance_default_raises() -> None:
    """Base SingletonHolder must require subclass override."""
    holder: SingletonHolder[_DummyService] = SingletonHolder()
    with pytest.raises(NotImplementedError):
        holder.get()


# ---------------------------------------------------------------------------
# Error hierarchy — round-trip the constructors so the dataclass-style fields
# get exercised end-to-end.
# ---------------------------------------------------------------------------


def test_service_error_basic() -> None:
    err = ServiceError("oops", details={"k": "v"})
    assert err.message == "oops"
    assert err.details == {"k": "v"}


def test_not_found_error_with_id() -> None:
    err = NotFoundError("Task", resource_id="abc")
    assert "abc" in err.message
    assert err.resource_id == "abc"


def test_not_found_error_without_id() -> None:
    err = NotFoundError("Task")
    assert err.message == "Task not found"


def test_validation_error_with_field() -> None:
    err = ValidationError("bad", field="title")
    assert err.field == "title"


def test_conflict_error_records_resource() -> None:
    err = ConflictError("dup", resource_type="Project")
    assert err.resource_type == "Project"


def test_unauthorized_error_with_reason() -> None:
    err = UnauthorizedError("delete", reason="role")
    assert "delete" in err.message
    assert "role" in err.message


def test_service_unavailable_error_with_reason() -> None:
    err = ServiceUnavailableError("postgres", reason="down")
    assert "postgres" in err.message
    assert "down" in err.message


# ---------------------------------------------------------------------------
# BaseService + SingletonService instantiation — bind logger, etc.
# ---------------------------------------------------------------------------


def test_base_service_binds_session_and_logger() -> None:
    class _Svc(BaseService):
        service_name = "x"

    fake_session = object()
    svc = _Svc(cast("Any", fake_session))
    assert svc.session is fake_session
    assert svc.log is not None


def test_singleton_service_binds_logger() -> None:
    class _Svc(SingletonService):
        service_name = "y"

    svc = _Svc()
    assert svc.log is not None
