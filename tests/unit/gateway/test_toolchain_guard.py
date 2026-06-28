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
from structlog.testing import capture_logs


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
async def test_guard_blocks_when_broken_and_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status="broken")
    env = await c._toolchain_broken_guard(uuid4(), MagicMock())
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "i_am_blocked" in body["remediate"]


@pytest.mark.asyncio
async def test_guard_passes_when_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status="ok")
    assert await c._toolchain_broken_guard(uuid4(), MagicMock()) is None


@pytest.mark.asyncio
async def test_guard_passes_when_status_unknown_or_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    for status in ("unknown", None):
        c = _make_choreographer(status=status)
        assert await c._toolchain_broken_guard(uuid4(), MagicMock()) is None


@pytest.mark.asyncio
async def test_guard_inert_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", False)
    c = _make_choreographer(status="broken")
    assert await c._toolchain_broken_guard(uuid4(), MagicMock()) is None
    # Flag off => the workspace is never consulted at all.
    c.git.toolchain_status_for_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_warns_loudly_on_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    # 'unknown' still fails open (never strands a task), but it must not be
    # silent — a warning is emitted so the hollow pass is visible to operators.
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status="unknown")
    with capture_logs() as logs:
        env = await c._toolchain_broken_guard(uuid4(), MagicMock())
    assert env is None
    assert any(e.get("event") == "toolchain.unverified_gate_pass" for e in logs)


@pytest.mark.asyncio
async def test_guard_silent_when_no_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    # No marker (None) is benign — flag on but not yet provisioned / not a test
    # project — and must stay silent so the warning means something.
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status=None)
    with capture_logs() as logs:
        env = await c._toolchain_broken_guard(uuid4(), MagicMock())
    assert env is None
    assert not any(e.get("event") == "toolchain.unverified_gate_pass" for e in logs)


@pytest.mark.asyncio
async def test_guard_reviewer_remediation_uses_pr_fail_not_i_am_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # F044: the pr_pass gate runs this guard on the REVIEWER's workspace. A PR
    # reviewer has no i_am_blocked verb, so the dev-path remediation ("call
    # i_am_blocked(reason='toolchain')") sends them to a verb they cannot call.
    # The reviewer's reject lever is pr_fail — the remediation must point there
    # so the PR goes back to needs_revision for the dev to fix the environment.
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status="broken")
    env = await c._toolchain_broken_guard(uuid4(), MagicMock(), reviewer=True)
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "i_am_blocked" not in body["remediate"]
    assert "pr_fail" in body["remediate"]


@pytest.mark.asyncio
async def test_guard_dev_remediation_still_uses_i_am_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # F044: the dev (i_am_done) path keeps i_am_blocked — a dev DOES have that
    # verb, so the original remediation is correct there. The reviewer flag must
    # not change the dev-path wording.
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    c = _make_choreographer(status="broken")
    env = await c._toolchain_broken_guard(uuid4(), MagicMock())
    assert env is not None
    body = env.as_dict()
    assert "i_am_blocked" in body["remediate"]
