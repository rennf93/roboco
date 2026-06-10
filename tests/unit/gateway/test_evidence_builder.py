"""Tests for evidence-payload + context-briefing assembly."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from roboco.services.gateway.evidence_builder import (
    BRIEFING_LIST_CAP,
    BriefingInputs,
    build_context_briefing,
    build_evidence_for_task,
    build_task_handoff,
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

    def test_task_handoff_defaults_none_and_surfaces_in_briefing(self) -> None:
        inputs = BriefingInputs(
            unread_a2a=[],
            unread_mentions=[],
            pending_notifications=[],
            task_metadata_gaps=[],
            recent_team_activity=[],
            blockers_in_my_lane=[],
        )
        assert build_context_briefing(inputs)["task_handoff"] is None

        with_handoff = BriefingInputs(
            unread_a2a=[],
            unread_mentions=[],
            pending_notifications=[],
            task_metadata_gaps=[],
            recent_team_activity=[],
            blockers_in_my_lane=[],
            task_handoff={"pr_number": 8},
        )
        assert build_context_briefing(with_handoff)["task_handoff"] == {"pr_number": 8}


class TestTaskHandoff:
    def test_none_task_returns_none(self) -> None:
        assert build_task_handoff(None, []) is None

    def test_no_prior_work_returns_none(self) -> None:
        t = _task(pr_number=None, pr_url=None, dev_notes="")
        t.commits = []  # _task's `commits or [...]` default would re-seed one
        t.acceptance_criteria_status = []
        assert build_task_handoff(t, []) is None

    def test_digest_from_prior_work(self) -> None:
        pr = 8
        t = _task(
            pr_number=pr,
            commits=[{"sha": "abc", "message": "feat: x"}],
            dev_notes="implemented the parser",
        )
        t.branch_name = "feature/backend/abc"
        t.acceptance_criteria_status = [{"criterion": "parses", "met": True}]
        digest = build_task_handoff(t, [{"summary": "chose recursive descent"}])
        assert digest is not None
        assert digest["pr_number"] == pr
        assert digest["branch_name"] == "feature/backend/abc"
        assert digest["commit_count"] == 1
        assert digest["dev_summary"] == "implemented the parser"
        assert digest["journal_highlights"] == [{"summary": "chose recursive descent"}]

    def test_surfaces_completed_dependencies(self) -> None:
        dep_id = uuid4()
        t = _task(pr_number=None, pr_url=None, dev_notes="")
        t.commits = []
        t.acceptance_criteria_status = []
        t.completed_dependency_ids = [dep_id]
        digest = build_task_handoff(t, [])
        # A just-unblocked task with no other prior work still surfaces the dep.
        assert digest is not None
        assert digest["completed_dependency_ids"] == [str(dep_id)]

    def test_caps_lists_and_type_guards(self) -> None:
        thirty = [{"sha": str(i)} for i in range(30)]
        t = _task(pr_number=7, commits=thirty)
        # Non-list / mismatched-type attributes degrade safely, never leak.
        t.acceptance_criteria_status = object()
        t.pr_url = object()
        t.branch_name = None
        not_a_list: Any = object()
        digest = build_task_handoff(t, not_a_list)
        assert digest is not None
        assert len(digest["recent_commits"]) == BRIEFING_LIST_CAP
        assert digest["acceptance_criteria_status"] == []
        assert digest["journal_highlights"] == []
        assert digest["pr_url"] is None
        assert digest["branch_name"] is None
