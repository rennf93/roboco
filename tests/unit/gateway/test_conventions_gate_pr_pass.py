"""The pr_pass conventions gate: a reviewer can't PASS a PR with block violations.

``_gate_decision`` runs ``_conventions_guard`` for ``verb == "pr_pass"`` only
(pr_fail stays available), exactly like the toolchain guard. These exercise the
shared guard the pr_pass path invokes: a ``block`` finding (or a validator that
could not run) refuses; ``warn`` passes; flag-off is inert.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps

_BLOCK_RESULT: dict[str, Any] = {
    "findings": [
        {
            "file": "app/routers/u.py",
            "line": 2,
            "level": "block",
            "fix_hint": "move it into models/",
        }
    ],
    "could_not_run": False,
}


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
async def test_pr_pass_guard_blocks_on_block_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(check_result=_BLOCK_RESULT)
    env = await c._conventions_guard(uuid4(), MagicMock(), {})
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "app/routers/u.py:2" in body["remediate"]
    assert "waiver" in body["remediate"]


@pytest.mark.asyncio
async def test_pr_pass_guard_allows_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(
        check_result={
            "findings": [{"file": "x.py", "line": 1, "level": "warn", "fix_hint": "h"}],
            "could_not_run": False,
        }
    )
    assert await c._conventions_guard(uuid4(), MagicMock(), {}) is None


@pytest.mark.asyncio
async def test_pr_pass_guard_blocks_when_validator_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(check_result={"findings": [], "could_not_run": True})
    assert await c._conventions_guard(uuid4(), MagicMock(), {}) is not None


@pytest.mark.asyncio
async def test_pr_pass_guard_could_not_run_remediation_uses_pr_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # F044: _conventions_guard is the pr_pass (reviewer) path. A reviewer has no
    # i_am_blocked verb, so the could_not_run remediation must point at pr_fail
    # (the reviewer's reject lever) — not tell them to call a verb they lack.
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(check_result={"findings": [], "could_not_run": True})
    env = await c._conventions_guard(uuid4(), MagicMock(), {})
    assert env is not None
    body = env.as_dict()
    assert "i_am_blocked" not in body["remediate"]
    assert "pr_fail" in body["remediate"]


@pytest.mark.asyncio
async def test_pr_pass_guard_inert_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    c = _make_choreographer(check_result=_BLOCK_RESULT)
    assert await c._conventions_guard(uuid4(), MagicMock(), {}) is None
