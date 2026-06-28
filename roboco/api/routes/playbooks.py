"""Playbook curation API — the Auditor (or CEO) reviews drafts.

GET lists drafts (or approved); approve flips a draft to approved (+ indexes it);
reject archives it with a reason. Curation is gated to the Auditor and the CEO —
delivery roles DRAFT via the gateway verb but never curate.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.playbook import PlaybookRejectBody
from roboco.models import AgentRole
from roboco.models.playbook import Playbook
from roboco.services.base import ConflictError, NotFoundError
from roboco.services.playbook import get_playbook_service

router = APIRouter()

_CURATOR_ROLES = frozenset({AgentRole.AUDITOR, AgentRole.CEO})


def _require_curator(agent: CurrentAgentContext) -> None:
    if agent.role not in _CURATOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Auditor or CEO may curate playbooks",
        )


@router.get("", response_model=list[Playbook])
async def list_playbooks(
    db: DbSession,
    agent: CurrentAgentContext,
    status_filter: str = Query(default="draft", alias="status"),
) -> list[Playbook]:
    """List playbooks by status (default: drafts awaiting review)."""
    _require_curator(agent)
    svc = get_playbook_service(db)
    rows = (
        await svc.list_approved()
        if status_filter == "approved"
        else await svc.list_drafts()
    )
    return [Playbook.model_validate(row) for row in rows]


@router.post("/{playbook_id}/approve", response_model=Playbook)
async def approve_playbook(
    playbook_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> Playbook:
    """Approve a draft playbook → approved (and indexed into the KB)."""
    _require_curator(agent)
    try:
        svc = get_playbook_service(db)
        playbook = await svc.approve(playbook_id, approver_id=agent.agent_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found"
        ) from exc
    except ConflictError as exc:
        # The playbook is not a draft (already approved/archived) — the
        # curation is already finished, not a missing resource.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    # Commit the status change BEFORE indexing: the RAG index write runs through
    # its own auto-committing connection, so indexing before commit would durably
    # land an approved playbook in the corpus even if this commit rolled back.
    await db.commit()
    await svc.index_approved(playbook)
    return Playbook.model_validate(playbook)


@router.post("/{playbook_id}/reject", response_model=Playbook)
async def reject_playbook(
    playbook_id: UUID,
    body: PlaybookRejectBody,
    db: DbSession,
    agent: CurrentAgentContext,
) -> Playbook:
    """Reject a draft playbook → archived, with the Auditor's reason."""
    _require_curator(agent)
    try:
        svc = get_playbook_service(db)
        playbook = await svc.reject(
            playbook_id, approver_id=agent.agent_id, reason=body.reason
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found"
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    # Commit the status change BEFORE de-indexing (see approve_playbook).
    await db.commit()
    await svc.unindex_playbook(playbook)
    return Playbook.model_validate(playbook)


@router.post("/{playbook_id}/archive", response_model=Playbook)
async def archive_playbook(
    playbook_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> Playbook:
    """Retire an approved playbook → archived (and de-indexed from the KB)."""
    _require_curator(agent)
    try:
        svc = get_playbook_service(db)
        playbook = await svc.archive(playbook_id, approver_id=agent.agent_id)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found"
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    # Commit the status change BEFORE de-indexing (see approve_playbook).
    await db.commit()
    await svc.unindex_playbook(playbook)
    return Playbook.model_validate(playbook)
