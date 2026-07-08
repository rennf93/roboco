"""The PR-gate turn cut: closure auto-submits assembled parents to the gate.

When every child of an assembled parent is terminal, the orchestrator used
to spawn the PM just to call submit_up/submit_root — a whole agent turn
whose substance (freshness rebase, integrity check, PR open) is
deterministic gate code. ``_try_auto_submit`` runs the REAL submit verb
through the internal API as the owning PM, unconditionally — this IS the
flow, not an opt-in switch. Only a gate rejection falls back to the classic
PM closure spawn. The PM's remaining turn is the one that needs judgment:
the final merge (or the revision) — and when it does fall back, the
rejection reason rides the closure prompt so the PM isn't rediscovering it
blind.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
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
async def test_cell_parent_auto_submits_as_owning_pm() -> None:
    orch = _orch()
    client = _client({"status": "awaiting_pr_review", "error": None})

    ok, reason = await orch._try_auto_submit(client, _CELL_TASK, "be-pm")
    assert ok is True
    assert reason is None

    (url,), kwargs = client.post.call_args
    assert url == f"{orch._api_url}/v1/flow/cell_pm/submit_up"
    assert kwargs["headers"]["X-Agent-ID"] == _CELL_TASK["assigned_to"]
    assert kwargs["headers"]["X-Agent-Role"] == "cell_pm"
    assert kwargs["json"]["task_id"] == _CELL_TASK["id"]
    assert len(kwargs["json"]["notes"]) >= _MIN_NOTES


@pytest.mark.asyncio
async def test_main_pm_root_auto_submits_submit_root() -> None:
    orch = _orch()
    client = _client({"status": "awaiting_pr_review", "error": None})
    task = {**_CELL_TASK, "team": "main_pm"}

    ok, reason = await orch._try_auto_submit(client, task, "main-pm")
    assert ok is True
    assert reason is None
    (url,), kwargs = client.post.call_args
    assert url == f"{orch._api_url}/v1/flow/main_pm/submit_root"
    assert kwargs["headers"]["X-Agent-Role"] == "main_pm"


@pytest.mark.asyncio
async def test_branchless_parent_never_auto_submits() -> None:
    """A branchless coordination parent (MegaTask umbrella) assembles no PR."""
    orch = _orch()
    client = _client({"error": None})
    task = {**_CELL_TASK, "branch_name": None}

    assert await orch._try_auto_submit(client, task, "be-pm") == (False, None)
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_umbrella_shape_never_auto_submits_even_with_a_stray_branch() -> None:
    """Belt-and-suspenders: _auto_submit_target consults the canonical
    is_branchless_coordination predicate (via _is_coordination_task), not
    just branch_name/project_id truthiness — so a MegaTask umbrella shape
    (batch_id set, no parent, no project/product) is excluded structurally,
    matching every other git-exemption site in the codebase."""
    orch = _orch()
    client = _client({"error": None})
    task = {
        **_CELL_TASK,
        "project_id": None,
        "batch_id": "44444444-4444-4444-4444-444444444444",
        "parent_task_id": None,
    }

    assert await orch._try_auto_submit(client, task, "be-pm") == (False, None)
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_gate_rejection_falls_back_to_pm_spawn() -> None:
    """A rejection envelope (e.g. integrity/freshness refusal) means the PM
    turn is genuinely needed — auto-submit yields to the closure spawn, and
    the reason is threaded back for the PM's prompt. This fallback is the
    sole safety net — there is no kill-switch on top of it."""
    orch = _orch()
    client = _client(
        {"error": "invalid_state", "message": "assembled branch behind base"}
    )

    ok, reason = await orch._try_auto_submit(client, _CELL_TASK, "be-pm")
    assert ok is False
    assert reason == "invalid_state: assembled branch behind base"
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_ac_coverage_gate_rejection_reason_includes_remediate() -> None:
    """A parent-AC-coverage refusal (tracing_gap) folds remediate into the
    reason so the PM's closure prompt names the uncovered criteria."""
    orch = _orch()
    remediate = (
        "1 parent acceptance criteria are not covered by a completed subtask "
        "before bubbling up: AC-2. Delegate a subtask to cover it."
    )
    client = _client(
        {
            "error": "tracing_gap",
            "message": f"missing required tracing: [...]. {remediate}",
            "remediate": remediate,
        }
    )

    ok, reason = await orch._try_auto_submit(client, _CELL_TASK, "be-pm")
    assert ok is False
    assert reason is not None
    assert "AC-2" in reason
    # remediate is already folded into message by Envelope._missing_message;
    # it must not be duplicated in the formatted reason.
    assert reason.count("AC-2") == 1


@pytest.mark.asyncio
async def test_subtasks_not_terminal_race_falls_back_cleanly() -> None:
    """A subtask flips non-terminal between the closure dispatcher's check
    and the submit call (the race) — the gate's own re-check refuses and
    auto-submit falls back cleanly, same as any other gate rejection."""
    orch = _orch()
    client = _client(
        {
            "error": "tracing_gap",
            "message": (
                "missing required tracing: ['subtasks not all terminal']. "
                "all subtasks must be in completed/cancelled before bubbling "
                "up. Non-terminal subtasks: ['55555555-5555-5555-5555-"
                "555555555555']"
            ),
        }
    )

    ok, reason = await orch._try_auto_submit(client, _CELL_TASK, "be-pm")
    assert ok is False
    assert reason is not None
    assert "subtasks not all terminal" in reason


@pytest.mark.asyncio
async def test_missing_assignment_falls_back_to_static_identity() -> None:
    orch = _orch()
    client = _client({"status": "awaiting_pr_review", "error": None})
    task = {**_CELL_TASK, "assigned_to": None}

    ok, reason = await orch._try_auto_submit(client, task, "be-pm")
    assert ok is True
    assert reason is None
    (_, kwargs) = client.post.call_args
    assert kwargs["headers"]["X-Agent-ID"] == AGENT_UUIDS["be-pm"]


@pytest.mark.asyncio
async def test_transport_error_falls_back() -> None:
    orch = _orch()
    client = MagicMock()
    client.post = AsyncMock(side_effect=RuntimeError("api down"))

    ok, reason = await orch._try_auto_submit(client, _CELL_TASK, "be-pm")
    assert ok is False
    assert reason == "auto-submit transport error: api down"


def test_closure_prompt_threads_auto_submit_reason() -> None:
    """The fallback closure prompt surfaces the exact gate refusal so the
    respawned PM doesn't re-run evidence-gathering to rediscover it blind."""
    orch = _orch()
    prompt = orch._build_pm_closure_prompt(
        _CELL_TASK,
        [],
        auto_submit_reason="tracing_gap: parent AC-2 not covered",
    )
    assert "already attempted to auto-submit" in prompt
    assert "tracing_gap: parent AC-2 not covered" in prompt


def test_closure_prompt_omits_note_when_no_reason() -> None:
    orch = _orch()
    prompt = orch._build_pm_closure_prompt(_CELL_TASK, [])
    assert "already attempted to auto-submit" not in prompt
