"""TgCockpitService — the Mini App's one-round-trip "Today" brief.

Composes existing read paths (TaskService listers, DashboardService's agent
snapshot, raw agent_spawn_sessions rows) into a single phone-sized payload
answering "does anything need me?". Deliberately DB-only and cheap: no live
GitHub calls, no release-readiness snapshot (that path clones + shells out to
git), no orchestrator singleton — the ship-state red/green proxy is the set
of open ci_watch fix tasks, which exists precisely when a watched repo went
red.

"Today" and the trailing 7-day series bucket by calendar day in
``settings.display_timezone`` (default UTC, a no-op for anyone who hasn't set
it), not the server's UTC day — a GMT+2 CEO's evening activity used to land
on the wrong display day. This intentionally bypasses ``UsageService.
get_today_summary`` (which reads the UTC-keyed ``daily_usage_rollups`` table,
unchanged and still used by the main CEO dashboard) and instead reads raw
``agent_spawn_sessions`` rows directly, bucketing them in Python by the
display timezone — the rollup table's key stays UTC-only, but the underlying
timestamped rows let the COCKPIT'S read side be timezone-correct without
touching the rollup pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from roboco.billing.pricing import is_ollama_cloud_model
from roboco.config import settings
from roboco.db.tables import AgentSpawnSessionTable, TaskTable
from roboco.foundation.policy import display_time
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.dashboard import get_dashboard_service
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.task import TaskService

# Phone-screen caps: the brief shows the top few and a count, never a feed.
_TASK_ITEM_CAP = 5
_WORKING_AGENT_CAP = 8
# Trailing window for the hero spend sparkline and the velocity bars.
_SERIES_DAYS = 7


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
            "fleet": await self.fleet(),
            "spend": await self._spend(),
            "velocity": await self._velocity(),
            "ship": {
                "version": settings.app_version,
                "open_release_proposal": needs_you["held_drafts"]["release_proposals"]
                > 0,
                "ci_fix_tasks": len(await tasks.list_open_ci_watch_tasks()),
            },
        }

    def _window_dates(self) -> list[date]:
        """The last ``_SERIES_DAYS`` calendar dates in the display timezone,
        oldest -> today."""
        return display_time.trailing_dates(settings.display_timezone, _SERIES_DAYS)

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

    async def fleet(self) -> dict[str, Any]:
        """Live-agent snapshot with per-agent task titles — shared by the
        Today brief and the bot's ``/agents`` command."""
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
            days = self._window_dates()
            (
                cost_by_day,
                tokens_by_day,
                models_by_day,
            ) = await self._session_metrics_by_day(days)
            series = [round(cost_by_day.get(d, 0.0), 4) for d in days]
            today_cost = series[-1] if series else 0.0
            prior_cost = series[-2] if len(series) >= 2 else 0.0  # noqa: PLR2004
            today_tokens = tokens_by_day.get(days[-1], 0) if days else 0
            today_models = models_by_day.get(days[-1], set()) if days else set()
            # $0 with real tokens spent, on a subscription-billed-but-
            # untracked model (an Ollama Cloud tag with no grounded rate) —
            # "subscription (untracked)", never a bare misleading "$0".
            subscription_billed = (
                today_cost == 0.0
                and today_tokens > 0
                and any(is_ollama_cloud_model(m) for m in today_models)
            )
            return {
                "tokens_today": today_tokens,
                "cost_today_usd": today_cost,
                "subscription_billed": subscription_billed,
                "series": series,
                "delta_pct": _pct_change(today_cost, prior_cost),
            }
        except Exception:  # pragma: no cover - defensive degradation
            self.log.warning("today-brief usage summary failed", exc_info=True)
            return {
                "tokens_today": 0,
                "cost_today_usd": 0.0,
                "subscription_billed": False,
                "series": [0.0] * _SERIES_DAYS,
                "delta_pct": None,
            }

    async def today_spend(self) -> dict[str, Any]:
        """Just today's tokens/cost/subscription_billed, no series — the
        bot's ``/usage`` command doesn't need the 7-day sparkline. Shares the
        Today brief's own ``_spend`` computation so the two can never
        silently disagree on what "today" means."""
        spend = await self._spend()
        return {
            "tokens_today": spend["tokens_today"],
            "cost_today_usd": spend["cost_today_usd"],
            "subscription_billed": spend["subscription_billed"],
        }

    async def _session_metrics_by_day(
        self, days: list[date]
    ) -> tuple[dict[date, float], dict[date, int], dict[date, set[str]]]:
        """One raw ``agent_spawn_sessions`` query spanning the whole window,
        bucketed into display-timezone calendar days: cost, total tokens, and
        the distinct models used per day. Backs both the spend hero (today +
        the 7-day series) and the subscription-billed detection off the SAME
        read, rather than two queries that could silently disagree on what
        "today" means."""
        tz = settings.display_timezone
        start_utc, _ = display_time.day_bounds_utc(tz, days[0])
        _, end_utc = display_time.day_bounds_utc(tz, days[-1])
        result = await self.session.execute(
            select(
                AgentSpawnSessionTable.started_at,
                AgentSpawnSessionTable.estimated_cost_usd,
                AgentSpawnSessionTable.tokens_input,
                AgentSpawnSessionTable.tokens_output,
                AgentSpawnSessionTable.tokens_cache_read,
                AgentSpawnSessionTable.tokens_cache_write,
                AgentSpawnSessionTable.model,
            ).where(
                AgentSpawnSessionTable.started_at >= start_utc,
                AgentSpawnSessionTable.started_at < end_utc,
            )
        )
        cost_by_day: dict[date, float] = {}
        tokens_by_day: dict[date, int] = {}
        models_by_day: dict[date, set[str]] = {}
        for row in result:
            d = display_time.local_date(row.started_at, tz)
            cost_by_day[d] = cost_by_day.get(d, 0.0) + float(
                row.estimated_cost_usd or 0.0
            )
            tokens_by_day[d] = tokens_by_day.get(d, 0) + (
                int(row.tokens_input or 0)
                + int(row.tokens_output or 0)
                + int(row.tokens_cache_read or 0)
                + int(row.tokens_cache_write or 0)
            )
            models_by_day.setdefault(d, set()).add(row.model or "")
        return cost_by_day, tokens_by_day, models_by_day

    async def _velocity(self) -> dict[str, Any]:
        """Per-day completed-task counts over the trailing window (the
        'shipped this week' bars) plus the window total, bucketed by the
        display timezone."""
        try:
            days = self._window_dates()
            tz = settings.display_timezone
            start_utc, _ = display_time.day_bounds_utc(tz, days[0])
            _, end_utc = display_time.day_bounds_utc(tz, days[-1])
            result = await self.session.execute(
                select(TaskTable.completed_at).where(
                    TaskTable.status == TaskStatus.COMPLETED,
                    TaskTable.completed_at.isnot(None),
                    TaskTable.completed_at >= start_utc,
                    TaskTable.completed_at < end_utc,
                )
            )
            by_day: dict[date, int] = {}
            for completed_at in result.scalars():
                if completed_at is None:  # excluded by the WHERE clause already
                    continue
                d = display_time.local_date(completed_at, tz)
                by_day[d] = by_day.get(d, 0) + 1
            series = [by_day.get(d, 0) for d in days]
            return {"series": series, "week_total": sum(series)}
        except Exception:  # pragma: no cover - defensive degradation
            self.log.warning("today-brief velocity failed", exc_info=True)
            return {"series": [0] * _SERIES_DAYS, "week_total": 0}


def _pct_change(current: float, prior: float) -> float | None:
    """Signed percent change vs the prior day; None when there's no prior
    baseline to compare against (a first day of spend shouldn't read as an
    infinite spike)."""
    if prior <= 0:
        return None
    return round((current - prior) / prior * 100, 1)


def get_tg_cockpit_service(session: AsyncSession) -> TgCockpitService:
    """Construct a TgCockpitService bound to ``session``."""
    return TgCockpitService(session)
