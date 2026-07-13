"""VaultJanitor — drift repair + archival + weekly org-report, one sweep.

Projection freshness is best-effort by design (event seams can be missed),
so this closes the loop by periodically re-checking DB state against the
filesystem.

The orchestrator loop (``_vault_janitor_loop``) ticks hourly, but this
service only does real work when a JSON state file under the vault root
(``RoboCo/_meta/.janitor_state.json``) says a day (sweep) or an ISO week
(report) has actually elapsed — restart-proof, unlike a naive
sleep-then-once-a-day loop that never fires again once the orchestrator
restarts more often than daily. Gated on ``obsidian_vault_enabled`` only
(the umbrella flag); the weekly report additionally checks
``vault_report_enabled``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.services.base import BaseService
from roboco.services.project import get_project_service
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.vault_writer import OrgReportData, VaultWriter

# Loop cadence: the orchestrator ticks this often; dueness (below) governs
# whether a tick actually does anything.
JANITOR_LOOP_INTERVAL_SECONDS = 3600
_SWEEP_INTERVAL = timedelta(hours=24)
_SAMPLE_SIZE = 20
_PAGE_SIZE = 100
# Per-cycle work caps (sibling-engine convention): a first-enable / long-
# downtime backlog drains in bounded hourly slices via the resume markers,
# never unbounded in one tick.
_MAX_REPROJECT_PER_CYCLE = 200
_MAX_ARCHIVE_PER_CYCLE = 200
_STATE_RELATIVE_PATH = "RoboCo/_meta/.janitor_state.json"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _state_path() -> Path:
    return Path(settings.vault_path) / _STATE_RELATIVE_PATH


def _load_state() -> dict[str, str]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_state(state: dict[str, str]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _parse_iso(value: str | None) -> datetime | None:
    """None on any malformed value (wrong type included) — a hand-edited or
    corrupted state file degrades to "no state", never a wedged loop."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _iso_week(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class VaultJanitor(BaseService):
    """One state-gated sweep: changed-task re-projection, sample drift
    verification, archival, and (weekly) the org-report note."""

    service_name = "vault_janitor"

    async def run_cycle(self) -> dict[str, int]:
        if not settings.obsidian_vault_enabled:
            return {}
        state = _load_state()
        now = datetime.now(UTC)
        repaired = archived = failed = 0
        if self._sweep_due(state, now):
            repaired, archived, failed, resume = await self._run_sweep(state, now)
            state["last_sweep"] = (resume or now).isoformat()
            self.log.info(
                "vault_drift_repaired", count=repaired, archived=archived, failed=failed
            )
        if settings.vault_report_enabled:
            week = _iso_week(now)
            if state.get("last_report_week") != week:
                await self._run_weekly_report(week)
                state["last_report_week"] = week
        _save_state(state)
        return {"repaired": repaired, "archived": archived, "failed": failed}

    def _sweep_due(self, state: dict[str, str], now: datetime) -> bool:
        last = _parse_iso(state.get("last_sweep"))
        return last is None or (now - last) >= _SWEEP_INTERVAL

    async def _run_sweep(
        self, state: dict[str, str], now: datetime
    ) -> tuple[int, int, int, datetime | None]:
        """(repaired, archived, failed, resume). ``resume`` is None once the
        changed-task backlog fully drained this tick; a capped tick instead
        returns the max touched-stamp actually processed, so ``last_sweep``
        advances only that far and the next hourly tick (immediately due
        again) picks up the tail with no gap."""
        from roboco.services.vault_writer import get_vault_writer

        since = _parse_iso(state.get("last_sweep")) or _EPOCH
        writer = get_vault_writer()
        task_svc = get_task_service(self.session)
        project_svc = get_project_service(self.session)

        repaired, failed, resume = await self._reproject_changed(
            writer, task_svc, project_svc, since
        )
        sample_repaired, sample_failed = await self._verify_sample(
            writer, task_svc, project_svc, since
        )
        archived, archive_failed = await self._archive_pass(
            writer, task_svc, project_svc, now, state
        )
        return (
            repaired + sample_repaired,
            archived,
            failed + sample_failed + archive_failed,
            resume,
        )

    async def _drain_capped(
        self,
        *,
        fetch: Callable[[int, int], Awaitable[list[Any]]],
        reproject: Callable[[Any], Awaitable[Any]],
        stamp: Callable[[Any], datetime],
        cap: int,
        what: str,
    ) -> tuple[int, int, datetime | None, bool]:
        """Capped, per-item-isolated page drain shared by the changed-task
        and archival passes: (processed, failed, last_stamp, drained).

        A raising item is logged and skipped — it re-qualifies whenever it
        changes again, or via the sample verifier — so one bad row never
        wedges the sweep. ``fetch`` must return items in ascending ``stamp``
        order: ``last_stamp`` is then the caller's resume marker when the
        cap cut the drain short (``drained`` False)."""
        processed = failed = offset = 0
        last: datetime | None = None
        drained = False
        while not drained and processed + failed < cap:
            limit = min(_PAGE_SIZE, cap - processed - failed)
            tasks = await fetch(limit, offset)
            drained = len(tasks) < limit
            offset += len(tasks)
            for task in tasks:
                try:
                    await reproject(task)
                    processed += 1
                except Exception as e:
                    failed += 1
                    self.log.warning(
                        "vault janitor item failed (skipped)",
                        what=what,
                        task_id=str(task.id),
                        error=str(e),
                    )
                last = stamp(task)
        return processed, failed, last, drained

    async def _reproject_changed(
        self, writer: VaultWriter, task_svc: Any, project_svc: Any, since: datetime
    ) -> tuple[int, int, datetime | None]:
        from roboco.services.vault_assembly import reproject_task

        processed, failed, last, drained = await self._drain_capped(
            fetch=lambda limit, offset: task_svc.list_updated_since(
                since, limit=limit, offset=offset
            ),
            reproject=lambda t: reproject_task(writer, task_svc, project_svc, t),
            stamp=lambda t: t.updated_at or t.created_at,
            cap=_MAX_REPROJECT_PER_CYCLE,
            what="reproject",
        )
        return processed, failed, None if drained else last

    async def _verify_sample(
        self, writer: VaultWriter, task_svc: Any, project_svc: Any, since: datetime
    ) -> tuple[int, int]:
        repaired = failed = 0
        for task in await task_svc.sample_stale_tasks(since, limit=_SAMPLE_SIZE):
            try:
                repaired += await self._verify_one(writer, task_svc, project_svc, task)
            except Exception as e:
                failed += 1
                self.log.warning(
                    "vault janitor item failed (skipped)",
                    what="verify",
                    task_id=str(task.id),
                    error=str(e),
                )
        return repaired, failed

    async def _verify_one(
        self, writer: VaultWriter, task_svc: Any, project_svc: Any, task: TaskTable
    ) -> int:
        from roboco.services.vault_assembly import reproject_task

        note = writer.find_task_note(str(task.id))
        if note is None:
            await reproject_task(writer, task_svc, project_svc, task)
            return 1
        if writer.task_note_status(note) == _enum_value(task.status):
            return 0
        writer.touch_task_frontmatter(
            task_id=str(task.id),
            status=_enum_value(task.status),
            team=_enum_value(task.team),
            pr_number=task.pr_number,
            pr_url=task.pr_url,
        )
        return 1

    async def _archive_pass(
        self,
        writer: VaultWriter,
        task_svc: Any,
        project_svc: Any,
        now: datetime,
        state: dict[str, str],
    ) -> tuple[int, int]:
        """(archived, failed). The watermark advances to the cutoff only when
        the candidate window fully drained; a capped tick advances it to the
        last processed candidate's terminal-stamp so the tail is picked up
        next tick."""
        if settings.vault_archive_days <= 0:
            return 0, 0
        cutoff = now - timedelta(days=settings.vault_archive_days)
        watermark = _parse_iso(state.get("archive_watermark")) or _EPOCH
        if watermark >= cutoff:
            return 0, 0

        from roboco.services.vault_assembly import reproject_task

        archived, failed, last, drained = await self._drain_capped(
            fetch=lambda limit, offset: task_svc.list_archive_candidates(
                watermark, cutoff, limit=limit, offset=offset
            ),
            reproject=lambda t: reproject_task(writer, task_svc, project_svc, t),
            stamp=lambda t: t.completed_at or t.updated_at or t.created_at,
            cap=_MAX_ARCHIVE_PER_CYCLE,
            what="archive",
        )
        marker = cutoff if drained else (last or watermark)
        state["archive_watermark"] = marker.isoformat()
        return archived, failed

    async def _run_weekly_report(self, week: str) -> None:
        from roboco.services.metrics import get_metrics_service
        from roboco.services.usage import get_usage_service
        from roboco.services.vault_writer import (
            BottleneckRow,
            OrgReportData,
            StageTimingRow,
            TeamReworkRow,
            get_vault_writer,
        )

        metrics_svc = get_metrics_service(self.session)
        velocity = await metrics_svc.get_velocity(days=7)
        stages = await metrics_svc.get_cycle_time_by_stage(days=7)
        bottlenecks = await metrics_svc.get_bottleneck_distribution(days=7)
        rework = await metrics_svc.get_rework_metrics(days=7)
        usage = await get_usage_service(self.session).get_summary(period="7d")

        data = OrgReportData(
            week=week,
            tasks_completed=velocity.tasks_completed,
            tasks_created=velocity.tasks_created,
            completion_rate=velocity.completion_rate,
            avg_cycle_hours=velocity.avg_completion_hours,
            rework_rate=rework.rate,
            rework_cost_usd=rework.rework_cost_usd,
            total_cost_usd=float(usage.get("total_cost_usd", 0.0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            stages=tuple(
                StageTimingRow(s.status, s.avg_seconds, s.sample_size) for s in stages
            ),
            bottlenecks=tuple(
                BottleneckRow(b.status, b.cumulative_seconds, b.pct_of_total)
                for b in bottlenecks.by_stage
            ),
            by_team_rework=tuple(TeamReworkRow(t.team, t.rate) for t in rework.by_team),
        )
        path = get_vault_writer().write_org_report(data)
        await self._notify_weekly_report(week, path, data)

    async def _notify_weekly_report(
        self, week: str, path: Path, data: OrgReportData
    ) -> None:
        """Best-effort: a notification failure never fails the sweep."""
        try:
            from roboco.services.notification import NotificationService

            summary = (
                f"{data.tasks_completed} completed, {data.rework_rate:.0%} rework, "
                f"${data.total_cost_usd:.2f} spent"
            )
            await NotificationService().send_weekly_report_notification(
                week=week, note_path=str(path), summary_line=summary
            )
        except Exception as e:
            self.log.warning(
                "weekly-report notification failed (best-effort)", error=str(e)
            )


def get_vault_janitor(session: AsyncSession) -> VaultJanitor:
    return VaultJanitor(session)
