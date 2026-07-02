"""The PR-gate turn cut: closure auto-submits assembled parents to the gate.

When every child of an assembled parent is terminal, the orchestrator used
to spawn the PM just to call submit_up/submit_root — a whole agent turn
whose substance (freshness rebase, integrity check, PR open) is
deterministic gate code. ``_try_auto_submit`` runs the REAL submit verb
through the internal API as the owning PM; only a gate rejection falls
back to the classic PM closure spawn. The PM's remaining turn is the one
that needs judgment: the final merge (or the revision).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.config import settings as cfg
from roboco.runtime.orchestrator import AGENT_UUIDS, AgentOrchestrator

# The commit/notes validator's minimum substantive length.
_MIN_NOTES = 20

_CELL_TASK: dict[str, Any] = {
    "id": "11111111-1111-1111-1111-111111111111",
    "team": "backend",
    "branch_name": "feature/backend/AAAA1111",
    "project_id": "22222222-2222-2222-2222-222222222222",
    "assigned_to": "33333333-3333-3333-3333-333333333333",
    "status": "in_progress",
}


def _orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._tick_handled_tasks = set()
    orch._bg_tasks = set()
    return orch


def _client(envelope: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = envelope
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_cell_parent_auto_submits_as_owning_pm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "pr_gate_auto_submit_enabled", True)
    orch = _orch()
    client = _client({"status": "awaiting_pr_review", "error": None})

    assert await orch._try_auto_submit(client, _CELL_TASK, "be-pm") is True

    (url,), kwargs = client.post.call_args
    assert url == f"{orch._api_url}/v1/flow/cell_pm/submit_up"
    assert kwargs["headers"]["X-Agent-ID"] == _CELL_TASK["assigned_to"]
    assert kwargs["headers"]["X-Agent-Role"] == "cell_pm"
    assert kwargs["json"]["task_id"] == _CELL_TASK["id"]
    assert len(kwargs["json"]["notes"]) >= _MIN_NOTES


@pytest.mark.asyncio
async def test_main_pm_root_auto_submits_submit_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "pr_gate_auto_submit_enabled", True)
    orch = _orch()
    client = _client({"status": "awaiting_pr_review", "error": None})
    task = {**_CELL_TASK, "team": "main_pm"}

    assert await orch._try_auto_submit(client, task, "main-pm") is True
    (url,), kwargs = client.post.call_args
    assert url == f"{orch._api_url}/v1/flow/main_pm/submit_root"
    assert kwargs["headers"]["X-Agent-Role"] == "main_pm"


@pytest.mark.asyncio
async def test_branchless_parent_never_auto_submits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A branchless coordination parent (MegaTask umbrella) assembles no PR."""
    monkeypatch.setattr(cfg, "pr_gate_auto_submit_enabled", True)
    orch = _orch()
    client = _client({"error": None})
    task = {**_CELL_TASK, "branch_name": None}

    assert await orch._try_auto_submit(client, task, "be-pm") is False
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_flag_off_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "pr_gate_auto_submit_enabled", False)
    orch = _orch()
    client = _client({"error": None})

    assert await orch._try_auto_submit(client, _CELL_TASK, "be-pm") is False
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_gate_rejection_falls_back_to_pm_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejection envelope (e.g. integrity/freshness refusal) means the PM
    turn is genuinely needed — auto-submit yields to the closure spawn."""
    monkeypatch.setattr(cfg, "pr_gate_auto_submit_enabled", True)
    orch = _orch()
    client = _client(
        {"error": "invalid_state", "message": "assembled branch behind base"}
    )

    assert await orch._try_auto_submit(client, _CELL_TASK, "be-pm") is False
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_missing_assignment_falls_back_to_static_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "pr_gate_auto_submit_enabled", True)
    orch = _orch()
    client = _client({"status": "awaiting_pr_review", "error": None})
    task = {**_CELL_TASK, "assigned_to": None}

    assert await orch._try_auto_submit(client, task, "be-pm") is True
    (_, kwargs) = client.post.call_args
    assert kwargs["headers"]["X-Agent-ID"] == AGENT_UUIDS["be-pm"]


@pytest.mark.asyncio
async def test_transport_error_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "pr_gate_auto_submit_enabled", True)
    orch = _orch()
    client = MagicMock()
    client.post = AsyncMock(side_effect=RuntimeError("api down"))

    assert await orch._try_auto_submit(client, _CELL_TASK, "be-pm") is False
