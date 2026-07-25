"""Schemas for the X (Twitter) engine's CEO surface."""

from datetime import datetime

from pydantic import BaseModel, Field

from roboco.services.x_client import MAX_TWEET_CHARS


class XMentionRefModel(BaseModel):
    """The mention a held reply answers."""

    id: str
    author_id: str
    text: str


class XFeatureRefModel(BaseModel):
    """The shipped feature a held spotlight draft covers."""

    slug: str
    title: str


class XCampaignRefModel(BaseModel):
    """One post's context within a War Room campaign. ``publish_after`` is
    V1 GUIDANCE only — never a schedule anything acts on."""

    campaign_name: str
    stage_label: str
    publish_after: str
    sequence: int


class XPostResponse(BaseModel):
    """One held draft (release post, mention reply, feature spotlight, or
    War Room campaign post) awaiting the CEO."""

    task_id: str
    source: str  # "x_post" | "x_reply" | "x_feature" | "x_campaign"
    title: str
    status: str
    body: str
    char_count: int
    release_version: str | None = None
    mention: XMentionRefModel | None = None
    feature: XFeatureRefModel | None = None
    campaign: XCampaignRefModel | None = None
    reject_reason: str | None = None
    project_slug: str | None = None
    project_name: str | None = None


class XPostApproveRequest(BaseModel):
    """Approve a draft, optionally overwriting the body first."""

    edited_body: str | None = Field(default=None, max_length=MAX_TWEET_CHARS)


class XPostExecuteResponse(BaseModel):
    """The outcome of an approve call."""

    status: str
    tweet_id: str | None = None
    detail: str


class XPostRejectRequest(BaseModel):
    """The CEO's reason for declining a draft."""

    reason: str = Field(min_length=4)


class XPostHistoryResponse(BaseModel):
    """One acted-on X draft (posted or rejected) — the CEO's history view."""

    task_id: str
    source: str  # "x_post" | "x_reply" | "x_feature" | "x_campaign"
    title: str
    status: str  # "completed" | "cancelled"
    body: str
    char_count: int
    release_version: str | None = None
    mention: XMentionRefModel | None = None
    feature: XFeatureRefModel | None = None
    campaign: XCampaignRefModel | None = None
    tweet_id: str | None = None
    reject_reason: str | None = None
    acted_at: datetime
    project_slug: str | None = None
    project_name: str | None = None


class XCredentialsStatus(BaseModel):
    """Whether the four OAuth 1.0a secrets are stored. Never the secrets themselves."""

    has_credentials: bool


class XCredentialsSetRequest(BaseModel):
    """Set (or, if all four are empty, clear) the four OAuth 1.0a secrets."""

    api_key: str = Field(default="")
    api_secret: str = Field(default="")
    access_token: str = Field(default="")
    access_token_secret: str = Field(default="")
