"""The Entire CLI, run on the dispatcher for the trails bridge.

The pinned CLI sits off PATH in the orchestrator image
(``docker/orchestrator.Dockerfile``, ``ENTIRE_CLI``). Every call is argv only
(``--flag=value``, so a value starting with ``-`` stays a value), with an
environment built from scratch: the API token travels as ``ENTIRE_TOKEN`` in
that environment alone, never argv or a file, and is scrubbed from anything
the call returns. ``--project`` / ``--repo`` select the trail, so the CLI runs
outside any git repository, in a dispatcher-private home.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.services.entire_hooks import ENTIRE_CLI
from roboco.services.workspace_git import SAFE_PATH

if TYPE_CHECKING:
    from collections.abc import Sequence

_REDACTED = "<REDACTED>"
_KILL_GRACE_SECONDS = 5


class EntireCliError(RuntimeError):
    """An Entire CLI call that failed, timed out, or printed unusable output.
    The message never carries the token."""


@dataclass(frozen=True)
class EntireCliResult:
    """One finished CLI call, token-scrubbed."""

    returncode: int
    stdout: str
    stderr: str

    def json(self) -> dict[str, Any]:
        """The call's ``--json`` output as an object."""
        try:
            parsed = json.loads(self.stdout)
        except json.JSONDecodeError as e:
            raise EntireCliError(
                f"entire printed no JSON: {self.stdout[:300]!r}"
            ) from e
        if not isinstance(parsed, dict):
            raise EntireCliError(f"entire printed a non-object: {self.stdout[:300]!r}")
        return parsed


def entire_home() -> Path:
    """The CLI's private ``HOME`` (config and cache), created 0700."""
    home = Path(settings.private_data_root, "entire-home")
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    return home


def _cli_env(token: str, home: Path) -> dict[str, str]:
    return {
        "PATH": SAFE_PATH,
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ENTIRE_TOKEN": token,
        "ENTIRE_TELEMETRY_OPTOUT": "1",
    }


def _scrub(text: str, token: str) -> str:
    return text.replace(token, _REDACTED) if token else text


async def run_entire(
    args: Sequence[str], *, token: str, timeout: float | None = None
) -> EntireCliResult:
    """Run ``entire <args>``; raise :class:`EntireCliError` unless it exits 0."""
    if not token:
        raise EntireCliError("no Entire token is configured")
    home = await asyncio.to_thread(entire_home)
    limit = (
        timeout if timeout is not None else settings.entire_trails_cli_timeout_seconds
    )
    proc = await asyncio.create_subprocess_exec(
        ENTIRE_CLI,
        *args,
        cwd=home,
        env=_cli_env(token, home),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=limit)
    except TimeoutError as e:
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        raise EntireCliError(f"entire {args[0]} timed out after {limit}s") from e
    result = EntireCliResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=_scrub(out.decode(errors="replace"), token),
        stderr=_scrub(err.decode(errors="replace"), token),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise EntireCliError(
            f"entire {' '.join(args[:2])} exited {result.returncode}: {detail[-1000:]}"
        )
    return result


def cli_available() -> bool:
    """Whether the pinned CLI is installed here (the orchestrator image)."""
    return os.access(ENTIRE_CLI, os.X_OK)
