"""Container entrypoint for the GROK Secretary agent — opencode serve.

The Grok analogue of ``secretary_main``: the same in-container ``POST /turn``
receiver and the same relay sink to ``/api/secretary/live/{id}/events``, but the
held-open session is an :class:`OpencodeServeSession` (``opencode serve``) rather
than a ``ClaudeSDKClient``. ``opencode.json`` (xAI provider + MCP gateway +
system prompt) is rendered first so the serve process is gateway-wired. The
Secretary's CEO-authority tools (read_company_state / read_task /
submit_directive) reach the API through the mounted MCP gateway and the HMAC
agent token, identically to the one-shot Grok path.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import structlog

from roboco.agent_sdk.intake_driver import IntakeDriver
from roboco.agent_sdk.intake_main import build_receiver, make_message_source
from roboco.agent_sdk.opencode_session import OpencodeServeSession, serve_port
from roboco.agent_sdk.secretary_main import make_relay_sink
from roboco.llm.providers import opencode_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

_RECEIVER_PORT = 9000  # ROBOCO_SDK_PORT — the orchestrator delivers messages here


async def main() -> None:  # pragma: no cover - needs the live container + opencode
    """Render opencode.json, then run the receiver + driver for the chat's life."""
    import uvicorn

    opencode_config.main()

    session_id = os.environ["ROBOCO_SECRETARY_SESSION_ID"]
    base_url = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
    cwd = os.environ.get("ROBOCO_WORKSPACE", "/app")

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
    logger.info("Grok secretary container starting", session_id=session_id)
    try:
        await asyncio.gather(server.serve(), driver.run())
    finally:
        await client.aclose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
