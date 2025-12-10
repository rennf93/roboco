"""
FastAPI Application Factory

Creates and configures the FastAPI application with all routes,
middleware, and event handlers.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from roboco.api.middleware import setup_middleware
from roboco.config import settings
from roboco.db.base import close_db, init_db
from roboco.logging import get_logger, setup_logging
from roboco.services.extraction import ExtractionPipeline, ExtractionService
from roboco.services.optimal import close_optimal_service, get_optimal_service
from roboco.services.transcription import TranscriptionService

# Setup logging before anything else
setup_logging()
logger = get_logger(__name__)

# Global service instances (initialized in lifespan)
transcription_service: TranscriptionService | None = None
extraction_pipeline: ExtractionPipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    global transcription_service, extraction_pipeline

    logger.info(
        "Starting RoboCo API",
        version=settings.app_version,
        environment=settings.environment,
    )

    # Startup
    if settings.environment == "development":
        # Only auto-create tables in development
        # Use Alembic migrations in production
        await init_db()
        logger.info("Database initialized (development mode)")

    # Initialize Phase 2 services
    transcription_service = TranscriptionService()
    await transcription_service.start()

    extraction_service = ExtractionService()
    extraction_pipeline = ExtractionPipeline(extraction_service)

    # Store in app state for access in routes
    app.state.transcription = transcription_service
    app.state.extraction = extraction_pipeline

    # Initialize Phase 3 services
    optimal_service = await get_optimal_service()
    app.state.optimal = optimal_service

    logger.info("All services initialized")

    yield

    # Shutdown
    logger.info("Shutting down RoboCo API")

    if transcription_service:
        await transcription_service.stop()

    # Close Phase 3 services
    await close_optimal_service()

    await close_db()

    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="RoboCo API",
        description="AI Agents Company - Messaging and Task Management API",
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # ==========================================================================
    # Middleware
    # ==========================================================================

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Setup custom middleware (error handling, logging, correlation IDs)
    setup_middleware(app)

    # ==========================================================================
    # Routes
    # ==========================================================================

    from roboco.api.routes import (
        channels,
        dashboard,
        health,
        journals,
        kanban,
        messages,
        notifications,
        optimal,
        orchestrator,
        sessions,
        stream,
        tasks,
    )

    # Health check
    app.include_router(health.router, tags=["Health"])

    # API v1
    api_prefix = "/api/v1"

    app.include_router(
        channels.router,
        prefix=f"{api_prefix}/channels",
        tags=["Channels"],
    )

    app.include_router(
        sessions.router,
        prefix=f"{api_prefix}/sessions",
        tags=["Sessions"],
    )

    app.include_router(
        messages.router,
        prefix=f"{api_prefix}/messages",
        tags=["Messages"],
    )

    app.include_router(
        notifications.router,
        prefix=f"{api_prefix}/notifications",
        tags=["Notifications"],
    )

    # Phase 2: Stream processing and permissions
    app.include_router(
        stream.router,
        prefix=f"{api_prefix}/stream",
        tags=["Stream Processing"],
    )

    # Phase 3: Intelligence - Optimal API and Journal API
    app.include_router(
        optimal.router,
        prefix=api_prefix,
        tags=["Optimal API"],
    )

    app.include_router(
        journals.router,
        prefix=api_prefix,
        tags=["Journals"],
    )

    # Phase 5: Management - Tasks, Kanban, Dashboards
    app.include_router(
        tasks.router,
        prefix=api_prefix,
        tags=["Tasks"],
    )

    app.include_router(
        kanban.router,
        prefix=api_prefix,
        tags=["Kanban"],
    )

    app.include_router(
        dashboard.router,
        prefix=api_prefix,
        tags=["Dashboard"],
    )

    # Phase 7: Agent Runtime
    app.include_router(
        orchestrator.router,
        prefix=f"{api_prefix}/orchestrator",
        tags=["Orchestrator"],
    )

    # ==========================================================================
    # WebSocket
    # ==========================================================================

    from roboco.api.websocket import router as ws_router

    app.include_router(ws_router, prefix="/ws", tags=["WebSocket"])

    return app


# Create the default application instance
app = create_app()
