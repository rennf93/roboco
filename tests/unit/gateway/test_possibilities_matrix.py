"""W7 possibilities matrix: the ``_work_appears_done`` predicate + fast path.

A task whose work is already done (commits + PR open + every acceptance
criterion addressed + no open findings) qualifies for the fast path. These
tests pin the predicate's truth table and the schema it actually reads (the
per-criterion rows the writer persists use ``artifact_ref``; the predicate
unions ``artifact_ref`` / ``referencing_artifact_id`` / ``addressed`` so it
sees real data, not the latent reader/writer key drift).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.envelope import Envelope


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
    t.pr_number = 12345 if pr_created else None
    t.acceptance_criteria = list(criteria)
    t.acceptance_criteria_status = ac_status
    return t


@pytest.mark.asyncio
async def test_work_appears_done_true_when_all_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t()) is True


@pytest.mark.asyncio
async def test_work_appears_done_false_when_no_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t(pr_created=False)) is False


@pytest.mark.asyncio
async def test_work_appears_done_false_when_no_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t(commits=())) is False


@pytest.mark.asyncio
async def test_work_appears_done_false_when_ac_unaddressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
async def test_work_appears_done_true_with_no_criteria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _no_findings)
    assert await c._work_appears_done(_t(criteria=(), ac_status=[])) is True


@pytest.mark.asyncio
async def test_work_appears_done_false_when_open_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    monkeypatch.setattr(c, "_open_finding_ids", _one_open)
    assert await c._work_appears_done(_t()) is False


@pytest.mark.asyncio
async def test_work_appears_done_false_when_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
async def test_quality_verdict_ci_failure_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


# ---------------------------------------------------------------------------
# _i_am_done_fast_path: gate ordering + transition chain (skips rich plan)
# ---------------------------------------------------------------------------


def _ctx(status: str = "claimed") -> Any:
    ctx = MagicMock()
    ctx.agent_id = uuid4()
    ctx.task_id = uuid4()
    ctx.task = _t(status=status)
    ctx.briefing = {}
    ctx.notes = "done"
    ctx.resolved_findings = None
    ctx.role_str = "developer"
    return ctx


@dataclass
class _FastPathMocks:
    """Typed handles to the patched verb-boundary mocks. mypy keeps the
    declared method types on ``c.<method>`` even after ``monkeypatch.setattr``
    (the runtime override is invisible to static analysis), so assertions
    must go through these locals typed as ``AsyncMock`` — not ``c.<method>``."""

    ok: AsyncMock
    reject: AsyncMock
    conventions: AsyncMock
    open_findings: AsyncMock
    record_milestone: AsyncMock


def _stub_fast_path(
    c: Choreographer, monkeypatch: pytest.MonkeyPatch, quality_rejection: Any = None
) -> _FastPathMocks:
    conventions = AsyncMock(return_value=None)
    open_findings = AsyncMock(return_value=())
    ok = AsyncMock(return_value="OK")
    reject = AsyncMock(return_value="REJECT")
    record_milestone = AsyncMock(return_value=None)
    monkeypatch.setattr(c, "_apply_resolved_findings", AsyncMock(return_value=None))
    monkeypatch.setattr(c, "_check_submit_qa_field_gates", AsyncMock(return_value=None))
    monkeypatch.setattr(c, "_behind_base_gate", AsyncMock(return_value=None))
    monkeypatch.setattr(c, "_ensure_branch_pushed", AsyncMock(return_value=None))
    monkeypatch.setattr(c, "_conventions_gate", conventions)
    monkeypatch.setattr(c, "_open_finding_ids", open_findings)
    monkeypatch.setattr(
        c,
        "_fast_path_quality_verdict",
        AsyncMock(return_value=(quality_rejection, False)),
    )
    monkeypatch.setattr(c, "_notify_qa", AsyncMock(return_value=None))
    monkeypatch.setattr(c, "_touch", AsyncMock(return_value=None))
    monkeypatch.setattr(c, "_record_milestone_progress", record_milestone)
    monkeypatch.setattr(c, "_build_i_am_done_ok", ok)
    monkeypatch.setattr(c, "_reject_i_am_done", reject)
    return _FastPathMocks(
        ok=ok,
        reject=reject,
        conventions=conventions,
        open_findings=open_findings,
        record_milestone=record_milestone,
    )


@pytest.mark.asyncio
async def test_fast_path_claimed_starts_without_set_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    stubs = _stub_fast_path(c, monkeypatch)
    await c._i_am_done_fast_path(_ctx(status="claimed"))
    stubs.ok.assert_awaited_once()  # OK path taken, no rejection
    c.task.start.assert_awaited_once()  # claimed -> in_progress
    c.task.set_plan.assert_not_awaited()  # rich plan SKIPPED (no set_plan)
    c.task.submit_verification.assert_awaited_once()
    c.task.submit_qa.assert_awaited_once()
    stubs.conventions.assert_awaited_once()  # conventions KEPT
    stubs.record_milestone.assert_awaited_once()


@pytest.mark.asyncio
async def test_fast_path_in_progress_skips_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    stubs = _stub_fast_path(c, monkeypatch)
    await c._i_am_done_fast_path(_ctx(status="in_progress"))
    stubs.ok.assert_awaited_once()
    c.task.start.assert_not_awaited()  # already in_progress
    c.task.submit_verification.assert_awaited_once()
    c.task.submit_qa.assert_awaited_once()


@pytest.mark.asyncio
async def test_fast_path_open_findings_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Choreographer(_deps())
    stubs = _stub_fast_path(c, monkeypatch)
    monkeypatch.setattr(c, "_open_finding_ids", AsyncMock(return_value=("f1abcd12",)))
    await c._i_am_done_fast_path(_ctx())
    stubs.reject.assert_awaited_once()
    c.task.submit_qa.assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_path_conventions_block_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    stubs = _stub_fast_path(c, monkeypatch)
    monkeypatch.setattr(
        c,
        "_conventions_gate",
        AsyncMock(return_value=Envelope.invalid_state(message="x", remediate="fix")),
    )
    await c._i_am_done_fast_path(_ctx())
    stubs.reject.assert_awaited_once()
    c.task.submit_qa.assert_not_awaited()


@pytest.mark.asyncio
async def test_fast_path_ci_failure_rejects_before_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = Choreographer(_deps())
    stubs = _stub_fast_path(c, monkeypatch)
    monkeypatch.setattr(
        c,
        "_fast_path_quality_verdict",
        AsyncMock(
            return_value=(
                Envelope.invalid_state(message="ci red", remediate="fix"),
                False,
            )
        ),
    )
    await c._i_am_done_fast_path(_ctx())
    stubs.reject.assert_awaited_once()
    c.task.submit_qa.assert_not_awaited()
