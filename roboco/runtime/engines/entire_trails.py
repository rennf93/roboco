"""Engine mixin: the Entire trails outbox drain (family: entire_trails)."""

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


class EntireTrailsEngine(_Base):
    """Mixin holding the Entire trails drain loop."""

    async def _entire_trails_loop(self) -> None:
        """Drain the Entire trails outbox (roboco/services/entire_trails/).

        Dormant unless ``entire_trails_enabled``. Each pass reconciles the
        open trails and runs every due outbox row in order per task tree;
        rows written while the flag was on still drain after it, so turning
        it off stops new trees from being bridged but loses nothing queued.
        """
        if not settings.entire_trails_enabled:
            return
        interval = settings.entire_trails_drain_interval_seconds
        self._record_loop_heartbeat("entire_trails", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_entire_trails_pass()
                self._record_loop_heartbeat("entire_trails", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("entire trails pass failed")

    async def _run_entire_trails_pass(self) -> None:
        """One drain pass; extracted from the loop so it is testable without
        the sleep."""
        from roboco.services.entire_trails.drain import drain_due

        done = await drain_due()
        if done:
            logger.info("entire trails: outbox rows done", count=done)
