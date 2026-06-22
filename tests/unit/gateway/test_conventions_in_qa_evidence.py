"""QA claim_review evidence carries the conventions validator findings (gated)."""

from __future__ import annotations

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
    assert await c._qa_convention_findings(uuid4(), MagicMock()) == findings


@pytest.mark.asyncio
async def test_empty_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    c = _make_choreographer(
        check_result={"findings": [{"file": "x"}], "could_not_run": False}
    )
    assert await c._qa_convention_findings(uuid4(), MagicMock()) == []


@pytest.mark.asyncio
async def test_could_not_run_surfaced_as_single_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(
        check_result={"findings": [], "could_not_run": True, "reason": "boom"}
    )
    out = await c._qa_convention_findings(uuid4(), MagicMock())
    assert len(out) == 1
    assert out[0]["could_not_run"] is True
    assert out[0]["reason"] == "boom"


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
    ev = build_evidence_for_task(_stub_task(), journal_highlights=[], files_changed=[])
    assert ev.as_dict()["convention_findings"] == []
