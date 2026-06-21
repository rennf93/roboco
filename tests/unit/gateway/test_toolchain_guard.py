"""The loud-fail guard: a delivery gate refuses when the suite can't run.

When toolchain matching is on and the acting agent's workspace recorded a
``broken`` toolchain status (the project's suite cannot be collected under the
provisioned interpreter), the dev/QA/PR gates must block — never let a role
"pass" on a source read. Off, or any non-broken / unknown status, never blocks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_choreographer(*, status: str | None) -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base["git"].toolchain_status_for_task.return_value = status
    return Choreographer(ChoreographerDeps(**base))


@pytest.mark.asyncio
async def test_guard_blocks_when_broken_and_flag_on(monkeypatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status="broken")
    env = await c._toolchain_broken_guard(uuid4(), MagicMock())
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "i_am_blocked" in body["remediate"]


@pytest.mark.asyncio
async def test_guard_passes_when_ok(monkeypatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status="ok")
    assert await c._toolchain_broken_guard(uuid4(), MagicMock()) is None


@pytest.mark.asyncio
async def test_guard_passes_when_status_unknown_or_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    for status in ("unknown", None):
        c = _make_choreographer(status=status)
        assert await c._toolchain_broken_guard(uuid4(), MagicMock()) is None


@pytest.mark.asyncio
async def test_guard_inert_when_flag_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", False)
    c = _make_choreographer(status="broken")
    assert await c._toolchain_broken_guard(uuid4(), MagicMock()) is None
    # Flag off => the workspace is never consulted at all.
    c.git.toolchain_status_for_task.assert_not_awaited()
