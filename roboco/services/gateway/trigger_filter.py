"""Stale-trigger cleanup + cooldown decisions.

Decides whether to spawn an agent for a (task, trigger) pair. Reads counts
from caller (recent spawns within window). Pure function — caller queries
the gateway_triggers table and persists the resulting decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from roboco.services.gateway.claimant_lock import is_stale


class TriggerKind(StrEnum):
    A2A = "a2a"
    NOTIFICATION = "notification"
    SCAN = "scan"
    ESCALATION = "escalation"


class SpawnDecision(StrEnum):
    SPAWN = "spawn"
    QUEUE = "queue"
    DROP = "drop"


@dataclass(frozen=True)
class Decision:
    outcome: SpawnDecision
    reason: str


@dataclass(frozen=True)
class SpawnConfig:
    """Numeric tunables for spawn-gating decisions."""

    cooldown_seconds: int
    role_rate_per_minute: int
    claim_stale_seconds: int


@dataclass(frozen=True)
class TriggerContext:
    """Trigger identity and recent-spawn counts passed by the caller."""

    kind: TriggerKind
    skill: str | None
    recent_spawns_for_task: int
    recent_spawns_for_role: int
    # Provider rate-limit fields.  Optional — callers that don't know the
    # provider (e.g. no-task spawns) leave these at their defaults so the
    # gate is a no-op.
    provider: str | None = None
    provider_rate_limited: bool = False


_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "cancelled"})

# A2A code_review only relevant when task is in awaiting_qa or earlier review states
_A2A_CODE_REVIEW_RELEVANT_STATES: frozenset[str] = frozenset(
    {"awaiting_qa", "claimed", "in_progress", "verifying"}
)


def decide_spawn(  # noqa: PLR0911
    *,
    task: Any,
    trigger: TriggerContext,
    config: SpawnConfig,
) -> Decision:
    """Apply five rules in order.

    stale > provider-rate-limit > claimant-lock > task-cooldown > role-rate
    """
    # 1. Stale-trigger cleanup
    if task.status in _TERMINAL_STATUSES:
        return Decision(SpawnDecision.DROP, "task in terminal state — trigger stale")

    if (
        trigger.kind is TriggerKind.A2A
        and trigger.skill == "code_review"
        and task.status not in _A2A_CODE_REVIEW_RELEVANT_STATES
    ):
        return Decision(
            SpawnDecision.DROP,
            f"a2a code_review for task in {task.status} — stale",
        )

    # 2. Provider rate-limit gate
    if trigger.provider_rate_limited:
        return Decision(
            SpawnDecision.QUEUE,
            f"provider {trigger.provider or 'unknown'} rate-limited",
        )

    # 3. Single-claimant invariant
    if task.active_claimant_id is not None and not is_stale(
        task, threshold_seconds=config.claim_stale_seconds
    ):
        return Decision(
            SpawnDecision.QUEUE,
            "task has active claimant with fresh heartbeat",
        )

    # 4. Per-task spawn cooldown
    if trigger.recent_spawns_for_task >= 1:
        return Decision(
            SpawnDecision.QUEUE,
            f"per-task spawn cooldown ({config.cooldown_seconds}s) active",
        )

    # 5. Per-role rate limit
    if trigger.recent_spawns_for_role >= config.role_rate_per_minute:
        return Decision(
            SpawnDecision.QUEUE,
            f"role spawn rate limit ({config.role_rate_per_minute}/min) reached",
        )

    return Decision(SpawnDecision.SPAWN, "all gates clear")
