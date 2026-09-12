"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: x_mentions)."""

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


class XMentionsEngine(_Base):
    """Mixin holding the "x_mentions" methods moved out of AgentOrchestrator."""

    async def _x_mentions_poll_loop(self) -> None:
        """X engine: poll mentions on an interval, hold meaningful ones as draft
        replies.

        Gated by ``x_replies_enabled`` (default off) on top of the engine
        master switch — release posting does not need this loop (those drafts
        are originated event-driven from the release-proposal approve hook), so
        a standard X deployment posts about releases without ever polling
        mentions. It never posts or replies — every draft is held for the CEO.
        """
        if not (settings.x_engine_enabled and settings.x_replies_enabled):
            return
        interval = settings.x_mentions_interval_seconds
        self._record_loop_heartbeat("x_mentions", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_x_mentions_cycle()
                self._record_loop_heartbeat("x_mentions", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("x-mentions poll cycle failed")

    async def _run_x_mentions_cycle(self) -> None:
        """One mentions-poll pass: run the engine, commit. Testable w/o the sleep."""
        from roboco.db import get_db_context
        from roboco.services.x_engine import get_x_engine

        async with get_db_context(pool="background") as db:
            await get_x_engine(db).run_cycle()
            await db.commit()
