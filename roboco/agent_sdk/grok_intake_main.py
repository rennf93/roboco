"""Container entrypoint for the GROK intake (prompter) agent — opencode serve.

The Grok analogue of ``intake_main``: the same in-container ``POST /turn``
receiver and the same relay sink to ``/api/prompter/live/{id}/events``, but the
held-open session is an :class:`OpencodeServeSession` (``opencode serve``)
instead of a ``ClaudeSDKClient``. ``opencode.json`` (xAI provider + MCP gateway +
system prompt) is rendered first so the serve process is gateway-wired exactly
like the one-shot Grok path. The ``IntakeDriver`` loop, message source, and relay
are reused unchanged — only the ``SessionFactory`` differs.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import structlog

from roboco.agent_sdk.intake_driver import IntakeDriver
from roboco.agent_sdk.intake_main import (
    build_receiver,
    make_message_source,
    make_relay_sink,
)
from roboco.agent_sdk.opencode_session import OpencodeServeSession, serve_port
from roboco.llm.providers import opencode_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

_RECEIVER_PORT = 9000  # ROBOCO_SDK_PORT — the orchestrator delivers messages here


async def main() -> None:  # pragma: no cover - needs the live container + opencode
    """Render opencode.json, then run the receiver + driver for the chat's life."""
    import uvicorn

    # Render opencode.json (provider/model/MCP gateway/instructions) from the
    # spawn env so `opencode serve` is gateway-wired before it starts.
    opencode_config.main()

    session_id = os.environ["ROBOCO_PROMPTER_SESSION_ID"]
    base_url = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
    cwd = os.environ.get("ROBOCO_WORKSPACE", "/data/workspace")

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    client = httpx.AsyncClient(timeout=30.0)

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[OpencodeServeSession]:
        async with OpencodeServeSession(port=serve_port(), cwd=cwd) as session:
            yield session

    driver = IntakeDriver(
        session_factory,
        make_message_source(queue),
        make_relay_sink(base_url, session_id, client),
    )

    bind_host = os.environ.get("ROBOCO_SDK_BIND_HOST", ".".join(["0"] * 4))
    server = uvicorn.Server(
        uvicorn.Config(
            build_receiver(queue),
            host=bind_host,
            port=_RECEIVER_PORT,
            log_level="warning",
        )
    )
    logger.info("Grok intake container starting", session_id=session_id)
    try:
        await asyncio.gather(server.serve(), driver.run())
    finally:
        await client.aclose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
