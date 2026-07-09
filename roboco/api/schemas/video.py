"""Schemas for the video engine's on-demand request + CEO approval surface."""

from datetime import datetime

from pydantic import BaseModel, Field

from roboco.foundation.policy.content.markers import MAX_VIDEO_RENDER_ATTEMPTS
from roboco.services.video_post_service import MAX_TIKTOK_CAPTION_CHARS
from roboco.services.x_client import MAX_TWEET_CHARS


class VideoRequestBody(BaseModel):
    """The CEO's on-demand video brief."""

    occasion: str = Field(..., min_length=1)
    brief: str = Field(..., min_length=1)
    platforms: list[str] = Field(..., min_length=1)


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


class TikTokCredentialsStatus(BaseModel):
    """Whether the four OAuth2 secrets are stored. Never the secrets themselves."""

    has_credentials: bool


class TikTokCredentialsSetRequest(BaseModel):
    """Set (or, if all four are empty, clear) the four OAuth2 secrets."""

    client_key: str = Field(default="")
    client_secret: str = Field(default="")
    access_token: str = Field(default="")
    refresh_token: str = Field(default="")
