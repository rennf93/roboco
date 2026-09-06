"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: env_sync)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.runtime.orchestrator import (
    logger,
)

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class EnvSyncEngine(_Base):
    """Mixin holding the "env_sync" methods moved out of AgentOrchestrator."""

    async def _env_sync_loop(self) -> None:
        """Env-sync: cascade prod→…→head so dev never falls behind prod.

        Dormant by default — returns immediately unless ``env_sync_enabled``,
        so a standard deployment adds zero behaviour. Each interval it loads the
        opted-in projects (a declared env ladder + a git token), cascades the
        ladder top-down via GitHub's merges API, and opens ONE sync PR + tracked
        task per conflicted repo. Never pushes prod (the cascade's target is a
        non-prod rung by construction), so "only the CEO merges master" holds.
        """
        if not settings.env_sync_enabled:
            return
        interval = settings.env_sync_interval_seconds
        self._record_loop_heartbeat("env_sync", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_env_sync_cycle()
                self._record_loop_heartbeat("env_sync", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("env-sync cycle failed")

    async def _run_env_sync_cycle(self) -> None:
        """One env-sync pass: load the opted-in set, run the engine, commit.

        Extracted from the loop so it is testable without the sleep. Warns when
        env-sync is armed but no project has a declared env ladder — so a
        misconfiguration isn't mistaken for "everything is in sync".
        """
        from roboco.db import get_db_context
        from roboco.services.env_sync_engine import get_env_sync_engine

        async with get_db_context(pool="background") as db:
            projects = await self._load_env_sync_set(db)
            if not projects:
                logger.warning(
                    "env-sync enabled but no project has a declared environment "
                    "ladder + git token — nothing to cascade"
                )
                return
            await get_env_sync_engine(db).run_cycle(projects)
            await db.commit()

    async def _load_env_sync_set(self, db: Any) -> list[Any]:
        """Projects opted into env-sync: a declared env ladder (more than one
        rung, so there are pairs to cascade) + a git_url, one per repo.

        A degenerate ladder (head==prod, one rung) has no pairs to cascade, so it
        is excluded here — there is nothing to sync. Collapse to one canonical
        project per repo: the engine's per-``git_url`` open-task dedup means a
        monorepo's several cell-projects share one cascade anyway.
        """
        from roboco.models.env_branches import ladder_pairs
        from roboco.services.project import get_project_service

        projects = await get_project_service(db).list_all(active_only=True)
        eligible = [
            p for p in projects if getattr(p, "git_url", None) and ladder_pairs(p)
        ]
        return self._projects_one_per_key(
            eligible,
            key_fn=lambda p: (self._repo_key(str(getattr(p, "git_url", "") or "")),),
        )
