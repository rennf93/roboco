"""Wave A4 (2026-05-12): credentials-stripped refresh fetch logs at DEBUG,
not WARNING, when stderr is the known-benign auth-failure signature.

The credential-less fetch is intentional (workspace.py:317-323 explains).
Logging the expected auth-fail at WARNING level pollutes every monitor.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from roboco.services.workspace import (
    WorkspaceService,
)


@pytest.mark.asyncio
async def test_fetch_auth_fail_logs_at_debug() -> None:
    """`fatal: could not read Username` stderr → DEBUG log, not WARNING."""
    workspace = Path("/tmp/fake-workspace")
    fake_result = CompletedProcess(
        args=["git", "fetch", "origin"],
        returncode=128,
        stdout="",
        stderr=(
            "fatal: could not read Username for 'https://github.com': "
            "terminal prompts disabled\n"
        ),
    )

    captured: list[tuple[str, str]] = []

    def capture_warning(event: str, **_kw: object) -> None:
        captured.append(("warning", event))

    def capture_debug(event: str, **_kw: object) -> None:
        captured.append(("debug", event))

    with (
        patch("roboco.services.workspace.subprocess.run", return_value=fake_result),
        patch("roboco.services.workspace.logger.warning", side_effect=capture_warning),
        patch("roboco.services.workspace.logger.debug", side_effect=capture_debug),
    ):
        await WorkspaceService._fetch_origin_best_effort(
            workspace=workspace, project_slug="roboco-api"
        )

    # The benign auth-fail should NOT be a WARNING.
    warnings = [e for (level, e) in captured if level == "warning"]
    debugs = [e for (level, e) in captured if level == "debug"]
    assert not warnings, f"expected no WARNING, got: {warnings}"
    assert debugs, "expected at least one DEBUG entry, got nothing"


@pytest.mark.asyncio
async def test_fetch_genuine_failure_still_warns() -> None:
    """Network errors / other real failures must still log at WARNING."""
    workspace = Path("/tmp/fake-workspace")
    fake_result = CompletedProcess(
        args=["git", "fetch", "origin"],
        returncode=128,
        stdout="",
        stderr=(
            "fatal: unable to access 'https://github.com/owner/repo.git/': "
            "Could not resolve host: github.com\n"
        ),
    )

    captured: list[tuple[str, str]] = []

    def capture_warning(event: str, **_kw: object) -> None:
        captured.append(("warning", event))

    def capture_debug(event: str, **_kw: object) -> None:
        captured.append(("debug", event))

    with (
        patch("roboco.services.workspace.subprocess.run", return_value=fake_result),
        patch("roboco.services.workspace.logger.warning", side_effect=capture_warning),
        patch("roboco.services.workspace.logger.debug", side_effect=capture_debug),
    ):
        await WorkspaceService._fetch_origin_best_effort(
            workspace=workspace, project_slug="roboco-api"
        )

    warnings = [e for (level, e) in captured if level == "warning"]
    assert warnings, f"network errors should still warn, got: {captured}"
