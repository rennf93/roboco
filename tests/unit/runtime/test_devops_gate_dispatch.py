"""The dispatcher's DevOps second-reviewer slot in _dispatch_pr_gate_work.

Flag on + the infra predicate applies + no devops verdict for the current
head => devops-1 is ALSO spawned for the gate task, AFTER the primary
reviewer pass (never starving the existing gate). Flag off, a busy devops-1,
or a current verdict => no spawn. The devops spawn never pre-claims: the
agent co-claims itself via claim_gate_review, leaving the primary reviewer's
ownership intact.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._tick_handled_tasks = set()
    return orch


def _gate_task() -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "awaiting_pr_review",
        "team": "backend",
        "task_type": "code",
        "title": "Harden the compose topology",
        "assigned_to": str(uuid4()),
        "project_slug": "proj-slug",
        "pr_number": 42,
        "pr_url": "https://example/pr/42",
        "branch_name": "feature/backend/root",
        "acceptance_criteria": [],
    }


@pytest.mark.asyncio
async def test_flag_off_never_spawns_devops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", False)
    orch = _orch()
    with (
        patch.object(
            orch, "_devops_gate_verdict_missing", new=AsyncMock(return_value=True)
        ) as predicate,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_devops_gate_review(_gate_task())
    # The flag check short-circuits before any predicate lookup.
    predicate.assert_not_awaited()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_predicate_hit_and_no_verdict_spawns_devops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    orch = _orch()
    task = _gate_task()
    prompt = "devops gate prompt"
    with (
        patch.object(
            orch, "_devops_gate_verdict_missing", new=AsyncMock(return_value=True)
        ),
        patch.object(
            orch, "_pm_respawn_should_gate", new=AsyncMock(return_value=False)
        ),
        patch.object(
            orch, "_build_devops_gate_prompt", new=MagicMock(return_value=prompt)
        ) as prompt_builder,
        patch.object(orch, "_task_git_context", new=MagicMock(return_value={})),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_devops_gate_review(task)
    spawn.assert_awaited_once()
    kwargs = spawn.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["agent_id"] == "devops-1"
    assert kwargs["task_id"] == task["id"]
    assert kwargs["initial_prompt"] == prompt
    assert kwargs["spawned_by"] == "_dispatch_pr_gate_work"
    prompt_builder.assert_called_once_with(task)


@pytest.mark.asyncio
async def test_no_predicate_or_current_verdict_never_spawns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    orch = _orch()
    with (
        patch.object(
            orch, "_devops_gate_verdict_missing", new=AsyncMock(return_value=False)
        ),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_devops_gate_review(_gate_task())
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_devops_is_never_double_spawned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "devops_enabled", True)
    orch = _orch()
    with (
        patch.object(orch, "_is_agent_active", new=MagicMock(return_value=True)),
        patch.object(
            orch, "_devops_gate_verdict_missing", new=AsyncMock(return_value=True)
        ) as predicate,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._dispatch_devops_gate_review(_gate_task())
    predicate.assert_not_awaited()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_devops_gate_slot_runs_after_the_primary_reviewer_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering guarantee: the devops pass iterates the same fetch set AFTER
    the primary reviewer loop, so the existing gate dispatch can never be
    starved by the second reviewer."""
    monkeypatch.setattr(settings, "devops_enabled", True)
    orch = _orch()
    o = cast("Any", orch)
    order: list[str] = []
    task = _gate_task()

    async def _primary(*_a: Any, **_kw: Any) -> None:
        order.append("primary")

    async def _devops(_task: dict[str, Any]) -> None:
        order.append("devops")

    # Drive the real _dispatch_pr_gate_work with the primary loop stubbed out
    # via _gate_task_ci_pending skip + reviewer selection returning None, and
    # the devops pass live.
    async def _fetch(_client: Any, _status: Any, **_kw: Any) -> list[dict[str, Any]]:
        return [task]

    def _gate_task_reviewer(_task: dict[str, Any]) -> str | None:
        order.append("primary-selection")
        return None  # no primary candidate this tick

    o._fetch_tasks = _fetch
    o._gate_task_reviewer = _gate_task_reviewer
    o._gate_task_ci_pending = AsyncMock(return_value=False)
    o._is_task_handled_this_tick = MagicMock(return_value=False)

    async def _devops_review(_t: dict[str, Any]) -> None:
        order.append("devops")

    o._dispatch_devops_gate_review = _devops_review

    await orch._dispatch_pr_gate_work(None)  # type: ignore[arg-type]

    assert "primary-selection" in order
    assert order.index("primary-selection") < order.index("devops")


@pytest.mark.asyncio
async def test_verdict_missing_fails_closed_on_lookup_error() -> None:
    """A DB/git failure in the predicate lookup must NOT spawn the second
    reviewer blind (fail-closed) - the primary reviewer's flow is unaffected
    either way."""
    orch = _orch()
    with patch(
        "roboco.db.base.get_db_context",
        MagicMock(side_effect=RuntimeError("db down")),
    ):
        assert not await orch._devops_gate_verdict_missing(_gate_task())


@pytest.mark.asyncio
async def test_verdict_missing_false_for_malformed_task_id() -> None:
    orch = _orch()
    task = _gate_task()
    task["id"] = "not-a-uuid"
    assert not await orch._devops_gate_verdict_missing(task)
