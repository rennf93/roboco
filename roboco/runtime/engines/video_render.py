"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: video_render)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.runtime.orchestrator import (
    _MAX_VIDEO_RENDER_ATTEMPTS,
    logger,
)

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class VideoRenderEngine(_Base):
    """Mixin holding the "video_render" methods moved out of AgentOrchestrator."""

    async def _video_render_loop(self) -> None:
        """Video engine: on an interval, render merged compositions to MP4 and
        materialize held video_post drafts.

        Dormant by default — returns immediately unless video_engine_enabled,
        so a standard deployment never scans for completed video tasks or
        reaches the rendering sidecar.
        """
        if not settings.video_engine_enabled:
            return
        interval = settings.video_render_interval_seconds
        self._record_loop_heartbeat("video_render", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_video_render_cycle()
                self._record_loop_heartbeat("video_render", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("video-render cycle failed")

    async def _run_video_render_cycle(self) -> None:
        """One render pass: render every completed authoring task carrying an
        unrendered composition. Testable w/o the sleep.

        Only resolves the id list here; each id is then handed to
        ``_render_video_task``, which owns its own short session boundaries
        (see that method's docstring). This loop itself never holds a
        session across a task's render.

        Skips the whole pass while the ``engines`` maintenance-pause scope is
        active: rendering calls the video-renderer sidecar and materializes
        a new held video_post draft, both autonomous origination work. A
        skipped tick is retried next cycle once resumed, so nothing is lost.
        """
        from roboco.db import get_db_context
        from roboco.services.maintenance_pause import PauseScope, is_paused
        from roboco.services.task import get_task_service

        async with get_db_context(pool="background") as db:
            if await is_paused(db, PauseScope.ENGINES):
                return
            task_ids = [
                t.id for t in await get_task_service(db).list_completed_video_tasks()
            ]
        for task_id in task_ids:
            await self._render_video_task(task_id)

    async def _render_video_task(self, task_id: Any) -> None:
        """Render one completed authoring task's composition, or skip/retry/fail.

        Runs in separate short DB sessions, never one held across the
        render: ``_resolve_video_render`` reads what to render and closes;
        ``_render_both_cuts`` opens its own session only for the project +
        read-clone resolve, closed before the renderer.render() calls (each
        drives the render sidecar over HTTP for tens to hundreds of
        seconds, the worst DB-pool-hold offender by 2-3 orders of
        magnitude when the video engine is armed, 2026-07-29
        pool-exhaustion incident class); the result is then written in a
        FRESH session that RE-FETCHES the task by id rather than reusing
        the resolve-phase reference, since that session has
        ``expire_on_commit=False``, so a stale in-memory attribute would
        otherwise go unnoticed instead of being re-read.

        Skips silently when the dev hasn't called ``propose_video`` yet (no
        ``composition_id``) or the task already reached a terminal render state
        (``rendered`` — idempotent re-run; ``failed`` — retries exhausted). A
        render failure (read-clone not yet synced to the just-merged
        composition, transient sidecar blip, bad response) is caught here so one
        broken task never blocks the cycle, and is RETRIED on later cycles up to
        ``_MAX_VIDEO_RENDER_ATTEMPTS`` before being marked terminally failed.
        """
        resolved = await self._resolve_video_render(task_id)
        if resolved is None:
            return
        draft, composition_id, project_id = resolved
        try:
            mp4_paths = await self._render_both_cuts(
                draft, composition_id, str(task_id), project_id
            )
        except Exception as exc:
            await self._fail_video_render_attempt(task_id, draft, exc)
            return
        await self._finish_video_render(task_id, draft, mp4_paths)

    async def _resolve_video_render(
        self, task_id: Any
    ) -> tuple[dict[str, Any], str, Any] | None:
        """What to render for ONE task, read in a short session released
        before the render below. None when there's nothing to render yet
        (no ``propose_video`` call) or the render already reached a
        terminal state."""
        from roboco.db import get_db_context
        from roboco.foundation.policy.content import markers
        from roboco.services.task import get_task_service

        async with get_db_context(pool="background") as db:
            task = await get_task_service(db).get(task_id)
            if task is None:
                return None
            draft = markers.get_video_draft(task) or {}
            composition_id = draft.get("composition_id")
            if not composition_id or draft.get("render_status") in (
                "rendered",
                "failed",
            ):
                return None
            return draft, composition_id, task.project_id

    async def _finish_video_render(
        self, task_id: Any, draft: dict[str, Any], mp4_paths: dict[str, str]
    ) -> None:
        """Re-fetch the task in a fresh session and materialize the render
        result. Never reuses the resolve-phase task reference across the
        render gap, see ``_render_video_task``'s docstring."""
        from roboco.db import get_db_context
        from roboco.services.task import get_task_service

        async with get_db_context(pool="background") as db:
            task = await get_task_service(db).get(task_id)
            if task is None:
                return
            await self._materialize_video_post(db, task, draft, mp4_paths)

    async def _fail_video_render_attempt(
        self, task_id: Any, draft: dict[str, Any], exc: Exception
    ) -> None:
        """Record one failed render attempt in a fresh session, re-fetching
        the task by id (see ``_render_video_task``'s docstring). Retries on
        later cycles up to ``_MAX_VIDEO_RENDER_ATTEMPTS`` before terminally
        failing the task and alerting the CEO."""
        from roboco.db import get_db_context
        from roboco.foundation.policy.content import markers
        from roboco.services.task import get_task_service

        attempts = int(draft.get("render_attempts", 0)) + 1
        terminal = attempts >= _MAX_VIDEO_RENDER_ATTEMPTS
        payload = {**draft, "render_attempts": attempts, "render_error": str(exc)}
        if terminal:
            payload["render_status"] = "failed"
        title = ""
        async with get_db_context(pool="background") as db:
            task = await get_task_service(db).get(task_id)
            if task is None:
                return
            markers.set_video_draft(task, payload)
            title = task.title
        logger.warning(
            "video-render: render attempt failed",
            task_id=str(task_id),
            attempts=attempts,
            terminal=terminal,
            error=str(exc),
        )
        if terminal:
            await self._notify_video_render_failure(task_id, title, str(exc))

    async def _notify_video_render_failure(
        self, task_id: Any, title: str, last_error: str
    ) -> None:
        """Send one CEO alert that a video render exhausted its retries.

        Best-effort, mirroring ``_notify_strategy_engine_failure`` — a
        notification-send failure must never raise out of the render loop.
        """
        try:
            from roboco.services.notification import NotificationService

            await NotificationService().send_ack_notification(
                from_agent="system",
                to_agent="ceo",
                body=(
                    f"[video engine] render terminally failed for task "
                    f"{title!r} ({_MAX_VIDEO_RENDER_ATTEMPTS} attempts "
                    f"exhausted): {last_error}"
                ),
                task_id=task_id,
            )
        except Exception:
            logger.exception(
                "video-render failure-notify dropped", task_id=str(task_id)
            )

    async def _render_both_cuts(
        self,
        draft: dict[str, Any],
        composition_id: str,
        render_key: str,
        project_id: Any,
    ) -> dict[str, str]:
        """Render the vertical + square cuts from the authoring task's OWN
        project's merged read-clone's motion/ dir; returns {"vertical": path,
        "square": path}. ``render_key`` (the source task id) scopes each
        cut's output path. ``project_id`` is the task's own ``project_id`` —
        never a fixed slug — so a video task authored against any opted-in
        project renders from that project's ``motion/`` dir, not RoboCo's.

        The project + read-clone resolve runs in its own short session,
        closed BEFORE the renderer.render() calls below (see
        ``_render_video_task``'s docstring for why).
        """
        from roboco.db import get_db_context
        from roboco.services.project import get_project_service
        from roboco.services.video_renderer_client import get_video_renderer
        from roboco.services.workspace import WorkspaceError, get_workspace_service

        async with get_db_context(pool="background") as db:
            project = (
                await get_project_service(db).get(project_id) if project_id else None
            )
            if project is None or not project.slug:
                raise WorkspaceError(
                    f"video-render: task's project not resolvable ({project_id})"
                )
            workspace = await get_workspace_service(db).ensure_read_clone(project.slug)
        motion_dir = str(workspace / "motion")
        input_props = draft.get("input_props") or {}
        renderer = get_video_renderer()
        cuts: dict[str, str] = {}
        for orientation in ("vertical", "square"):
            cuts[orientation] = await renderer.render(
                source_dir=motion_dir,
                composition_id=composition_id,
                input_props=input_props,
                orientation=orientation,
                render_key=render_key,
            )
        return cuts

    async def _materialize_video_post(
        self, db: Any, task: Any, draft: dict[str, Any], mp4_paths: dict[str, str]
    ) -> None:
        """Materialize the held video_post draft, then mark the source task
        rendered — the idempotency key the next cycle's scan checks."""
        from roboco.foundation.policy.content import markers
        from roboco.services.video_engine import get_video_engine

        await get_video_engine(db)._originate_video_post(
            source_task=task,
            mp4_paths=mp4_paths,
            captions={
                "x": draft.get("x_caption", ""),
                "tiktok": draft.get("tiktok_caption", ""),
            },
            platforms=draft.get("platforms") or [],
        )
        rendered_payload = {**draft, "render_status": "rendered"}
        rendered_payload.pop("render_error", None)  # clear any prior-attempt error
        markers.set_video_draft(task, rendered_payload)
