"""Tests for evidence-payload + context-briefing assembly."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from roboco.services.gateway.evidence_builder import (
    BRIEFING_LIST_CAP,
    FINDING_EVIDENCE_EXCERPT_CAP,
    BriefingInputs,
    build_context_briefing,
    build_evidence_for_task,
    build_task_handoff,
    render_findings,
)

_EXPECTED_TWO = 2


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
        # Empty sections are omitted to keep the per-verb payload compact — an
        # all-empty briefing collapses to {}.
        assert b == {}

    def test_omits_empty_keeps_nonempty(self) -> None:
        inputs = BriefingInputs(
            unread_a2a=[{"id": "a1"}],
            unread_mentions=[],
            pending_notifications=[],
            task_metadata_gaps=[],
            recent_team_activity=[],
            blockers_in_my_lane=[],
        )
        assert build_context_briefing(inputs) == {"unread_a2a": [{"id": "a1"}]}

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
        # A None handoff is an empty section → omitted from the briefing.
        assert "task_handoff" not in build_context_briefing(inputs)

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

    def test_company_goals_defaults_none_and_surfaces_in_briefing(self) -> None:
        inputs = BriefingInputs(
            unread_a2a=[],
            unread_mentions=[],
            pending_notifications=[],
            task_metadata_gaps=[],
            recent_team_activity=[],
            blockers_in_my_lane=[],
        )
        # None goals is an empty section → omitted from the briefing.
        assert "company_goals" not in build_context_briefing(inputs)

        with_goals = BriefingInputs(
            unread_a2a=[],
            unread_mentions=[],
            pending_notifications=[],
            task_metadata_gaps=[],
            recent_team_activity=[],
            blockers_in_my_lane=[],
            company_goals={"north_star": "win"},
        )
        assert build_context_briefing(with_goals)["company_goals"] == {
            "north_star": "win"
        }


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


class TestPrReviewSurface:
    """The persisted pr_fail verdict + issues must surface in the PM
    briefing's task_handoff, not just the fire-and-forget a2a."""

    def test_surfaces_pr_fail_verdict_and_issues(self) -> None:
        t = _task(pr_number=138, commits=[{"sha": "abc", "message": "feat: x"}])
        t.notes_structured = {
            "pr_review": {
                "verdict": "failed",
                "summary": "In-path PR-review gate requested changes.",
                "issues": ["missing null guard", "no test for the edge case"],
                "head_sha": "aaaa1111bbbb2222",
            }
        }
        digest = build_task_handoff(t, [])
        assert digest is not None
        pr_review = digest["pr_review"]
        assert pr_review["verdict"] == "failed"
        assert pr_review["issues"] == [
            "missing null guard",
            "no test for the edge case",
        ]
        assert pr_review["head_sha"] == "aaaa1111bbbb2222"

    def test_no_pr_review_field_when_none_present(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        t.notes_structured = None
        digest = build_task_handoff(t, [])
        assert digest is not None
        # Absent pr_review ⇒ no key (not a None-valued key) so a PM without a
        # prior gate verdict doesn't see a misleading empty slot.
        assert "pr_review" not in digest

    def test_pr_review_alone_is_prior_work_worth_resuming(self) -> None:
        """A task with no commits/dev-notes but a prior pr_fail verdict still
        surfaces the handoff so the owning PM reads the change-requests."""
        t = _task(pr_number=None, pr_url=None, dev_notes="")
        t.commits = []
        t.acceptance_criteria_status = []
        t.notes_structured = {
            "pr_review": {"verdict": "failed", "issues": ["fix the off-by-one"]}
        }
        digest = build_task_handoff(t, [])
        assert digest is not None
        assert digest["pr_review"]["issues"] == ["fix the off-by-one"]


def _finding_row(**over: Any) -> MagicMock:
    base: dict[str, Any] = {
        "id": uuid4(),
        "round": 2,
        "origin": "qa",
        "status": "open",
        "severity": "major",
        "file": "roboco/services/task.py",
        "line": 10,
        "expected": "raises on bad input",
        "actual": "swallows the error",
        "fix": "add the raise",
        "evidence": None,
    }
    base.update(over)
    return MagicMock(**base)


class TestRenderFindings:
    def test_renders_compact_dict_with_id8_prefix(self) -> None:
        row = _finding_row()
        rendered = render_findings([row])
        assert len(rendered) == 1
        entry = rendered[0]
        assert entry["id"] == str(row.id)[:8]
        assert entry["round"] == row.round
        assert entry["origin"] == "qa"
        assert entry["status"] == "open"
        assert entry["file"] == "roboco/services/task.py"
        assert entry["line"] == row.line
        assert entry["expected"] == "raises on bad input"
        assert entry["actual"] == "swallows the error"
        assert entry["fix"] == "add the raise"

    def test_none_rows_render_empty(self) -> None:
        assert render_findings(None) == []

    def test_caps_defensively(self) -> None:
        rows = [_finding_row() for _ in range(BRIEFING_LIST_CAP + 5)]
        assert len(render_findings(rows)) == BRIEFING_LIST_CAP

    def test_evidence_excerpt_clipped_with_omission_note(self) -> None:
        long_evidence = "x" * (FINDING_EVIDENCE_EXCERPT_CAP + 50)
        row = _finding_row(evidence=long_evidence)
        entry = render_findings([row])[0]
        assert entry["evidence"] is not None
        assert len(entry["evidence"]) < len(long_evidence)
        assert "chars omitted" in entry["evidence"]
        assert entry["evidence"].startswith("x" * FINDING_EVIDENCE_EXCERPT_CAP)

    def test_short_evidence_is_not_annotated(self) -> None:
        row = _finding_row(evidence="short excerpt")
        entry = render_findings([row])[0]
        assert entry["evidence"] == "short excerpt"


class TestEvidencePayloadFindings:
    def test_revision_and_prior_findings_render(self) -> None:
        t = _task()
        open_row = _finding_row(status="open")
        all_row = _finding_row(status="verified", round=1)
        ev = build_evidence_for_task(
            t,
            journal_highlights=[],
            files_changed=[],
            revision_findings=[open_row],
            prior_findings=[open_row, all_row],
        )
        assert len(ev.revision_findings) == 1
        assert ev.revision_findings[0]["status"] == "open"
        assert len(ev.prior_findings) == _EXPECTED_TWO

    def test_findings_default_empty_no_noise(self) -> None:
        t = _task()
        ev = build_evidence_for_task(t, journal_highlights=[], files_changed=[])
        assert ev.revision_findings == []
        assert ev.prior_findings == []

    def test_as_dict_omits_empty_findings_lists(self) -> None:
        """Empty findings lists must not serialize into the envelope at all —
        an absent key reads as 'nothing here', identical to an empty list,
        at zero token cost (matches build_task_handoff's posture)."""
        t = _task()
        ev = build_evidence_for_task(t, journal_highlights=[], files_changed=[])
        body = ev.as_dict()
        assert "revision_findings" not in body
        assert "prior_findings" not in body
        assert "convention_findings" not in body
        # Non-empty EvidencePayload fields (even empty lists like
        # files_changed/commits) are unaffected — only the three findings
        # fields get the omit-when-empty treatment.
        assert "commits" in body
        assert "files_changed" in body

    def test_as_dict_keeps_non_empty_findings_lists(self) -> None:
        t = _task()
        open_row = _finding_row(status="open")
        ev = build_evidence_for_task(
            t,
            journal_highlights=[],
            files_changed=[],
            revision_findings=[open_row],
            prior_findings=[open_row],
        )
        body = ev.as_dict()
        assert len(body["revision_findings"]) == 1
        assert len(body["prior_findings"]) == 1


class TestTaskHandoffRevisionFindings:
    def test_open_findings_surface_under_revision_findings(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        row = _finding_row(status="open", file="api/routes/foo.py", line=17)
        digest = build_task_handoff(t, [], [row])
        assert digest is not None
        assert len(digest["revision_findings"]) == 1
        entry = digest["revision_findings"][0]
        assert entry["file"] == "api/routes/foo.py"
        assert entry["line"] == row.line

    def test_empty_ledger_is_silent(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        digest = build_task_handoff(t, [], [])
        assert digest is not None
        assert "revision_findings" not in digest

    def test_no_findings_arg_is_silent(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        digest = build_task_handoff(t, [])
        assert digest is not None
        assert "revision_findings" not in digest

    def test_open_findings_alone_is_prior_work_worth_resuming(self) -> None:
        """A task with no commits/dev-notes but an open finding still
        surfaces the handoff — the bounced dev must see it."""
        t = _task(pr_number=None, pr_url=None, dev_notes="")
        t.commits = []
        t.acceptance_criteria_status = []
        digest = build_task_handoff(t, [], [_finding_row()])
        assert digest is not None
        assert len(digest["revision_findings"]) == 1

    def test_caps_at_briefing_list_cap(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        rows = [_finding_row() for _ in range(BRIEFING_LIST_CAP + 5)]
        digest = build_task_handoff(t, [], rows)
        assert digest is not None
        assert len(digest["revision_findings"]) == BRIEFING_LIST_CAP


class TestExtractQaReview:
    def test_surfaces_verdict_summary_and_findings_count(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        t.notes_structured = {
            "qa": {
                "summary": "2 findings, both blocking",
                "verdict": "failed",
                "findings": [{"expected": "x", "actual": "y"}, {"expected": "a"}],
            }
        }
        digest = build_task_handoff(t, [])
        assert digest is not None
        qa_review = digest["qa_review"]
        assert qa_review["verdict"] == "failed"
        assert qa_review["summary"] == "2 findings, both blocking"
        assert qa_review["findings_count"] == _EXPECTED_TWO

    def test_absent_when_no_qa_slot(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        t.notes_structured = None
        digest = build_task_handoff(t, [])
        assert digest is not None
        assert "qa_review" not in digest


class TestExtractPmReview:
    def test_surfaces_summary_and_findings_count_no_verdict(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        t.notes_structured = {
            "pm_review": {
                "summary": "merge-review reject",
                "findings": [{"expected": "x", "actual": "y"}],
            }
        }
        digest = build_task_handoff(t, [])
        assert digest is not None
        pm_review = digest["pm_review"]
        assert pm_review["summary"] == "merge-review reject"
        assert pm_review["findings_count"] == 1
        assert "verdict" not in pm_review

    def test_absent_when_no_pm_review_slot(self) -> None:
        t = _task(pr_number=8, commits=[{"sha": "abc", "message": "feat: x"}])
        t.notes_structured = {}
        digest = build_task_handoff(t, [])
        assert digest is not None
        assert "pm_review" not in digest
