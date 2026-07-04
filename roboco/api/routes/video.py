"""On-demand video-request API — the CEO asks for a bespoke marketing video
outside the release/spotlight triggers. CEO-only. A disabled engine or an
``open_video_task`` no-op (dedup, open cap, unresolvable project) returns a
clear, non-error response rather than fabricating a task."""

from __future__ import annotations

from fastapi import APIRouter

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.video import VideoRequestBody, VideoRequestResponse
from roboco.config import settings
from roboco.security import guard_deco
from roboco.services.video_engine import get_video_engine

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="request an on-demand video")


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
