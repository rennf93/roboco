"""Gateway pre-spawn check and its DB helpers, extracted from orchestrator.

These functions consult the ``trigger_filter`` spawn cooldown and the
``RateLimitStateTracker`` before the orchestrator launches a container. They
talk to the database (``GatewayTriggerTable``) but hold no orchestrator state,
so they live here as module-level coroutines. ``orchestrator.py`` re-imports
``gateway_pre_spawn_check`` for backwards compatibility.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from roboco.config import settings

logger = structlog.get_logger()


async def _count_recent_spawns_for_task(
    db_session: Any,
    task_id: Any,
    cutoff: datetime,
) -> int:
    """Count recent SPAWN decisions for ``task_id`` since ``cutoff``."""
    from sqlalchemy import select

    from roboco.db.tables import GatewayTriggerTable

    result = await db_session.execute(
        select(GatewayTriggerTable).where(
            GatewayTriggerTable.task_id == task_id,
            GatewayTriggerTable.created_at >= cutoff,
            GatewayTriggerTable.decision == "spawn",
        )
    )
    return len(result.scalars().all())


async def _count_recent_spawns_for_role(
    db_session: Any,
    target_role: str,
    cutoff: datetime,
) -> int:
    """Count recent SPAWN decisions for ``target_role`` since ``cutoff``."""
    from sqlalchemy import select

    from roboco.db.tables import GatewayTriggerTable

    result = await db_session.execute(
        select(GatewayTriggerTable).where(
            GatewayTriggerTable.target_role == target_role,
            GatewayTriggerTable.created_at >= cutoff,
            GatewayTriggerTable.decision == "spawn",
        )
    )
    return len(result.scalars().all())


async def _record_trigger_decision(
    db_session: Any,
    task_id: Any,
    trigger_kind: str,
    target_role: str,
    decision: Any,
) -> None:
    """Persist a gateway trigger decision row."""
    from uuid import uuid4 as _uuid4

    from roboco.db.tables import GatewayTriggerTable

    row = GatewayTriggerTable(
        id=_uuid4(),
        trigger_kind=trigger_kind,
        task_id=task_id,
        target_role=target_role,
        decision=decision.outcome.value,
        decision_reason=decision.reason,
    )
    db_session.add(row)
    await db_session.flush()


async def gateway_pre_spawn_check(
    *,
    task_id: str | None,
    trigger_kind: str,
    target_role: str,
    provider: str | None = None,
) -> tuple[str, str]:
    """Consult trigger_filter before spawning a container.

    Returns a ``(outcome, reason)`` tuple where ``outcome`` is one of
    ``"spawn"``, ``"queue"``, or ``"drop"``.

    The trigger_filter spawn cooldown runs unconditionally for every spawn.

    Args:
        provider: Optional provider name (e.g. ``"anthropic"``) for the
            agent about to be spawned.  When given, the
            ``RateLimitStateTracker`` is consulted and a QUEUE decision is
            returned when that provider is currently rate-limited.
    """
    from roboco.db.base import get_session_factory
    from roboco.services.gateway.trigger_filter import (
        Decision,
        SpawnConfig,
        SpawnDecision,
        TriggerContext,
        TriggerKind,
        decide_spawn,
    )

    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.spawn_cooldown_seconds)
    role_cutoff = datetime.now(tz=UTC) - timedelta(seconds=60)

    # When no task_id we cannot query counts; allow (no-task spawns like idle PMs).
    if task_id is None:
        return SpawnDecision.SPAWN, "no task_id — no-task spawn, skip gate"

    try:
        from sqlalchemy import select as _select

        from roboco.db.tables import TaskTable as _TaskTable

        factory = get_session_factory()
        async with factory() as db:
            recent_for_task = await _count_recent_spawns_for_task(db, task_id, cutoff)
            recent_for_role = await _count_recent_spawns_for_role(
                db, target_role, role_cutoff
            )

            # Load the lightweight task proxy needed by is_stale / decide_spawn.
            task_result = await db.execute(
                _select(_TaskTable).where(_TaskTable.id == task_id)
            )
            task_row = task_result.scalars().first()

            if task_row is None:
                return SpawnDecision.SPAWN, "task not found in DB — allow by default"

            # Check provider rate-limit status when a provider is known.
            # Failure is non-fatal — degrade to False (allow spawn) so Redis
            # unavailability never permanently blocks the dispatcher.
            provider_rate_limited = False
            if provider is not None:
                try:
                    from roboco.services.gateway.rate_limit_tracker import (
                        RateLimitStateTracker,
                    )

                    provider_rate_limited = await RateLimitStateTracker(
                        provider
                    ).is_rate_limited()
                except Exception:
                    provider_rate_limited = False

            trigger = TriggerContext(
                kind=TriggerKind(trigger_kind),
                skill=None,
                recent_spawns_for_task=recent_for_task,
                recent_spawns_for_role=recent_for_role,
                provider=provider,
                provider_rate_limited=provider_rate_limited,
            )
            config = SpawnConfig(
                cooldown_seconds=settings.spawn_cooldown_seconds,
                role_rate_per_minute=settings.role_spawn_rate_per_minute,
                claim_stale_seconds=settings.claim_stale_seconds,
            )
            decision: Decision = decide_spawn(
                task=task_row, trigger=trigger, config=config
            )

            await _record_trigger_decision(
                db, task_id, trigger_kind, target_role, decision
            )
            await db.commit()

        return decision.outcome.value, decision.reason

    except Exception as exc:
        # Gateway errors must never block a spawn — degrade gracefully.
        logger.warning(
            "Gateway pre-spawn check failed; defaulting to spawn",
            task_id=task_id,
            trigger_kind=trigger_kind,
            error=str(exc),
        )
        return "spawn", f"gateway error (degraded): {exc}"
