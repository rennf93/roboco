"""
Test Runner Service

Orchestrates CI/CD-style commands (tests, lint, format, typecheck, build)
in agent workspaces. The API routes are thin adapters over this service —
all workspace resolution, subprocess dispatch, and output parsing happens
here so routes only handle HTTP translation.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

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
from roboco.services.base import (
    BaseService,
    NotFoundError,
    ServiceError,
    ServiceUnavailableError,
    ValidationError,
)
from roboco.services.project import get_project_service
from roboco.services.workspace import WorkspaceError, get_workspace_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

# Command timeout in seconds — long enough for full test runs.
_CMD_TIMEOUT = 300

# Minimum parts for lint output parsing (file:line:col:message).
_LINT_PARTS_MIN = 4

# Minimum parts for type error parsing (file:line:message).
_TYPE_ERROR_PARTS_MIN = 3


@dataclass(frozen=True)
class _ProjectContext:
    """Resolved (project, workspace) pair for a command run."""

    project: Any
    workspace: Path


class TestRunnerService(BaseService):
    """Runs project CI commands in an agent's workspace."""

    service_name: ClassVar[str] = "test_runner"

    # =========================================================================
    # WORKSPACE RESOLUTION
    # =========================================================================

    async def _resolve_legacy_workspace(self, project: Any, project_slug: str) -> Path:
        """Legacy single-path project config (no agent_id given)."""
        workspace_path = getattr(project, "workspace_path", None)
        if not workspace_path:
            raise ValidationError(
                f"Project '{project_slug}' has no workspace configured and "
                "no agent_id provided for dynamic workspace resolution"
            )
        workspace = Path(workspace_path)
        if not workspace.exists():
            raise ValidationError(f"Workspace path does not exist: {workspace}")
        return workspace

    async def _resolve_agent_workspace(
        self, project: Any, project_slug: str, agent_id: UUID
    ) -> Path:
        """Resolve workspace via WorkspaceService (ensure/resolve per setting)."""
        workspace_service = get_workspace_service(self.session)
        try:
            if settings.workspace_auto_clone:
                return await workspace_service.ensure_workspace(
                    project_slug=project_slug,
                    agent_id=agent_id,
                    git_url=project.git_url,
                    default_branch=project.default_branch or "main",
                )
            workspace = await workspace_service.resolve_workspace(
                project_slug=project_slug,
                agent_id=agent_id,
            )
            if not workspace.exists():
                raise ValidationError(
                    f"Workspace does not exist: {workspace}. "
                    "Clone the repository first or enable auto_clone."
                )
            return workspace
        except WorkspaceError as e:
            raise ValidationError(str(e)) from e

    async def _load_project_and_workspace(
        self,
        project_slug: str,
        agent_id: UUID | None,
    ) -> _ProjectContext:
        """Fetch project + resolve its workspace. Raises typed errors."""
        service = get_project_service(self.session)
        project = await service.get_by_slug(project_slug)
        if not project:
            raise NotFoundError(resource_type="Project", resource_id=project_slug)

        if agent_id is None:
            workspace = await self._resolve_legacy_workspace(project, project_slug)
        else:
            workspace = await self._resolve_agent_workspace(
                project, project_slug, agent_id
            )
        return _ProjectContext(project=project, workspace=workspace)

    # =========================================================================
    # COMMAND RUNNER
    # =========================================================================

    async def _run_command(
        self,
        workspace: Path,
        command: str,
        timeout: int = _CMD_TIMEOUT,
    ) -> subprocess.CompletedProcess[str]:
        """Run a shell command in the workspace (non-blocking).

        Commands are tokenized with shlex — no shell features (pipes,
        redirects, env expansion) supported. Project-configured commands
        are expected to be simple exe + args.
        """
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
            raise ServiceUnavailableError(
                service_name="test_runner",
                reason=f"Command timed out after {timeout}s: {command}",
            ) from e
        except FileNotFoundError as e:
            binary = argv[0] if argv else command
            raise ValidationError(
                f"Command binary not found: '{binary}'. "
                "Update the project's configured command (e.g., replace 'make test' "
                "with 'uv run pytest') or ensure the binary is installed in the "
                "runtime environment."
            ) from e

    def _project_cmd(self, project: Any, attr: str) -> str | None:
        """Fetch a configured command for the project, or None if unset.

        Prior behavior raised ValidationError on a missing command, but
        QA agents ended up hitting 400s on every probe when the project
        simply hadn't opted into that CI tool (e.g., lint_command=null
        because the task is a README edit). Now callers get `None` and
        return a `skipped=True` success response — no gate violation,
        clear signal to the agent that there's nothing to run.
        """
        cmd = getattr(project, attr, None)
        return str(cmd) if cmd else None

    @staticmethod
    def _skip_reason(project_slug: str, label: str) -> str:
        return (
            f"Project '{project_slug}' has no {label} configured — check "
            "skipped. Review the change manually if it's relevant."
        )

    # =========================================================================
    # OUTPUT PARSERS
    # =========================================================================

    @staticmethod
    def _parse_pytest_counts(output: str) -> tuple[int, int, int]:
        """Parse (passed, failed, skipped) from pytest-style output."""
        if "passed" not in output:
            return 0, 0, 0

        def _first_int(pattern: str) -> int:
            match = re.search(pattern, output)
            return int(match.group(1)) if match else 0

        return (
            _first_int(r"(\d+) passed"),
            _first_int(r"(\d+) failed"),
            _first_int(r"(\d+) skipped"),
        )

    @staticmethod
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

    def _parse_lint_output(self, output: str) -> list[LintIssue]:
        return [
            issue
            for line in output.split("\n")
            if (issue := self._parse_lint_line(line)) is not None
        ]

    @staticmethod
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

    def _parse_typecheck_output(self, output: str) -> list[TypecheckError]:
        return [
            err
            for line in output.split("\n")
            if (err := self._parse_typecheck_line(line)) is not None
        ]

    @staticmethod
    def _build_format_cmd(base_cmd: str, data: FormatRequest) -> str:
        cmd = base_cmd
        if data.check_only:
            cmd = f"{cmd} --check"
        if data.path:
            cmd = f"{cmd} {data.path}"
        return cmd

    # =========================================================================
    # PUBLIC ORCHESTRATION
    # =========================================================================

    async def get_status(self, project_slug: str, agent_id: UUID) -> TestStatusResponse:
        """Placeholder status endpoint — validates workspace resolution."""
        await self._load_project_and_workspace(project_slug, agent_id)
        return TestStatusResponse(
            project_slug=project_slug,
            passed=True,
            summary=(
                "No test results stored yet. Run roboco_test_run() to execute tests."
            ),
            last_run=None,
        )

    async def run_tests(self, agent_id: UUID, data: TestRunRequest) -> TestRunResponse:
        ctx = await self._load_project_and_workspace(data.project_slug, agent_id)
        base = self._project_cmd(ctx.project, "test_command")
        if not base:
            return TestRunResponse(
                project_slug=data.project_slug,
                passed=True,
                skipped=True,
                skip_reason=self._skip_reason(data.project_slug, "test_command"),
            )
        cmd = base
        if data.test_path:
            cmd = f"{cmd} {data.test_path}"
        if data.verbose:
            cmd = f"{cmd} -v"

        result = await self._run_command(ctx.workspace, cmd)
        output = result.stdout + result.stderr
        passed, failed, skipped = self._parse_pytest_counts(output)

        return TestRunResponse(
            project_slug=data.project_slug,
            passed=result.returncode == 0,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            output=output[:10000],
            failures=[],
        )

    async def run_lint(self, agent_id: UUID, data: LintRequest) -> LintResponse:
        ctx = await self._load_project_and_workspace(data.project_slug, agent_id)
        base = self._project_cmd(ctx.project, "lint_command")
        if not base:
            return LintResponse(
                project_slug=data.project_slug,
                passed=True,
                skipped=True,
                skip_reason=self._skip_reason(data.project_slug, "lint_command"),
            )
        cmd = base
        if data.fix:
            cmd = f"{cmd} --fix"
        if data.path:
            cmd = f"{cmd} {data.path}"

        result = await self._run_command(ctx.workspace, cmd)
        output = result.stdout + result.stderr
        return LintResponse(
            project_slug=data.project_slug,
            passed=result.returncode == 0,
            issues=self._parse_lint_output(output),
            fixed_count=0,
        )

    async def run_format(self, agent_id: UUID, data: FormatRequest) -> FormatResponse:
        ctx = await self._load_project_and_workspace(data.project_slug, agent_id)
        base = self._project_cmd(ctx.project, "format_command")
        if not base:
            return FormatResponse(
                project_slug=data.project_slug,
                skipped=True,
                skip_reason=self._skip_reason(data.project_slug, "format_command"),
            )
        result = await self._run_command(
            ctx.workspace, self._build_format_cmd(base, data)
        )
        output = result.stdout + result.stderr
        files_modified = output.count("reformatted") if not data.check_only else 0
        files_unchanged = output.count("unchanged") or output.count("already formatted")
        return FormatResponse(
            project_slug=data.project_slug,
            files_modified=files_modified,
            files_unchanged=files_unchanged,
        )

    async def run_typecheck(
        self, agent_id: UUID, data: TypecheckRequest
    ) -> TypecheckResponse:
        ctx = await self._load_project_and_workspace(data.project_slug, agent_id)
        base = self._project_cmd(ctx.project, "typecheck_command")
        if not base:
            return TypecheckResponse(
                project_slug=data.project_slug,
                passed=True,
                skipped=True,
                skip_reason=self._skip_reason(data.project_slug, "typecheck_command"),
            )
        cmd = f"{base} {data.path}" if data.path else base
        result = await self._run_command(ctx.workspace, cmd)
        output = result.stdout + result.stderr
        return TypecheckResponse(
            project_slug=data.project_slug,
            passed=result.returncode == 0,
            errors=self._parse_typecheck_output(output),
        )

    async def run_build(self, agent_id: UUID, data: BuildRequest) -> BuildResponse:
        ctx = await self._load_project_and_workspace(data.project_slug, agent_id)
        base = self._project_cmd(ctx.project, "build_command")
        if not base:
            return BuildResponse(
                project_slug=data.project_slug,
                success=True,
                skipped=True,
                skip_reason=self._skip_reason(data.project_slug, "build_command"),
            )
        start = time.time()
        result = await self._run_command(ctx.workspace, base)
        duration = time.time() - start
        return BuildResponse(
            project_slug=data.project_slug,
            success=result.returncode == 0,
            duration_seconds=round(duration, 2),
            output=(result.stdout + result.stderr)[:10000],
        )


def get_test_runner_service(session: AsyncSession) -> TestRunnerService:
    """Factory for TestRunnerService."""
    return TestRunnerService(session)


__all__ = ["ServiceError", "TestRunnerService", "get_test_runner_service"]
