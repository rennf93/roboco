"""Container entrypoint for the GROK Secretary agent — grok CLI.

The Grok analogue of ``secretary_main``: the same in-container ``POST /turn``
receiver and the same relay sink to ``/api/secretary/live/{id}/events``, but the
held-open session is a :class:`GrokCliSession` (per-turn headless ``grok -p``,
resuming one session id) rather than a ``ClaudeSDKClient``. ``~/.grok/config.toml``
is rendered first to wire the Secretary's CEO-authority tools (read_company_state
/ read_task / search_tasks / submit_directive) as the ``roboco-secretary`` MCP
server, which
calls ``/api/secretary/*`` with the container's HMAC agent token — the same auth
the one-shot Grok path uses.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import structlog

from roboco.agent_sdk.grok_cli_session import GrokCliSession
from roboco.agent_sdk.intake_driver import IntakeDriver
from roboco.agent_sdk.intake_main import build_receiver, make_message_source
from roboco.agent_sdk.secretary_main import make_relay_sink
from roboco.llm.providers.grok_cli_config import (
    GROK_CONFIG_PATH,
    render_config_toml,
    write_agents_md,
    write_grok_fable_hooks,
    write_grok_hooks,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

_RECEIVER_PORT = 9000  # ROBOCO_SDK_PORT — the orchestrator delivers messages here


def _render_grok_config(base_url: str) -> None:
    """Write ``~/.grok/config.toml`` wiring the ``roboco-secretary`` MCP server.

    ``uv run --directory /app`` pins the project env + working directory to
    ``/app`` so ``-m roboco.mcp.secretary_server`` resolves the installed package
    (the ModuleNotFound lesson). The directive tools authenticate from the
    container's HMAC env, forwarded into the server's env below.
    """
    mcp_servers = {
        "roboco-secretary": {
            "command": "uv",
            "args": [
                "run",
                "--directory",
                "/app",
                "--no-sync",
                "python",
                "-m",
                "roboco.mcp.secretary_server",
            ],
            "env": {
                "ROBOCO_API_URL": base_url,
                "ROBOCO_AGENT_ID": os.environ.get("ROBOCO_AGENT_ID", ""),
                "ROBOCO_AGENT_ROLE": os.environ.get("ROBOCO_AGENT_ROLE", "secretary"),
                "ROBOCO_AGENT_TOKEN": os.environ.get("ROBOCO_AGENT_TOKEN", ""),
                "UV_PROJECT_ENVIRONMENT": "/app/.venv",
            },
        }
    }
    GROK_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GROK_CONFIG_PATH.write_text(
        render_config_toml({"mcpServers": mcp_servers}), encoding="utf-8"
    )


async def main() -> None:  # pragma: no cover - needs the live container + grok
    """Render config.toml, then run the receiver + driver for the chat's life."""
    import uvicorn

    session_id = os.environ["ROBOCO_SECRETARY_SESSION_ID"]
    base_url = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
    cwd = os.environ.get("ROBOCO_WORKSPACE", "/app")

    _render_grok_config(base_url)
    # Install the role blueprint as grok's global system prompt (~/.grok/AGENTS.md)
    # and the bash-guard PreToolUse hook (defense-in-depth; a no-op while shell is
    # disallowed, but survives any future shell re-enable, matching the one-shot path).
    write_agents_md()
    write_grok_hooks()
    write_grok_fable_hooks()

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    client = httpx.AsyncClient(timeout=30.0)

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[GrokCliSession]:
        async with GrokCliSession(
            cwd=cwd,
            agent_id=os.environ.get("ROBOCO_AGENT_ID", ""),
            model=os.environ.get("ROBOCO_AGENT_MODEL", "grok-build"),
            usage_file=os.environ.get("ROBOCO_GROK_USAGE_FILE"),
        ) as session:
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
