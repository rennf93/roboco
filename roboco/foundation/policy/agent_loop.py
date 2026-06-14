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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BudgetPolicy:
    """Per-agent runtime budget + loop policy.

    All thresholds are env-overridable at the consumer layer (agent_sdk
    reads ROBOCO_AGENT_TOOL_CALL_WARN etc. and falls back to these
    defaults). The dataclass holds the canonical defaults.
    """

    tool_call_warn_at: int = 50
    tool_call_halt_at: int = 150
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
    verb_retry_max_per_minute: int = 3  # default cap for verbs not in VERB_RETRY_LIMITS


DEFAULT_BUDGET: BudgetPolicy = BudgetPolicy()


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
