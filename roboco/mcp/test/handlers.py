"""
Test MCP Server Handlers

Handler functions for test/CI operations. Each handler:
1. Validates project has the required command configured
2. Calls the internal API to execute the command
3. Returns formatted response with results and guidance
"""

from dataclasses import dataclass
from typing import Any

from roboco.mcp.utils import (
    ApiClient,
    format_error_response,
    format_success_response,
)


@dataclass
class TestContext:
    """Common context for test operations."""

    client: ApiClient
    project_slug: str
    task_id: str
    agent_id: str


# =============================================================================
# READ-ONLY HANDLERS
# =============================================================================


async def handle_test_status(
    client: ApiClient,
    project_slug: str,
    task_id: str | None,
    _agent_id: str,
) -> dict[str, Any]:
    """Handle test status request."""
    params: dict[str, Any] = {"project_slug": project_slug}
    if task_id:
        params["task_id"] = task_id

    resp = await client.get("/test/status", params=params)
    if not resp.ok:
        return format_error_response(
            "TEST_STATUS_FAILED",
            "Failed to get test status",
            {"status": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    passed = data.get("passed", False)
    summary = data.get("summary", "No test results available")

    return format_success_response(
        data,
        guidance=f"Last run: {'PASSED' if passed else 'FAILED'}. {summary}",
    )


# =============================================================================
# TEST EXECUTION HANDLERS
# =============================================================================


async def handle_test_run(
    ctx: TestContext,
    test_path: str | None,
    verbose: bool,
) -> dict[str, Any]:
    """Handle test run request."""
    payload: dict[str, Any] = {
        "project_slug": ctx.project_slug,
        "task_id": ctx.task_id,
        "agent_id": ctx.agent_id,
        "verbose": verbose,
    }
    if test_path:
        payload["test_path"] = test_path

    resp = await ctx.client.post("/test/run", json=payload)
    if not resp.ok:
        return format_error_response(
            "TEST_RUN_FAILED",
            "Failed to run tests",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check project has test_command configured.",
        )

    data = resp.json()
    passed = data.get("passed", False)
    pass_count = data.get("passed_count", 0)
    fail_count = data.get("failed_count", 0)
    total = pass_count + fail_count

    if passed:
        guidance = f"All {total} tests passed!"
        next_step = "SUBMIT_VERIFICATION" if total > 0 else None
    else:
        guidance = f"{fail_count}/{total} tests failed. Fix issues before proceeding."
        next_step = "FIX_TESTS"

    return format_success_response(data, guidance=guidance, next_step=next_step)


async def handle_test_lint(
    ctx: TestContext,
    fix: bool,
    path: str | None,
) -> dict[str, Any]:
    """Handle lint request."""
    payload: dict[str, Any] = {
        "project_slug": ctx.project_slug,
        "task_id": ctx.task_id,
        "agent_id": ctx.agent_id,
        "fix": fix,
    }
    if path:
        payload["path"] = path

    resp = await ctx.client.post("/test/lint", json=payload)
    if not resp.ok:
        return format_error_response(
            "LINT_FAILED",
            "Failed to run linter",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check project has lint_command configured.",
        )

    data = resp.json()
    issues = data.get("issues", [])
    fixed = data.get("fixed_count", 0)

    if not issues:
        guidance = "No lint issues found!"
        if fixed:
            guidance = f"Fixed {fixed} issues. No remaining issues."
    else:
        guidance = f"{len(issues)} lint issue(s) found."
        if fix:
            guidance += f" {fixed} auto-fixed, {len(issues)} remaining."
        guidance += " Review and fix before proceeding."

    return format_success_response(
        data,
        guidance=guidance,
        next_step="FIX_LINT" if issues else None,
    )


async def handle_test_format(
    ctx: TestContext,
    check_only: bool,
    path: str | None,
) -> dict[str, Any]:
    """Handle format request."""
    payload: dict[str, Any] = {
        "project_slug": ctx.project_slug,
        "task_id": ctx.task_id,
        "agent_id": ctx.agent_id,
        "check_only": check_only,
    }
    if path:
        payload["path"] = path

    resp = await ctx.client.post("/test/format", json=payload)
    if not resp.ok:
        return format_error_response(
            "FORMAT_FAILED",
            "Failed to run formatter",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check project has format_command configured.",
        )

    data = resp.json()
    files_modified = data.get("files_modified", 0)

    if check_only:
        if files_modified == 0:
            guidance = "All files properly formatted!"
        else:
            guidance = (
                f"{files_modified} file(s) need formatting. Run without check_only."
            )
    elif files_modified == 0:
        guidance = "All files already formatted."
    else:
        guidance = f"Formatted {files_modified} file(s)."

    return format_success_response(data, guidance=guidance)


async def handle_test_typecheck(
    client: ApiClient,
    project_slug: str,
    task_id: str,
    path: str | None,
    agent_id: str,
) -> dict[str, Any]:
    """Handle type check request."""
    payload: dict[str, Any] = {
        "project_slug": project_slug,
        "task_id": task_id,
        "agent_id": agent_id,
    }
    if path:
        payload["path"] = path

    resp = await client.post("/test/typecheck", json=payload)
    if not resp.ok:
        return format_error_response(
            "TYPECHECK_FAILED",
            "Failed to run type checker",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check project has typecheck_command configured.",
        )

    data = resp.json()
    errors = data.get("errors", [])
    error_count = len(errors)

    if error_count == 0:
        guidance = "No type errors found!"
    else:
        guidance = f"{error_count} type error(s) found. Fix before proceeding."

    return format_success_response(
        data,
        guidance=guidance,
        next_step="FIX_TYPES" if errors else None,
    )


async def handle_test_build(
    client: ApiClient,
    project_slug: str,
    task_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle build request."""
    payload: dict[str, Any] = {
        "project_slug": project_slug,
        "task_id": task_id,
        "agent_id": agent_id,
    }

    resp = await client.post("/test/build", json=payload)
    if not resp.ok:
        return format_error_response(
            "BUILD_FAILED",
            "Build failed",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check project has build_command configured and deps installed.",
        )

    data = resp.json()
    success = data.get("success", False)
    duration = data.get("duration_seconds", 0)

    if success:
        guidance = f"Build succeeded in {duration:.1f}s!"
    else:
        guidance = "Build failed. Check output for errors."

    return format_success_response(
        data,
        guidance=guidance,
        next_step="FIX_BUILD" if not success else None,
    )
