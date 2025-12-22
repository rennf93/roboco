"""
RoboCo Bootstrap Script

Orchestrates system initialization: database, event bus, API server, and agent runtime.
Data constants are in roboco/seeds/, database operations in roboco/db/seed.py.
"""

import asyncio
from pathlib import Path

import structlog
import uvicorn

from roboco.agents import set_reasoning_stream_callback
from roboco.api.deps import set_orchestrator
from roboco.api.websocket import broadcast_agent_chunk
from roboco.config import settings
from roboco.db import bootstrap_database
from roboco.events import EventBus, register_default_handlers, set_event_context
from roboco.runtime import AgentOrchestrator
from roboco.services.notification import NotificationService

logger = structlog.get_logger()


class _BootstrapHolder:
    """Holder for bootstrap singleton instances."""

    orchestrator: AgentOrchestrator | None = None


async def _run_api_server() -> None:
    """Run the uvicorn API server."""
    config = uvicorn.Config(
        "roboco.api.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        reload=False,  # Don't reload in production/container
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main(
    skip_db: bool = False,
    skip_orchestrator: bool = False,
    spawn_agents: list[str] | None = None,
) -> None:
    """
    Main bootstrap entry point.

    Args:
        skip_db: Skip database initialization
        skip_orchestrator: Skip starting orchestrator
        spawn_agents: List of agent IDs to spawn immediately
    """
    logger.info("RoboCo Bootstrap starting...")

    if not skip_db:
        await bootstrap_database()

    if skip_orchestrator:
        logger.info("Orchestrator skipped, exiting")
        return

    # Initialize event bus
    event_bus = EventBus()
    await event_bus.connect()
    register_default_handlers(event_bus)
    await event_bus.start_listening()
    logger.info("Event bus initialized")

    # Initialize orchestrator
    orchestrator = AgentOrchestrator(
        blueprints_dir=Path("agents/blueprints"),
    )
    _BootstrapHolder.orchestrator = orchestrator

    # Set orchestrator in API routes
    set_orchestrator(orchestrator)

    # Wire up event handler dependencies (dependency injection)
    notification_service = NotificationService()
    set_event_context(
        notification_service=notification_service,
        orchestrator=orchestrator,
    )

    # Wire up agent reasoning stream (dependency injection)
    set_reasoning_stream_callback(broadcast_agent_chunk)

    # Start API server in background task
    api_task = asyncio.create_task(_run_api_server())
    logger.info("API server starting", host=settings.host, port=settings.port)

    # Wait a moment for API to start before orchestrator begins polling
    await asyncio.sleep(2)

    await orchestrator.start()

    # Spawn requested agents
    if spawn_agents:
        startup_prompt = (
            "You are starting up. Call roboco_task_scan() to look for pending work."
        )
        for agent_id in spawn_agents:
            try:
                await orchestrator.spawn_agent(
                    agent_id=agent_id,
                    initial_prompt=startup_prompt,
                )
            except Exception as e:
                logger.error("Failed to spawn agent", agent_id=agent_id, error=str(e))

    try:
        # Wait for API server task (runs forever unless cancelled)
        await api_task
    except asyncio.CancelledError:
        pass
    finally:
        await orchestrator.stop()
        await event_bus.disconnect()
        _BootstrapHolder.orchestrator = None
        logger.info("RoboCo shutdown complete")


if __name__ == "__main__":
    from roboco.cli import cli

    cli()
