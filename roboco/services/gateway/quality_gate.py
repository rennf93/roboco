"""Run a project's fast quality gate in a developer's workspace.

Invoked by the developer's ``i_am_done`` submit so a red gate is caught at the
dev's desk instead of surfacing in QA review or CI. The gate runs the project's
configured *non-mutating* fast checks (lint, typecheck); the slow test suite
intentionally stays on CI. The gate is fail-open on infrastructure errors (a
missing workspace or absent toolchain never blocks a submit) and fail-closed on
an actual check failure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# A single check can be slow (mypy on a large tree); cap it generously but
# never hang the submit verb forever.
_GATE_TIMEOUT_SECONDS = 600
# Cap the remediate excerpt so a huge lint dump doesn't bloat the envelope.
_OUTPUT_EXCERPT_CHARS = 2000


@dataclass(frozen=True)
class GateResult:
    """Outcome of a pre-submit quality gate run."""

    passed: bool
    skipped: bool = False
    failures: tuple[str, ...] = ()
    output: str = ""

    @property
    def summary(self) -> str:
        if self.skipped:
            return "no quality commands configured (skipped)"
        if self.passed:
            return "all checks passed"
        return f"failed: {', '.join(self.failures)}"

    @property
    def output_excerpt(self) -> str:
        """The tail of the combined output (where errors usually are)."""
        return self.output[-_OUTPUT_EXCERPT_CHARS:]


async def run_quality_commands(
    workspace: Path, commands: list[tuple[str, str]]
) -> GateResult:
    """Run each ``(name, command)`` in ``workspace`` and aggregate the result.

    Every command runs (we don't stop at the first failure) so the developer
    sees all gate failures in one shot. A non-zero exit is a failure; its
    combined stdout+stderr is captured for the remediate hint.
    """
    if not commands:
        return GateResult(passed=True, skipped=True)
    failures: list[str] = []
    chunks: list[str] = []
    for name, command in commands:
        return_code, out = await _run_one(workspace, command)
        chunks.append(f"$ {command}\n{out.strip()}")
        if return_code != 0:
            failures.append(name)
    return GateResult(
        passed=not failures,
        failures=tuple(failures),
        output="\n\n".join(chunks),
    )


async def _run_one(workspace: Path, command: str) -> tuple[int, str]:
    """Run one operator-configured command string in the workspace shell."""
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_GATE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        proc.kill()
        # Reap the killed process and close the stdout/stderr pipe transports
        # (communicate() was cancelled, so it never closed them). Without this
        # the process lingers as a transient zombie and the FDs leak.
        await proc.wait()
        return 124, f"command timed out after {_GATE_TIMEOUT_SECONDS}s"
    except asyncio.CancelledError:
        # An outer cancellation (e.g. FlowVerbTimeoutMiddleware's own
        # asyncio.timeout expiring around the whole submit) throws in here
        # instead of the wait_for's own TimeoutError above — same orphaned
        # child + leaked FDs if left unkilled. Kill/reap, then propagate.
        proc.kill()
        await proc.wait()
        raise
    rc = proc.returncode
    if rc is None:
        # communicate() returned without a recorded exit code (the process
        # was terminated out-of-band). Fail closed — an unknown status must
        # not pass the gate.
        return 1, stdout.decode("utf-8", errors="replace")
    return rc, stdout.decode("utf-8", errors="replace")
