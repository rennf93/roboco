"""Schemas for the video engine's on-demand request + CEO approval surface."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.api.schemas.project_fields import task_project_fields
from roboco.foundation.policy.content import markers
from roboco.foundation.policy.content.markers import MAX_VIDEO_RENDER_ATTEMPTS
from roboco.services.video_post_service import MAX_TIKTOK_CAPTION_CHARS
from roboco.services.x_client import MAX_TWEET_CHARS

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


class VideoRequestBody(BaseModel):
    """The CEO's on-demand video brief, scoped to a specific project."""

    occasion: str = Field(..., min_length=1)
    brief: str = Field(..., min_length=1)
    platforms: list[str] = Field(..., min_length=1)
    project_id: UUID


class VideoRequestResponse(BaseModel):
    """The outcome of an on-demand video request."""

    status: str  # "opened" | "disabled" | "not_opened"
    task_id: str | None = None
    detail: str


class VideoPostResponse(BaseModel):
    """One held video_post draft (rendered clip) awaiting the CEO."""

    task_id: str
    source: str  # "video_post"
    title: str
    status: str
    occasion: str
    script: str
    platforms: list[str]
    x_caption: str | None = None
    tiktok_caption: str | None = None
    reject_reason: str | None = None
    mp4_paths: dict[str, str] = Field(default_factory=dict)
    source_task_id: str | None = None  # the authoring task this draft rendered from
    project_slug: str | None = None
    project_name: str | None = None


class VideoPostApproveRequest(BaseModel):
    """Approve a draft, optionally overwriting one or both captions first."""

    x_caption: str | None = Field(default=None, max_length=MAX_TWEET_CHARS)
    tiktok_caption: str | None = Field(
        default=None, max_length=MAX_TIKTOK_CAPTION_CHARS
    )


class VideoPostExecuteResponse(BaseModel):
    """The outcome of an approve call."""

    status: str
    posted: dict[str, str]
    detail: str


class VideoPostRejectRequest(BaseModel):
    """The CEO's reason for declining a draft."""

    reason: str = Field(min_length=4)


class VideoPostHistoryResponse(BaseModel):
    """One acted-on video_post draft (posted or rejected) — the CEO's
    history view."""

    task_id: str
    source: str  # "video_post"
    title: str
    status: str  # "completed" | "cancelled"
    occasion: str
    script: str
    platforms: list[str]
    x_caption: str | None = None
    tiktok_caption: str | None = None
    reject_reason: str | None = None
    posted: dict[str, str] = Field(default_factory=dict)  # platform -> posted id
    acted_at: datetime
    source_task_id: str | None = None  # the authoring task this draft rendered from
    project_slug: str | None = None
    project_name: str | None = None


class VideoPipelineItemResponse(BaseModel):
    """One in-flight source=video item — the Social page's pipeline-strip
    basis. Spans the authoring task's whole pre-post lifecycle: any
    non-terminal delivery status, then the render loop's retry/failure
    states. composition_id/render_status/render_attempts/render_error live
    in the orchestration_markers.video_draft JSON, not a column."""

    task_id: str
    title: str
    occasion: str
    status: str
    pr_number: int | None = None
    composition_id: str | None = None
    render_status: str | None = None
    render_attempts: int = 0
    max_attempts: int = MAX_VIDEO_RENDER_ATTEMPTS
    render_error: str | None = None
    project_slug: str | None = None
    project_name: str | None = None


class PreviewFrameResponse(BaseModel):
    """One extracted ``request_render`` preview frame — index/timestamp decoded
    server-side from the sidecar's self-describing filename."""

    index: int
    file: str
    timestamp_seconds: float


class VideoPreviewFramesResponse(BaseModel):
    """A video-authoring task's ``request_render`` preview — the CEO's only
    look at the rendered artifact before the post-completion render loop
    produces the real MP4 (an ``awaiting_ceo_approval`` task otherwise has
    nothing to watch). Frames keyed by orientation; an absent/empty key was
    never rendered. Metadata (composition_id, duration_seconds, head_sha,
    dirty, rendered_at) comes from the last ``render_preview`` marker."""

    task_id: str
    composition_id: str | None = None
    duration_seconds: float | None = None
    head_sha: str | None = None
    dirty: bool | None = None
    rendered_at: str | None = None
    frames: dict[str, list[PreviewFrameResponse]] = Field(default_factory=dict)


class TikTokCredentialsStatus(BaseModel):
    """Whether the four OAuth2 secrets are stored. Never the secrets themselves."""

    has_credentials: bool


class TikTokCredentialsSetRequest(BaseModel):
    """Set (or, if all four are empty, clear) the four OAuth2 secrets."""

    client_key: str = Field(default="")
    client_secret: str = Field(default="")
    access_token: str = Field(default="")
    refresh_token: str = Field(default="")


def status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def task_to_video_post_response(task: "TaskTable") -> VideoPostResponse:
    draft = markers.get_video_draft(task) or {}
    project_slug, project_name = task_project_fields(task)
    return VideoPostResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=status_value(task),
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


def task_to_pipeline_item(task: "TaskTable") -> VideoPipelineItemResponse:
    draft = markers.get_video_draft(task) or {}
    project_slug, project_name = task_project_fields(task)
    return VideoPipelineItemResponse(
        task_id=str(task.id),
        title=task.title,
        occasion=str(draft.get("occasion") or ""),
        status=status_value(task),
        pr_number=task.pr_number,
        composition_id=draft.get("composition_id"),
        render_status=draft.get("render_status"),
        render_attempts=int(draft.get("render_attempts", 0)),
        render_error=draft.get("render_error"),
        project_slug=project_slug,
        project_name=project_name,
    )


def posted_ids(draft: dict[str, Any]) -> dict[str, str]:
    """Every ``{platform}_posted_id`` key stamped by approve, keyed by
    platform (e.g. ``{"x": "..", "tiktok": ".."}``)."""
    suffix = "_posted_id"
    return {
        k[: -len(suffix)]: str(v) for k, v in draft.items() if k.endswith(suffix) and v
    }


def task_to_video_post_history_response(task: "TaskTable") -> VideoPostHistoryResponse:
    draft = markers.get_video_draft(task) or {}
    project_slug, project_name = task_project_fields(task)
    return VideoPostHistoryResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=status_value(task),
        occasion=str(draft.get("occasion") or ""),
        script=str(draft.get("script") or ""),
        platforms=list(draft.get("platforms") or []),
        x_caption=draft.get("x_caption"),
        tiktok_caption=draft.get("tiktok_caption"),
        reject_reason=markers.get_video_reject_reason(task),
        posted=posted_ids(draft),
        acted_at=task.updated_at or task.created_at,
        source_task_id=draft.get("source_task_id"),
        project_slug=project_slug,
        project_name=project_name,
    )
