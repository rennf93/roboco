"""
Test API Routes

Test and CI/CD operations for agents working on code tasks.
These endpoints are called by the Test MCP Server.

Uses multi-agent workspace structure - each agent gets their own
workspace at: {workspaces_root}/{project_slug}/{team}/{agent_slug}/
"""

import subprocess
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.test import (
    BuildRequest,
    BuildResponse,
    FormatRequest,
    FormatResponse,
    LintIssue,
    LintRequest,
    LintResponse,
    TestRunRequest,
    TestRunResponse,
    TestStatusResponse,
    TypecheckError,
    TypecheckRequest,
    TypecheckResponse,
)
from roboco.config import settings
from roboco.services.project import get_project_service
from roboco.services.workspace import WorkspaceError, get_workspace_service

router = APIRouter()

# Command timeout in seconds
_CMD_TIMEOUT = 300  # 5 minutes for long-running tests

# Minimum parts for lint output parsing (file:line:col:message)
_LINT_PARTS_MIN = 4

# Minimum parts for type error parsing (file:line:message)
_TYPE_ERROR_PARTS_MIN = 3


async def _get_project_and_workspace(
    db: DbSession,
    project_slug: str,
    agent_id: UUID | None = None,
) -> tuple[object, Path]:
    """
    Get the project and workspace path for an agent.

    Uses multi-agent workspace structure if agent_id is provided.
    """
    service = get_project_service(db)
    project = await service.get_by_slug(project_slug)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_slug}' not found",
        )

    # If no agent_id, fall back to legacy workspace_path
    if agent_id is None:
        if not project.workspace_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Project '{project_slug}' has no workspace configured "
                "and no agent_id provided for dynamic workspace resolution",
            )
        workspace = Path(project.workspace_path)
        if not workspace.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workspace path does not exist: {workspace}",
            )
        return project, workspace

    # Use workspace service for multi-agent workspace resolution
    workspace_service = get_workspace_service(db)

    try:
        if settings.workspace_auto_clone:
            workspace = await workspace_service.ensure_workspace(
                project_slug=project_slug,
                agent_id=agent_id,
                git_url=project.git_url,
                default_branch=project.default_branch or "main",
            )
        else:
            workspace = await workspace_service.resolve_workspace(
                project_slug=project_slug,
                agent_id=agent_id,
            )
            if not workspace.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Workspace does not exist: {workspace}. "
                    "Clone the repository first or enable auto_clone.",
                )
    except WorkspaceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return project, workspace


