"""TgCockpitService — the Mini App's one-round-trip "Today" brief.

Composes existing read paths (TaskService listers, DashboardService's agent
snapshot, UsageService's day rollup) into a single phone-sized payload
answering "does anything need me?". Deliberately DB-only and cheap: no live
GitHub calls, no release-readiness snapshot (that path clones + shells out to
git), no orchestrator singleton — the ship-state red/green proxy is the set
of open ci_watch fix tasks, which exists precisely when a watched repo went
red.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import TaskTable
from roboco.foundation.policy.content import markers
from roboco.services.base import BaseService
from roboco.services.dashboard import get_dashboard_service
from roboco.services.task import get_task_service
from roboco.services.usage import get_usage_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.task import TaskService

# Phone-screen caps: the brief shows the top few and a count, never a feed.
_TASK_ITEM_CAP = 5
_WORKING_AGENT_CAP = 8


def _task_item(task: TaskTable) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "title": task.title,
        "status": task.status.value if task.status else "",
        "team": task.team.value if task.team else None,
        "updated_at": task.updated_at or task.created_at,
    }


class TgCockpitService(BaseService):
    """Read-only aggregate behind ``GET /api/telegram/today``."""

    service_name = "tg_cockpit"

    async def today(self) -> dict[str, Any]:
        tasks = get_task_service(self.session)
        needs_you = await self._needs_you(tasks)
        return {
            "needs_you": needs_you,
            "fleet": await self._fleet(),
            "spend": await self._spend(),
            "ship": {
                "version": settings.app_version,
                "open_release_proposal": needs_you["held_drafts"]["release_proposals"]
                > 0,
                "ci_fix_tasks": len(await tasks.list_open_ci_watch_tasks()),
            },
        }

    async def _needs_you(self, tasks: TaskService) -> dict[str, Any]:
        awaiting = await tasks.list_awaiting_ceo_approval()
        blocked = await tasks.list_blocked()
        held = {
            "release_proposals": len(await tasks.list_open_release_proposals()),
            "x_posts": len(await tasks.list_open_x_posts()),
            "video_posts": len(await tasks.list_open_video_post_drafts()),
            "roadmap_items": await self._pending_roadmap_items(tasks),
        }
        return {
            "total": len(awaiting) + len(blocked) + sum(held.values()),
            "awaiting_ceo_count": len(awaiting),
            "awaiting_ceo": [_task_item(t) for t in awaiting[:_TASK_ITEM_CAP]],
            "blocked_count": len(blocked),
            "blocked": [_task_item(t) for t in blocked[:_TASK_ITEM_CAP]],
            "held_drafts": held,
        }

    async def _pending_roadmap_items(self, tasks: TaskService) -> int:
        """Proposed (still-undecided) items across every open roadmap cycle."""
        pending = 0
        for cycle in await tasks.list_open_roadmap_cycles():
            payload = markers.get_roadmap_cycle(cycle) or {}
            pending += sum(
                1
                for item in payload.get("items") or []
                if item.get("status") == "proposed"
            )
        return pending

    async def _fleet(self) -> dict[str, Any]:
        snapshot = await get_dashboard_service(self.session).get_all_agent_status()
        agents: list[dict[str, Any]] = snapshot.get("agents", [])
        working = [a for a in agents if a.get("current_task_id")][:_WORKING_AGENT_CAP]
        titles = await self._task_titles([a["current_task_id"] for a in working])
        return {
            "total": snapshot.get("total", len(agents)),
            "by_status": snapshot.get("by_status", {}),
            "working": [
                {
                    "name": a.get("name", ""),
                    "role": a.get("role", ""),
                    "team": a.get("team"),
                    "task_title": titles.get(str(a["current_task_id"])),
                }
                for a in working
            ],
        }

    async def _task_titles(self, task_ids: list[Any]) -> dict[str, str]:
        if not task_ids:
            return {}
        ids: list[UUID] = [tid for tid in task_ids if tid is not None]
        result = await self.session.execute(
            select(TaskTable.id, TaskTable.title).where(TaskTable.id.in_(ids))
        )
        return {str(row.id): row.title for row in result}

    async def _spend(self) -> dict[str, Any]:
        # Mirrors the CEO dashboard overview: a usage hiccup degrades the
        # brief to zeros instead of failing the whole endpoint.
        try:
            summary = await get_usage_service(self.session).get_today_summary()
            return {
                "tokens_today": int(summary.get("tokens_today", 0)),
                "cost_today_usd": float(summary.get("cost_today_usd", 0.0)),
            }
        except Exception:  # pragma: no cover - defensive degradation
            self.log.warning("today-brief usage summary failed", exc_info=True)
            return {"tokens_today": 0, "cost_today_usd": 0.0}


def get_tg_cockpit_service(session: AsyncSession) -> TgCockpitService:
    """Construct a TgCockpitService bound to ``session``."""
    return TgCockpitService(session)
