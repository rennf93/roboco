"""Every gateway intent verb is either in VERB_REQUIREMENTS or VERBS_WITHOUT_TRACING."""

from __future__ import annotations

from roboco.foundation.policy import tracing
from roboco.lifecycle import spec


def test_every_intent_verb_has_a_tracing_decision() -> None:
    intent_verbs = set(spec._INTENT_VERBS.keys())
    in_table = set(tracing.VERB_REQUIREMENTS)
    in_waived = set(tracing.VERBS_WITHOUT_TRACING)

    uncovered = intent_verbs - in_table - in_waived
    assert uncovered == set(), (
        f"verbs in lifecycle.spec without tracing decision: {uncovered}"
    )


def test_every_requirement_is_used_by_at_least_one_verb() -> None:
    """No dangling enum values."""
    used: set[tracing.Requirement] = set()
    for reqs in tracing.VERB_REQUIREMENTS.values():
        used.update(reqs)

    unused = set(tracing.Requirement) - used
    assert unused == set(), f"Requirement values referenced by no verb: {unused}"
