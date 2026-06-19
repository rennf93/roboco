"""Container entrypoint for the GROK intake (prompter) agent — grok CLI.

The Grok analogue of ``intake_main``: the same in-container ``POST /turn``
receiver and the same relay sink to ``/api/prompter/live/{id}/events``, but the
held-open session is a :class:`GrokCliSession` (per-turn headless ``grok -p``,
resuming one session id) instead of a ``ClaudeSDKClient``. ``~/.grok/config.toml``
is rendered first to wire the intake agent's one action tool, ``propose_draft``,
as the ``roboco-intake`` MCP server. Intake is a human-only interviewer with no
gateway verbs; its only MCP server is ``roboco-intake``. The ``IntakeDriver``
loop, message source, and relay are reused unchanged — only the
``SessionFactory`` differs.
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
from roboco.agent_sdk.intake_main import (
    build_receiver,
    make_message_source,
    make_relay_sink,
)
from roboco.llm.providers.grok_cli_config import (
    GROK_CONFIG_PATH,
    render_config_toml,
    write_agents_md,
    write_grok_hooks,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()

_RECEIVER_PORT = 9000  # ROBOCO_SDK_PORT — the orchestrator delivers messages here


def _render_grok_config(base_url: str, session_id: str) -> None:
    """Write ``~/.grok/config.toml`` wiring the ``roboco-intake`` MCP server.

    ``uv run --directory /app`` pins both the project env (the baked
    ``/app/.venv``) and the working directory to ``/app`` so ``-m
    roboco.mcp.intake_server`` resolves the INSTALLED package, never a workspace
    clone that might shadow it (the ModuleNotFound lesson).
    """
    mcp_servers = {
        "roboco-intake": {
            "command": "uv",
            "args": [
                "run",
                "--directory",
                "/app",
                "--no-sync",
                "python",
                "-m",
                "roboco.mcp.intake_server",
            ],
            "env": {
                "ROBOCO_API_URL": base_url,
                "ROBOCO_PROMPTER_SESSION_ID": session_id,
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

    session_id = os.environ["ROBOCO_PROMPTER_SESSION_ID"]
    base_url = os.environ.get("ROBOCO_API_URL", "http://roboco-orchestrator:8000")
    cwd = os.environ.get("ROBOCO_WORKSPACE", "/data/workspace")

    _render_grok_config(base_url, session_id)
    # Install the role blueprint as grok's global system prompt (~/.grok/AGENTS.md)
    # and the bash-guard PreToolUse hook (defense-in-depth: a no-op while shell is
    # disallowed, but survives any future shell re-enable, matching the one-shot path).
    write_agents_md()
    write_grok_hooks()

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    client = httpx.AsyncClient(timeout=30.0)

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[GrokCliSession]:
        async with GrokCliSession(
            cwd=cwd,
            agent_id=os.environ.get("ROBOCO_AGENT_ID", ""),
            model=os.environ.get("ROBOCO_AGENT_MODEL", "grok-build"),
            usage_file=os.environ.get("ROBOCO_GROK_USAGE_FILE"),
            # Intake reads sibling product repos that sit outside its cwd under
            # the mounted workspaces tree — keep those reads allowed.
            extra_args=["--allow", "Read(/data/workspaces/**)"],
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
    logger.info("Grok intake container starting", session_id=session_id)
    try:
        await asyncio.gather(server.serve(), driver.run())
    finally:
        await client.aclose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
