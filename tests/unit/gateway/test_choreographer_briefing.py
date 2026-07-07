"""``institutional_memory_status`` sentinel reaches the briefing envelope.

The ``context_briefing["institutional_memory"]`` block carries a ``status``
field distinguishing the five underlying states so an agent can tell
"searched, nothing" (below_floor / empty) from "search broke" (error) from
"subsystem off" (disabled) from "lessons injected" (ok). Additive only —
lessons is empty unless status is ``ok``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer

_FLOOR = 0.6


def _choreographer(
    *,
    similar_memory_out: dict[str, object] | Exception,
) -> tuple[Choreographer, AsyncMock]:
    repo = AsyncMock()
    repo.list_unread_a2a.return_value = []
    repo.list_unread_mentions.return_value = []
    repo.list_pending_notifications.return_value = []
    repo.task_metadata_gaps.return_value = []
    repo.recent_team_activity.return_value = []
    repo.blockers_in_lane.return_value = []
    repo.company_goals.return_value = None
    repo.journal_highlights_for_task.return_value = []
    if isinstance(similar_memory_out, Exception):
        repo.similar_memory = AsyncMock(side_effect=similar_memory_out)
    else:
        repo.similar_memory = AsyncMock(return_value=similar_memory_out)
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer")
    choreo = object.__new__(Choreographer)
    choreo._deps = MagicMock(evidence_repo=repo, task=task_svc)
    return choreo, repo


def _task() -> MagicMock:
    return MagicMock(
        title="Add retry backoff",
        task_type=MagicMock(value="code"),
    )


class TestInstitutionalMemoryStatus:
    @pytest.mark.asyncio
    async def test_disabled_when_subsystem_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", False)
        choreo, _ = _choreographer(similar_memory_out={"items": [], "status": "ok"})
        briefing = await choreo._briefing_for(uuid4(), uuid4(), task=_task(), full=True)
        assert briefing["institutional_memory"]["status"] == "disabled"
        assert briefing["institutional_memory"]["lessons"] == []

    @pytest.mark.asyncio
    async def test_error_when_search_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", True)
        # similar_memory itself swallows RAG errors and returns status=error;
        # simulate that contract (the choreographer trusts the repo's status).
        choreo, _ = _choreographer(similar_memory_out={"items": [], "status": "error"})
        briefing = await choreo._briefing_for(uuid4(), uuid4(), task=_task(), full=True)
        assert briefing["institutional_memory"]["status"] == "error"
        assert briefing["institutional_memory"]["lessons"] == []

    @pytest.mark.asyncio
    async def test_empty_when_search_yields_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", True)
        choreo, _ = _choreographer(similar_memory_out={"items": [], "status": "empty"})
        briefing = await choreo._briefing_for(uuid4(), uuid4(), task=_task(), full=True)
        assert briefing["institutional_memory"]["status"] == "empty"
        assert briefing["institutional_memory"]["lessons"] == []

    @pytest.mark.asyncio
    async def test_below_floor_when_all_under_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", True)
        choreo, _ = _choreographer(
            similar_memory_out={"items": [], "status": "below_floor"}
        )
        briefing = await choreo._briefing_for(uuid4(), uuid4(), task=_task(), full=True)
        assert briefing["institutional_memory"]["status"] == "below_floor"
        assert briefing["institutional_memory"]["lessons"] == []

    @pytest.mark.asyncio
    async def test_ok_injects_lessons(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", True)
        lesson = {"kind": "learning", "summary": "s", "source": "src", "score": 0.9}
        choreo, _ = _choreographer(
            similar_memory_out={"items": [lesson], "status": "ok"}
        )
        briefing = await choreo._briefing_for(uuid4(), uuid4(), task=_task(), full=True)
        assert briefing["institutional_memory"]["status"] == "ok"
        assert briefing["institutional_memory"]["lessons"] == [lesson]

    @pytest.mark.asyncio
    async def test_slim_briefing_omits_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slim (non-full) briefings don't run the heavy section, so the block
        is absent — preserves the existing slim/full split."""
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", True)
        choreo, _ = _choreographer(similar_memory_out={"items": [], "status": "ok"})
        briefing = await choreo._briefing_for(uuid4(), uuid4(), task=_task())
        assert "institutional_memory" not in briefing
