"""pass_review requires a matched, evidenced verification per acceptance
criterion — not just a count of arbitrary strings.

Live failure this closes: QA passed a rendered video shipping 3 of the
brief's 4 named scenes because nothing forced the reviewer to walk each
acceptance criterion individually. Mirrors the test idiom in
test_qa_ac_coverage.py, one level stricter: criteria_verified entries must
each match a real AC (by id or exact text) and carry substantive evidence.
"""

from __future__ import annotations

from types import SimpleNamespace

from roboco.services.gateway.choreographer import Choreographer

_EVIDENCE_CAP = 500


def _task(criteria: list[str], ids: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        acceptance_criteria=criteria,
        acceptance_criteria_ids=ids or [],
    )


def test_no_criteria_imposes_no_requirement() -> None:
    pairs, rej = Choreographer._validate_criteria_verified(_task([]), None)
    assert pairs == []
    assert rej is None


def test_none_supplied_lists_every_criterion_verbatim() -> None:
    criteria = [
        "scene 1 renders",
        "scene 2 renders",
        "scene 3 renders",
        "scene 4 renders",
    ]
    t = _task(criteria)
    pairs, rej = Choreographer._validate_criteria_verified(t, None)
    assert pairs == []
    assert rej is not None
    body = rej.as_dict()
    assert body["error"] == "invalid_state", body
    for crit in criteria:
        assert crit in body["message"]


def test_empty_list_is_treated_as_none_supplied() -> None:
    t = _task(["a"])
    pairs, rej = Choreographer._validate_criteria_verified(t, [])
    assert pairs == []
    assert rej is not None


def test_partial_coverage_names_the_missing_criterion() -> None:
    t = _task(["a", "b", "c"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t,
        [
            {"criterion": "a", "evidence": "frame 1 shows a rendered"},
            {"criterion": "b", "evidence": "frame 2 shows b rendered"},
        ],
    )
    assert pairs == []
    assert rej is not None
    assert "c" in rej.as_dict()["message"]


def test_unmatched_criterion_is_rejected_naming_valid_ones() -> None:
    t = _task(["a", "b"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t,
        [
            {"criterion": "a", "evidence": "frame 1 shows a rendered"},
            {"criterion": "not-a-real-ac", "evidence": "frame 2 shows something"},
        ],
    )
    assert pairs == []
    assert rej is not None
    body = rej.as_dict()
    assert "not-a-real-ac" in body["message"]
    assert "a" in body["remediate"] and "b" in body["remediate"]


def test_missing_criterion_key_is_rejected() -> None:
    t = _task(["a"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t, [{"evidence": "frame 1 shows a rendered"}]
    )
    assert pairs == []
    assert rej is not None


def test_blank_evidence_is_rejected() -> None:
    t = _task(["a"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t, [{"criterion": "a", "evidence": "   "}]
    )
    assert pairs == []
    assert rej is not None


def test_soup_evidence_is_rejected() -> None:
    t = _task(["a"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t, [{"criterion": "a", "evidence": "wip"}]
    )
    assert pairs == []
    assert rej is not None


def test_overlong_evidence_is_rejected() -> None:
    t = _task(["a"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t, [{"criterion": "a", "evidence": "x" * (_EVIDENCE_CAP + 100)}]
    )
    assert pairs == []
    assert rej is not None
    assert str(_EVIDENCE_CAP) in rej.as_dict()["message"]


def test_full_coverage_by_exact_text_passes() -> None:
    t = _task(["a", "b"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t,
        [
            {"criterion": "a", "evidence": "frame 1 shows a rendered fully"},
            {"criterion": "b", "evidence": "frame 2 shows b rendered fully"},
        ],
    )
    assert rej is None
    assert pairs == [
        ("a", "frame 1 shows a rendered fully"),
        ("b", "frame 2 shows b rendered fully"),
    ]


def test_full_coverage_by_ac_id_passes() -> None:
    t = _task(["scene renders"], ids=["AC-1"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t, [{"criterion": "AC-1", "evidence": "rendered-frame path: out/frame3.png"}]
    )
    assert rej is None
    assert pairs == [("AC-1", "rendered-frame path: out/frame3.png")]


def test_extra_entries_beyond_the_ac_set_are_allowed() -> None:
    t = _task(["a"])
    pairs, rej = Choreographer._validate_criteria_verified(
        t,
        [{"criterion": "a", "evidence": "frame 1 shows a rendered fully"}],
    )
    assert rej is None
    assert len(pairs) == 1


def test_render_criteria_verified_matches_style() -> None:
    lines = Choreographer._render_criteria_verified(
        [("scene 1 renders", "frame 12 shows scene 1 fully")]
    )
    assert lines == ["[AC] scene 1 renders — verified: frame 12 shows scene 1 fully"]


def test_merge_criteria_verified_into_notes() -> None:
    merged = Choreographer._merge_criteria_verified_into_notes(
        "base review", [("a", "evidence a"), ("b", "evidence b")]
    )
    assert "[AC] a — verified: evidence a" in merged
    assert "[AC] b — verified: evidence b" in merged


def test_merge_with_no_pairs_returns_notes_unchanged() -> None:
    assert Choreographer._merge_criteria_verified_into_notes("base", []) == "base"
