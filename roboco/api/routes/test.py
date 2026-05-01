"""
Test API Routes

CI/CD operations for agents working on code tasks. Thin HTTP plumbing —
workspace resolution, subprocess dispatch, and output parsing live in
`TestRunnerService`.
"""

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.test import (
    BuildRequest,
    BuildResponse,
    FormatRequest,
    FormatResponse,
    LintRequest,
    LintResponse,
    TestRunRequest,
    TestRunResponse,
    TestStatusResponse,
    TypecheckRequest,
    TypecheckResponse,
)
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    ServiceUnavailableError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.test_runner import get_test_runner_service

router = APIRouter()


def _translate_error(e: ServiceError) -> HTTPException:
    """Service errors → HTTP status."""
    if isinstance(e, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
    if isinstance(e, UnauthorizedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    if isinstance(e, ValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    if isinstance(e, ServiceUnavailableError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=e.message
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
    )


@router.get("/status", response_model=TestStatusResponse)
async def get_test_status(
    db: DbSession,
    agent: CurrentAgentContext,
    project_slug: str = Query(...),
    _task_id: str | None = Query(default=None),
) -> TestStatusResponse:
    """Get test status for a project."""
    service = get_test_runner_service(db)
    try:
        return await service.get_status(project_slug, agent.agent_id)
    except ServiceError as e:
        raise _translate_error(e) from e


@router.post("/run", response_model=TestRunResponse)
async def run_tests(
    data: TestRunRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TestRunResponse:
    """Run tests for a project."""
    service = get_test_runner_service(db)
    try:
        return await service.run_tests(agent.agent_id, data)
    except ServiceError as e:
        raise _translate_error(e) from e


@router.post("/lint", response_model=LintResponse)
async def run_lint(
    data: LintRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> LintResponse:
    """Run linter for a project."""
    service = get_test_runner_service(db)
    try:
        return await service.run_lint(agent.agent_id, data)
    except ServiceError as e:
        raise _translate_error(e) from e


@router.post("/format", response_model=FormatResponse)
async def run_format(
    data: FormatRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> FormatResponse:
    """Run formatter for a project."""
    service = get_test_runner_service(db)
    try:
        return await service.run_format(agent.agent_id, data)
    except ServiceError as e:
        raise _translate_error(e) from e


@router.post("/typecheck", response_model=TypecheckResponse)
async def run_typecheck(
    data: TypecheckRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TypecheckResponse:
    """Run type checker for a project."""
    service = get_test_runner_service(db)
    try:
        return await service.run_typecheck(agent.agent_id, data)
    except ServiceError as e:
        raise _translate_error(e) from e


@router.post("/build", response_model=BuildResponse)
async def run_build(
    data: BuildRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> BuildResponse:
    """Run build command for a project."""
    service = get_test_runner_service(db)
    try:
        return await service.run_build(agent.agent_id, data)
    except ServiceError as e:
        raise _translate_error(e) from e
