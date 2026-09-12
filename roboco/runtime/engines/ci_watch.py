"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: ci_watch)."""

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


class CiWatchEngine(_Base):
    """Mixin holding the "ci_watch" methods moved out of AgentOrchestrator."""

    async def _ci_watch_loop(self) -> None:
        """Multi-repo CI-watch: watch every opted-in project's CI, open fix tasks.

        Dormant by default — returns immediately unless ``ci_watch_enabled``, so
        a standard deployment adds zero behaviour. It generalizes the single-repo
        self-heal loop (which is untouched) to every project with
        ``ci_watch_enabled`` set; like self-heal it only OPENS a fix task and
        never starts / approves / merges / deploys. The per-cycle session commits
        any opened task here.
        """
        if not settings.ci_watch_enabled:
            return
        interval = settings.ci_watch_interval_seconds
        self._record_loop_heartbeat("ci_watch", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_ci_watch_cycle()
                self._record_loop_heartbeat("ci_watch", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("ci-watch cycle failed")

    async def _run_ci_watch_cycle(self) -> None:
        """One CI-watch pass: load the watch set, run the engine, commit.

        Extracted from the loop so it is testable without the sleep. A loud
        warning fires when CI-watch is armed but no project opted in (so a
        misconfiguration isn't mistaken for "all green").
        """
        from roboco.db import get_db_context
        from roboco.services.ci_watch_engine import get_ci_watch_engine

        async with get_db_context(pool="background") as db:
            watch_set = await self._load_ci_watch_set(db)
            if not watch_set:
                logger.warning(
                    "ci-watch enabled but no project has ci_watch_enabled — "
                    "nothing to watch"
                )
                return
            await get_ci_watch_engine(db).run_cycle(watch_set)
            await db.commit()

    async def _load_ci_watch_set(self, db: Any) -> list[Any]:
        """Opted-in projects (``ci_watch_enabled`` + a git_url), one per
        (repo, workflow).

        A monorepo's several cell-projects can each carry their OWN
        ``ci_watch_workflow`` (e.g. a backend CI workflow distinct from the
        frontend's). Collapsing to one canonical project per REPO would watch
        only the canonical cell's workflow and miss a red on the others (the
        under-count). Collapse to one canonical project per (repo, effective
        workflow) instead — every distinct workflow is sampled once, and the
        engine's per-``git_url`` fix-task dedup still prevents a duplicate fix
        task for the same repo. The effective workflow is the project override
        or ``ci_watch_default_workflow`` (matching ``MultiProjectCITelemetrySource``).
        """
        from roboco.services.project import get_project_service

        projects = await get_project_service(db).list_all(active_only=True)
        watched = [
            p
            for p in projects
            if getattr(p, "ci_watch_enabled", False) and getattr(p, "git_url", None)
        ]
        return self._projects_one_per_key(
            watched,
            key_fn=lambda p: (
                self._repo_key(str(getattr(p, "git_url", "") or "")),
                self._effective_ci_watch_workflow(p),
            ),
        )

    @staticmethod
    def _effective_ci_watch_workflow(project: Any) -> str | None:
        """The workflow that will actually be polled for ``project``.

        Mirrors ``MultiProjectCITelemetrySource._sample_for``: the project's
        ``ci_watch_workflow`` override, falling back to the global
        ``ci_watch_default_workflow``. Used as the per-(repo, workflow) collapse
        key so two cells sharing a workflow still collapse to one sample.
        """
        workflow = str(
            getattr(project, "ci_watch_workflow", None)
            or settings.ci_watch_default_workflow
        ).strip()
        return workflow or None
