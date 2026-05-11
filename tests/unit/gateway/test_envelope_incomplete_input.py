"""Envelope.incomplete_input is the structured-rejection envelope for
under-filled inputs (spec §5.2.1 interrogation pattern). It's distinct
from tracing_gap (which is for end-of-workflow gates).
"""

from __future__ import annotations

from roboco.services.gateway.envelope import Envelope


def test_incomplete_input_carries_missing_and_field_hints() -> None:
    env = Envelope.incomplete_input(
        missing=["acceptance_criteria", "nature"],
        field_hints={
            "acceptance_criteria": "non-empty list[str]",
            "nature": "one of: technical | bugfix | feature | refactor | docs",
        },
        remediate="re-issue with these fields filled",
    )
    body = env.as_dict()
    assert body["error"] == "incomplete_input"
    assert body["missing"] == ["acceptance_criteria", "nature"]
    assert body["remediate"] == "re-issue with these fields filled"
    assert body["field_hints"]["acceptance_criteria"] == "non-empty list[str]"


def test_incomplete_input_distinct_from_tracing_gap() -> None:
    """incomplete_input and tracing_gap MUST have different `error` values
    so prompts can teach agents to handle them differently."""
    env_inc = Envelope.incomplete_input(
        missing=["x"], field_hints={"x": "y"}, remediate="z"
    )
    env_gap = Envelope.tracing_gap(missing=["x"], remediate="z")
    assert env_inc.as_dict()["error"] != env_gap.as_dict()["error"]
