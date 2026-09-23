"""The Decisions spend guard (spec 3.1, fleet-wide cost model).

Only the OpenRouter fallback tier spends (the Laya sidecar is $0 by
construction), so spend exposure is operational, not financial. Two alert
paths, both in-process (no DB, no Redis) and both fail-open (a notification
failure must never break the decision flow):

1. **402 twice in a rolling hour.** A Decisions call answered HTTP 402
   (payment required / credits exhausted) counts; two within one hour fire
   ONE ack-required CEO notification, then the counter resets. Independent
   of any spend threshold: a dry key alerts even at $0 of recorded spend.
2. **Cumulative daily spend threshold.** Every successful call's
   ``usage.cost`` is summed per tier per UTC calendar day; when the
   OpenRouter total crosses ``settings.decisions_cost_alert_usd`` the CEO
   gets ONE notification per day. The Laya tier's cost is still recorded
   (as 0.0) but can never alert.

State lives in a module-level dict (not bare ``global`` statements) and is
per-process, mirroring the circuit breaker's in-memory doctrine.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic

import structlog

from roboco.config import settings

logger = structlog.get_logger(__name__)

_OPENROUTER_TIER = "openrouter"
_402_WINDOW_SECONDS = 3600.0
_402_ALERT_COUNT = 2

# In-process, per-process alert state. A dict (not bare globals) to keep
# mutation explicit and the reset test hook trivial.
_STATE: dict = {
    # tier -> list of time.monotonic() stamps of recent 402 responses.
    "402_timestamps": {},
    # tier -> {utc_date_iso: cumulative spend}.
    "spend_by_day": {},
    # tier -> utc_date_iso of the day we last fired the spend alert.
    "spend_alerted_day": {},
}

# Fire-and-forget alert tasks, retained so they are not garbage-collected
# mid-send and so tests can drain them deterministically.
_pending_alert_tasks: set[asyncio.Task] = set()


def record_402(tier: str) -> None:
    """Count one HTTP 402 from a Decisions call (spec 3.1). Two within a
    rolling hour fire ONE CEO notification, then the counter resets."""
    now = monotonic()
    timestamps = _STATE["402_timestamps"].setdefault(tier, [])
    timestamps[:] = [t for t in timestamps if now - t < _402_WINDOW_SECONDS]
    timestamps.append(now)
    if len(timestamps) < _402_ALERT_COUNT:
        return
    timestamps.clear()
    logger.warning(
        "decisions 402 twice in an hour; alerting CEO",
        tier=tier,
        window_seconds=_402_WINDOW_SECONDS,
    )
    _schedule_alert(
        subject="Decisions: OpenRouter fallback returned 402 twice in an hour",
        body=(
            "The Decisions service's OpenRouter fallback tier returned HTTP "
            "402 (insufficient credits) twice within one hour, so the key "
            "has likely run dry. Decisions fail open to the self-hosted "
            "sidecar (or the pre-Decisions floor) with no behavior cliff, "
            "but the fallback tier is not spending until credits are "
            "restored. Fix: top up OpenRouter credits or turn the fallback "
            "off on the feature-flag settings. This alert fires at most "
            "once per rolling hour."
        ),
        tier=tier,
    )


def record_spend(tier: str, cost: float | None) -> None:
    """Record one successful Decisions call's ``usage.cost`` per tier per
    UTC calendar day and alert once per day when the OpenRouter cumulative
    total crosses ``decisions_cost_alert_usd``. The Laya tier is $0 by
    construction: recording it is free, alerting on it is impossible."""
    spend = float(cost or 0.0)
    today = datetime.now(UTC).date().isoformat()
    per_day = _STATE["spend_by_day"].setdefault(tier, {})
    per_day[today] = per_day.get(today, 0.0) + spend
    if tier != _OPENROUTER_TIER:
        return
    threshold = settings.decisions_cost_alert_usd
    if per_day[today] <= threshold:
        return
    if _STATE["spend_alerted_day"].get(tier) == today:
        return
    _STATE["spend_alerted_day"][tier] = today
    logger.warning(
        "decisions daily fallback spend crossed the threshold; alerting CEO",
        tier=tier,
        day=today,
        spend_usd=per_day[today],
        threshold_usd=threshold,
    )
    _schedule_alert(
        subject="Decisions: daily OpenRouter fallback spend crossed the threshold",
        body=(
            f"The Decisions service's OpenRouter fallback tier has spent "
            f"${per_day[today]:.4f} today (UTC), crossing the configured "
            f"alert threshold of ${threshold:.2f}. Every call is capped "
            f"client-side, so this is a volume signal, not a runaway bill. "
            f"This alert fires at most once per UTC day. Fix: review the "
            f"decisions usage on the OpenRouter dashboard and lower the "
            f"activity or turn the fallback tier off if unintended."
        ),
        tier=tier,
        day=today,
        spend_usd=per_day[today],
        threshold_usd=threshold,
    )


def _schedule_alert(*, subject: str, body: str, **context: object) -> None:
    """Fire the CEO notification without blocking the decision path. Called
    from sync client code inside a running loop; the task is tracked so it
    survives and so tests can drain it."""
    try:
        task = asyncio.get_running_loop().create_task(
            _send_ceo_alert(subject, body, **context)
        )
    except RuntimeError:
        # No running loop (nothing in production hits this: both call sites
        # run inside the orchestrator's loop). The alert state is already
        # recorded; never retry, stay fail-open.
        logger.warning(
            "decisions spend alert dropped: no running event loop",
            subject=subject,
        )
        return
    _pending_alert_tasks.add(task)
    task.add_done_callback(_pending_alert_tasks.discard)


async def _send_ceo_alert(subject: str, body: str, **context: object) -> None:
    """CEO-facing, ack-required, fail-open (spec 3.1): a notification
    failure must never break the decision flow. Opens its own session, the
    client call sites hold none."""
    from roboco.db.base import get_session_factory
    from roboco.models.base import NotificationPriority, NotificationType
    from roboco.models.notification import CreateNotificationParams
    from roboco.services.notification import NotificationService

    try:
        async with get_session_factory()() as session:
            await NotificationService(session)._create_notification(
                CreateNotificationParams(
                    notification_type=NotificationType.ALERT,
                    priority=NotificationPriority.HIGH,
                    from_agent="system",
                    to_agents=["ceo"],
                    subject=subject,
                    body=body,
                    requires_ack=True,
                    bypass_purpose_dedup=True,
                )
            )
        logger.warning("decisions spend alert sent", subject=subject, **context)
    except Exception as exc:
        logger.warning(
            "failed to send the decisions spend alert",
            subject=subject,
            error=str(exc),
        )


async def drain_pending_alerts() -> None:
    """Test hook: await the fire-and-forget alert tasks so a test observes
    the notifications landing."""
    if _pending_alert_tasks:
        await asyncio.gather(*_pending_alert_tasks)


def reset_spend_guard() -> None:
    """Test hook: clear all in-memory alert state."""
    _STATE["402_timestamps"].clear()
    _STATE["spend_by_day"].clear()
    _STATE["spend_alerted_day"].clear()
