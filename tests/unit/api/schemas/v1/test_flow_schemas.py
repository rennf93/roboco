"""Schema-level invariants for /api/v1/flow/* request models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from roboco.api.schemas.v1.flow import DelegateRequest
from roboco.models.base import Complexity

# Minimal valid payload for DelegateRequest — only the required fields.
# estimated_complexity is varied per test.
_BASE = {
    "parent_task_id": "00000000-0000-0000-0000-000000000001",
    "title": "wire up the thing",
    "description": "wire up the thing end to end with tests",
    "assigned_to": "be-dev-1",
    "team": "backend",
    "task_type": "code",
    "nature": "technical",
    "acceptance_criteria": ["it works"],
}


def _payload(**overrides: Any) -> dict[str, Any]:
    return {**_BASE, **overrides}


def test_delegate_request_rejects_critical_complexity() -> None:
    # "critical" is not a Complexity enum member (LOW|MEDIUM|HIGH); must 422
    # at the boundary, not pass-then-500 at flush.
    with pytest.raises(ValidationError):
        DelegateRequest.model_validate(_payload(estimated_complexity="critical"))


def test_delegate_request_accepts_low_medium_high() -> None:
    for val in ("low", "medium", "high"):
        req = DelegateRequest.model_validate(_payload(estimated_complexity=val))
        assert req.estimated_complexity == Complexity(val)
