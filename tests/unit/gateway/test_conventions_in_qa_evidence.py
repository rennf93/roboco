"""QA claim_review evidence carries the conventions validator findings (gated)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.evidence_builder import build_evidence_for_task


def _make_choreographer(*, check_result: dict[str, Any]) -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base["git"].conventions_check_for_task.return_value = check_result
    return Choreographer(ChoreographerDeps(**base))


@pytest.mark.asyncio
async def test_findings_surfaced_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    findings = [{"file": "x.py", "line": 1, "level": "warn", "fix_hint": "h"}]
    c = _make_choreographer(check_result={"findings": findings, "could_not_run": False})
    gaps: list[str] = []
    assert (
        await c._qa_convention_findings(uuid4(), MagicMock(), timeout=30.0, gaps=gaps)
        == findings
    )
    assert gaps == []


@pytest.mark.asyncio
async def test_empty_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    c = _make_choreographer(
        check_result={"findings": [{"file": "x"}], "could_not_run": False}
    )
    gaps: list[str] = []
    assert (
        await c._qa_convention_findings(uuid4(), MagicMock(), timeout=30.0, gaps=gaps)
        == []
    )
    assert gaps == []


@pytest.mark.asyncio
async def test_could_not_run_surfaced_as_single_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-timeout could_not_run reason ("boom") stays fail-open in
    convention_findings but must NOT also spam evidence_gaps — only a
    detected timeout does (see test_claim_review_conventions_timeout_
    degrades_with_gap in test_evidence_assembly_bounded_legs.py)."""
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(
        check_result={"findings": [], "could_not_run": True, "reason": "boom"}
    )
    gaps: list[str] = []
    out = await c._qa_convention_findings(uuid4(), MagicMock(), timeout=30.0, gaps=gaps)
    assert len(out) == 1
    assert out[0]["could_not_run"] is True
    assert out[0]["reason"] == "boom"
    assert gaps == []


def _stub_task() -> MagicMock:
    task = MagicMock()
    task.pr_number = None
    task.pr_url = None
    task.commits = []
    task.dev_notes = None
    task.acceptance_criteria_status = []
    return task


def test_evidence_payload_includes_convention_findings() -> None:
    findings = [{"file": "x", "line": 1}]
    ev = build_evidence_for_task(
        _stub_task(),
        journal_highlights=[],
        files_changed=[],
        convention_findings=findings,
    )
    assert ev.as_dict()["convention_findings"] == findings


def test_evidence_payload_convention_findings_default_empty() -> None:
    """An empty findings list is omitted from as_dict() entirely (zero-noise
    posture, matching build_task_handoff) — the attribute itself stays []."""
    ev = build_evidence_for_task(_stub_task(), journal_highlights=[], files_changed=[])
    assert ev.convention_findings == []
    assert "convention_findings" not in ev.as_dict()


# ---------------------------------------------------------------------------
# _build_qa_claim_evidence: skip conventions when the git legs above already
# timed out (evidence_gaps non-empty — the warm-workspace assumption failed).
# ---------------------------------------------------------------------------


def _stub_empty_ledger(session: MagicMock) -> None:
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
    )


def _evidence_choreographer(git_svc: AsyncMock) -> Choreographer:
    """Full Choreographer wired for ``_build_qa_claim_evidence`` directly
    (not the whole claim_review verb) — journal/evidence_repo/session
    stubbed empty so the build reaches the conventions call site cleanly."""
    task_svc = AsyncMock()
    _stub_empty_ledger(task_svc.session)
    evidence_repo = AsyncMock()
    evidence_repo.journal_highlights_for_task.return_value = []
    evidence_repo.ancestor_context_for_task.return_value = []
    deps = ChoreographerDeps(
        task=task_svc,
        work_session=AsyncMock(),
        git=git_svc,
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=evidence_repo,
    )
    return Choreographer(deps)


def _evidence_task(task_id: Any) -> MagicMock:
    return MagicMock(
        id=task_id,
        branch_name="feature/backend/abc",
        pr_number=8,
        pr_url="https://github.com/x/y/pull/8",
        commits=[],
        dev_notes=None,
        acceptance_criteria_status=[],
        parent_task_id=None,
        description=None,
    )


@pytest.mark.asyncio
async def test_conventions_skipped_when_git_legs_already_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The combined diff_and_files leg times out (evidence_gaps ends up
    non-empty) — _qa_convention_findings (and the real
    conventions_check_for_task it would call) must never run; the caller
    fills in a could_not_run skip entry and its own evidence_gaps note
    instead."""
    monkeypatch.setattr(settings, "conventions_enabled", True)
    monkeypatch.setattr(settings, "evidence_assembly_timeout_seconds", 0.02)

    async def _hangs(*_args: object, **_kwargs: object) -> Any:
        await asyncio.sleep(5)
        return "unreachable"

    git_svc = AsyncMock()
    git_svc.diff_and_files.side_effect = _hangs
    c = _evidence_choreographer(git_svc)
    task_id = uuid4()
    t = _evidence_task(task_id)

    ev = await c._build_qa_claim_evidence(uuid4(), t, task_id)
    body = ev.as_dict()

    git_svc.conventions_check_for_task.assert_not_awaited()
    assert body["convention_findings"] == [
        {
            "could_not_run": True,
            "reason": "skipped: git evidence legs timed out (cold/contended workspace)",
        }
    ]
    gaps = body["evidence_gaps"]
    # A combined-leg timeout kills diff AND files_changed together — the
    # gap note names both losses, not just "pr diff".
    assert any("pr diff + files_changed unavailable" in g for g in gaps)
    assert any("conventions findings unavailable" in g for g in gaps)


@pytest.mark.asyncio
async def test_conventions_runs_when_legs_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skip only engages when evidence_gaps is non-empty — a normal
    build (both legs succeed) still runs conventions exactly as before."""
    monkeypatch.setattr(settings, "conventions_enabled", True)
    git_svc = AsyncMock()
    git_svc.diff_and_files.return_value = ("diff content", ["README.md"])
    git_svc.conventions_check_for_task.return_value = {
        "findings": [],
        "could_not_run": False,
    }
    c = _evidence_choreographer(git_svc)
    task_id = uuid4()
    t = _evidence_task(task_id)

    ev = await c._build_qa_claim_evidence(uuid4(), t, task_id)
    body = ev.as_dict()

    git_svc.conventions_check_for_task.assert_awaited_once()
    call_kwargs = git_svc.conventions_check_for_task.await_args.kwargs
    assert call_kwargs["changed_files"] == ["README.md"]
    assert "evidence_gaps" not in body


@pytest.mark.asyncio
async def test_conventions_not_skipped_stub_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with degraded legs, a disabled subsystem must stay empty (no
    misleading could_not_run stub implying conventions would otherwise have
    run) — the skip guard checks conventions_enabled too."""
    monkeypatch.setattr(settings, "conventions_enabled", False)
    monkeypatch.setattr(settings, "evidence_assembly_timeout_seconds", 0.02)

    async def _hangs(*_args: object, **_kwargs: object) -> Any:
        await asyncio.sleep(5)
        return "unreachable"

    git_svc = AsyncMock()
    git_svc.diff_and_files.side_effect = _hangs
    c = _evidence_choreographer(git_svc)
    task_id = uuid4()
    t = _evidence_task(task_id)

    ev = await c._build_qa_claim_evidence(uuid4(), t, task_id)
    body = ev.as_dict()

    git_svc.conventions_check_for_task.assert_not_awaited()
    assert "convention_findings" not in body
    gaps = body["evidence_gaps"]
    assert not any("conventions findings unavailable" in g for g in gaps)
