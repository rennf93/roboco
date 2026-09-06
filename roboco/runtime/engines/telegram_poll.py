"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: telegram_poll)."""

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


class TelegramPollEngine(_Base):
    """Mixin holding the "telegram_poll" methods moved out of AgentOrchestrator."""

    async def _telegram_poll_loop(self) -> None:
        """Telegram V2: on an interval, long-poll getUpdates and dispatch any
        commands/callbacks.

        Dormant unless BOTH ``telegram_enabled`` AND ``telegram_inbound_enabled``
        are on (the engine's own ``run_cycle`` additionally no-ops without
        stored credentials) — a standard deployment polls nothing. The sleep
        interval is a floor between long-poll re-issues; each ``getUpdates``
        call itself already blocks server-side up to
        ``telegram_poll_timeout_seconds``.
        """
        if not (settings.telegram_enabled and settings.telegram_inbound_enabled):
            return
        interval = settings.telegram_poll_interval_seconds
        self._record_loop_heartbeat("telegram_poll", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_telegram_poll_cycle()
                self._record_loop_heartbeat("telegram_poll", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("telegram poll cycle failed")

    async def _run_telegram_poll_cycle(self) -> None:
        """One Telegram poll pass: run the engine, commit. Testable w/o sleep."""
        from roboco.db import get_db_context
        from roboco.services.telegram_inbound import get_telegram_inbound_engine

        async with get_db_context(pool="background") as db:
            await get_telegram_inbound_engine(db).run_cycle()
            await db.commit()
