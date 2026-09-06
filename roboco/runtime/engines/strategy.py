"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: strategy)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.runtime.orchestrator import (
    _STRATEGY_FAIL_CEO_NOTIFY_THRESHOLD,
    _StrategyLoopState,
    logger,
)

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class StrategyEngine(_Base):
    """Mixin holding the "strategy" methods moved out of AgentOrchestrator."""

    async def _strategy_engine_loop(self) -> None:
        """Engine 2: periodically surface goal drift / idle / stranded work.

        Dormant by default — returns immediately unless ``strategy_engine_enabled``
        is set, so it adds zero behaviour to a standard deployment. Notify-only;
        it never spends or builds. A persistently failing cycle surfaces to the
        CEO once per failure episode (#193) instead of silently logging forever.
        """
        if not settings.strategy_engine_enabled:
            return
        state = self._new_strategy_loop_state()
        interval = settings.strategy_engine_interval_seconds
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._strategy_engine_cycle(state)
            except asyncio.CancelledError:
                break

    @staticmethod
    def _new_strategy_loop_state() -> _StrategyLoopState:
        return _StrategyLoopState()

    async def _strategy_engine_cycle(self, state: _StrategyLoopState) -> None:
        """Run one strategy-engine pass; track consecutive failures (#193).

        On success the failure state resets (a fresh failure episode later
        re-notifies). On a non-cancel failure, count it and notify the CEO once
        per episode past ``_STRATEGY_FAIL_CEO_NOTIFY_THRESHOLD``.
        """
        from roboco.db import get_db_context
        from roboco.services.strategy_engine import get_strategy_engine

        try:
            async with get_db_context(pool="background") as db:
                await get_strategy_engine(db).run_cycle()
            state.failures = 0
            state.notified = False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("strategy engine cycle failed")
            state.failures += 1
            if (
                state.failures >= _STRATEGY_FAIL_CEO_NOTIFY_THRESHOLD
                and not state.notified
            ):
                state.notified = True
                await self._notify_strategy_engine_failure(state.failures)

    async def _notify_strategy_engine_failure(self, fail_count: int) -> None:
        """Send one CEO alert that the strategy engine is persistently failing."""
        try:
            from roboco.services.notification import NotificationService

            await NotificationService().send_ack_notification(
                from_agent="system",
                to_agent="ceo",
                body=(
                    "[strategy engine] persistently failing: the last "
                    f"{fail_count} cycles raised and produced no "
                    "observations. Check the orchestrator logs."
                ),
            )
        except Exception:
            logger.exception("strategy engine failure-notify dropped")
