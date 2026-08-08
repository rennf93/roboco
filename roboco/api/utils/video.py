"""
Video Route Helpers

Route-glue helpers backing roboco/api/routes/video.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import HTTPException, status

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.project_fields import task_project_fields
from roboco.api.schemas.video import (
    PreviewFrameResponse,
    VideoPipelineItemResponse,
    VideoPostHistoryResponse,
    VideoPostResponse,
)
from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.services.project import get_project_service
from roboco.services.task import VIDEO_SOURCE, get_task_service
from roboco.services.tiktok_client import build_tiktok_poster
from roboco.services.tiktok_credentials import get_tiktok_credentials_service
from roboco.services.video_post_service import get_video_post_service
from roboco.services.x_credentials import get_x_credentials_service
from roboco.services.x_video_client import build_x_video_poster

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.video_post_service import VideoPostService


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the video engine")


def _resolve_video_cut(task: TaskTable, cut: str) -> Path:
    """Resolve the on-disk MP4 path for ``cut`` off the task's held draft, or
    404. The ``is_relative_to`` confinement check stays even though the MinIO
    key is a basename (traversal-proof) — it also guards the ``FileResponse``
    fallback path that reads ``mp4_path`` straight from disk."""
    draft = markers.get_video_draft(task) or {}
    mp4_path = (draft.get("mp4_paths") or {}).get(cut)
    if not mp4_path or not Path(mp4_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No rendered {cut} cut"
        )
    output_dir = Path(settings.video_output_dir).resolve()
    if not Path(mp4_path).resolve().is_relative_to(output_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No rendered {cut} cut"
        )
    return Path(mp4_path)


def _status_value(task: TaskTable) -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: TaskTable) -> VideoPostResponse:
    draft = markers.get_video_draft(task) or {}
    project_slug, project_name = task_project_fields(task)
    return VideoPostResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=_status_value(task),
        occasion=str(draft.get("occasion") or ""),
        script=str(draft.get("script") or ""),
        platforms=list(draft.get("platforms") or []),
        x_caption=draft.get("x_caption"),
        tiktok_caption=draft.get("tiktok_caption"),
        reject_reason=markers.get_video_reject_reason(task),
        mp4_paths=dict(draft.get("mp4_paths") or {}),
        source_task_id=draft.get("source_task_id"),
        project_slug=project_slug,
        project_name=project_name,
    )


async def _real_video_post_service(db: AsyncSession) -> VideoPostService:
    """A VideoPostService wired with the real posters, built from stored
    credentials. Only ``approve`` needs live posters — list/reject never
    call one, so they use the inert Null defaults (``get_video_post_service
    (db)``) instead."""
    x_creds = await get_x_credentials_service(db).get_decrypted()
    x_poster = build_x_video_poster(x_creds, timeout=settings.x_request_timeout_seconds)
    tiktok_creds = await get_tiktok_credentials_service(db).get_decrypted()
    tiktok_poster = build_tiktok_poster(
        tiktok_creds, session=db, timeout=settings.video_request_timeout_seconds
    )
    return get_video_post_service(db, x_poster=x_poster, tiktok_poster=tiktok_poster)


def _to_pipeline_item(task: TaskTable) -> VideoPipelineItemResponse:
    draft = markers.get_video_draft(task) or {}
    project_slug, project_name = task_project_fields(task)
    return VideoPipelineItemResponse(
        task_id=str(task.id),
        title=task.title,
        occasion=str(draft.get("occasion") or ""),
        status=_status_value(task),
        pr_number=task.pr_number,
        composition_id=draft.get("composition_id"),
        render_status=draft.get("render_status"),
        render_attempts=int(draft.get("render_attempts", 0)),
        render_error=draft.get("render_error"),
        project_slug=project_slug,
        project_name=project_name,
    )


def _resolve_preview_path(root: Path, file_path: str) -> Path | None:
    """Resolve ``file_path`` against the workspace ``root``, refusing
    anything that escapes it. A leading ``/`` is stripped before joining —
    pathlib's ``/`` operator otherwise lets an absolute right operand
    discard ``root`` entirely — then the joined path must resolve to an
    existing file still under ``root``. The confinement check shared by the
    CEO composition-HTML proxy and the preview-frame streamer below."""
    candidate = (root / file_path.lstrip("/")).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


# request_render's self-describing filename (video-renderer/render.js):
# frame-<idx>-of-<n>-at-<t>s.png — no manifest needed to recover order/timestamp.
_FRAME_NAME_RE = re.compile(r"^frame-(\d+)-of-\d+-at-([\d.]+)s\.png$")


def _previews_root(project_slug: str, task_id: UUID) -> Path:
    """The container-shared dir request_render extracts frames to — same
    path every agent container mounts (content_actions._render_extract_frames),
    so this resolves identically regardless of who rendered. Resolved (like
    the sibling composition-preview route resolves its workspace root before
    calling ``_resolve_preview_path``) — otherwise a symlinked
    ``workspaces_root`` makes ``candidate.is_relative_to(root)`` mismatch and
    every legit frame 404s."""
    return (
        Path(settings.workspaces_root) / project_slug / ".previews" / task_id.hex[:8]
    ).resolve()


def _list_orientation_frames(dir_path: Path) -> list[PreviewFrameResponse]:
    """Sorted, filename-parsed frames for one orientation dir. Empty when
    that orientation was never rendered (dir missing) — the directory
    listing is authoritative for both orientations at once; the
    render_preview marker only ever reflects the last request_render call's
    single orientation."""
    if not dir_path.is_dir():
        return []
    frames = []
    for p in dir_path.iterdir():
        m = _FRAME_NAME_RE.match(p.name)
        if m:
            frames.append(
                PreviewFrameResponse(
                    index=int(m.group(1)),
                    file=p.name,
                    timestamp_seconds=float(m.group(2)),
                )
            )
    return sorted(frames, key=lambda f: f.index)


async def _resolve_video_task_project(
    task_id: UUID, db: AsyncSession
) -> tuple[TaskTable, ProjectTable]:
    """Task + project resolution shared by the two preview-frame routes below
    — mirrors get_video_preview's inline checks (source=video, has a
    project, project has a slug)."""
    task = await get_task_service(db).get(task_id)
    if task is None or task.source != VIDEO_SOURCE or task.project_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such video task"
        )
    project = await get_project_service(db).get(cast("UUID", task.project_id))
    if project is None or not project.slug:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return task, project


def _posted_ids(draft: dict[str, Any]) -> dict[str, str]:
    """Every ``{platform}_posted_id`` key stamped by approve, keyed by
    platform (e.g. ``{"x": "..", "tiktok": ".."}``)."""
    suffix = "_posted_id"
    return {
        k[: -len(suffix)]: str(v) for k, v in draft.items() if k.endswith(suffix) and v
    }


def _to_history_response(task: TaskTable) -> VideoPostHistoryResponse:
    draft = markers.get_video_draft(task) or {}
    project_slug, project_name = task_project_fields(task)
    return VideoPostHistoryResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=_status_value(task),
        occasion=str(draft.get("occasion") or ""),
        script=str(draft.get("script") or ""),
        platforms=list(draft.get("platforms") or []),
        x_caption=draft.get("x_caption"),
        tiktok_caption=draft.get("tiktok_caption"),
        reject_reason=markers.get_video_reject_reason(task),
        posted=_posted_ids(draft),
        acted_at=task.updated_at or task.created_at,
        source_task_id=draft.get("source_task_id"),
        project_slug=project_slug,
        project_name=project_name,
    )
