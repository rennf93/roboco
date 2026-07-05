"""Schemas for the video engine's on-demand request + CEO approval surface."""

from __future__ import annotations

from pydantic import BaseModel, Field

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


class TikTokCredentialsStatus(BaseModel):
    """Whether the four OAuth2 secrets are stored. Never the secrets themselves."""

    has_credentials: bool


class TikTokCredentialsSetRequest(BaseModel):
    """Set (or, if all four are empty, clear) the four OAuth2 secrets."""

    client_key: str = Field(default="")
    client_secret: str = Field(default="")
    access_token: str = Field(default="")
    refresh_token: str = Field(default="")
