"""
Documentation API Routes

File management endpoints for agent documentation.
Agents use these to write/read docs without path confusion.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from roboco.agents_config import get_agent_team
from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.docs import (
    DocRefResponse,
    ListDocsResponse,
    ReadDocResponse,
    WriteDocRequest,
    WriteDocResponse,
)
from roboco.api.utils.docs import unauthorized_response as _unauthorized_response
from roboco.security import guard_deco
from roboco.services.base import NotFoundError, UnauthorizedError, ValidationError
from roboco.services.docs import WriteDocInput, get_docs_service

router = APIRouter()


# Module-level Query defaults
_list_task_id_query: UUID | None = Query(None, description="Filter by task ID")
_read_path_query: str = Query(
    ..., description="Normalized path (e.g., 'backend/api/endpoints.md')"
)


# =============================================================================
# WRITE ENDPOINT
# =============================================================================


# 2 MiB, not the 64 KiB used elsewhere: this route writes whole reference docs,
# and the repo's own tree already carries an 82 KiB page and a 1.5 MiB aggregate
# map. A cap under the real corpus would 413 a legitimate roboco_docs_write and
# surface to the agent only as WRITE_FAILED, with no hint that size was the cause.
@router.post("/write", response_model=WriteDocResponse)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=2097152)
@guard_deco.content_type_filter(["application/json"])
async def write_doc(
    data: WriteDocRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WriteDocResponse | JSONResponse:
    """
    Write a documentation file.

    Team folder is determined automatically from agent ID.
    Subfolder is determined by doc_type.

    The document will be:
    - Written to /app/docs/{team}/{type_folder}/{filename}
    - Tracked in task.documents for database traceability
    - Indexed in RAG for searchability
    """
    service = get_docs_service(db)

    try:
        rel_path, doc_ref, is_update = await service.write_doc(
            agent_id=str(agent.agent_id),
            req=WriteDocInput(
                task_id=data.task_id,
                filename=data.filename,
                doc_type=data.doc_type.value,
                title=data.title,
                content=data.content,
            ),
        )
        await db.commit()

        return WriteDocResponse(
            status="updated" if is_update else "created",
            path=rel_path,
            doc_ref=DocRefResponse(
                path=doc_ref.path,
                title=doc_ref.title,
                doc_type=doc_ref.doc_type,
                version=doc_ref.version,
                created_by=doc_ref.created_by,
                created_at=doc_ref.created_at,
                updated_by=doc_ref.updated_by,
                updated_at=doc_ref.updated_at,
                commit_status=doc_ref.commit_status,
            ),
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.message,
        ) from e
    except UnauthorizedError as e:
        return _unauthorized_response(e)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=e.message,
        ) from e


# =============================================================================
# READ ENDPOINT
# =============================================================================


@router.get("/read", response_model=ReadDocResponse)
async def read_doc(
    db: DbSession,
    agent: CurrentAgentContext,
    path: str = _read_path_query,
) -> ReadDocResponse | JSONResponse:
    """
    Read a documentation file by path.

    Path should be normalized (e.g., "backend/api/endpoints.md").
    """
    service = get_docs_service(db)

    try:
        content, size_bytes = await service.read_doc(
            agent_id=str(agent.agent_id),
            path=path,
        )

        return ReadDocResponse(
            path=path,
            content=content,
            size_bytes=size_bytes,
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.message,
        ) from e
    except UnauthorizedError as e:
        return _unauthorized_response(e)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=e.message,
        ) from e


# =============================================================================
# LIST ENDPOINT
# =============================================================================


@router.get("/list", response_model=ListDocsResponse)
async def list_docs(
    db: DbSession,
    agent: CurrentAgentContext,
    task_id: UUID | None = _list_task_id_query,
) -> ListDocsResponse | JSONResponse:
    """
    List documentation files.

    If task_id is provided, lists docs for that task.
    Otherwise, lists all docs for the agent's team.
    """
    service = get_docs_service(db)

    try:
        docs = await service.list_docs(
            agent_id=str(agent.agent_id),
            task_id=task_id,
        )

        # Get team for response
        team = get_agent_team(str(agent.agent_id)) or "unknown"

        return ListDocsResponse(
            documents=[
                DocRefResponse(
                    path=d.path,
                    title=d.title,
                    doc_type=d.doc_type,
                    version=d.version,
                    created_by=d.created_by,
                    created_at=d.created_at,
                    updated_by=d.updated_by,
                    updated_at=d.updated_at,
                )
                for d in docs
            ],
            team=team,
            count=len(docs),
        )
    except UnauthorizedError as e:
        return _unauthorized_response(e)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=e.message,
        ) from e


# =============================================================================
# DELETE ENDPOINT
# =============================================================================


@router.delete("/delete")
@guard_deco.rate_limit(requests=20, window=60)
async def delete_doc(
    db: DbSession,
    agent: CurrentAgentContext,
    path: str = _read_path_query,
) -> Response:
    """
    Delete a documentation file.

    Returns 204 on success; a denied delete returns the gateway Envelope
    (HTTP 403) with a non-null remediate, like the other docs endpoints.
    """
    service = get_docs_service(db)

    try:
        await service.delete_doc(
            agent_id=str(agent.agent_id),
            path=path,
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.message,
        ) from e
    except UnauthorizedError as e:
        return _unauthorized_response(e)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=e.message,
        ) from e
