"""Video engine API — on-demand requests, the held-draft approve/reject/list
queue, and the engine's own poster credentials. CEO-only throughout. Nothing
here posts except an explicit ``approve``; credentials are write-only (the
API never returns plaintext)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.video import (
    TikTokCredentialsSetRequest,
    TikTokCredentialsStatus,
    VideoPipelineItemResponse,
    VideoPostApproveRequest,
    VideoPostExecuteResponse,
    VideoPostHistoryResponse,
    VideoPostRejectRequest,
    VideoPostResponse,
    VideoRequestBody,
    VideoRequestResponse,
)
from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services import minio_client
from roboco.services.project import get_project_service
from roboco.services.task import VIDEO_POST_SOURCE, VIDEO_SOURCE, get_task_service
from roboco.services.tiktok_client import build_tiktok_poster
from roboco.services.tiktok_credentials import (
    TikTokCredentialsValidationError,
    get_tiktok_credentials_service,
)
from roboco.services.video_engine import get_video_engine
from roboco.services.video_post_service import (
    VideoCaptionTooLongError,
    get_video_post_service,
)
from roboco.services.workspace import WorkspaceError, get_workspace_service
from roboco.services.x_credentials import get_x_credentials_service
from roboco.services.x_video_client import build_x_video_poster

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.video_post_service import VideoPostService

router = APIRouter()
tiktok_router = APIRouter()

_VALID_CUTS = ("vertical", "square")


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


@router.post("/request", response_model=VideoRequestResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def request_video(
    data: VideoRequestBody,
    db: DbSession,
    agent: CurrentAgentContext,
) -> VideoRequestResponse:
    """Open a UX/UI video-authoring task for the CEO's on-demand brief,
    scoped to ``data.project_id``.

    Returns ``disabled`` when the video engine is off. 404s when
    ``project_id`` doesn't resolve to a project, or that project hasn't
    opted into the video engine (``video_engine_enabled`` is off on it).
    Returns ``not_opened`` when ``open_video_task`` no-ops for any other
    reason (a duplicate occasion or the open-post cap) — not an error, just
    nothing to do.
    """
    _require_ceo(agent)
    if not settings.video_engine_enabled:
        return VideoRequestResponse(
            status="disabled",
            detail="The video engine is disabled (video_engine_enabled is off).",
        )
    engine = get_video_engine(db)
    project = await engine.resolve_authoring_project(
        project_id=data.project_id, occasion=data.occasion
    )
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project not found or not opted into the video engine",
        )
    task = await engine.open_video_task(
        occasion=data.occasion,
        script=data.brief,
        platforms=data.platforms,
        brief=data.brief,
        project_id=data.project_id,
    )
    if task is None:
        return VideoRequestResponse(
            status="not_opened",
            detail=(
                "No video task was opened (a duplicate occasion or the open-post cap)."
            ),
        )
    await db.commit()
    return VideoRequestResponse(
        status="opened", task_id=str(task.id), detail="Video-authoring task opened."
    )


def _status_value(task: TaskTable) -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: TaskTable) -> VideoPostResponse:
    draft = markers.get_video_draft(task) or {}
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


@router.get("/posts", response_model=list[VideoPostResponse])
async def list_video_posts(
    db: DbSession, agent: CurrentAgentContext
) -> list[VideoPostResponse]:
    """Every held video_post draft (rendered clip) awaiting the CEO."""
    _require_ceo(agent)
    tasks = await get_video_post_service(db).list_held_video_posts()
    return [_to_response(t) for t in tasks]


def _to_pipeline_item(task: TaskTable) -> VideoPipelineItemResponse:
    draft = markers.get_video_draft(task) or {}
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
    )


@router.get("/pipeline", response_model=list[VideoPipelineItemResponse])
async def list_video_pipeline(
    db: DbSession, agent: CurrentAgentContext
) -> list[VideoPipelineItemResponse]:
    """Every in-flight source=video authoring task, from claim through the
    render loop's retry/failure states — the Social page's pipeline-
    visibility strip. A rendered task has already materialized its
    video_post draft (visible instead via ``/posts``) and drops out here."""
    _require_ceo(agent)
    tasks = await get_task_service(db).list_video_pipeline_tasks()
    return [_to_pipeline_item(t) for t in tasks]


@router.post("/pipeline/{task_id}/rerender", response_model=VideoPipelineItemResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
async def rerender_video_task(
    task_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> VideoPipelineItemResponse:
    """Clear a completed video-authoring task's render idempotency keys
    (``render_status``/``render_attempts``/``render_error``) so the next
    render cycle re-picks it up and re-renders it. 404s when there's no such
    completed authoring task with a proposed composition."""
    _require_ceo(agent)
    task = await get_video_engine(db).rerender(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such completed video task with a proposed composition",
        )
    await db.commit()
    return _to_pipeline_item(task)


def _resolve_preview_path(root: Path, file_path: str) -> Path | None:
    """Resolve ``file_path`` against the workspace ``root``, refusing
    anything that escapes it. A leading ``/`` is stripped before joining —
    pathlib's ``/`` operator otherwise lets an absolute right operand
    discard ``root`` entirely — then the joined path must resolve to an
    existing file still under ``root``. The sole confinement check for the
    CEO preview proxy."""
    candidate = (root / file_path.lstrip("/")).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


@router.get("/preview/{task_id}/{file_path:path}", response_model=None)
async def get_video_preview(
    task_id: UUID,
    file_path: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> FileResponse:
    """Serve a video-authoring task's composition HTML + sibling assets
    (kit/public/etc.) straight off its project's merged read-clone — the
    panel's live preview iframe. ``file_path`` is relative to the resolved
    workspace root (e.g. ``motion/compositions/<id>/vertical.html``);
    confined there so it can't traverse out, per ``_resolve_preview_path``.
    CEO-only; the response carries explicit iframe-permitting headers so the
    panel can embed it.
    """
    _require_ceo(agent)
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
    try:
        workspace = await get_workspace_service(db).ensure_read_clone(project.slug)
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    resolved = _resolve_preview_path(workspace.resolve(), file_path)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such preview file"
        )
    return FileResponse(
        resolved,
        headers={
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


def _posted_ids(draft: dict[str, Any]) -> dict[str, str]:
    """Every ``{platform}_posted_id`` key stamped by approve, keyed by
    platform (e.g. ``{"x": "..", "tiktok": ".."}``)."""
    suffix = "_posted_id"
    return {
        k[: -len(suffix)]: str(v) for k, v in draft.items() if k.endswith(suffix) and v
    }


def _to_history_response(task: TaskTable) -> VideoPostHistoryResponse:
    draft = markers.get_video_draft(task) or {}
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
    )


@router.get("/posts/history", response_model=list[VideoPostHistoryResponse])
async def list_video_post_history(
    db: DbSession,
    agent: CurrentAgentContext,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[VideoPostHistoryResponse]:
    """Posted or rejected video_post drafts, newest-acted-first, bounded by
    `limit`."""
    _require_ceo(agent)
    tasks = await get_video_post_service(db).list_video_post_history(limit=limit)
    return [_to_history_response(t) for t in tasks]


@router.get("/posts/{task_id}/media", response_model=None)
async def get_video_post_media(
    task_id: UUID,
    cut: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> StreamingResponse | FileResponse:
    """Serve one rendered MP4 cut of a held video_post draft — the panel
    preview player's ``src``. 404s on a missing task/cut/file; 400 on a
    ``cut`` outside {vertical, square}.

    When MinIO is configured (``minio_endpoint`` set) the route streams the
    object from MinIO via ``minio_client.get_object_stream`` (key = the
    basename of ``mp4_path``). Auth stays end-to-end — ``_require_ceo`` is
    kept, no presigned URLs, no redirect — so the panel's axios-blob flow is
    unchanged (same URL, headers, body — just chunked). Falls back to
    ``FileResponse`` from the local render dir when MinIO is unconfigured OR
    on ``S3Error`` (old renders not yet in MinIO / MinIO down). The local file
    existence + confinement checks stay as defense-in-depth."""
    _require_ceo(agent)
    if cut not in _VALID_CUTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"cut must be one of {_VALID_CUTS!r}",
        )
    task = await get_task_service(db).get(task_id)
    if task is None or task.source != VIDEO_POST_SOURCE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such video draft"
        )
    mp4_path = _resolve_video_cut(task, cut)
    key = mp4_path.name
    if minio_client.get_client() is not None:
        try:
            # Eager probe so a missing object / down MinIO raises HERE — the
            # fallback below catches it. get_object_stream is a lazy generator,
            # so wrapping StreamingResponse(...) alone wouldn't catch S3Error:
            # it fires on the first next(), after Starlette has started
            # streaming and the response is no longer take-back-able.
            await asyncio.to_thread(minio_client.stat_object, key)
        except Exception:
            # NoSuchKey (old render not yet in MinIO) or MinIO down — fall
            # back to the local file, which is the source of truth. Auth
            # already passed; this is purely a storage-read fallback.
            pass
        else:
            return StreamingResponse(
                minio_client.get_object_stream(key),
                media_type="video/mp4",
            )
    return FileResponse(mp4_path, media_type="video/mp4")


@router.post("/posts/{task_id}/approve", response_model=VideoPostExecuteResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def approve_video_post(
    task_id: UUID,
    data: VideoPostApproveRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> VideoPostExecuteResponse:
    """Post the draft's platforms (optionally with the CEO's edited captions).

    Idempotent: approving an already-posted draft returns ``already_posted``
    without calling any poster again.
    """
    _require_ceo(agent)
    svc = await _real_video_post_service(db)
    try:
        result = await svc.approve(
            task_id, x_caption=data.x_caption, tiktok_caption=data.tiktok_caption
        )
    except VideoCaptionTooLongError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such open video draft"
        )
    await db.commit()
    return VideoPostExecuteResponse(
        status=result.status, posted=result.posted, detail=result.detail
    )


@router.post("/posts/{task_id}/reject", response_model=VideoPostResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_video_post(
    task_id: UUID,
    data: VideoPostRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> VideoPostResponse:
    """Decline the draft with a reason; it is cancelled (never posted)."""
    _require_ceo(agent)
    task = await get_video_post_service(db).reject(task_id, data.reason)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such open video draft"
        )
    await db.commit()
    return _to_response(task)


@tiktok_router.get("/credentials", response_model=TikTokCredentialsStatus)
async def get_tiktok_credentials(
    db: DbSession, agent: CurrentAgentContext
) -> TikTokCredentialsStatus:
    """Whether the four TikTok OAuth2 secrets are stored. Never the secrets."""
    _require_ceo(agent)
    has_creds = await get_tiktok_credentials_service(db).has_credentials()
    return TikTokCredentialsStatus(has_credentials=has_creds)


@tiktok_router.post("/credentials", response_model=TikTokCredentialsStatus)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def set_tiktok_credentials(
    data: TikTokCredentialsSetRequest, db: DbSession, agent: CurrentAgentContext
) -> TikTokCredentialsStatus:
    """Set (or, passing all four empty, clear) the four TikTok OAuth2 secrets."""
    _require_ceo(agent)
    svc = get_tiktok_credentials_service(db)
    try:
        has_creds = await svc.set_credentials(
            client_key=data.client_key,
            client_secret=data.client_secret,
            access_token=data.access_token,
            refresh_token=data.refresh_token,
        )
    except TikTokCredentialsValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    await db.commit()
    return TikTokCredentialsStatus(has_credentials=has_creds)
