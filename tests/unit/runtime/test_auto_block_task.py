"""`_auto_block_task` must be state-aware and never log an empty error.

Live incident: the orchestrator logged `{"error": "", "event": "Failed to
auto-block task"}` for a task whose owning container had died mid-
awaiting_qa — an empty error string with no diagnostic value, from a PATCH
attempting to force a task QA already owns back to "blocked". This pins:

- a task already past dev control (awaiting_qa, terminal, ...) is skipped
  with an info log, no PATCH attempted
- a still-blockable task (pending) proceeds to the PATCH as before
- a PATCH failure logs a non-empty error even for exception types whose
  str() is empty (e.g. a bare TimeoutError)
- a failed status pre-check does not swallow the block attempt itself
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orch() -> AgentOrchestrator:
    return AgentOrchestrator.__new__(AgentOrchestrator)


def _resp(status_code_ok: bool, payload: dict[str, Any]) -> MagicMock:
    r = MagicMock()
    r.is_success = status_code_ok
    r.json.return_value = payload
    return r


@pytest.mark.asyncio
async def test_auto_block_skips_task_already_in_awaiting_qa() -> None:
    orch = _make_orch()
    client: Any = AsyncMock()
    client.get.return_value = _resp(True, {"status": "awaiting_qa"})

    await orch._auto_block_task(client, "tid-1", "container died")

    client.patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_block_skips_completed_task() -> None:
    orch = _make_orch()
    client: Any = AsyncMock()
    client.get.return_value = _resp(True, {"status": "completed"})

    await orch._auto_block_task(client, "tid-2", "stale readiness check")

    client.patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_block_proceeds_for_pending_task() -> None:
    """The main existing use case (stuck pending tasks) must be unaffected."""
    orch = _make_orch()
    client: Any = AsyncMock()
    client.get.return_value = _resp(True, {"status": "pending"})

    await orch._auto_block_task(client, "tid-3", "needs a project_id")

    client.patch.assert_awaited_once()
    args, kwargs = client.patch.await_args
    assert "tid-3" in args[0]
    assert kwargs["json"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_auto_block_proceeds_when_status_precheck_fails() -> None:
    """A GET failure must not swallow the block attempt — fall through."""
    orch = _make_orch()
    client: Any = AsyncMock()
    client.get.side_effect = RuntimeError("network down")

    await orch._auto_block_task(client, "tid-4", "some reason")

    client.patch.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_block_logs_nonempty_error_for_blank_exception() -> None:
    """str(TimeoutError()) is '' — the log must still carry a real message."""
    assert str(TimeoutError()) == ""  # the exact gotcha this guards against
    orch = _make_orch()
    client: Any = AsyncMock()
    client.get.return_value = _resp(True, {"status": "pending"})
    client.patch.side_effect = TimeoutError()

    with patch("roboco.runtime.orchestrator.logger") as mock_logger:
        await orch._auto_block_task(client, "tid-5", "some reason")

    mock_logger.error.assert_called_once()
    _, kwargs = mock_logger.error.call_args
    assert kwargs["error"], "error field must never be blank"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
