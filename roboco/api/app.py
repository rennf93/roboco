"""
FastAPI Application Factory

Creates and configures the FastAPI application with all routes,
middleware, and event handlers.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from roboco.api.deps import _auth_required
from roboco.api.middleware import setup_middleware
from roboco.api.routes.a2a import router as a2a_router
from roboco.api.routes.a2a import wellknown_router as a2a_wellknown_router
from roboco.api.routes.agents import router as agents_router
from roboco.api.routes.channels import router as channels_router
from roboco.api.routes.dashboard import router as dashboard_router
from roboco.api.routes.docs import router as docs_router
from roboco.api.routes.git import router as git_router
from roboco.api.routes.groups import router as groups_router
from roboco.api.routes.health import router as health_router
from roboco.api.routes.journals import router as journals_router
from roboco.api.routes.kanban import router as kanban_router
from roboco.api.routes.messages import router as messages_router
from roboco.api.routes.notifications import router as notifications_router
from roboco.api.routes.optimal import router as optimal_router
from roboco.api.routes.orchestrator import router as orchestrator_router
from roboco.api.routes.product import router as product_router
from roboco.api.routes.project import router as project_router
from roboco.api.routes.provider import router as provider_router
from roboco.api.routes.sessions import router as sessions_router
from roboco.api.routes.stream import router as stream_router
from roboco.api.routes.tasks import router as tasks_router
from roboco.api.routes.v1 import do as do_module
from roboco.api.routes.v1 import flow_auditor as flow_auditor_module
from roboco.api.routes.v1 import flow_board as flow_board_module
from roboco.api.routes.v1 import flow_cell_pm as flow_cell_pm_module
from roboco.api.routes.v1 import flow_dev as flow_dev_module
from roboco.api.routes.v1 import flow_doc as flow_doc_module
from roboco.api.routes.v1 import flow_main_pm as flow_main_pm_module
from roboco.api.routes.v1 import flow_qa as flow_qa_module
from roboco.api.routes.work_session import router as work_session_router
from roboco.api.websocket import router as ws_router
from roboco.config import settings
from roboco.db.base import close_db, init_db
from roboco.logging import get_logger, setup_logging
from roboco.services.extraction import ExtractionPipeline, ExtractionService
from roboco.services.learning import get_learning_service
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

    if not _auth_required():
        logger.warning(
            "Agent auth is in HEADER-TRUST mode (ROBOCO_AGENT_AUTH_REQUIRED is "
            "not set to true): the API accepts X-Agent-Id / X-Agent-Role without "
            "verifying a signed token, so any client that can reach it may act as "
            "any role, including 'ceo'. Acceptable only on a trusted private "
            "network. Set ROBOCO_AGENT_AUTH_REQUIRED=true and do NOT expose this "
            "API to untrusted networks.",
        )

    # Startup: apply Alembic migrations (+ create_all fallback for fresh DBs).
    # init_db runs on every environment now — migrations are idempotent via
    # alembic_version, and this is the only way new schema (e.g. enum value
    # additions like NotificationType.APPROVAL) reaches the running DB.
    await init_db()
    logger.info("Database initialized")

    # Initialize Phase 2 services
    _AppServices.transcription = TranscriptionService()
    await _AppServices.transcription.start()

    extraction_service = ExtractionService()
    _AppServices.extraction = ExtractionPipeline(extraction_service)

    # Store in app state for access in routes
    app.state.transcription = _AppServices.transcription
    app.state.extraction = _AppServices.extraction

    # Initialize OptimalService (RAG) - BLOCKS until fully ready
    # This ensures /health only returns 200 when RAG is operational
    # Typical initialization time: 30-90 seconds (embedding + indexing)
    try:
        logger.info("Initializing OptimalService (RAG)...")
        optimal_service = await get_optimal_service()
        app.state.optimal = optimal_service
        logger.info("OptimalService (RAG) initialized successfully")
    except Exception as e:
        logger.warning(
            "OptimalService (RAG) initialization failed - RAG features disabled",
            error=str(e),
        )
        app.state.optimal = None

    # Wire the learning-propagation singleton to OptimalService. Without this,
    # record_learning() raises "not initialized" and every task completion logs
    # "Failed to extract learnings". Skipped when RAG is disabled (no optimal).
    if app.state.optimal is not None:
        try:
            learning_service = await get_learning_service()
            await learning_service.initialize(app.state.optimal)
            logger.info("LearningPropagationService initialized")
        except Exception as e:
            logger.warning("LearningPropagationService init failed", error=str(e))

    logger.info("All services initialized, API ready")

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
        docs_url="/docs",  # if settings.debug else None,
        redoc_url="/redoc",  # if settings.debug else None,
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

    # A2A Protocol: Well-known endpoints at root level
    # (/.well-known/agent.json, /agents/{id}/.well-known/agent.json)
    app.include_router(a2a_wellknown_router, tags=["A2A Protocol"])

    # API v1
    api_prefix = "/api"

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
        groups_router,
        prefix=f"{api_prefix}/groups",
        tags=["Groups"],
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

    # A2A Protocol: API endpoints
    app.include_router(
        a2a_router,
        prefix=f"{api_prefix}/a2a",
        tags=["A2A Protocol"],
    )

    # Git Integration
    app.include_router(
        git_router,
        prefix=f"{api_prefix}/git",
        tags=["Git Operations"],
    )

    # Project Management
    app.include_router(
        project_router,
        prefix=f"{api_prefix}/projects",
        tags=["Projects"],
    )

    # Product Management
    app.include_router(
        product_router,
        prefix=f"{api_prefix}/products",
        tags=["Products"],
    )

    # AI Providers (model routing + Ollama-cloud fallback)
    app.include_router(
        provider_router,
        prefix=f"{api_prefix}/providers",
        tags=["Providers"],
    )

    # Work Sessions
    app.include_router(
        work_session_router,
        prefix=f"{api_prefix}/work-sessions",
        tags=["Work Sessions"],
    )

    # Documentation
    app.include_router(
        docs_router,
        prefix=f"{api_prefix}/docs",
        tags=["Documentation"],
    )

    # API v1 — intent-verb flow endpoints
    app.include_router(flow_dev_module.router)

    # API v1 — intent-verb QA flow endpoints
    app.include_router(flow_qa_module.router)

    # API v1 — intent-verb documenter flow endpoints
    app.include_router(flow_doc_module.router)

    # API v1 — intent-verb cell PM flow endpoints
    app.include_router(flow_cell_pm_module.router)

    # API v1 — intent-verb main PM flow endpoints
    app.include_router(flow_main_pm_module.router)

    # API v1 — intent-verb board flow endpoints
    app.include_router(flow_board_module.router)

    # API v1 — intent-verb auditor flow endpoints
    app.include_router(flow_auditor_module.router)

    # API v1 — content-tool endpoints
    app.include_router(do_module.router)

    # ==========================================================================
    # WebSocket
    # ==========================================================================
    app.include_router(ws_router, prefix="/ws", tags=["WebSocket"])

    return app


# Create the default application instance
app = create_app()
