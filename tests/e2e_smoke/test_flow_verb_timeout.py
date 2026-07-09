"""Scenario: a hung gateway flow-verb must release its task-row FOR UPDATE
lock within a bounded server-side timeout — not hold it forever.

Reproduces the 2026-07-07 wedge (a ``kimi-k2.7-code:cloud`` agent on task
79d686f0): a verb whose request transaction held the ``SELECT ... FOR
UPDATE`` lock on the task row never committed. uvicorn does not cancel the
endpoint coroutine on client disconnect, and ``get_db`` only rolled back on
``Exception`` (not a hang), so the row lock was held indefinitely — every
later task-row write on that task blocked on the lock and timed out, while
plain reads (``evidence``) and journal writes (``note``, a different row)
stayed fast. The fix is a server-side ``asyncio.timeout`` on flow verbs
(``FlowVerbTimeoutMiddleware``): on expiry the inner app is cancelled,
``get_db`` rolls back (releasing the lock), and a clean retryable 504
``gateway_timeout`` envelope is returned.

The hang is injected INSIDE the verb's own transaction: ``claim`` acquires
the FOR UPDATE lock, then ``set_plan`` sleeps past the server timeout. The
sleep fires on the FIRST ``set_plan`` call only — a retry ``i_will_plan``
short-circuits as idempotent re-entry (the umbrella scenario's claim-time
gate dance relies on the same behavior: the composed sequence runs once,
retries skip it), so the hang has to be on the one call that actually runs.

Two tests give the empirical proof:

- ``test_flow_verb_timeout_releases_row_lock`` (fix ARMED, server timeout
  1s): verb-1's hang is cancelled at 1s, ``get_db`` rolls back (lock
  released), a 504 ``gateway_timeout`` comes back, and verb-2 re-acquires
  the row and reaches the post-claim gate (``tracing_gap``). Reaching the
  gate is the success marker — the verb completed its composed transaction.
- ``test_flow_verb_holds_lock_when_timeout_disarmed`` (fix DISARMED, server
  timeout 1000s + MCP client timeout 3s): verb-1's hang is NOT cancelled,
  so it holds the FOR UPDATE lock past the client's HTTP timeout — the
  empirical reproduction of the wedge on the same branch, by turning the
  fix off.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
from tests.e2e_smoke.arcs import (
    origin_branch,
    seed_company,
    seed_project,
    seed_task,
    set_branch_name,
)
from tests.e2e_smoke.harness import ScriptedAgent, expect_error

if TYPE_CHECKING:
    from uuid import UUID

    import pytest
    from tests.e2e_smoke.arcs import Company
    from tests.e2e_smoke.harness import E2EStack

# Pydantic's IWillPlanRequest.approach enforces >= 150 chars at the HTTP
# boundary, so every i_will_plan call needs a compliant approach.
_APPROACH = (
    "Plan and delegate the page-scoped refresh button work to the frontend "
    "cell: land the provider/hook, add the navbar button, remove the inline "
    "buttons, and route one planning subtask to fe-pm for delivery."
)
_SUB_TASKS = [
    {
        "title": "Frontend cell: refresh button",
        "description": (
            "Delegate the navbar refresh button to fe-pm: land the "
            "provider/hook and wire the click handler into the page."
        ),
    }
]
_PLAN = "Land the refresh button via the frontend cell."
# set_plan sleeps this long inside the verb's own transaction. On the fix
# (1s server timeout) the sleep is cancelled well before this; disarmed
# (1000s server timeout, 3s client timeout) the client trips first.
_HANG_SECONDS = 8.0
_SERVER_TIMEOUT_SECONDS = 1.0
_DISARMED_SERVER_TIMEOUT_SECONDS = 1000.0
# The MCP client's HTTP timeout for the disarmed reproduction — must be less
# than _HANG_SECONDS so the client trips before the sleep ends. Applied
# AFTER priming the per-agent flow_server reload (a pre-call patch is
# clobbered by the reload on the first flow() call).
_MCP_CLIENT_TIMEOUT_SECONDS = 3.0


def _seed_planning_root(
    stack: E2EStack, company: Company
) -> tuple[ScriptedAgent, UUID]:
    """Seed a PENDING MAIN_PM planning root + its origin branch for the test."""
    from roboco.models import Team
    from roboco.models.base import TaskStatus, TaskType

    project_id, _project_slug = seed_project(stack, company)
    main_pm = ScriptedAgent(stack, company.main_pm_id, "main-pm", "main_pm")
    task_id = seed_task(
        stack,
        title="Root: page-scoped refresh button",
        description="Frontend-only root: provider/hook + navbar button.",
        acceptance_criteria=["the refresh button lands on master"],
        task_type=TaskType.PLANNING,
        team=Team.MAIN_PM,
        project_id=project_id,
        created_by=company.main_pm_id,
        assigned_to=company.main_pm_id,
        status=TaskStatus.PENDING,
    )
    branch = f"feature/main_pm/{str(task_id)[:8]}"
    origin_branch(stack, branch, start="master")
    set_branch_name(stack, task_id, branch)
    return main_pm, task_id


def _patch_hang_in_set_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make the first ``TaskService.set_plan`` call sleep past the timeout.

    The hang is inside the verb's own transaction: claim has already
    acquired the FOR UPDATE row lock when set_plan runs, so the sleep holds
    the lock until the transaction commits or rolls back. Only the FIRST
    call sleeps — a retry i_will_plan short-circuits as re-entry and never
    re-runs set_plan, so a per-call counter would never reach a second hang.
    """
    from roboco.services.task import TaskService

    real_set_plan = TaskService.set_plan
    hung = {"done": False}

    async def hung_set_plan(
        self: TaskService, task_id: UUID, plan: str | dict[str, Any]
    ) -> Any:
        if not hung["done"]:
            hung["done"] = True
            await asyncio.sleep(_HANG_SECONDS)
        return await real_set_plan(self, task_id, plan)

    monkeypatch.setattr(TaskService, "set_plan", hung_set_plan)


