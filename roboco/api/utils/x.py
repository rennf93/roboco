"""
X (Twitter) Route Helpers

Route-glue helpers backing roboco/api/routes/x.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.api.schemas.project_fields import task_project_fields
from roboco.api.schemas.x import (
    XBarflyRefModel,
    XCampaignRefModel,
    XEditorialRefModel,
    XFeatureRefModel,
    XMentionRefModel,
    XPostHistoryResponse,
    XPostResponse,
)
from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


def require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the X engine queue")


def status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def to_response(task: "TaskTable") -> XPostResponse:
    body = markers.get_x_draft_body(task) or task.description or ""
    mention = markers.get_x_mention_ref(task)
    feature = markers.get_x_feature_ref(task)
    campaign = markers.get_x_campaign_ref(task)
    editorial = markers.get_x_editorial_ref(task)
    barfly = markers.get_barfly_reply_ref(task)
    project_slug, project_name = task_project_fields(task)
    return XPostResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=status_value(task),
        body=body,
        char_count=len(body),
        release_version=markers.get_x_release_version(task),
        mention=XMentionRefModel(**mention) if mention else None,
        feature=XFeatureRefModel(**feature) if feature else None,
        campaign=XCampaignRefModel(**campaign) if campaign else None,
        editorial=XEditorialRefModel(**editorial) if editorial else None,
        barfly=XBarflyRefModel(**barfly) if barfly else None,
        reject_reason=markers.get_x_reject_reason(task),
        project_slug=project_slug,
        project_name=project_name,
    )


def to_history_response(task: "TaskTable") -> XPostHistoryResponse:
    body = markers.get_x_draft_body(task) or task.description or ""
    mention = markers.get_x_mention_ref(task)
    feature = markers.get_x_feature_ref(task)
    campaign = markers.get_x_campaign_ref(task)
    editorial = markers.get_x_editorial_ref(task)
    barfly = markers.get_barfly_reply_ref(task)
    project_slug, project_name = task_project_fields(task)
    return XPostHistoryResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=status_value(task),
        body=body,
        char_count=len(body),
        release_version=markers.get_x_release_version(task),
        mention=XMentionRefModel(**mention) if mention else None,
        feature=XFeatureRefModel(**feature) if feature else None,
        campaign=XCampaignRefModel(**campaign) if campaign else None,
        editorial=XEditorialRefModel(**editorial) if editorial else None,
        barfly=XBarflyRefModel(**barfly) if barfly else None,
        tweet_id=markers.get_x_posted_tweet_id(task),
        reject_reason=markers.get_x_reject_reason(task),
        acted_at=task.updated_at or task.created_at,
        project_slug=project_slug,
        project_name=project_name,
    )
