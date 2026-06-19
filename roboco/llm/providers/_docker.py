"""Shared Docker container-lifecycle helpers for providers.

Small, dependency-free wrappers around the ``docker`` CLI so each provider's
``stop`` / ``health_check`` reads the same way. Spawn and remove stay in the
providers (spawn is backend-specific; remove delegates to the orchestrator so
its log-dump-before-remove behaviour is preserved).
"""

from __future__ import annotations

import asyncio

from roboco.llm.providers.base import ProviderError


async def stop_container(name: str, graceful: bool = True) -> None:
    """Stop a container by name/id (``docker stop`` = SIGTERM, ``kill`` = SIGKILL)."""
    verb = "stop" if graceful else "kill"
    proc = await asyncio.create_subprocess_exec(
        "docker",
        verb,
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ProviderError(f"docker {verb} {name} failed: {stderr.decode().strip()}")


async def container_running(name: str) -> bool:
    """Return True if the named container exists and is running."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "inspect",
        "--format={{.State.Running}}",
        name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return proc.returncode == 0 and stdout.decode().strip() == "true"
