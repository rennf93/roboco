"""Auto-extracted engine mixin -- the Decisions trajectory labeler loop.

Grades unlabeled decision_log rows of the trajectory-class pilots against
what the tasks table says happened after each decision (spec 12.1). The
labeler only writes outcome columns on decision_log: it gates nothing,
sends nothing, and spawns nothing, so it rides the master decisions flag
with no separate switch and degrades to a no-op when it is off.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.runtime.orchestrator import (
    logger,
)

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class DecisionsLabelerEngine(_Base):
    """Mixin holding the decisions outcome-labeler loop methods."""

    async def _decisions_labeler_loop(self) -> None:
        """Periodic grading of unlabeled trajectory-class decision rows."""
        if not settings.decisions_enabled:
            return
        interval = settings.decisions_labeler_interval_seconds
        self._record_loop_heartbeat("decisions_labeler", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_decisions_labeler_cycle()
                self._record_loop_heartbeat("decisions_labeler", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("decisions labeler cycle failed")

    async def _run_decisions_labeler_cycle(self) -> None:
        """One grading pass on a background-pool session, then commit."""
        from roboco.db import get_db_context
        from roboco.services.decisions import trajectory

        async with get_db_context(pool="background") as db:
            graded = await trajectory.run_trajectory_pass(db)
            await db.commit()
        if graded:
            logger.info(
                "decisions trajectory labeler graded rows", labeled=graded
            )
