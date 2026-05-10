"""DelegateRequest schema must enforce TASK_AT_CREATE field constraints."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.api.schemas.v2.flow import DelegateRequest


def _ok_payload() -> dict:
    return {
        "parent_task_id": uuid4(),
        "title": "Add user lookup endpoint",
        "description": (
            "Add GET /v1/users/{id} returning the user JSON for the dashboard."
        ),
        "assigned_to": "be-dev-1",
        "team": "backend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "acceptance_criteria": [
            "returns 404 for unknown user",
            "returns 200 + user JSON for known user",
        ],
    }


def test_delegate_request_accepts_complete_payload() -> None:
    DelegateRequest(**_ok_payload())


def test_delegate_request_rejects_empty_acceptance_criteria() -> None:
    payload = _ok_payload()
    payload["acceptance_criteria"] = []
    with pytest.raises(ValidationError) as exc_info:
        DelegateRequest(**payload)
    assert "acceptance_criteria" in str(exc_info.value)


def test_delegate_request_rejects_missing_acceptance_criteria() -> None:
    payload = _ok_payload()
    del payload["acceptance_criteria"]
    with pytest.raises(ValidationError):
        DelegateRequest(**payload)


def test_delegate_request_rejects_missing_nature() -> None:
    payload = _ok_payload()
    del payload["nature"]
    with pytest.raises(ValidationError):
        DelegateRequest(**payload)


def test_delegate_request_rejects_missing_estimated_complexity() -> None:
    payload = _ok_payload()
    del payload["estimated_complexity"]
    with pytest.raises(ValidationError):
        DelegateRequest(**payload)


def test_delegate_request_rejects_short_description() -> None:
    payload = _ok_payload()
    payload["description"] = "x"
    with pytest.raises(ValidationError):
        DelegateRequest(**payload)
