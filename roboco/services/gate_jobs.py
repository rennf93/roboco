"""Background execution of the pre-submit quality gate.

``i_am_done`` used to run the project's fast gate (lint + typecheck in a
throwaway container) inline, which dominated the verb's latency. The gate now
runs as one in-process job per task: the verb returns once the cheap guards
pass, and the job's ``finish`` callback moves the task onward when the gate
verdict lands.

Safety properties the registry owns:

* one live job per task (a repeat launch is refused, never doubled);
* a job that times out or raises yields a FAILED verdict, never a pass, so a
  crashed gate can neither wedge the task nor let it skip the gate;
* a raising ``finish`` is logged and swallowed - the task simply stays where it
  was (before QA).

The registry is process-local by design. A restart loses an in-flight job, and
that is safe: the task never left its pre-QA status. The developer is never
respawned on ``verifying``, so recovery is the API lifespan's periodic sweep
(``relaunch_lost_gate_jobs``, at startup and then every timeout / 4): it
relaunches only a run older than the job timeout plus a grace period, so a job
still live in another process is never doubled.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.quality_gate import GateResult

logger = structlog.get_logger()

GateRunner = Callable[[], Awaitable["GateResult"]]
GateFinisher = Callable[["GateResult"], Awaitable[object]]


def failed_verdict(reason: str) -> GateResult:
    """A red verdict for a gate that could not produce a real result."""
    # Deferred: importing the gateway package at module load would cycle back
    # here through the choreographer.
    from roboco.services.gateway.quality_gate import GateResult

    return GateResult(passed=False, failures=(reason,), output=reason)


class GateJobRegistry:
    """Tracks the live gate job of each task."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, asyncio.Task[None]] = {}

    def is_running(self, task_id: UUID) -> bool:
        job = self._jobs.get(task_id)
        return job is not None and not job.done()

    def launch(
        self,
        task_id: UUID,
        run: GateRunner,
        finish: GateFinisher,
        *,
        timeout_seconds: float,
    ) -> bool:
        """Start the job; False when this task already has one running."""
        if self.is_running(task_id):
            return False
        job = asyncio.create_task(self._execute(task_id, run, finish, timeout_seconds))
        self._jobs[task_id] = job
        job.add_done_callback(lambda done: self._forget(task_id, done))
        return True

    def _forget(self, task_id: UUID, done: asyncio.Task[None]) -> None:
        if self._jobs.get(task_id) is done:
            del self._jobs[task_id]

    @staticmethod
    async def _execute(
        task_id: UUID, run: GateRunner, finish: GateFinisher, timeout_seconds: float
    ) -> None:
        try:
            verdict = await asyncio.wait_for(run(), timeout=timeout_seconds)
        except TimeoutError:
            logger.warning("gate_job_timeout", task_id=str(task_id))
            verdict = failed_verdict(
                f"quality gate timed out after {timeout_seconds:.0f}s"
            )
        except Exception as exc:
            logger.warning("gate_job_crashed", task_id=str(task_id), error=str(exc))
            verdict = failed_verdict(f"quality gate job crashed: {exc}")
        try:
            await finish(verdict)
        except Exception as exc:
            logger.error("gate_job_finish_failed", task_id=str(task_id), error=str(exc))


_registry = GateJobRegistry()


def get_gate_job_registry() -> GateJobRegistry:
    return _registry
