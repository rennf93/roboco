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
from roboco.api.routes.agents import router as agents_router
from roboco.api.routes.channels import router as channels_router
from roboco.api.routes.dashboard import router as dashboard_router
from roboco.api.routes.health import router as health_router
from roboco.api.routes.journals import router as journals_router
from roboco.api.routes.kanban import router as kanban_router
from roboco.api.routes.messages import router as messages_router
from roboco.api.routes.notifications import router as notifications_router
from roboco.api.routes.optimal import router as optimal_router
from roboco.api.routes.orchestrator import router as orchestrator_router
from roboco.api.routes.sessions import router as sessions_router
from roboco.api.routes.stream import router as stream_router
from roboco.api.routes.tasks import router as tasks_router
from roboco.api.websocket import router as ws_router
from roboco.config import settings
from roboco.db.base import close_db, init_db
from roboco.logging import get_logger, setup_logging
from roboco.services.extraction import ExtractionPipeline, ExtractionService
from roboco.services.optimal import close_optimal_service, get_optimal_service
from roboco.services.transcription import TranscriptionService

# Setup logging before anything else
setup_logging()
logger = get_logger(__name__)


class _AppServices:
    """Holder for application service instances (initialized in lifespan)."""

    transcription: TranscriptionService | None = None
    extraction: ExtractionPipeline | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
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
    _AppServices.transcription = TranscriptionService()
    await _AppServices.transcription.start()

    extraction_service = ExtractionService()
    _AppServices.extraction = ExtractionPipeline(extraction_service)

    # Store in app state for access in routes
    app.state.transcription = _AppServices.transcription
    app.state.extraction = _AppServices.extraction

    # Initialize Phase 3 services (RAG - optional, non-blocking)
    try:
        optimal_service = await get_optimal_service()
        app.state.optimal = optimal_service
        logger.info("OptimalService (RAG) initialized successfully")
    except Exception as e:
        logger.warning(
            "OptimalService (RAG) initialization failed - RAG features disabled",
            error=str(e),
        )
        app.state.optimal = None

    logger.info("All services initialized")

    yield

    # Shutdown
    logger.info("Shutting down RoboCo API")

    if _AppServices.transcription:
        await _AppServices.transcription.stop()

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
        docs_url="/docs", # if settings.debug else None,
        redoc_url="/redoc", # if settings.debug else None,
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

    # Health check
    app.include_router(health_router, tags=["Health"])

    # API v1
    api_prefix = "/api/v1"

    app.include_router(
        agents_router,
        prefix=f"{api_prefix}/agents",
        tags=["Agents"],
    )

    app.include_router(
        channels_router,
        prefix=f"{api_prefix}/channels",
        tags=["Channels"],
    )

    app.include_router(
        sessions_router,
        prefix=f"{api_prefix}/sessions",
        tags=["Sessions"],
    )

    app.include_router(
        messages_router,
        prefix=f"{api_prefix}/messages",
        tags=["Messages"],
    )

    app.include_router(
        notifications_router,
        prefix=f"{api_prefix}/notifications",
        tags=["Notifications"],
    )

    # Phase 2: Stream processing and permissions
    app.include_router(
        stream_router,
        prefix=f"{api_prefix}/stream",
        tags=["Stream Processing"],
    )

    # Phase 3: Intelligence - Optimal API and Journal API
    app.include_router(
        optimal_router,
        prefix=f"{api_prefix}/optimal",
        tags=["Optimal API"],
    )

    app.include_router(
        journals_router,
        prefix=f"{api_prefix}/journals",
        tags=["Journals"],
    )

    # Phase 5: Management - Tasks, Kanban, Dashboards
    app.include_router(
        tasks_router,
        prefix=f"{api_prefix}/tasks",
        tags=["Tasks"],
    )

    app.include_router(
        kanban_router,
        prefix=f"{api_prefix}/kanban",
        tags=["Kanban"],
    )

    app.include_router(
        dashboard_router,
        prefix=f"{api_prefix}/dashboard",
        tags=["Dashboard"],
    )

    # Phase 7: Agent Runtime
    app.include_router(
        orchestrator_router,
        prefix=f"{api_prefix}/orchestrator",
        tags=["Orchestrator"],
    )

    # ==========================================================================
    # WebSocket
    # ==========================================================================
    app.include_router(ws_router, prefix="/ws", tags=["WebSocket"])

    return app


# Create the default application instance
app = create_app()
