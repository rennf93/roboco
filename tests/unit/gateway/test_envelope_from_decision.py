"""Envelope.from_decision maps a Decision to the right rejection envelope."""

from __future__ import annotations

import pytest
from roboco.foundation.policy import lifecycle as spec
from roboco.services.gateway.envelope import Envelope


def test_from_decision_not_authorized_maps_to_not_authorized_envelope() -> None:
    d = spec.Decision.reject(
        kind="not_authorized",
        message="role 'developer' may not call delegate",
        remediate="only PMs delegate",
    )
    env = Envelope.from_decision(d, briefing={})
    assert env.error == "not_authorized"
    assert env.message == "role 'developer' may not call delegate"
    assert env.remediate == "only PMs delegate"


def test_from_decision_invalid_state_maps_to_invalid_state_envelope() -> None:
    d = spec.Decision.reject(
        kind="invalid_state",
        message="task in 'pending', 'open_pr' requires ['in_progress']",
        remediate="claim the task first via i_will_work_on",
    )
    env = Envelope.from_decision(d, briefing={})
    assert env.error == "invalid_state"


def test_from_decision_tracing_gap_carries_missing() -> None:
    d = spec.Decision.tracing_gap(
        missing=["plan", "journal:decision"],
        remediate="provide plan and a journal:decision entry",
    )
    env = Envelope.from_decision(d, briefing={})
    assert env.error == "tracing_gap"
    assert env.missing == ["plan", "journal:decision"]
    assert env.remediate == "provide plan and a journal:decision entry"


def test_from_decision_self_review_maps_to_not_authorized_with_hint() -> None:
    d = spec.Decision.reject(
        kind="self_review",
        message="self-review blocked",
        remediate="another QA must review",
    )
    env = Envelope.from_decision(d, briefing={})
    assert env.error == "not_authorized"
    assert env.message is not None
    assert "self-review" in env.message.lower()


def test_from_decision_allowed_raises() -> None:
    """Constructing a rejection envelope from an allow Decision is a bug."""
    d = spec.Decision.allow()
    with pytest.raises(ValueError, match="cannot build rejection from allow Decision"):
        Envelope.from_decision(d, briefing={})
