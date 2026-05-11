"""TaskCreate (POST /tasks request schema) enforces TASK_AT_CREATE."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.models.task import TaskCreate


def _ok_payload() -> dict:
    return {
        "title": "Add user lookup endpoint",
        "description": (
            "Add GET /v1/users/{id} returning user JSON for the dashboard."
        ),
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "team": "backend",
        "project_id": uuid4(),
        "acceptance_criteria": ["returns 404 for unknown user"],
    }


def test_task_create_accepts_complete_payload() -> None:
    TaskCreate(**_ok_payload())


def test_task_create_rejects_empty_acceptance_criteria() -> None:
    payload = _ok_payload()
    payload["acceptance_criteria"] = []
    with pytest.raises(ValidationError):
        TaskCreate(**payload)


def test_task_create_rejects_missing_nature() -> None:
    payload = _ok_payload()
    del payload["nature"]
    with pytest.raises(ValidationError):
        TaskCreate(**payload)


def test_task_create_rejects_missing_task_type() -> None:
    payload = _ok_payload()
    del payload["task_type"]
    with pytest.raises(ValidationError):
        TaskCreate(**payload)


def test_task_create_rejects_short_description() -> None:
    payload = _ok_payload()
    payload["description"] = "x"
    with pytest.raises(ValidationError):
        TaskCreate(**payload)
