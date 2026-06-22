"""The i_am_done conventions gate: block-level violations refuse the submit.

With the flag on, a ``block`` finding (or a validator that could not run) on the
dev's changed files refuses i_am_done with the offending ``file:line`` + a fix
hint. ``warn`` findings never block; the flag-off path is fully inert.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.briefing = {}
    return ctx


@pytest.mark.asyncio
async def test_block_finding_refuses_with_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(check_result=_BLOCK_RESULT)
    env = await c._conventions_gate(_ctx())
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "app/routers/u.py:2" in body["remediate"]
    assert "move it into models/" in body["remediate"]


@pytest.mark.asyncio
async def test_warn_only_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(
        check_result={
            "findings": [{"file": "x.py", "line": 1, "level": "warn", "fix_hint": "h"}],
            "could_not_run": False,
        }
    )
    assert await c._conventions_gate(_ctx()) is None


@pytest.mark.asyncio
async def test_could_not_run_blocks_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    c = _make_choreographer(check_result={"findings": [], "could_not_run": True})
    env = await c._conventions_gate(_ctx())
    assert env is not None
    assert "could not run" in env.as_dict()["message"]


@pytest.mark.asyncio
async def test_flag_off_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", False)
    c = _make_choreographer(check_result=_BLOCK_RESULT)
    assert await c._conventions_gate(_ctx()) is None


def test_no_findings_passes() -> None:
    result: dict[str, Any] = {"findings": [], "could_not_run": False}
    assert Choreographer._conventions_rejection(result, {}) is None


@pytest.mark.asyncio
async def test_gate_records_findings_even_when_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "conventions_enabled", True)
    recorded: list[dict[str, Any]] = []

    async def _spy(_task: Any, result: dict[str, Any]) -> None:
        recorded.append(result)

    c = _make_choreographer(check_result=_BLOCK_RESULT)
    monkeypatch.setattr(c, "_record_convention_findings", _spy)
    env = await c._conventions_gate(_ctx())
    assert env is not None  # still blocks
    assert recorded and recorded[0] is _BLOCK_RESULT
