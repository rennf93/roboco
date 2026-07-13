"""Dispatch prompts render the revision-findings ledger inline.

The REVISION_REQUIRED block (developer respawn) and the PM triage prompts'
bounced-block (cell_pm / main_pm respawn onto a needs_revision root) both
render the task's open ledger findings — id, file:line, expected -> actual
-> fix — instead of pointing at ``qa_notes`` / ``pm_notes`` fields that
``evidence()`` never populated.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import (
    _PROMPT_FINDINGS_CAP,
    AgentOrchestrator,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


class _Row:
    """A bare stand-in for a ``TaskReviewFindingTable`` row."""

    def __init__(
        self,
        *,
        file: str | None = "roboco/services/task.py",
        line: int | None = 42,
        expected: str = "raises ValueError",
        actual: str = "swallows the error",
        fix: str | None = "add the raise",
    ) -> None:
        self.id = uuid4()
        self.file = file
        self.line = line
        self.expected = expected
        self.actual = actual
        self.fix = fix


def _patch_findings_repo(rows: list[_Row]) -> tuple[Any, Any]:
    @asynccontextmanager
    async def _fake_ctx() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    repo = AsyncMock()
    repo.list_for_task = AsyncMock(return_value=rows)
    return (
        patch("roboco.db.base.get_db_context", _fake_ctx),
        patch(
            "roboco.services.repositories.review_findings.ReviewFindingsRepository",
            return_value=repo,
        ),
    )


# ---------------------------------------------------------------------------
# _open_findings_prompt_block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renders_file_line_expected_actual_fix() -> None:
    orch = _orch()
    row = _Row()
    db_ctx, repo_ctx = _patch_findings_repo([row])
    with db_ctx, repo_ctx:
        block = await orch._open_findings_prompt_block(str(uuid4()))

    assert "roboco/services/task.py:42" in block
    assert "raises ValueError" in block
    assert "swallows the error" in block
    assert "add the raise" in block
    assert f"F-{str(row.id)[:8]}" in block


@pytest.mark.asyncio
async def test_empty_ledger_returns_empty_string() -> None:
    orch = _orch()
    db_ctx, repo_ctx = _patch_findings_repo([])
    with db_ctx, repo_ctx:
        assert await orch._open_findings_prompt_block(str(uuid4())) == ""


@pytest.mark.asyncio
async def test_no_task_id_returns_empty_string_without_db() -> None:
    orch = _orch()
    with patch("roboco.db.base.get_db_context") as ctx:
        assert await orch._open_findings_prompt_block("") == ""
    ctx.assert_not_called()


@pytest.mark.asyncio
async def test_caps_at_ten_with_overflow_line() -> None:
    orch = _orch()
    rows = [_Row(actual=f"issue {i}") for i in range(_PROMPT_FINDINGS_CAP + 3)]
    db_ctx, repo_ctx = _patch_findings_repo(rows)
    with db_ctx, repo_ctx:
        block = await orch._open_findings_prompt_block(str(uuid4()))

    lines = block.splitlines()
    assert len(lines) == _PROMPT_FINDINGS_CAP + 1  # 10 findings + overflow line
    assert "+3 more via evidence()" in lines[-1]


@pytest.mark.asyncio
async def test_db_error_fails_open_to_empty_string() -> None:
    orch = _orch()
    with patch("roboco.db.base.get_db_context", side_effect=RuntimeError("db down")):
        assert await orch._open_findings_prompt_block(str(uuid4())) == ""


# ---------------------------------------------------------------------------
# _build_dev_prompt — REVISION_REQUIRED renders the block inline
# ---------------------------------------------------------------------------


def _task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "title": "Fix the parser",
        "status": "needs_revision",
        "plan": "did the thing",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_revision_required_prompt_embeds_seeded_finding() -> None:
    orch = _orch()
    task = _task()
    row = _Row(file="api/routes/foo.py", line=17, actual="missing null guard")
    db_ctx, repo_ctx = _patch_findings_repo([row])
    with db_ctx, repo_ctx:
        prompt = await orch._build_dev_prompt(task)

    assert "api/routes/foo.py:17" in prompt
    assert "missing null guard" in prompt
    assert "qa_notes" not in prompt
    assert "pm_notes" not in prompt


@pytest.mark.asyncio
async def test_non_revision_prompt_never_touches_db() -> None:
    """EXECUTING (in_progress) must not pay for a findings-ledger fetch."""
    orch = _orch()
    task = _task(status="in_progress")
    with patch("roboco.db.base.get_db_context") as ctx:
        prompt = await orch._build_dev_prompt(task)
    ctx.assert_not_called()
    assert "IN PROGRESS" in prompt


@pytest.mark.asyncio
async def test_revision_required_with_no_findings_still_renders() -> None:
    orch = _orch()
    task = _task()
    db_ctx, repo_ctx = _patch_findings_repo([])
    with db_ctx, repo_ctx:
        prompt = await orch._build_dev_prompt(task)

    assert "REVISION REQUESTED" in prompt
    assert "no findings on the ledger" in prompt


# ---------------------------------------------------------------------------
# PM triage prompts — the bounced-block
# ---------------------------------------------------------------------------


def test_pm_triage_prompt_prepends_bounced_block_when_given() -> None:
    orch = _orch()
    prompt = orch._build_pm_triage_prompt(
        _task(team="backend"), bounced_block="[F-abcd1234] api.py:9 — x -> y"
    )
    assert prompt.startswith("## THIS TASK BOUNCED")
    assert "[F-abcd1234] api.py:9" in prompt
    assert "You are the PM for backend team" in prompt


def test_pm_triage_prompt_omits_block_when_empty() -> None:
    orch = _orch()
    prompt = orch._build_pm_triage_prompt(_task(team="backend"), bounced_block="")
    assert "THIS TASK BOUNCED" not in prompt
    assert prompt.startswith("You are the PM for backend team")


def test_main_pm_triage_prompt_prepends_bounced_block_when_given() -> None:
    orch = _orch()
    prompt = orch._build_main_pm_triage_prompt(
        _task(), bounced_block="[F-abcd1234] api.py:9 — x -> y"
    )
    assert prompt.startswith("## THIS ROOT BOUNCED")
    assert "[F-abcd1234] api.py:9" in prompt
    assert "You are the MAIN PM at RoboCo" in prompt


def test_main_pm_triage_prompt_omits_block_when_empty() -> None:
    orch = _orch()
    prompt = orch._build_main_pm_triage_prompt(_task(), bounced_block="")
    assert "THIS ROOT BOUNCED" not in prompt
    assert prompt.startswith("You are the MAIN PM at RoboCo")


# ---------------------------------------------------------------------------
# _get_prompt_for_agent — threads the bounced-block into cell_pm/main_pm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_prompt_for_agent_fetches_bounced_block_for_needs_revision_pm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orch()
    monkeypatch.setattr(
        orch, "_revision_bounced_block", AsyncMock(return_value="[F-11112222] x")
    )
    prompt = await orch._get_prompt_for_agent("main-pm", _task(status="needs_revision"))
    assert "[F-11112222] x" in prompt


@pytest.mark.asyncio
async def test_revision_bounced_block_skips_fetch_when_not_needs_revision() -> None:
    orch = _orch()
    with patch("roboco.db.base.get_db_context") as ctx:
        block = await orch._revision_bounced_block(_task(status="in_progress"))
    assert block == ""
    ctx.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
