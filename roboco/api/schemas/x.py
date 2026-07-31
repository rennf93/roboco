"""Schemas for the X (Twitter) engine's CEO surface."""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from roboco.api.schemas.project_fields import task_project_fields
from roboco.foundation.policy.content import markers
from roboco.services.x_client import MAX_TWEET_CHARS

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


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


class XBarflyRefModel(BaseModel):
    """The screened X conversation a held Barfly reply answers."""

    tweet_id: str
    author_handle: str
    text: str
    rationale: str


class XPostResponse(BaseModel):
    """One held draft (release post, mention reply, feature spotlight, War
    Room campaign post, or Barfly conversation reply) awaiting the CEO."""

    task_id: str
    source: str  # x_post | x_reply | x_feature | x_editorial | x_campaign | x_barfly
    title: str
    status: str
    body: str
    char_count: int
    release_version: str | None = None
    mention: XMentionRefModel | None = None
    feature: XFeatureRefModel | None = None
    campaign: XCampaignRefModel | None = None
    barfly: XBarflyRefModel | None = None
    reject_reason: str | None = None
    project_slug: str | None = None
    project_name: str | None = None


def _task_status_value(task: "TaskTable") -> str:
    """Render a task's status as a plain string, enum or raw value alike."""
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def task_to_post_response(task: "TaskTable") -> XPostResponse:
    """Render a held/open X-draft task as the CEO-facing queue entry."""
    body = markers.get_x_draft_body(task) or task.description or ""
    mention = markers.get_x_mention_ref(task)
    feature = markers.get_x_feature_ref(task)
    campaign = markers.get_x_campaign_ref(task)
    barfly = markers.get_barfly_reply_ref(task)
    project_slug, project_name = task_project_fields(task)
    return XPostResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=_task_status_value(task),
        body=body,
        char_count=len(body),
        release_version=markers.get_x_release_version(task),
        mention=XMentionRefModel(**mention) if mention else None,
        feature=XFeatureRefModel(**feature) if feature else None,
        campaign=XCampaignRefModel(**campaign) if campaign else None,
        barfly=XBarflyRefModel(**barfly) if barfly else None,
        reject_reason=markers.get_x_reject_reason(task),
        project_slug=project_slug,
        project_name=project_name,
    )


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
    source: str  # x_post | x_reply | x_feature | x_editorial | x_campaign | x_barfly
    title: str
    status: str  # "completed" | "cancelled"
    body: str
    char_count: int
    release_version: str | None = None
    mention: XMentionRefModel | None = None
    feature: XFeatureRefModel | None = None
    campaign: XCampaignRefModel | None = None
    barfly: XBarflyRefModel | None = None
    tweet_id: str | None = None
    reject_reason: str | None = None
    acted_at: datetime
    project_slug: str | None = None
    project_name: str | None = None


def task_to_post_history_response(task: "TaskTable") -> XPostHistoryResponse:
    """Render a posted/rejected X-draft task as the CEO-facing history entry."""
    body = markers.get_x_draft_body(task) or task.description or ""
    mention = markers.get_x_mention_ref(task)
    feature = markers.get_x_feature_ref(task)
    campaign = markers.get_x_campaign_ref(task)
    barfly = markers.get_barfly_reply_ref(task)
    project_slug, project_name = task_project_fields(task)
    return XPostHistoryResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=_task_status_value(task),
        body=body,
        char_count=len(body),
        release_version=markers.get_x_release_version(task),
        mention=XMentionRefModel(**mention) if mention else None,
        feature=XFeatureRefModel(**feature) if feature else None,
        campaign=XCampaignRefModel(**campaign) if campaign else None,
        barfly=XBarflyRefModel(**barfly) if barfly else None,
        tweet_id=markers.get_x_posted_tweet_id(task),
        reject_reason=markers.get_x_reject_reason(task),
        acted_at=task.updated_at or task.created_at,
        project_slug=project_slug,
        project_name=project_name,
    )


class XCredentialsStatus(BaseModel):
    """Whether the four OAuth 1.0a secrets are stored. Never the secrets themselves."""

    has_credentials: bool


class XCredentialsSetRequest(BaseModel):
    """Set (or, if all four are empty, clear) the four OAuth 1.0a secrets."""

    api_key: str = Field(default="")
    api_secret: str = Field(default="")
    access_token: str = Field(default="")
    access_token_secret: str = Field(default="")
