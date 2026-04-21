"""
Test API Routes

Test and CI/CD operations for agents working on code tasks.
These endpoints are called by the Test MCP Server.

Uses multi-agent workspace structure - each agent gets their own
workspace at: {workspaces_root}/{project_slug}/{team}/{agent_slug}/
"""

import shlex
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


def _resolve_legacy_workspace(project: object, project_slug: str) -> Path:
    """Resolve workspace using the legacy single-path project config."""
    workspace_path = getattr(project, "workspace_path", None)
    if not workspace_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project '{project_slug}' has no workspace configured "
            "and no agent_id provided for dynamic workspace resolution",
        )
    workspace = Path(workspace_path)
    if not workspace.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workspace path does not exist: {workspace}",
        )
    return workspace


async def _resolve_agent_workspace(
    db: DbSession, project: object, project_slug: str, agent_id: UUID
) -> Path:
    """Resolve workspace via WorkspaceService (ensure/resolve per setting)."""
    workspace_service = get_workspace_service(db)
    try:
        if settings.workspace_auto_clone:
            return await workspace_service.ensure_workspace(
                project_slug=project_slug,
                agent_id=agent_id,
                git_url=project.git_url,  # type: ignore[attr-defined]
                default_branch=project.default_branch or "main",  # type: ignore[attr-defined]
            )
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
        return workspace
    except WorkspaceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


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

    if agent_id is None:
        return project, _resolve_legacy_workspace(project, project_slug)

    workspace = await _resolve_agent_workspace(db, project, project_slug, agent_id)
    return project, workspace


async def _run_command(
    workspace: Path,
    command: str,
    timeout: int = _CMD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command in the workspace (non-blocking)."""
    import asyncio

    # Tokenize with shlex so we can drop `shell=True` — project-supplied
    # commands are simple exe + args (pytest, ruff, mypy). Shell-specific
    # features (pipes, redirects, env expansion) aren't supported here.
    argv = shlex.split(command)

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            check=False,
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


def _parse_pytest_counts(output: str) -> tuple[int, int, int]:
    """Parse (passed, failed, skipped) counts from pytest-style output."""
    if "passed" not in output:
        return 0, 0, 0
    import re

    def _first_int(pattern: str) -> int:
        match = re.search(pattern, output)
        return int(match.group(1)) if match else 0

    return (
        _first_int(r"(\d+) passed"),
        _first_int(r"(\d+) failed"),
        _first_int(r"(\d+) skipped"),
    )


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

    cmd = test_cmd
    if data.test_path:
        cmd = f"{cmd} {data.test_path}"
    if data.verbose:
        cmd = f"{cmd} -v"

    result = await _run_command(workspace, cmd)
    output = result.stdout + result.stderr
    passed_count, failed_count, skipped_count = _parse_pytest_counts(output)

    return TestRunResponse(
        project_slug=data.project_slug,
        passed=result.returncode == 0,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        output=output[:10000],  # Limit output size
        failures=[],
    )


# =============================================================================
# LINT ENDPOINT
# =============================================================================


def _parse_lint_line(line: str) -> LintIssue | None:
    """Parse a single ruff-style lint output line."""
    if "::" in line or not line.strip():
        return None
    parts = line.split(":", 3)
    if len(parts) < _LINT_PARTS_MIN:
        return None
    try:
        return LintIssue(
            file=parts[0],
            line=int(parts[1]),
            column=int(parts[2]),
            code=parts[3].split()[0] if parts[3].strip() else "E",
            message=parts[3].strip(),
        )
    except (ValueError, IndexError):
        return None


def _parse_lint_output(output: str) -> list[LintIssue]:
    """Parse ruff-style lint output into structured issues."""
    issues: list[LintIssue] = []
    for line in output.split("\n"):
        if issue := _parse_lint_line(line):
            issues.append(issue)
    return issues


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

    cmd = lint_cmd
    if data.fix:
        cmd = f"{cmd} --fix"
    if data.path:
        cmd = f"{cmd} {data.path}"

    result = await _run_command(workspace, cmd)
    output = result.stdout + result.stderr

    return LintResponse(
        project_slug=data.project_slug,
        passed=result.returncode == 0,
        issues=_parse_lint_output(output),
        fixed_count=0,
    )


# =============================================================================
# FORMAT ENDPOINT
# =============================================================================


def _build_format_cmd(base_cmd: str, data: FormatRequest) -> str:
    """Apply --check and path modifiers to the base format command."""
    cmd = base_cmd
    if data.check_only:
        cmd = f"{cmd} --check"
    if data.path:
        cmd = f"{cmd} {data.path}"
    return cmd


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

    result = await _run_command(workspace, _build_format_cmd(format_cmd, data))
    output = result.stdout + result.stderr

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


def _parse_typecheck_line(line: str) -> TypecheckError | None:
    """Parse a single mypy-style type error line."""
    if ": error:" not in line:
        return None
    parts = line.split(":", 2)
    if len(parts) < _TYPE_ERROR_PARTS_MIN:
        return None
    try:
        return TypecheckError(
            file=parts[0],
            line=int(parts[1]),
            message=parts[2].replace(" error:", "").strip(),
        )
    except (ValueError, IndexError):
        return None


def _parse_typecheck_output(output: str) -> list[TypecheckError]:
    """Parse mypy-style output into structured type errors."""
    errors: list[TypecheckError] = []
    for line in output.split("\n"):
        if err := _parse_typecheck_line(line):
            errors.append(err)
    return errors


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

    cmd = typecheck_cmd
    if data.path:
        cmd = f"{cmd} {data.path}"

    result = await _run_command(workspace, cmd)
    output = result.stdout + result.stderr

    return TypecheckResponse(
        project_slug=data.project_slug,
        passed=result.returncode == 0,
        errors=_parse_typecheck_output(output),
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
