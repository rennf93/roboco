"""
Test MCP Server

Exposes test and CI/CD operations to Claude Code agents.
Uses project-configured commands for tests, linting, formatting, etc.

Tools (Developer/QA):
- roboco_test_run: Run project tests
- roboco_test_lint: Run linter
- roboco_test_format: Run code formatter
- roboco_test_typecheck: Run type checker
- roboco_test_build: Run build command

Tools (All - Read-only):
- roboco_test_status: Check last test run status
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.mcp.test.handlers import (
    handle_test_build,
    handle_test_format,
    handle_test_lint,
    handle_test_run,
    handle_test_status,
    handle_test_typecheck,
)
from roboco.mcp.utils import ApiClient


def _register_readonly_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register read-only test tools available to all agents."""

    @mcp.tool()
    async def roboco_test_status(
        project_slug: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Check the status of the last test run.

        Args:
            project_slug: Project identifier (e.g., 'roboco', 'roboco-panel')
            task_id: Optional task ID for context

        Returns:
            Last test run status with pass/fail and summary
        """
        return await handle_test_status(client, project_slug, task_id, agent_id)


def _register_test_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register test execution tools for developers and QA."""

    @mcp.tool()
    async def roboco_test_run(
        project_slug: str,
        task_id: str,
        test_path: str | None = None,
        verbose: bool = False,
    ) -> dict[str, Any]:
        """
        Run project tests.

        Uses the project's configured test_command (e.g., 'uv run pytest').
        Results are recorded for the task.

        Args:
            project_slug: Project identifier
            task_id: Task ID for tracking
            test_path: Optional specific test file/directory
            verbose: Enable verbose output

        Returns:
            Test results with pass/fail counts and output
        """
        from roboco.mcp.test.handlers import TestContext

        ctx = TestContext(
            client=client, project_slug=project_slug, task_id=task_id, agent_id=agent_id
        )
        return await handle_test_run(ctx, test_path, verbose)

    @mcp.tool()
    async def roboco_test_lint(
        project_slug: str,
        task_id: str,
        fix: bool = False,
        path: str | None = None,
    ) -> dict[str, Any]:
        """
        Run linter on project code.

        Uses the project's configured lint_command (e.g., 'uv run ruff check .').

        Args:
            project_slug: Project identifier
            task_id: Task ID for tracking
            fix: Auto-fix issues if possible
            path: Optional specific path to lint

        Returns:
            Lint results with issues found
        """
        from roboco.mcp.test.handlers import TestContext

        ctx = TestContext(
            client=client, project_slug=project_slug, task_id=task_id, agent_id=agent_id
        )
        return await handle_test_lint(ctx, fix, path)

    @mcp.tool()
    async def roboco_test_format(
        project_slug: str,
        task_id: str,
        check_only: bool = False,
        path: str | None = None,
    ) -> dict[str, Any]:
        """
        Run code formatter.

        Uses the project's configured format_command (e.g., 'uv run ruff format .').

        Args:
            project_slug: Project identifier
            task_id: Task ID for tracking
            check_only: Only check, don't modify files
            path: Optional specific path to format

        Returns:
            Format results with files modified
        """
        from roboco.mcp.test.handlers import TestContext

        ctx = TestContext(
            client=client, project_slug=project_slug, task_id=task_id, agent_id=agent_id
        )
        return await handle_test_format(ctx, check_only, path)

    @mcp.tool()
    async def roboco_test_typecheck(
        project_slug: str,
        task_id: str,
        path: str | None = None,
    ) -> dict[str, Any]:
        """
        Run type checker.

        Uses the project's configured typecheck_command (e.g., 'uv run mypy src/').

        Args:
            project_slug: Project identifier
            task_id: Task ID for tracking
            path: Optional specific path to check

        Returns:
            Type check results with errors found
        """
        return await handle_test_typecheck(
            client, project_slug, task_id, path, agent_id
        )

    @mcp.tool()
    async def roboco_test_build(
        project_slug: str,
        task_id: str,
    ) -> dict[str, Any]:
        """
        Run project build command.

        Uses the project's configured build_command (e.g., 'pnpm build').

        Args:
            project_slug: Project identifier
            task_id: Task ID for tracking

        Returns:
            Build results with success/failure
        """
        return await handle_test_build(client, project_slug, task_id, agent_id)


def create_test_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Test MCP server for a specific agent.

    Tools are registered based on role:
    - All agents: status check (read-only)
    - Developers/QA: run tests, lint, format, typecheck, build

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server with role-appropriate tools
    """
    from roboco.agents_config import get_agent_role

    mcp = FastMCP(f"roboco-test-{agent_id}", json_response=True)
    client = ApiClient(agent_id)
    role = get_agent_role(agent_id)

    # Read-only tools available to ALL agents
    _register_readonly_tools(mcp, client, agent_id)

    # Test execution tools for developers, QA, PMs, and management
    test_roles = (
        "developer",
        "qa",
        "cell_pm",
        "main_pm",
        "product_owner",
        "auditor",
        "ceo",
    )
    if role in test_roles:
        _register_test_tools(mcp, client, agent_id)

    return mcp


if __name__ == "__main__":
    import sys

    _MIN_ARGS = 2
    if len(sys.argv) < _MIN_ARGS:
        print("Usage: python test_server.py <agent_id>")
        sys.exit(1)

    agent_id_cli = sys.argv[1]
    server = create_test_mcp_server(agent_id_cli)
    server.run()
