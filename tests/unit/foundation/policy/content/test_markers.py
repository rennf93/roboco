"""Tests for the orchestration-marker accessors."""

from __future__ import annotations

from types import SimpleNamespace

from roboco.foundation.policy.content import markers as m


def _task(om: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(orchestration_markers=om)


def test_original_developer_roundtrip() -> None:
    t = _task()
    assert m.get_original_developer(t) is None
    m.set_original_developer(t, "00000000-0000-0000-0001-000000000002")
    assert m.get_original_developer(t) == "00000000-0000-0000-0001-000000000002"


def test_required_cells_roundtrip() -> None:
    t = _task()
    assert m.get_required_cells(t) == []
    m.set_required_cells(t, ["backend", "frontend"])
    assert m.get_required_cells(t) == ["backend", "frontend"]


def test_dismissed_flag() -> None:
    t = _task()
    assert m.is_dismissed(t) is False
    m.mark_dismissed(t)
    assert m.is_dismissed(t) is True


def test_set_marker_reassigns_dict_for_orm_dirty_tracking() -> None:
    t = _task({"a": 1})
    before = t.orchestration_markers
    m.set_marker(t, "b", 2)
    # A new dict object — SQLAlchemy only flags JSON columns dirty on reassign.
    assert t.orchestration_markers is not before
    assert t.orchestration_markers == {"a": 1, "b": 2}


def test_clear_marker_nulls_when_empty() -> None:
    t = _task({"x": 1})
    m.clear_marker(t, "x")
    assert t.orchestration_markers is None
    # Clearing an absent key is a no-op.
    m.clear_marker(t, "missing")
    assert t.orchestration_markers is None


def test_escalation_roundtrip() -> None:
    t = _task()
    assert m.get_escalation(t) is None
    m.set_escalation(t, from_slug="be-pm", to_slug="main-pm", reason="re-open please")
    assert m.get_escalation(t) == {
        "from": "be-pm",
        "to": "main-pm",
        "reason": "re-open please",
    }


def test_approve_and_start_notes_roundtrip() -> None:
    t = _task()
    assert m.get_approve_and_start_notes(t) is None
    m.set_approve_and_start_notes(t, "Board approved; build it.")
    assert m.get_approve_and_start_notes(t) == "Board approved; build it."


def test_transition_note_roundtrip_keyed_by_event() -> None:
    t = _task()
    assert m.get_transition_note(t, "ceo_rejection") is None
    m.set_transition_note(t, "completion", "Reviewed and merged.")
    m.set_transition_note(t, "ceo_rejection", "Needs the migration first.")
    # Each event keeps its own note; setting one doesn't clobber another.
    assert m.get_transition_note(t, "completion") == "Reviewed and merged."
    assert m.get_transition_note(t, "ceo_rejection") == "Needs the migration first."
    assert m.get_transition_note(t, "never_set") is None


def test_video_draft_roundtrip() -> None:
    t = _task()
    assert m.get_video_draft(t) is None
    m.set_video_draft(
        t,
        {
            "occasion": "release v1.0.0",
            "script": "Here's what shipped...",
            "platforms": ["x", "tiktok"],
            "brief": "Announce the release",
        },
    )
    draft = m.get_video_draft(t)
    assert draft is not None
    assert draft["occasion"] == "release v1.0.0"
    assert draft["platforms"] == ["x", "tiktok"]


def test_video_draft_extended_not_replaced() -> None:
    """The render pass extends the authoring marker rather than clobbering it —
    the caller is responsible for spreading the existing dict (set_video_draft
    itself just reassigns whatever payload it is given)."""
    t = _task()
    m.set_video_draft(t, {"occasion": "spotlight: org-memory", "script": "x"})
    existing = m.get_video_draft(t) or {}
    m.set_video_draft(
        t, {**existing, "mp4_paths": {"vertical": "a.mp4", "square": "b.mp4"}}
    )
    draft = m.get_video_draft(t)
    assert draft is not None
    assert draft["occasion"] == "spotlight: org-memory"
    assert draft["mp4_paths"] == {"vertical": "a.mp4", "square": "b.mp4"}


def test_documenter_self_heal_head_supersede() -> None:
    t = _task()
    m.set_documenter(t, "doc-uuid")
    m.set_self_heal_fingerprint(t, "deadbeef")
    m.set_external_pr_head(t, "sha123")
    m.set_external_pr_supersede(t, "pr=1 review=2 closed=1")
    assert m.get_documenter(t) == "doc-uuid"
    assert m.get_self_heal_fingerprint(t) == "deadbeef"
    assert m.get_external_pr_head(t) == "sha123"
    assert m.get_external_pr_supersede(t) == "pr=1 review=2 closed=1"
