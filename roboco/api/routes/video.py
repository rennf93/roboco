"""Video engine API — on-demand requests, the held-draft approve/reject/list
queue, and the engine's own poster credentials. CEO-only throughout. Nothing
here posts except an explicit ``approve``; credentials are write-only (the
API never returns plaintext)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.video import (
    TikTokCredentialsSetRequest,
    TikTokCredentialsStatus,
    VideoPostApproveRequest,
    VideoPostExecuteResponse,
    VideoPostRejectRequest,
    VideoPostResponse,
    VideoRequestBody,
    VideoRequestResponse,
)
from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services.task import VIDEO_POST_SOURCE, get_task_service
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
    """Open a UX/UI video-authoring task for the CEO's on-demand brief.

    Returns ``disabled`` when the video engine is off, and ``not_opened``
    when ``open_video_task`` no-ops (a duplicate occasion, the open cap, or
    an unresolvable project) — neither is an error, just nothing to do.
    """
    _require_ceo(agent)
    if not settings.video_engine_enabled:
        return VideoRequestResponse(
            status="disabled",
            detail="The video engine is disabled (video_engine_enabled is off).",
        )
    task = await get_video_engine(db).open_video_task(
        occasion=data.occasion,
        script=data.brief,
        platforms=data.platforms,
        brief=data.brief,
    )
    if task is None:
        return VideoRequestResponse(
            status="not_opened",
            detail=(
                "No video task was opened (a duplicate occasion, the open-post"
                " cap, or the project isn't resolvable)."
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


@router.get("/posts/{task_id}/media")
async def get_video_post_media(
    task_id: UUID,
    cut: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> FileResponse:
    """Serve one rendered MP4 cut of a held video_post draft — the panel
    preview player's ``src``. 404s on a missing task/cut/file; 400 on a
    ``cut`` outside {vertical, square}."""
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
    draft = markers.get_video_draft(task) or {}
    mp4_path = (draft.get("mp4_paths") or {}).get(cut)
    if not mp4_path or not Path(mp4_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No rendered {cut} cut"
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
