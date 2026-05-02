"""Tests for evidence-payload + context-briefing assembly."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from roboco.services.gateway.evidence_builder import (
    BRIEFING_LIST_CAP,
    BriefingInputs,
    build_context_briefing,
    build_evidence_for_task,
)


def _task(
    *,
    pr_number: int | None = 8,
    pr_url: str | None = "https://github.com/x/y/pull/8",
    commits: list[dict] | None = None,
    dev_notes: str = "did stuff",
) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.pr_number = pr_number
    t.pr_url = pr_url
    t.commits = commits or [{"sha": "abc123", "message": "feat: x"}]
    t.dev_notes = dev_notes
    t.acceptance_criteria_status = []
    return t


class TestEvidence:
    def test_basic_payload(self) -> None:
        t = _task()
        ev = build_evidence_for_task(
            t, journal_highlights=[], files_changed=["README.md"]
        )
        assert ev.pr_url == "https://github.com/x/y/pull/8"
        assert ev.commits == [{"sha": "abc123", "message": "feat: x"}]
        assert "README.md" in ev.files_changed

    def test_no_pr_returns_empty_url(self) -> None:
        t = _task(pr_number=None, pr_url=None)
        ev = build_evidence_for_task(t, journal_highlights=[], files_changed=[])
        assert ev.pr_url is None
        assert ev.pr_number is None


class TestContextBriefing:
    def test_empty_briefing(self) -> None:
        inputs = BriefingInputs(
            unread_a2a=[],
            unread_mentions=[],
            pending_notifications=[],
            task_metadata_gaps=[],
            recent_team_activity=[],
            blockers_in_my_lane=[],
        )
        b = build_context_briefing(inputs)
        for key in (
            "unread_a2a",
            "unread_mentions",
            "pending_notifications",
            "task_metadata_gaps",
            "recent_team_activity",
            "blockers_in_my_lane",
        ):
            assert b[key] == []

    def test_lists_capped_at_10(self) -> None:
        twenty = [{"i": i} for i in range(20)]
        inputs = BriefingInputs(
            unread_a2a=twenty,
            unread_mentions=twenty,
            pending_notifications=twenty,
            task_metadata_gaps=[],
            recent_team_activity=twenty,
            blockers_in_my_lane=twenty,
        )
        b = build_context_briefing(inputs)
        assert len(b["unread_a2a"]) == BRIEFING_LIST_CAP
        assert len(b["unread_mentions"]) == BRIEFING_LIST_CAP
        assert len(b["pending_notifications"]) == BRIEFING_LIST_CAP
        assert len(b["recent_team_activity"]) == BRIEFING_LIST_CAP
        assert len(b["blockers_in_my_lane"]) == BRIEFING_LIST_CAP