def test_flow_verb_timeout_releases_row_lock(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix ARMED: a hung verb is cancelled, its lock released, retry proceeds."""
    from roboco.config import settings

    stack = e2e_stack
    company = seed_company(stack)
    main_pm, task_id = _seed_planning_root(stack, company)

    # Server-side verb timeout: 1s. A hung verb is cancelled and its
    # transaction rolled back at this boundary instead of holding the FOR
    # UPDATE lock forever.
    monkeypatch.setattr(settings, "flow_verb_timeout_seconds", _SERVER_TIMEOUT_SECONDS)
    _patch_hang_in_set_plan(monkeypatch)

    # 1. verb-1: claim -> FOR UPDATE lock -> set_plan HANGS. The server
    #    cancels at 1s, get_db rolls back (releasing the lock), and a 504
    #    gateway_timeout envelope comes back within the default client
    #    window (the fix makes the hang bounded; no client-side race).
    env1 = main_pm.flow(
        "i_will_plan",
        task_id=str(task_id),
        plan=_PLAN,
        approach=_APPROACH,
        sub_tasks=_SUB_TASKS,
    )
    expect_error(env1, "gateway_timeout", "verb-1 must return a bounded 504, not hang")

    # 2. verb-2: the task was rolled back to PENDING by verb-1's
    #    cancellation, so claim re-enters and acquires the row (only
    #    possible because verb-1 released the lock). set_plan runs for
    #    real, start commits, and the post-claim tracing gate fires (no
    #    decision note) -> tracing_gap. Reaching the gate is the success
    #    marker: on master the row would still be locked by verb-1's stuck
    #    coroutine and this claim would hang on the FOR UPDATE.
    env2 = main_pm.flow(
        "i_will_plan",
        task_id=str(task_id),
        plan=_PLAN,
        approach=_APPROACH,
        sub_tasks=_SUB_TASKS,
    )
    expect_error(
        env2,
        "tracing_gap",
        "verb-2 reaches the post-claim gate — the row lock was released",
    )


def test_flow_verb_holds_lock_when_timeout_disarmed(
    e2e_stack: E2EStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix DISARMED: with no bounded server timeout, the hung verb holds the
    lock past the client's HTTP timeout — the empirical reproduction of the
    2026-07-07 wedge on the same branch, by turning the fix off.
    """
    from roboco.config import settings
    from roboco.mcp import flow_server

    stack = e2e_stack
    company = seed_company(stack)
    main_pm, task_id = _seed_planning_root(stack, company)

    # Disarm the server-side timeout: 1000s. The hung verb is NOT cancelled
    # by the server, so it holds the FOR UPDATE lock for the full sleep.
    monkeypatch.setattr(
        settings, "flow_verb_timeout_seconds", _DISARMED_SERVER_TIMEOUT_SECONDS
    )
    _patch_hang_in_set_plan(monkeypatch)

    # Prime the per-agent flow_server reload with a no-op verb (i_am_idle
    # touches no task). The reload resets module globals (the env-derived
    # _TIMEOUT), so a pre-call patch would be clobbered; after this call the
    # module is pinned to this agent and the patch below survives.
    # i_will_plan is a default-budget verb (not in SLOW_VERBS), so the
    # client selects _TIMEOUT for it.
    main_pm.flow("i_am_idle")
    monkeypatch.setattr(flow_server, "_TIMEOUT", _MCP_CLIENT_TIMEOUT_SECONDS)

    # verb-1: claim -> FOR UPDATE lock -> set_plan HANGS. The server does
    # not cancel (1000s timeout), so the verb holds the lock past the
    # client's 3s HTTP timeout — the wedge. httpx raises, never returning
    # an envelope.
    verb1_hung = False
    try:
        main_pm.flow(
            "i_will_plan",
            task_id=str(task_id),
            plan=_PLAN,
            approach=_APPROACH,
            sub_tasks=_SUB_TASKS,
        )
    except httpx.TimeoutException:
        verb1_hung = True
    assert verb1_hung, (
        "verb-1 did NOT hang with the server timeout disarmed — the wedge is "
        "not reproduced; either the hang injection broke or the server is "
        "cancelling despite the disarmed timeout"
    )
