"""
Test API Schemas

Request/response models for test/CI operation endpoints.
"""

from datetime import datetime

from pydantic import BaseModel

# =============================================================================
# STATUS
# =============================================================================


class TestStatusResponse(BaseModel):
    """Test status response."""

    project_slug: str
    passed: bool
    summary: str
    last_run: datetime | None = None
    passed_count: int = 0
    failed_count: int = 0


# =============================================================================
# TEST RUN
# =============================================================================


class TestRunRequest(BaseModel):
    """Request to run tests."""

    project_slug: str
    task_id: str
    agent_id: str
    test_path: str | None = None
    verbose: bool = False


class TestRunResponse(BaseModel):
    """Response from test run.

    `skipped=True` + `skip_reason` indicates the project opted out of
    this check (e.g., no test_command configured). That's distinct
    from a failure — QA should treat it as "nothing to verify here,
    move on" rather than a gate violation.
    """

    project_slug: str
    passed: bool
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    duration_seconds: float = 0
    output: str = ""
    failures: list[str] = []
    skipped: bool = False
    skip_reason: str | None = None


# =============================================================================
# LINT
# =============================================================================


class LintRequest(BaseModel):
    """Request to run linter."""

    project_slug: str
    task_id: str
    agent_id: str
    fix: bool = False
    path: str | None = None


class LintIssue(BaseModel):
    """A lint issue found."""

    file: str
    line: int
    column: int
    code: str
    message: str


class LintResponse(BaseModel):
    """Response from lint run.

    See `TestRunResponse` for `skipped` / `skip_reason` semantics.
    """

    project_slug: str
    passed: bool
    issues: list[LintIssue] = []
    fixed_count: int = 0
    skipped: bool = False
    skip_reason: str | None = None


# =============================================================================
# FORMAT
# =============================================================================


class FormatRequest(BaseModel):
    """Request to run formatter."""

    project_slug: str
    task_id: str
    agent_id: str
    check_only: bool = False
    path: str | None = None


class FormatResponse(BaseModel):
    """Response from format run.

    See `TestRunResponse` for `skipped` / `skip_reason` semantics.
    """

    project_slug: str
    files_modified: int = 0
    files_unchanged: int = 0
    skipped: bool = False
    skip_reason: str | None = None


# =============================================================================
# TYPECHECK
# =============================================================================


class TypecheckRequest(BaseModel):
    """Request to run type checker."""

    project_slug: str
    task_id: str
    agent_id: str
    path: str | None = None


class TypecheckError(BaseModel):
    """A type check error found."""

    file: str
    line: int
    message: str


class TypecheckResponse(BaseModel):
    """Response from type check run.

    See `TestRunResponse` for `skipped` / `skip_reason` semantics.
    """

    project_slug: str
    passed: bool
    errors: list[TypecheckError] = []
    skipped: bool = False
    skip_reason: str | None = None


# =============================================================================
# BUILD
# =============================================================================


class BuildRequest(BaseModel):
    """Request to run build."""

    project_slug: str
    task_id: str
    agent_id: str


class BuildResponse(BaseModel):
    """Response from build run.

    See `TestRunResponse` for `skipped` / `skip_reason` semantics.
    """

    project_slug: str
    success: bool
    duration_seconds: float = 0
    output: str = ""
    skipped: bool = False
    skip_reason: str | None = None
