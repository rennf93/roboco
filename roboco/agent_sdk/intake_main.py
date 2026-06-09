"""Container entrypoint for the intake (``prompter``) agent — the live session.

Runs as the agent-prompter container's command. Wires the ``IntakeDriver`` to:

- an in-process HTTP **receiver** (`POST /turn`) the orchestrator delivers the
  human's messages to — this is the driver's ``MessageSource``;
- a **relay** ``EventSink`` that POSTs each ``StreamChunk`` to the
  orchestrator's `/live/{session}/events` endpoint.

One long-lived ``ClaudeSDKClient`` (opened by the driver's ``SdkIntakeSession``)
holds the whole conversation. The container stays up until reaped.

The wiring helpers are unit-tested; ``main()`` (env + uvicorn + SDK) is not — it
needs the live container.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog
from fastapi import FastAPI
from pydantic import BaseModel, Field

from roboco.agent_sdk.intake_driver import (
    IntakeDriver,
    StreamChunk,
    build_intake_options,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

logger = structlog.get_logger()

_RECEIVER_PORT = 9000  # ROBOCO_SDK_PORT — the orchestrator delivers messages here


class _Turn(BaseModel):
    text: str = Field(..., min_length=1)


def make_message_source(
    queue: asyncio.Queue[str | None],
) -> Callable[[], Awaitable[str | None]]:
    """A ``MessageSource`` backed by the receiver queue. ``None`` ends the loop."""

    async def _next() -> str | None:
        return await queue.get()

    return _next


def make_relay_sink(
    base_url: str, session_id: str, client: httpx.AsyncClient
) -> Callable[[StreamChunk], Awaitable[None]]:
    """An ``EventSink`` that POSTs each chunk to the orchestrator relay."""
    url = f"{base_url}/api/prompter/live/{session_id}/events"

    async def _emit(chunk: StreamChunk) -> None:
        try:
            await client.post(
                url,
                json={
                    "kind": chunk.kind,
                    "text": chunk.text,
                    "tool": chunk.tool,
                    "data": chunk.data,
                },
            )
        except Exception as exc:
            logger.error("Relay POST failed", session_id=session_id, error=str(exc))

    return _emit


def build_receiver(queue: asyncio.Queue[str | None]) -> FastAPI:
    """The in-container HTTP receiver: `POST /turn` enqueues the human's message."""
    app = FastAPI()

    @app.post("/turn")
    async def turn(body: _Turn) -> dict[str, bool]:
        await queue.put(body.text)
        return {"queued": True}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def main() -> None:  # pragma: no cover - needs the live container + SDK
    """Wire receiver + driver and run them concurrently for the chat's lifetime."""
    import uvicorn

    # Silence the Claude CLI's "configuration file not found at ~/.claude.json"
    # warning (printed 3x at startup) that otherwise drowns the container logs.
    # The CLI self-heals the file anyway; pre-creating an empty config just stops
    # the noise before the SDK spawns the CLI. Best-effort — never fail the boot.
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        try:
            claude_json.write_text("{}", encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not pre-create ~/.claude.json", error=str(exc))

    session_id = os.environ["ROBOCO_PROMPTER_SESSION_ID"]
    base_url = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
    cwd = os.environ.get("ROBOCO_WORKSPACE", "/data/workspace")
    system_prompt = Path("/app/system-prompt.md").read_text(encoding="utf-8")
    model = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or None

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    client = httpx.AsyncClient(timeout=30.0)

    # Tools + MCP + lockdown are all decided inside build_intake_options now
    # (hard allowlist + the propose_draft tool + host-env isolation).
    options = build_intake_options(
        system_prompt=system_prompt,
        cwd=cwd,
        model=model,
    )

    from roboco.agent_sdk.intake_driver import SdkIntakeSession

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[SdkIntakeSession]:
        async with SdkIntakeSession(options) as session:
            yield session

    driver = IntakeDriver(
        session_factory,
        make_message_source(queue),
        make_relay_sink(base_url, session_id, client),
    )

    # Bind all interfaces so the orchestrator reaches the receiver on the docker
    # network. Built from octets (bandit B104 false positive — same as the SDK
    # sidecar); override via ROBOCO_SDK_BIND_HOST for local dev.
    bind_host = os.environ.get("ROBOCO_SDK_BIND_HOST", ".".join(["0"] * 4))
    server = uvicorn.Server(
        uvicorn.Config(
            build_receiver(queue),
            host=bind_host,
            port=_RECEIVER_PORT,
            log_level="warning",
        )
    )
    logger.info("Intake container starting", session_id=session_id)
    try:
        await asyncio.gather(server.serve(), driver.run())
    finally:
        await client.aclose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
