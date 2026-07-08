"""X (Twitter) engine API — the CEO approves/rejects held drafts and manages
credentials. CEO-only throughout. Nothing here posts except an explicit
``approve``; credentials are write-only (the API never returns plaintext)."""

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.api.schemas.x import (
    XCredentialsSetRequest,
    XCredentialsStatus,
    XFeatureRefModel,
    XMentionRefModel,
    XPostApproveRequest,
    XPostExecuteResponse,
    XPostHistoryResponse,
    XPostRejectRequest,
    XPostResponse,
)
from roboco.foundation.policy.content import markers
from roboco.security import guard_deco
from roboco.services.x_credentials import (
    XCredentialsValidationError,
    get_x_credentials_service,
)
from roboco.services.x_post_service import XPostBodyTooLongError, get_x_post_service

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on the X engine queue")


def _status_value(task: "TaskTable") -> str:
    raw = task.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _to_response(task: "TaskTable") -> XPostResponse:
    body = markers.get_x_draft_body(task) or task.description or ""
    mention = markers.get_x_mention_ref(task)
    feature = markers.get_x_feature_ref(task)
    return XPostResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=_status_value(task),
        body=body,
        char_count=len(body),
        release_version=markers.get_x_release_version(task),
        mention=XMentionRefModel(**mention) if mention else None,
        feature=XFeatureRefModel(**feature) if feature else None,
        reject_reason=markers.get_x_reject_reason(task),
    )


@router.get("/posts", response_model=list[XPostResponse])
async def list_x_posts(
    db: DbSession, agent: CurrentAgentContext
) -> list[XPostResponse]:
    """Every held X draft (release posts + mention replies) awaiting the CEO."""
    _require_ceo(agent)
    tasks = await get_x_post_service(db).list_open_posts()
    return [_to_response(t) for t in tasks]


def _to_history_response(task: "TaskTable") -> XPostHistoryResponse:
    body = markers.get_x_draft_body(task) or task.description or ""
    mention = markers.get_x_mention_ref(task)
    feature = markers.get_x_feature_ref(task)
    return XPostHistoryResponse(
        task_id=str(task.id),
        source=task.source,
        title=task.title,
        status=_status_value(task),
        body=body,
        char_count=len(body),
        release_version=markers.get_x_release_version(task),
        mention=XMentionRefModel(**mention) if mention else None,
        feature=XFeatureRefModel(**feature) if feature else None,
        tweet_id=markers.get_x_posted_tweet_id(task),
        reject_reason=markers.get_x_reject_reason(task),
        acted_at=task.updated_at or task.created_at,
    )


@router.get("/posts/history", response_model=list[XPostHistoryResponse])
async def list_x_post_history(
    db: DbSession,
    agent: CurrentAgentContext,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[XPostHistoryResponse]:
    """Posted or rejected X drafts, newest-acted-first, bounded by `limit`."""
    _require_ceo(agent)
    tasks = await get_x_post_service(db).list_post_history(limit=limit)
    return [_to_history_response(t) for t in tasks]


@router.post("/posts/{task_id}/approve", response_model=XPostExecuteResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def approve_x_post(
    task_id: UUID,
    data: XPostApproveRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> XPostExecuteResponse:
    """Post the draft to X (optionally with the CEO's edited body).

    Idempotent: approving an already-posted draft returns ``already_posted``
    without calling the X API again.
    """
    _require_ceo(agent)
    svc = get_x_post_service(db)
    try:
        result = await svc.approve(task_id, data.edited_body)
    except XPostBodyTooLongError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such open X draft"
        )
    await db.commit()
    return XPostExecuteResponse(
        status=result.status, tweet_id=result.tweet_id, detail=result.detail
    )


@router.post("/posts/{task_id}/reject", response_model=XPostResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def reject_x_post(
    task_id: UUID,
    data: XPostRejectRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> XPostResponse:
    """Decline the draft with a reason; it is cancelled (never posted)."""
    _require_ceo(agent)
    svc = get_x_post_service(db)
    task = await svc.reject(task_id, data.reason)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such open X draft"
        )
    await db.commit()
    return _to_response(task)


@router.get("/credentials", response_model=XCredentialsStatus)
async def get_x_credentials(
    db: DbSession, agent: CurrentAgentContext
) -> XCredentialsStatus:
    """Whether the four OAuth 1.0a secrets are stored. Never the secrets."""
    _require_ceo(agent)
    has_creds = await get_x_credentials_service(db).has_credentials()
    return XCredentialsStatus(has_credentials=has_creds)


@router.post("/credentials", response_model=XCredentialsStatus)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=8192)
@guard_deco.block_clouds()
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.usage_monitor(max_calls=30, window=3600)
async def set_x_credentials(
    data: XCredentialsSetRequest, db: DbSession, agent: CurrentAgentContext
) -> XCredentialsStatus:
    """Set (or, passing all four empty, clear) the four OAuth 1.0a secrets."""
    _require_ceo(agent)
    svc = get_x_credentials_service(db)
    try:
        has_creds = await svc.set_credentials(
            api_key=data.api_key,
            api_secret=data.api_secret,
            access_token=data.access_token,
            access_token_secret=data.access_token_secret,
        )
    except XCredentialsValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    await db.commit()
    return XCredentialsStatus(has_credentials=has_creds)
