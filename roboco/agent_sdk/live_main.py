"""Provider-generic interactive live-chat driver (secretary + intake).

The container entrypoint for the ``roboco-agent-<provider>-live`` images:
holds the SAME POST /turn receiver + relay sink the Claude/grok drivers use,
but each turn is one headless CLI invocation
(:class:`roboco.agent_sdk.live_cli_session.HeadlessCliTurnSession`) on the
provider the operator routed - hummin, codex, gemini, kimi, or either
opencode dialect (openrouter/nebius). The provider selects the argv builder
(:mod:`roboco.agent_sdk.live_providers`); the role (ROBOCO_AGENT_ROLE)
selects the relay (secretary vs prompter) - everything else is shared with
the existing drivers unchanged.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from roboco.agent_sdk.intake_driver import IntakeDriver
from roboco.agent_sdk.intake_main import build_receiver, make_message_source
from roboco.agent_sdk.live_cli_session import HeadlessCliTurnSession
from roboco.agent_sdk.live_providers import get_live_argv_builder
from roboco.agent_sdk.secretary_main import make_relay_sink as _secretary_relay_sink
from roboco.llm.providers.hummin_live_config import render_extension_file

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

_RECEIVER_PORT = 9000  # ROBOCO_SDK_PORT - the orchestrator delivers turns here


def _make_relay_sink(
    role: str, base_url: str, session_id: str, client: httpx.AsyncClient
) -> Any:
    """The relay sink for the chat's role (secretary vs prompter paths)."""
    if role == "secretary":
        return _secretary_relay_sink(base_url, session_id, client)
    from roboco.agent_sdk.intake_main import make_relay_sink as _intake_relay_sink

    return _intake_relay_sink(base_url, session_id, client)


async def main() -> None:  # pragma: no cover - needs the live container + CLI
    """Render provider glue, then run the receiver + driver for the chat."""
    import uvicorn

    provider = os.environ.get("ROBOCO_LIVE_PROVIDER", "").strip()
    if not provider:
        raise RuntimeError("ROBOCO_LIVE_PROVIDER is not set - cannot pick a CLI")
    argv_builder = get_live_argv_builder(provider)

    role = os.environ.get("ROBOCO_AGENT_ROLE", "intake")
    # hummin's tool bridge rides the rendered pi extension (no MCP client).
    if provider == "hummin":
        render_extension_file(role)

    session_id = os.environ.get("ROBOCO_SECRETARY_SESSION_ID", "") or os.environ.get(
        "ROBOCO_PROMPTER_SESSION_ID", ""
    )
    base_url = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
    cwd = os.environ.get("ROBOCO_WORKSPACE", "/app")

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    client = httpx.AsyncClient(timeout=30.0)

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[HeadlessCliTurnSession]:
        async with HeadlessCliTurnSession(
            argv_builder=argv_builder,
            cwd=cwd,
            usage_file=os.environ.get("ROBOCO_LIVE_USAGE_FILE"),
        ) as session:
            yield session

    driver = IntakeDriver(
        session_factory,
        make_message_source(queue),
        _make_relay_sink(role, base_url, session_id, client),
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
    logger.info(
        "Live CLI chat container starting",
        provider=provider,
        role=role,
        session_id=session_id,
    )
    try:
        await asyncio.gather(server.serve(), driver.run())
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
