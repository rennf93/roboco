"""A local-shell stand-in for ``run_in_workspace_container``.

The container runner has its own tests (tests/unit/services/
test_workspace_container.py). Callers' logic — gate aggregation, the
dep-update probe's clone → upgrade → status sequence — is exercised with real
commands, run here in a plain subprocess instead of a throwaway container.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from roboco.services.workspace_container import WorkspaceContainerResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


async def run_in_local_shell(
    cwd: Path,
    argv: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    merge_stderr: bool = False,
) -> WorkspaceContainerResult:
    """Run ``argv`` in ``cwd`` locally with the container runner's signature."""
    del env  # the container's env is the image's; locally, inherit ours
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return WorkspaceContainerResult(
        proc.returncode if proc.returncode is not None else 1,
        out.decode(),
        (err or b"").decode(),
    )
