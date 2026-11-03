"""Background-job bodies for the ``i_am_done`` quality gate.

The request that launched the job is long gone by the time these run, so each
opens its own database session. ``run_gate_job`` is the slow part (the gate
itself); ``finish_gate_job`` is the callback that applies the verdict.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import structlog

from roboco.db.base import get_session_factory
from roboco.services.gateway.quality_gate import GateResult

if TYPE_CHECKING:
    from uuid import UUID

logger = structlog.get_logger()


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
            result: GateResult = await choreographer.git.run_pre_submit_quality_gate(
                agent_id, task
            )
        except Exception as exc:
            logger.warning("quality_gate_skipped", task_id=str(task_id), error=str(exc))
            return GateResult(passed=True, skipped=True)
        return result


async def relaunch_lost_gate_jobs() -> int:
    """Restart recovery: relaunch the gate for every ``verifying`` task with no
    live job. The registry is process-local, so a restart drops in-flight jobs,
    and nothing dispatches an author onto ``verifying`` — without this the task
    would sit there forever. Returns how many jobs were launched.
    """
    from roboco.api.deps import get_choreographer
    from roboco.config import settings
    from roboco.services.gate_jobs import get_gate_job_registry

    registry = get_gate_job_registry()
    async with get_session_factory()() as db:
        choreographer = await get_choreographer(db)
        stranded = [
            (t.id, t.assigned_to)
            for t in await choreographer.task.list_verifying_with_assignee()
        ]
    launched = 0
    for task_id, agent_id in stranded:
        if registry.launch(
            task_id,
            partial(run_gate_job, agent_id, task_id),
            partial(finish_gate_job, agent_id, task_id, ""),
            timeout_seconds=settings.gate_job_timeout_seconds,
        ):
            launched += 1
    return launched


async def finish_gate_job(
    agent_id: UUID, task_id: UUID, notes: str, verdict: GateResult
) -> str:
    """Apply the verdict in a fresh committed transaction; returns its outcome."""
    from roboco.api.deps import get_choreographer

    async with get_session_factory()() as db:
        choreographer = await get_choreographer(db)
        outcome = await choreographer.apply_gate_verdict(
            agent_id, task_id, notes, verdict
        )
        await db.commit()
    return outcome
