"""Background-job bodies for the ``i_am_done`` quality gate.

The request that launched the job is long gone by the time these run, so each
opens its own database session. ``run_gate_job`` is the slow part (the gate
itself); ``finish_gate_job`` is the callback that applies the verdict.
"""

from __future__ import annotations

import asyncio
import time
from functools import partial
from typing import TYPE_CHECKING, Any

import structlog

from roboco.db.base import get_session_factory
from roboco.foundation.policy.content import markers
from roboco.services.gateway.quality_gate import GateResult
from roboco.utils.timing import timed_phase

if TYPE_CHECKING:
    from uuid import UUID

logger = structlog.get_logger()

# Slack past the job timeout before a verifying task's run counts as lost, so a
# job still finishing in another process (blue-green overlap) is never doubled.
_RECOVERY_GRACE_SECONDS = 60.0


async def run_gate_job(agent_id: UUID, task_id: UUID) -> GateResult:
    """Run the project's fast quality gate in the developer's workspace.

    Fail-open on gate-infrastructure errors (missing workspace or toolchain),
    exactly as the inline gate was: only an actual check failure is red. A
    crash outside that call propagates and the registry turns it into a red
    verdict.
    """
    from roboco.api.deps import get_choreographer

    async with get_session_factory()() as db:
        choreographer = await get_choreographer(db)
        task = await choreographer.task.get(task_id)
        if task is None:
            return GateResult(passed=True, skipped=True)
        try:
            with timed_phase("i_am_done_gate", "quality_gate", task_id=str(task_id)):
                result: GateResult = (
                    await choreographer.git.run_pre_submit_quality_gate(agent_id, task)
                )
        except Exception as exc:
            logger.warning("quality_gate_skipped", task_id=str(task_id), error=str(exc))
            return GateResult(passed=True, skipped=True)
        return result


def _run_is_lost(task: Any, now: float, lost_after: float) -> bool:
    """A stamped run younger than ``lost_after`` may still be live in another
    process; an unstamped (legacy) or older one is lost."""
    run = markers.get_gate_run(task)
    started = run.get("started_at") if run else None
    return not isinstance(started, int | float) or now - started >= lost_after


async def relaunch_lost_gate_jobs() -> int:
    """Restart recovery: relaunch the gate for every ``verifying`` task whose
    run is genuinely lost. The registry is process-local, so a restart drops
    in-flight jobs, and nothing dispatches an author onto ``verifying`` —
    without this the task would sit there forever.

    A task is only taken when its last gate run is older than the job timeout
    plus a grace period (a younger one may still be running in another
    process), and the takeover is a fresh token stamped and committed BEFORE
    launching, which also invalidates any stale job's verdict. The developer's
    persisted submit notes are reused. Returns how many jobs were launched.
    """
    from roboco.api.deps import get_choreographer
    from roboco.config import settings
    from roboco.services.gate_jobs import get_gate_job_registry

    registry = get_gate_job_registry()
    lost_after = settings.gate_job_timeout_seconds + _RECOVERY_GRACE_SECONDS
    claimed: list[tuple[UUID, UUID, str, str]] = []
    async with get_session_factory()() as db:
        choreographer = await get_choreographer(db)
        now = time.time()
        for t in await choreographer.task.list_verifying_with_assignee():
            if registry.is_running(t.id) or not _run_is_lost(t, now, lost_after):
                continue
            run = markers.get_gate_run(t)
            notes = str(run.get("notes") or "") if run else ""
            token = await choreographer.task.stamp_gate_run(t.id, notes)
            if token is not None:
                claimed.append((t.id, t.assigned_to, notes, token))
        await db.commit()
    launched = 0
    for task_id, agent_id, notes, token in claimed:
        if registry.launch(
            task_id,
            partial(run_gate_job, agent_id, task_id),
            partial(finish_gate_job, agent_id, task_id, notes, token),
            timeout_seconds=settings.gate_job_timeout_seconds,
        ):
            launched += 1
    return launched


async def run_gate_recovery_loop(interval_seconds: float | None = None) -> None:
    """Sweep for lost gate jobs forever; only cancellation ends it.

    A blue-green overlap means the startup sweep can run while the old color's
    jobs are still live (younger than the age threshold, so skipped), and
    nothing else would relaunch them once that color stops. Sweeping on an
    interval picks them up as soon as they age past the threshold. A failed
    sweep is logged and retried at the next tick.
    """
    from roboco.config import settings

    interval = interval_seconds or settings.gate_job_timeout_seconds / 4
    while True:
        try:
            relaunched = await relaunch_lost_gate_jobs()
            if relaunched:
                logger.info("Relaunched lost quality gate jobs", count=relaunched)
        except Exception as exc:
            logger.warning("Gate job recovery sweep failed", error=str(exc))
        await asyncio.sleep(interval)


async def finish_gate_job(
    agent_id: UUID, task_id: UUID, notes: str, token: str, verdict: GateResult
) -> str:
    """Apply the verdict in a fresh committed transaction; returns its outcome."""
    from roboco.api.deps import get_choreographer

    async with get_session_factory()() as db:
        choreographer = await get_choreographer(db)
        outcome = await choreographer.apply_gate_verdict(
            agent_id, task_id, notes, token, verdict
        )
        await db.commit()
    return outcome
