"""Claim-scoped context briefing — `_briefing_for(full=...)`.

Only context-acquisition verbs (give_me_work / claim / plan / resume / triage)
carry the heavy, verb-invariant sections (company_goals, recent_team_activity,
blockers_in_my_lane, task_handoff, institutional_memory). Every other verb gets
the slim signals-only briefing, and the heavy repo queries are not even issued.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer
from roboco.services.gateway.evidence_builder import (
    EVIDENCE_DIFF_CAP_CHARS,
    build_evidence_for_task,
    truncate_diff,
)

_PR_NUMBER = 8


def _choreographer_with_repo() -> tuple[Choreographer, AsyncMock]:
    repo = AsyncMock()
    repo.list_unread_a2a.return_value = [{"conversation_id": "c1", "unread": 2}]
    repo.list_unread_mentions.return_value = []
    repo.list_pending_notifications.return_value = [{"notification_id": "n1"}]
    repo.task_metadata_gaps.return_value = []
    repo.recent_team_activity.return_value = [{"task_id": "t1", "status": "pending"}]
    repo.blockers_in_lane.return_value = [{"task_id": "b1"}]
    repo.company_goals.return_value = {"north_star": "win"}
    repo.journal_highlights_for_task.return_value = []
    choreo = object.__new__(Choreographer)
    choreo._deps = MagicMock(evidence_repo=repo)
    return choreo, repo


class TestBriefingScope:
    @pytest.mark.asyncio
    async def test_slim_default_carries_signals_only(self) -> None:
        choreo, repo = _choreographer_with_repo()
        briefing = await choreo._briefing_for(uuid4(), None)
        assert briefing["unread_a2a"] == [{"conversation_id": "c1", "unread": 2}]
        assert briefing["pending_notifications"] == [{"notification_id": "n1"}]
        for heavy in (
            "company_goals",
            "recent_team_activity",
            "blockers_in_my_lane",
            "task_handoff",
            "institutional_memory",
        ):
            assert heavy not in briefing
        # The heavy queries are not even issued on the slim path.
        repo.recent_team_activity.assert_not_awaited()
        repo.blockers_in_lane.assert_not_awaited()
        repo.company_goals.assert_not_awaited()
        repo.journal_highlights_for_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_carries_heavy_sections(self) -> None:
        choreo, repo = _choreographer_with_repo()
        briefing = await choreo._briefing_for(uuid4(), None, full=True)
        assert briefing["company_goals"] == {"north_star": "win"}
        assert briefing["recent_team_activity"] == [
            {"task_id": "t1", "status": "pending"}
        ]
        assert briefing["blockers_in_my_lane"] == [{"task_id": "b1"}]
        repo.company_goals.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_full_with_task_builds_handoff(self) -> None:
        choreo, repo = _choreographer_with_repo()
        task_id = uuid4()
        task = MagicMock(
            pr_number=_PR_NUMBER,
            pr_url="https://github.com/x/y/pull/8",
            branch_name="feature/backend/abc",
            commits=[{"sha": "abc123", "message": "feat: x"}],
            quick_context=None,
        )
        briefing = await choreo._briefing_for(uuid4(), task_id, task=task, full=True)
        assert briefing["task_handoff"]["pr_number"] == _PR_NUMBER
        repo.journal_highlights_for_task.assert_awaited_once_with(task_id)

    @pytest.mark.asyncio
    async def test_slim_with_task_omits_handoff(self) -> None:
        choreo, repo = _choreographer_with_repo()
        task = MagicMock(pr_number=8, commits=[{"sha": "abc123"}])
        briefing = await choreo._briefing_for(uuid4(), uuid4(), task=task)
        assert "task_handoff" not in briefing
        repo.journal_highlights_for_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_include_company_goals_fetches_only_that_heavy_field(self) -> None:
        """The board_triage idle-branch opt-in: company_goals reaches the
        briefing without pulling in the rest of ``full``'s heavy sections."""
        choreo, repo = _choreographer_with_repo()
        briefing = await choreo._briefing_for(uuid4(), None, include_company_goals=True)
        assert briefing["company_goals"] == {"north_star": "win"}
        repo.company_goals.assert_awaited_once()
        for heavy in (
            "recent_team_activity",
            "blockers_in_my_lane",
            "task_handoff",
            "institutional_memory",
        ):
            assert heavy not in briefing
        # No heavy queries beyond the one company_goals lookup.
        repo.recent_team_activity.assert_not_awaited()
        repo.blockers_in_lane.assert_not_awaited()
        repo.journal_highlights_for_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_true_ignores_include_company_goals_no_double_fetch(
        self,
    ) -> None:
        """``full=True`` already resolves company_goals via the heavy-sections
        batch; passing include_company_goals=True too must not issue a second
        query."""
        choreo, repo = _choreographer_with_repo()
        briefing = await choreo._briefing_for(
            uuid4(), None, full=True, include_company_goals=True
        )
        assert briefing["company_goals"] == {"north_star": "win"}
        repo.company_goals.assert_awaited_once()


class TestPayloadCaps:
    def test_truncate_diff_caps_and_annotates(self) -> None:
        big = "x" * (EVIDENCE_DIFF_CAP_CHARS + 5_000)
        capped = truncate_diff(big)
        assert capped is not None
        assert len(capped) < len(big)
        assert capped.startswith("x" * 100)  # head preserved
        assert "diff truncated" in capped

    def test_truncate_diff_passes_small_and_none(self) -> None:
        assert truncate_diff("small diff") == "small diff"
        assert truncate_diff(None) is None

    def test_build_evidence_caps_the_diff(self) -> None:
        task = MagicMock(
            pr_number=None,
            pr_url=None,
            commits=[],
            dev_notes=None,
            acceptance_criteria_status=[],
        )
        ev = build_evidence_for_task(
            task,
            journal_highlights=[],
            files_changed=[],
            pr_diff_summary="y" * (EVIDENCE_DIFF_CAP_CHARS * 2),
        )
        assert ev.pr_diff_summary is not None
        assert "diff truncated" in ev.pr_diff_summary