async def _run_command(
    workspace: Path,
    command: str,
    timeout: int = _CMD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command in the workspace (non-blocking)."""
    import asyncio

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        return await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Command timed out after {timeout}s: {command}",
        ) from e


# =============================================================================
# STATUS ENDPOINT
# =============================================================================


@router.get("/status", response_model=TestStatusResponse)
async def get_test_status(
    db: DbSession,
    agent: CurrentAgentContext,
    project_slug: str = Query(...),
    _task_id: str | None = Query(default=None),
) -> TestStatusResponse:
    """Get test status for a project."""
    _project, _workspace = await _get_project_and_workspace(
        db, project_slug, agent.agent_id
    )

    # Return status - in a full implementation this would query stored results
    return TestStatusResponse(
        project_slug=project_slug,
        passed=True,
        summary="No test results stored yet. Run roboco_test_run() to execute tests.",
        last_run=None,
    )


# =============================================================================
# TEST RUN ENDPOINT
# =============================================================================


@router.post("/run", response_model=TestRunResponse)
async def run_tests(
    data: TestRunRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TestRunResponse:
    """Run tests for a project."""
    project, workspace = await _get_project_and_workspace(
        db, data.project_slug, agent.agent_id
    )

    test_cmd = getattr(project, "test_command", None)
    if not test_cmd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{data.project_slug}' has no test_command configured",
        )

    # Add optional path and verbose flag
    cmd = test_cmd
    if data.test_path:
        cmd = f"{cmd} {data.test_path}"
    if data.verbose:
        cmd = f"{cmd} -v"

    result = await _run_command(workspace, cmd)

    # Parse pytest-style output (simplified)
    passed = result.returncode == 0
    output = result.stdout + result.stderr

    # Try to parse counts from output
    passed_count, failed_count, skipped_count = 0, 0, 0
    failures: list[str] = []

    if "passed" in output:
        # Simple parsing - would be more robust in production
        import re

        match = re.search(r"(\d+) passed", output)
        if match:
            passed_count = int(match.group(1))
        match = re.search(r"(\d+) failed", output)
        if match:
            failed_count = int(match.group(1))
        match = re.search(r"(\d+) skipped", output)
        if match:
            skipped_count = int(match.group(1))

    return TestRunResponse(
        project_slug=data.project_slug,
        passed=passed,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        output=output[:10000],  # Limit output size
        failures=failures,
    )


# =============================================================================
# LINT ENDPOINT
# =============================================================================


@router.post("/lint", response_model=LintResponse)
async def run_lint(
    data: LintRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> LintResponse:
    """Run linter for a project."""
    project, workspace = await _get_project_and_workspace(
        db, data.project_slug, agent.agent_id
    )

    lint_cmd = getattr(project, "lint_command", None)
    if not lint_cmd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{data.project_slug}' has no lint_command configured",
        )

    # Add optional fix flag and path
    cmd = lint_cmd
    if data.fix:
        cmd = f"{cmd} --fix"
    if data.path:
        cmd = f"{cmd} {data.path}"

    result = await _run_command(workspace, cmd)

    passed = result.returncode == 0
    output = result.stdout + result.stderr
    issues: list[LintIssue] = []
    fixed_count = 0

    # Parse ruff-style output (simplified)
    for line in output.split("\n"):
        if "::" in line or not line.strip():
            continue
        # Try to parse "file:line:col: code message"
        parts = line.split(":", 3)
        if len(parts) >= _LINT_PARTS_MIN:
            try:
                issues.append(
                    LintIssue(
                        file=parts[0],
                        line=int(parts[1]),
                        column=int(parts[2]),
                        code=parts[3].split()[0] if parts[3].strip() else "E",
                        message=parts[3].strip(),
                    )
                )
            except (ValueError, IndexError):
                continue

    return LintResponse(
        project_slug=data.project_slug,
        passed=passed,
        issues=issues,
        fixed_count=fixed_count,
    )


# =============================================================================
# FORMAT ENDPOINT
# =============================================================================


@router.post("/format", response_model=FormatResponse)
async def run_format(
    data: FormatRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> FormatResponse:
    """Run formatter for a project."""
    project, workspace = await _get_project_and_workspace(
        db, data.project_slug, agent.agent_id
    )

    format_cmd = getattr(project, "format_command", None)
    if not format_cmd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{data.project_slug}' has no format_command configured",
        )

    # Add optional check flag and path
    cmd = format_cmd
    if data.check_only:
        cmd = f"{cmd} --check"
    if data.path:
        cmd = f"{cmd} {data.path}"

    result = await _run_command(workspace, cmd)
    output = result.stdout + result.stderr

    # Count files modified (simplified parsing)
    files_modified = output.count("reformatted") if not data.check_only else 0
    files_unchanged = output.count("unchanged") or output.count("already formatted")

    return FormatResponse(
        project_slug=data.project_slug,
        files_modified=files_modified,
        files_unchanged=files_unchanged,
    )


# =============================================================================
# TYPECHECK ENDPOINT
# =============================================================================


@router.post("/typecheck", response_model=TypecheckResponse)
async def run_typecheck(
    data: TypecheckRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TypecheckResponse:
    """Run type checker for a project."""
    project, workspace = await _get_project_and_workspace(
        db, data.project_slug, agent.agent_id
    )

    typecheck_cmd = getattr(project, "typecheck_command", None)
    if not typecheck_cmd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{data.project_slug}' has no typecheck_command configured",
        )

    # Add optional path
    cmd = typecheck_cmd
    if data.path:
        cmd = f"{cmd} {data.path}"

    result = await _run_command(workspace, cmd)

    passed = result.returncode == 0
    output = result.stdout + result.stderr
    errors: list[TypecheckError] = []

    # Parse mypy-style output (simplified)
    for line in output.split("\n"):
        if ": error:" in line:
            parts = line.split(":", 2)
            if len(parts) >= _TYPE_ERROR_PARTS_MIN:
                try:
                    errors.append(
                        TypecheckError(
                            file=parts[0],
                            line=int(parts[1]),
                            message=parts[2].replace(" error:", "").strip(),
                        )
                    )
                except (ValueError, IndexError):
                    continue

    return TypecheckResponse(
        project_slug=data.project_slug,
        passed=passed,
        errors=errors,
    )


# =============================================================================
# BUILD ENDPOINT
# =============================================================================


@router.post("/build", response_model=BuildResponse)
async def run_build(
    data: BuildRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> BuildResponse:
    """Run build command for a project."""
    project, workspace = await _get_project_and_workspace(
        db, data.project_slug, agent.agent_id
    )

    build_cmd = getattr(project, "build_command", None)
    if not build_cmd:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{data.project_slug}' has no build_command configured",
        )

    import time

    start = time.time()
    result = await _run_command(workspace, build_cmd)
    duration = time.time() - start

    return BuildResponse(
        project_slug=data.project_slug,
        success=result.returncode == 0,
        duration_seconds=round(duration, 2),
        output=(result.stdout + result.stderr)[:10000],
    )
