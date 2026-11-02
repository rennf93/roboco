"""The QA claim_review next-hint names the REAL verdict verbs.

GLM-flash QA sessions pattern-matched the old short forms ("pass(notes)")
onto whatever verb they last emitted and looped claim_review/give_me_work
without ever issuing the verdict (2026-09-29 fleet wedge: 385 consecutive
claim_review calls, zero pass_review calls). The hint must carry the exact
verb names, the exact task id, and the exact verdict parameters.
"""

from __future__ import annotations

from types import SimpleNamespace

from roboco.foundation.policy import lifecycle


def test_qa_review_hint_names_real_verbs() -> None:
    hint = lifecycle._next_hint_qa_review(SimpleNamespace(id="abc-123"))

    assert "pass_review" in hint
    assert "fail_review" in hint
    assert "pass(notes)" not in hint
    assert "fail(issues)" not in hint
    # The exact task id is interpolated so the model can copy the call.
    assert "abc-123" in hint
    # The mandatory per-AC verification gate is named with its shape.
    assert "criteria_verified" in hint


def test_qa_review_hint_registers_on_claim_review_intent() -> None:
    verb = lifecycle._INTENT_VERBS["claim_review"]
    assert verb.next_hint is lifecycle._next_hint_qa_review
