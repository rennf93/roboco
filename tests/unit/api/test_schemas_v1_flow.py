"""Schema-level tests for v1 flow request bodies."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from roboco.api.schemas.v1.flow import DelegateRequest


def test_delegate_request_requires_task_type() -> None:
    """task_type must be supplied explicitly — no magic default.

    Background: the 2026-05-08 smoke-test trace showed main-pm calling
    delegate without task_type, the schema defaulted to 'code', the
    cell PM downstream couldn't plan a code-typed parent (pre-fix), and
    the run deadlocked. Make the field required so misuse fails at the
    HTTP boundary with a clear 422.
    """
    with pytest.raises(ValidationError) as exc:
        DelegateRequest(
            parent_task_id=uuid4(),
            title="t",
            description="add the new endpoint plus tests",
            assigned_to="be-dev-1",
            team="backend",
            nature="technical",
            estimated_complexity="medium",
            acceptance_criteria=["returns 200"],
            # task_type intentionally omitted
        )
    assert "task_type" in str(exc.value)


def test_delegate_request_accepts_explicit_task_type() -> None:
    req = DelegateRequest(
        parent_task_id=uuid4(),
        title="t",
        description="add the new endpoint plus tests",
        assigned_to="be-dev-1",
        team="backend",
        task_type="code",
        nature="technical",
        estimated_complexity="medium",
        acceptance_criteria=["returns 200"],
    )
    assert req.task_type == "code"
