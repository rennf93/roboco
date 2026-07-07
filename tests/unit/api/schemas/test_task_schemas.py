"""SoftBlockRequest.resolver_type typed — typos 422, not silent AGENT."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from roboco.api.schemas.tasks import SoftBlockRequest
from roboco.models.base import BlockerResolverType


def test_soft_block_request_defaults_to_agent() -> None:
    req = SoftBlockRequest.model_validate(
        {"reason": "r", "blocker_type": "external", "what_needed": "w"}
    )
    assert req.resolver_type == BlockerResolverType.AGENT


def test_soft_block_request_accepts_human() -> None:
    req = SoftBlockRequest.model_validate(
        {
            "reason": "r",
            "blocker_type": "external",
            "what_needed": "w",
            "resolver_type": "human",
        }
    )
    assert req.resolver_type == BlockerResolverType.HUMAN


def test_soft_block_request_rejects_typo_resolver_type() -> None:
    # A CEO typo ("huamn") must surface as a 422 at the API boundary,
    # not silently downgrade to AGENT inside the service.
    with pytest.raises(ValidationError):
        SoftBlockRequest.model_validate(
            {
                "reason": "r",
                "blocker_type": "external",
                "what_needed": "w",
                "resolver_type": "huamn",
            }
        )
