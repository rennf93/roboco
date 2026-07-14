"""W7 possibilities matrix: the ``_work_appears_done`` predicate + fast path.

A task whose work is already done (commits + PR open + every acceptance
criterion addressed + no open findings) qualifies for the fast path. These
tests pin the predicate's truth table and the schema it actually reads (the
per-criterion rows the writer persists use ``artifact_ref``; the predicate
unions ``artifact_ref`` / ``referencing_artifact_id`` / ``addressed`` so it
sees real data, not the latent reader/writer key drift).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _deps() -> ChoreographerDeps:
    return ChoreographerDeps(
        task=AsyncMock(),
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=AsyncMock(),
    )


async def _no_findings(_task_id: Any) -> tuple[()]:
    return ()


async def _one_open(_task_id: Any) -> tuple[str, ...]:
    return ("f1abcd12",)


def _t(
    *,
    status: str = "claimed",
    commits: tuple[int, ...] = (1,),
    pr_created: bool = True,
    pr_number: int | None = 12345,
    criteria: tuple[str, ...] = ("ac1", "ac2"),
    ac_status: list[dict[str, Any]] | None = None,
) -> MagicMock:
    if ac_status is None:
        ac_status = [
            {"criterion": "ac1", "addressed": True, "artifact_ref": "sha1"},
            {"criterion": "ac2", "addressed": True, "artifact_ref": "sha2"},
        ]
    t = MagicMock()
    t.id = uuid4()
    t.status = status
    t.commits = commits
    t.pr_created = pr_created
    t.pr_number = pr_number
    t.acceptance_criteria = list(criteria)
    t.acceptance_criteria_status = ac_status
    return t


@pytest.mark.asyncio
async def test_work_appears_done_true_when_all_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t()) is True


@pytest.mark.asyncio
async def test_work_appears_done_false_when_no_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t(pr_created=False, pr_number=None)) is False


@pytest.mark.asyncio
async def test_work_appears_done_false_when_no_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t(commits=())) is False


@pytest.mark.asyncio
async def test_work_appears_done_false_when_ac_unaddressed(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    t = _t(
        ac_status=[
            {"criterion": "ac1", "addressed": True, "artifact_ref": "sha1"},
            {"criterion": "ac2", "addressed": False, "artifact_ref": None},
        ]
    )
    assert await c._work_appears_done(t) is False


@pytest.mark.asyncio
async def test_work_appears_done_true_with_no_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t(criteria=(), ac_status=[])) is True


@pytest.mark.asyncio
async def test_work_appears_done_false_when_open_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _one_open)
    assert await c._work_appears_done(_t()) is False


@pytest.mark.asyncio
async def test_work_appears_done_false_when_terminal_status(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t(status="awaiting_qa")) is False
    assert await c._work_appears_done(_t(status="completed")) is False
    assert await c._work_appears_done(_t(status="needs_revision")) is False


@pytest.mark.asyncio
async def test_work_appears_done_reads_referencing_artifact_id_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    t = _t(
        ac_status=[
            {"criterion": "ac1", "referencing_artifact_id": "sha1"},
            {"criterion": "ac2", "referencing_artifact_id": "sha2"},
        ]
    )
    assert await c._work_appears_done(t) is True

# ---------------------------------------------------------------------------
# _fast_path_quality_verdict: CI-green proxy for the skipped local gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quality_verdict_ci_success_skips_local_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(
        c, "_resolve_ci_status", AsyncMock(return_value={"state": "success"})
    )
    local = AsyncMock(return_value=None)
    monkeypatch.setattr(c, "_check_quality_gate", local)
    rejection, ran_local = await c._fast_path_quality_verdict(
        MagicMock(task_id=uuid4(), task=MagicMock(), briefing={})
    )
    assert rejection is None
    assert ran_local is False
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_quality_verdict_ci_failure_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(
        c,
        "_resolve_ci_status",
        AsyncMock(return_value={"state": "failure", "failing_checks": ["lint"]}),
    )
    local = AsyncMock(return_value=None)
    monkeypatch.setattr(c, "_check_quality_gate", local)
    rejection, ran_local = await c._fast_path_quality_verdict(
        MagicMock(task_id=uuid4(), task=MagicMock(), briefing={})
    )
    assert rejection is not None
    assert ran_local is False
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_quality_verdict_no_ci_falls_back_to_local_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(
        c, "_resolve_ci_status", AsyncMock(return_value={"state": "no_ci_configured"})
    )
    local = AsyncMock(return_value=None)
    monkeypatch.setattr(c, "_check_quality_gate", local)
    rejection, ran_local = await c._fast_path_quality_verdict(
        MagicMock(task_id=uuid4(), task=MagicMock(), briefing={})
    )
    assert rejection is None
    assert ran_local is True
    local.assert_awaited_once()


@pytest.mark.asyncio
async def test_quality_verdict_unresolvable_falls_back_to_local_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_resolve_ci_status", AsyncMock(return_value=None))
    local = AsyncMock(return_value=None)
    monkeypatch.setattr(c, "_check_quality_gate", local)
    rejection, ran_local = await c._fast_path_quality_verdict(
        MagicMock(task_id=uuid4(), task=MagicMock(), briefing={})
    )
    assert rejection is None
    assert ran_local is True
    local.assert_awaited_once()
