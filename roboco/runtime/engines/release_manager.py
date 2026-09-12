"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: release_manager)."""

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


class ReleaseManagerEngine(_Base):
    """Mixin holding the "release_manager" methods moved out of AgentOrchestrator."""

    async def _release_manager_loop(self) -> None:
        """Gated release manager: at a logical point, propose a CEO-gated release.

        Dormant by default — returns immediately unless ``release_manager_enabled``,
        so a standard deployment adds zero behaviour. Each interval it runs the
        deterministic readiness sweep and originates at most one HELD proposal for
        the CEO; it NEVER publishes, merges, or deploys. The per-cycle session
        commits any opened proposal here.
        """
        if not settings.release_manager_enabled:
            return
        interval = settings.release_manager_interval_seconds
        self._record_loop_heartbeat("release_manager", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_release_manager_cycle()
                self._record_loop_heartbeat("release_manager", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("release-manager cycle failed")

    async def _run_release_manager_cycle(self) -> None:
        """One release-manager pass: run the engine, commit. Testable w/o the sleep."""
        from roboco.db import get_db_context
        from roboco.services.release_manager_engine import get_release_manager_engine

        async with get_db_context(pool="background") as db:
            await get_release_manager_engine(db).run_cycle()
            await db.commit()

    async def _self_heal_loop(self) -> None:
        """Engine 4: watch RoboCo's OWN CI, surface regressions, open fix tasks.

        Dormant by default — returns immediately unless ``self_heal_enabled``, so
        a standard deployment makes no CI call and adds zero behaviour. It only
        NOTIFIES the CEO and (behind ``self_heal_originate_enabled``) opens a
        PENDING fix task into RoboCo's own lifecycle; it never starts, merges, or
        deploys. The per-cycle session commits any opened task here.
        """
        if not settings.self_heal_enabled:
            return
        from roboco.db import get_db_context
        from roboco.services.self_heal_engine import get_self_heal_engine

        # Operability: self-heal is armed but has no target → it will silently
        # no-op every cycle. Say so once at startup so a misconfiguration (unset
        # or wrong ROBOCO_SELF_HEAL_PROJECT_SLUG) isn't mistaken for "all green".
        if not settings.self_heal_project_slug.strip():
            logger.warning(
                "self-heal enabled but self_heal_project_slug is unset — the loop "
                "will not detect anything until the target project is configured"
            )

        interval = settings.self_heal_interval_seconds
        self._record_loop_heartbeat("self_heal", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                async with get_db_context(pool="background") as db:
                    await get_self_heal_engine(db).run_cycle()
                    await db.commit()
                self._record_loop_heartbeat("self_heal", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("self-heal cycle failed")
