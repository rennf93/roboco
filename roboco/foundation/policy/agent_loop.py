"""Agent-loop foundation.

Owns budget thresholds, loop-detection action, and per-verb circuit breakers.

Replaces:
  - agent_sdk/server.py: 527-534 (hand-coded constants for warn/halt/loop thresholds)
  - runtime/orchestrator.py: 3807 (_PM_RESPAWN_MAX_UNPRODUCTIVE)
  - docker/scripts/post-tool-budget-hook.sh exit-0-on-loop (now exit 1)

The verb-level circuit breaker (VERB_RETRY_LIMITS) is NEW. The gateway had
no per-verb retry cap — dogfooding showed i_am_done retried 5+ times in 2
minutes within the global budget. With the runtime tracker in place,
exceeding VERB_RETRY_LIMITS[verb] attempts in 60s returns
Envelope.circuit_open.

VERB_ABSOLUTE_RETRY_MULTIPLIER is NEW. A 2026-07-08 production loop showed
the 60s sliding window never trips on a slow drip — one rejected i_am_done
every 3-4 minutes empties the window between attempts, so the agent ground
for 30+ minutes without the breaker ever seeing more than 1 attempt at a
time. absolute_retry_limit_for() adds a session-scoped, never-pruned
cumulative cap alongside the window so pacing can't defeat the breaker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from roboco.models.base import TaskType


@dataclass(frozen=True)
class BudgetPolicy:
    """Per-agent runtime budget + loop policy.

    All thresholds are env-overridable at the consumer layer (agent_sdk
    reads ROBOCO_AGENT_TOOL_CALL_WARN etc. and falls back to these
    defaults). The dataclass holds the canonical defaults.
    """

    # 150 interrupted legitimate multi-file dev work mid-task (repeated
    # budget-sweep bounces — each a wasted spawn plus the resumed agent's
    # re-verification turns); 300 keeps the cost guard while clearing a
    # real task's footprint. Warn scales with it.
    tool_call_warn_at: int = 100
    tool_call_halt_at: int = 300
    loop_threshold: int = 3  # same tool+args repeats to trigger
    loop_window: int = 10  # rolling-window size
    loop_action: Literal["warn", "halt"] = "halt"  # NEW: was effectively "warn"
    pm_respawn_max_unproductive: int = 3
    # A same-status respawn that emitted a tracing_gap is normally treated as a
    # rule-following retry and resets the unproductive counter. That reset is
    # bounded: a task whose EVERY respawn trips the SAME tracing_gap is a stuck
    # loop, not progress (e.g. a cold-respawned PM that can never satisfy the
    # unblock journal-decision gate). After this many consecutive resets the
    # gap stops counting as progress and strikes accrue, so the loop gate fires.
    pm_respawn_max_tracing_resets: int = 3
    # A status CHANGE normally resets the unproductive counter (forward
    # progress). That reset is also bounded for REVISITED statuses: an
    # A<->B oscillation (blocked <-> in_progress) changes status on every
    # spawn yet advances nothing — 2026-07-02 a dev looped for two hours
    # (8 spawns) without ever tripping the gate. A status never seen before
    # on this (agent, task) still always fully resets.
    pm_respawn_max_revisit_resets: int = 2
    # A tripped breaker skips spawns until the task's status changes — which
    # can't happen while the spawn is blocked, so a DB-durable counter
    # (migration 051) wedges forever: a deploy that fixes the underlying loop
    # (auth/prompt/schema) can't clear it without manual surgery. Once tripped
    # the breaker freezes last_check at the trip tick and, after this cooldown,
    # lets ONE spawn through. A still-wedged task re-trips after the threshold
    # (bounded re-burn); a fixed one advances and the status-change path fully
    # resets. Restore re-stamps last_check to now, so a freshly restored row
    # still trips immediately (durability preserved) — only a row tripped for
    # longer than the cooldown self-heals.
    pm_respawn_trip_cooldown_seconds: int = 300
    verb_retry_max_per_minute: int = 3  # default cap for verbs not in VERB_RETRY_LIMITS


DEFAULT_BUDGET: BudgetPolicy = BudgetPolicy()


# Per-TaskType default $ budget (USD), consulted ONLY when a task's own
# `budget_usd` is null AND ROBOCO_TASK_BUDGETS_ENABLED is on (see
# roboco/config.py). Relative sizing reflects typical turn/tool-call weight:
# CODE is the most token-heavy (multi-file edits, gate runs, revisions);
# RESEARCH/DESIGN sit mid (web research + note/asset writing); PLANNING is
# lighter prose; DOCUMENTATION and ADMINISTRATIVE are the cheapest, mostly
# read-and-write-notes work.
TASK_TYPE_DEFAULT_BUDGET_USD: dict[TaskType, float] = {
    TaskType.CODE: 5.0,
    TaskType.RESEARCH: 2.0,
    TaskType.DESIGN: 2.0,
    TaskType.PLANNING: 1.5,
    TaskType.DOCUMENTATION: 1.0,
    TaskType.ADMINISTRATIVE: 0.5,
}


def default_budget_usd_for(task_type: TaskType) -> float:
    """The TASK_TYPE_DEFAULT_BUDGET_USD entry for ``task_type``.

    Falls back to the CODE tier (the most generous) for any TaskType this
    dict has not been kept in sync with, so a future TaskType addition fails
    open (a spend cap that's too generous) rather than crashing the sweep.
    """
    return TASK_TYPE_DEFAULT_BUDGET_USD.get(
        task_type, TASK_TYPE_DEFAULT_BUDGET_USD[TaskType.CODE]
    )


def effective_task_budget_usd(task: Any) -> float:
    """A task's effective $ cap: its own ``budget_usd``, or the TaskType
    default when null. The one place this resolution happens — the
    orchestrator's budget sweep and the ``unblock`` re-check both call this
    instead of re-deriving the null-fallback themselves."""
    budget_usd = getattr(task, "budget_usd", None)
    if budget_usd is not None:
        return float(budget_usd)
    return default_budget_usd_for(task.task_type)


# Per-verb retry caps. Verbs that hit a tracing_gap or invalid_state and
# get retried more than this many times in a 60s window will receive a
# circuit_open envelope on the next attempt.
VERB_RETRY_LIMITS: dict[str, int] = {
    # Handoff verbs the 2026-05-10 smoke run showed retry-storming:
    "i_am_done": 3,
    "submit_up": 3,
    "complete": 3,
    "delegate": 3,
    # QA / Doc handoffs. Keys are the public MCP verb names (what the SDK
    # receives via /verb/attempted, derived from the flow URL path).
    # IntentSpec uses `pass_review`/`fail_review` internally; the MCP layer
    # exposes them as `pass`/`fail`. Dogfooding surfaced the mismatch — the
    # old keys here never matched any actual rejection.
    "pass": 3,
    "fail": 3,
    "i_documented": 3,
    # PR open is more network-flake-tolerant:
    "open_pr": 5,
    # Block / escalation paths:
    "i_am_blocked": 3,
    "escalate_up": 3,
    "escalate_to_ceo": 3,
    "unblock": 3,
}


# Verbs that are NOT subject to the per-verb circuit breaker. Read-only or
# discovery operations the agent uses to figure out what's going on.
UNLIMITED_RETRY_VERBS: frozenset[str] = frozenset(
    {
        "give_me_work",
        "triage",
        "triage_all",
        "evidence",
        "i_am_idle",
        "unclaim",
        "resume",
        "claim_review",
        "claim_doc_task",
        "i_will_work_on",
        "i_will_plan",  # claim verbs — agent may retry on transient lock contention
    }
)


def retry_limit_for(verb: str) -> int | None:
    """Return the per-verb retry cap, or None for unlimited.

    Lookup order:
      1. VERB_RETRY_LIMITS[verb] — explicit per-verb cap
      2. UNLIMITED_RETRY_VERBS — None (no cap)
      3. DEFAULT_BUDGET.verb_retry_max_per_minute — fallback for unknown verbs
    """
    if verb in UNLIMITED_RETRY_VERBS:
        return None
    return VERB_RETRY_LIMITS.get(verb, DEFAULT_BUDGET.verb_retry_max_per_minute)


# Multiplier applied to retry_limit_for(verb) for the session-scoped
# ABSOLUTE cap (see module docstring). i_am_done's windowed cap is 3, so its
# absolute cap is 9 total rejections in one container session, regardless
# of how the attempts are spaced.
VERB_ABSOLUTE_RETRY_MULTIPLIER: int = 3


def absolute_retry_limit_for(verb: str) -> int | None:
    """Session-scoped cumulative cap: retry_limit_for(verb) * the multiplier.

    None for verbs retry_limit_for treats as unlimited — a verb exempt from
    the windowed breaker is exempt from the absolute one too.
    """
    limit = retry_limit_for(verb)
    if limit is None:
        return None
    return limit * VERB_ABSOLUTE_RETRY_MULTIPLIER
