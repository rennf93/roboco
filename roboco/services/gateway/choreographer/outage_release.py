"""Release a review claim whose evidence assembly died on a review-path outage.

The detection leaf (``review_path_health``) owns the outage state; this only
reads it. When ``claim_review`` / ``claim_gate_review`` assembles evidence while
a ``review_path_tools`` outage is ACTIVE and the assembly failed, the claim is
released back to its review queue exactly like the stale-claim reaper does
(``TaskService.release_review_claim_for_reaper``) and the caller gets a
retryable envelope naming the outage. With no active outage nothing here fires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from roboco.services.gateway.envelope import Envelope
from roboco.services.review_path_health import (
    OUTAGE_TYPE,
    get_review_path_tracker,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

logger = structlog.get_logger()


async def release_claim_on_outage(
    task_service: Any,
    task_id: UUID,
    briefing: dict[str, Any] | None,
    *,
    verb: str,
) -> Envelope | None:
    """Release the claim and return the retryable envelope, or ``None`` when
    no outage is active (or the release itself could not run)."""
    state = await get_review_path_tracker().state()
    if not state["outage_active"]:
        return None
    try:
        await task_service.release_review_claim_for_reaper(task_id)
        await task_service.session.commit()
    except Exception as exc:
        logger.warning(
            "outage_claim_release_failed", task_id=str(task_id), error=str(exc)
        )
        return None
    logger.info("review_claim_released_on_outage", task_id=str(task_id), verb=verb)
    return _retryable_envelope(task_id, verb, state, briefing)


async def build_or_release[T](
    task_service: Any,
    task_id: UUID,
    briefing: dict[str, Any] | None,
    *,
    verb: str,
    build: Callable[[], Awaitable[T]],
    has_gaps: Callable[[Any], bool],
) -> tuple[T | None, Envelope | None]:
    """Run the evidence build; ``(result, None)`` normally, ``(None, envelope)``
    when it failed (raised, or degraded to gaps) during an active outage. A
    failure with no active outage re-raises / returns the result unchanged."""
    try:
        result = await build()
    except Exception:
        await task_service.session.rollback()
        released = await release_claim_on_outage(
            task_service, task_id, briefing, verb=verb
        )
        if released is None:
            raise
        return None, released
    if has_gaps(result):
        released = await release_claim_on_outage(
            task_service, task_id, briefing, verb=verb
        )
        if released is not None:
            return None, released
    return result, None


def _retryable_envelope(
    task_id: UUID, verb: str, state: dict[str, Any], briefing: dict[str, Any] | None
) -> Envelope:
    since = state["window_start"]
    since_txt = f" (since {since.isoformat()})" if since else ""
    return Envelope(
        error="invalid_state",
        task_id=str(task_id),
        message=(
            f"retryable: the review path is in an active {OUTAGE_TYPE} outage"
            f"{since_txt}; evidence assembly failed, so your claim was released"
            " back to the review queue instead of being consumed."
        ),
        remediate=(
            f"wait for the {OUTAGE_TYPE} outage to recover, then call {verb}"
            f"({task_id}) again; do not issue a verdict from partial evidence"
        ),
        context_briefing=briefing or {},
    )
