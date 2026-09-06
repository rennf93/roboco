"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: _shared)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import status as http_status

from roboco.foundation import identity as _foundation
from roboco.runtime.orchestrator import (
    AgentState,
    logger,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.maintenance_pause import PauseScope
    from roboco.services.uptime import UptimeLedger


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class SharedEngine(_Base):
    """Mixin holding the "_shared" methods moved out of AgentOrchestrator."""

    # Re-declared (not just inherited from _Base above): mypy's Protocol
    # attribute inference cannot determine the type of an inherited member
    # that a method both reads AND assigns within the same method body
    # (read-then-write in one scope; see _refresh_uptime below) without a
    # bare re-declaration directly on the concrete class.
    if TYPE_CHECKING:
        _uptime: UptimeLedger | None
        _uptime_loaded_at: datetime | None

    def _record_loop_heartbeat(self, name: str, interval: float) -> None:
        self._loop_heartbeats[name] = (time.monotonic(), interval)

    def _fire_audit(
        self,
        *,
        event_type: str,
        agent_slug: str,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        """Emit an agent-lifecycle audit event without blocking the caller.

        Strong-refs the Task so it isn't garbage-collected before it
        commits to `audit_log`. Silently skips if there's no running loop
        (e.g. sync unit tests).
        """
        import contextlib as _ctx

        from roboco.services.audit import get_audit_service

        with _ctx.suppress(RuntimeError):
            bg = asyncio.get_running_loop().create_task(
                get_audit_service().log_agent_event(
                    event_type=event_type,
                    agent_slug=agent_slug,
                    task_id=task_id,
                    details=details or {},
                    severity=severity,
                )
            )
            self._bg_tasks.add(bg)
            bg.add_done_callback(self._bg_tasks.discard)

    def _get_agent_team(self, agent_id: str) -> str | None:
        """Get team from agent_id. Returns None for unknown slugs."""
        try:
            return _foundation.team_for_slug(agent_id).value
        except KeyError:
            return None

    def _schedule_bg(self, coro: "Coroutine[Any, Any, None]") -> None:
        """Fire-and-forget a coroutine, strong-reffed so it isn't GC'd mid-flight.

        Silently no-ops when there's no running loop (sync unit tests); the coro
        is closed to avoid a "never awaited" warning.
        """
        import contextlib as _ctx

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with _ctx.suppress(Exception):
                coro.close()
            return
        bg = loop.create_task(coro)
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._bg_tasks.discard)

    @staticmethod
    def _repo_key(git_url: str) -> str:
        """Normalized repo identity (case/.git/trailing-slash insensitive).

        Delegates to :func:`roboco.utils.converters.repo_key` so the dedupe
        queries and the poll-set collapse share one source of truth (#1267).
        """
        from roboco.utils.converters import repo_key

        return repo_key(git_url)

    @classmethod
    def _projects_one_per_key(
        cls, projects: list[Any], *, key_fn: "Callable[[Any], tuple[Any, ...]]"
    ) -> list[Any]:
        """One canonical project per distinct key (deterministic by slug).

        ``key_fn`` defines what distinguishes a duplicate: repo identity for
        external-PR discovery (one review per PR per repo); ``(repo, workflow)``
        for CI-watch and ``(repo, command)`` for dep-update so a monorepo's
        several cell-projects — each potentially carrying its OWN workflow /
        lockfile command — are each sampled once instead of collapsing to the
        canonical cell's value (the under-count fixed by F115). The first
        project (by slug) per key is the canonical pick; the engine's
        per-``git_url`` fix-task dedup still prevents duplicate fix tasks for the
        same repo. Projects without a git_url are skipped.
        """
        seen: set[tuple[Any, ...]] = set()
        canonical: list[Any] = []
        for project in sorted(projects, key=lambda p: str(getattr(p, "slug", ""))):
            git_url = getattr(project, "git_url", None)
            if not git_url:
                continue
            key = key_fn(project)
            if key in seen:
                continue
            seen.add(key)
            canonical.append(project)
        return canonical

    def _is_agent_active(self, agent_id: str) -> bool:
        """Check if an agent is currently running."""
        if agent_id not in self._instances:
            return False
        return self._instances[agent_id].state == AgentState.ACTIVE

    async def _refresh_uptime(self, db: "AsyncSession") -> "UptimeLedger":
        """Reload the fleet uptime ledger, throttled to UPTIME_REFRESH_SECONDS.

        Fails open: a load error keeps the previous ledger (or an empty one,
        no recorded downtime, on the very first load), so every
        ``_active_age`` caller degrades to plain wall-clock elapsed instead
        of raising. ``UptimeLedger.load`` picks up the marker `start()` set
        via `_mark_running_and_beat`, so a heartbeat gap after this process
        booted is never read as downtime, only a broken audit write - this
        process knows it is alive.
        """
        from roboco.services.uptime import UptimeLedger

        now = datetime.now(UTC)
        loaded_at = self._uptime_loaded_at
        if (
            self._uptime is not None
            and loaded_at is not None
            and (now - loaded_at).total_seconds() < self.UPTIME_REFRESH_SECONDS
        ):
            return self._uptime
        try:
            self._uptime = await UptimeLedger.load(
                db, since=now - timedelta(days=self.UPTIME_LOOKBACK_DAYS)
            )
        except Exception as exc:
            logger.warning(
                "Uptime ledger refresh failed; keeping previous", error=str(exc)
            )
            if self._uptime is None:
                self._uptime = UptimeLedger([], now)
        self._uptime_loaded_at = now
        return self._uptime

    async def _is_paused(self, scope: "PauseScope") -> bool:
        """Open a short-lived session and check ``scope``'s maintenance-pause
        state. ``is_paused`` itself never raises (it fails closed
        internally), so no extra try/except is needed at call sites."""
        from roboco.db.base import get_session_factory
        from roboco.services.maintenance_pause import is_paused

        factory = get_session_factory()
        async with factory() as db:
            return await is_paused(db, scope)

    async def _fetch_subtasks(
        self, client: httpx.AsyncClient, parent_id: str
    ) -> list[dict[str, Any]]:
        """Fetch direct subtasks for a parent task."""
        try:
            resp = await client.get(
                f"{self._api_url}/tasks",
                params={"parent_task_id": parent_id},
            )
            if resp.status_code == http_status.HTTP_200_OK:
                data = resp.json()
                tasks = data.get("tasks", data) if isinstance(data, dict) else data
                return list(tasks) if tasks else []
        except Exception as e:
            logger.warning(
                "Failed to fetch subtasks", parent_id=parent_id, error=str(e)
            )
        return []
