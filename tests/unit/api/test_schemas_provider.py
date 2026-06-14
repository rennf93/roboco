"""Coverage for roboco.api.schemas.provider helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from roboco.api.schemas.provider import assignment_to_response
from roboco.models.base import AssignmentScope, ModelProvider

if TYPE_CHECKING:
    from roboco.db.tables import ModelAssignmentTable


def test_assignment_to_response_round_trip() -> None:
    provider = SimpleNamespace(type=ModelProvider.ANTHROPIC)
    row = cast(
        "ModelAssignmentTable",
        SimpleNamespace(
            id=uuid4(),
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            provider=provider,
            model_name="opus",
        ),
    )
    response = assignment_to_response(row)
    assert response.scope == AssignmentScope.GLOBAL
    assert response.provider_type == ModelProvider.ANTHROPIC
    assert response.model_name == "opus"
