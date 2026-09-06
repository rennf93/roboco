"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: dep_update)."""

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


class DepUpdateEngine(_Base):
    """Mixin holding the "dep_update" methods moved out of AgentOrchestrator."""

    async def _dep_update_loop(self) -> None:
        """Dependency-update bot: probe opted-in projects, open update tasks.

        Dormant by default — returns immediately unless ``dep_update_enabled``.
        Each interval (default weekly) it loads projects with a
        ``dep_update_command``, collapses to one per repo, and runs
        ``DepUpdateEngine.run_cycle``; it only OPENS a task and never starts /
        approves / merges / deploys. Separate from the self-heal and CI-watch
        loops.
        """
        if not settings.dep_update_enabled:
            return
        interval = settings.dep_update_interval_seconds
        self._record_loop_heartbeat("dep_update", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_dep_update_cycle()
                self._record_loop_heartbeat("dep_update", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("dep-update cycle failed")

    async def _run_dep_update_cycle(self) -> None:
        """One dep-update pass: load eligible projects, run the engine, commit.

        Extracted from the loop so it is testable without the sleep. Warns when
        the bot is armed but no project has a ``dep_update_command`` set.
        """
        from roboco.db import get_db_context
        from roboco.services.dep_update_engine import get_dep_update_engine

        async with get_db_context(pool="background") as db:
            projects = await self._load_dep_update_set(db)
            if not projects:
                logger.warning(
                    "dep-update enabled but no project has a dep_update_command — "
                    "nothing to probe"
                )
                return
            await get_dep_update_engine(db).run_cycle(projects)
            await db.commit()

    async def _load_dep_update_set(self, db: Any) -> list[Any]:
        """Projects with a ``dep_update_command`` + a git_url, one per
        (repo, command).

        A monorepo's several cell-projects can each carry their OWN
        ``dep_update_command`` (different ecosystems → different lockfiles,
        e.g. ``uv lock --upgrade`` vs ``pnpm update -L``). Collapsing to one
        canonical project per REPO would probe only the canonical cell's
        lockfile and miss the others' drift (the under-count). Collapse to one
        canonical project per (repo, command) instead — every distinct command
        is probed once, and the engine's per-``git_url`` open-task dedup still
        prevents a duplicate update task for the same repo.
        """
        from roboco.services.project import get_project_service

        projects = await get_project_service(db).list_all(active_only=True)
        eligible = [
            p
            for p in projects
            if str(getattr(p, "dep_update_command", None) or "").strip()
            and getattr(p, "git_url", None)
        ]
        return self._projects_one_per_key(
            eligible,
            key_fn=lambda p: (
                self._repo_key(str(getattr(p, "git_url", "") or "")),
                str(getattr(p, "dep_update_command", None) or "").strip(),
            ),
        )
