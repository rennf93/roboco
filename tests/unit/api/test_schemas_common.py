"""api.schemas.common coverage."""

from __future__ import annotations

from roboco.api.schemas.common import (
    ApiResponse,
    ErrorCode,
    ErrorDetail,
    ListResponse,
    error_response,
    list_response,
    success_response,
)

# ---------------------------------------------------------------------------
# success_response
# ---------------------------------------------------------------------------


def test_success_response_basic() -> None:
    out = success_response({"key": "value"})
    assert out["status"] == "success"
    assert out["data"] == {"key": "value"}


def test_success_response_with_guidance() -> None:
    out = success_response({"x": 1}, guidance="next step")
    assert out["guidance"] == "next step"


def test_success_response_with_next_step() -> None:
    out = success_response({"x": 1}, next_step="EXECUTE")
    assert out["next_step"] == "EXECUTE"


def test_success_response_with_all_fields() -> None:
    out = success_response({"x": 1}, guidance="g", next_step="EXECUTE")
    assert out["guidance"] == "g"
    assert out["next_step"] == "EXECUTE"


# ---------------------------------------------------------------------------
# error_response
# ---------------------------------------------------------------------------


def test_error_response_basic() -> None:
    out = error_response("NOT_FOUND", "missing")
    assert out["error"]["code"] == "NOT_FOUND"
    assert out["error"]["message"] == "missing"


def test_error_response_with_details() -> None:
    out = error_response("INVALID", "bad", details={"field": "x"})
    assert out["error"]["details"] == {"field": "x"}


def test_error_response_with_hint() -> None:
    out = error_response("RAG_FAILED", "x", hint="try later")
    assert out["error"]["hint"] == "try later"


# ---------------------------------------------------------------------------
# list_response
# ---------------------------------------------------------------------------


def test_list_response_no_more() -> None:
    out = list_response(items=[1, 2, 3], total=3, offset=0, limit=20)
    assert out["has_more"] is False
    assert out["items"] == [1, 2, 3]


def test_list_response_with_more() -> None:
    out = list_response(items=[1, 2], total=10, offset=0, limit=2)
    assert out["has_more"] is True


# ---------------------------------------------------------------------------
# Error codes constants
# ---------------------------------------------------------------------------


def test_error_codes_defined() -> None:
    assert ErrorCode.NOT_FOUND == "NOT_FOUND"
    assert ErrorCode.ACCESS_DENIED == "ACCESS_DENIED"
    assert ErrorCode.TASK_NOT_FOUND == "TASK_NOT_FOUND"
    assert ErrorCode.PERMISSION_DENIED == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def test_error_detail_model() -> None:
    e = ErrorDetail(code="X", message="m")
    assert e.code == "X"
    assert e.details is None


def test_api_response_model() -> None:
    r = ApiResponse[dict](status="success", data={"k": "v"})
    assert r.status == "success"
    assert r.data == {"k": "v"}


def test_list_response_model() -> None:
    r = ListResponse[int](items=[1, 2], total=2)
    assert r.total == 2
    assert r.has_more is False
