"""
FastAPI Application Factory

Creates and configures the FastAPI application with all routes,
middleware, and event handlers.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from roboco.api.auth.routes import mount_cloud_auth
from roboco.api.auth.seed import ensure_seed_user_startup
from roboco.api.deps import _auth_required, get_orchestrator_or_none
from roboco.api.middleware import setup_middleware
from roboco.api.routes.a2a import router as a2a_router
from roboco.api.routes.a2a import wellknown_router as a2a_wellknown_router
from roboco.api.routes.agents import router as agents_router
from roboco.api.routes.cockpit import router as cockpit_router
from roboco.api.routes.company_goals import router as company_goals_router
from roboco.api.routes.dashboard import router as dashboard_router
from roboco.api.routes.docs import router as docs_router
from roboco.api.routes.git import router as git_router
from roboco.api.routes.health import router as health_router
from roboco.api.routes.journals import router as journals_router
from roboco.api.routes.kanban import router as kanban_router
from roboco.api.routes.notifications import router as notifications_router
from roboco.api.routes.optimal import router as optimal_router
from roboco.api.routes.orchestrator import router as orchestrator_router
from roboco.api.routes.pitch import router as pitch_router
from roboco.api.routes.playbooks import router as playbooks_router
from roboco.api.routes.product import router as product_router
from roboco.api.routes.project import router as project_router
from roboco.api.routes.prompter_live import router as prompter_live_router
from roboco.api.routes.provider import router as provider_router
from roboco.api.routes.release import router as release_router
from roboco.api.routes.research import router as research_router
from roboco.api.routes.roadmap import router as roadmap_router
from roboco.api.routes.secretary import router as secretary_router
from roboco.api.routes.secretary_live import router as secretary_live_router
from roboco.api.routes.settings import router as settings_router
from roboco.api.routes.stream import router as stream_router
from roboco.api.routes.system import router as system_router
from roboco.api.routes.tasks import router as tasks_router
from roboco.api.routes.usage import router as usage_router
from roboco.api.routes.v1 import do as do_module
from roboco.api.routes.v1 import flow_auditor as flow_auditor_module
from roboco.api.routes.v1 import flow_board as flow_board_module
from roboco.api.routes.v1 import flow_cell_pm as flow_cell_pm_module
from roboco.api.routes.v1 import flow_dev as flow_dev_module
from roboco.api.routes.v1 import flow_doc as flow_doc_module
from roboco.api.routes.v1 import flow_main_pm as flow_main_pm_module
from roboco.api.routes.v1 import flow_pr_reviewer as flow_pr_reviewer_module
from roboco.api.routes.v1 import flow_qa as flow_qa_module
from roboco.api.routes.video import router as video_router
from roboco.api.routes.work_session import router as work_session_router
from roboco.api.routes.x import router as x_router
from roboco.api.websocket import router as ws_router
from roboco.config import settings
from roboco.db.base import close_db, get_session_factory, init_db
from roboco.logging import get_logger, setup_logging
from roboco.security import apply_guard, guarded_lifespan
from roboco.services.extraction import ExtractionPipeline, ExtractionService
from roboco.services.learning import get_learning_service
from roboco.services.optimal import close_optimal_service, get_optimal_service
from roboco.services.settings import apply_persisted_feature_flags
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

    # Cloud auth: idempotently upsert the single seeded CEO login user.
    # No-op unless ROBOCO_CLOUD_AUTH_ENABLED (see ensure_seed_user_startup).
    await ensure_seed_user_startup()

    # Overlay panel-persisted feature-flag overrides onto the live config so the
    # rest of startup (and the dispatch loops) read the panel's choices; unset
    # flags keep their env/config default. Best-effort — a failure here must not
    # block startup, the env defaults still apply.
    try:
        async with get_session_factory()() as _flags_db:
            applied_flags = await apply_persisted_feature_flags(_flags_db)
        if applied_flags:
            logger.info("Applied persisted feature-flag overrides", flags=applied_flags)
    except Exception as e:
        logger.warning("Feature-flag overlay failed; using env defaults", error=str(e))

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

    # Stop the orchestrator BEFORE closing the DB / OptimalService. stop()
    # cancels the background loops, stops the agents (finalizing work sessions
    # + agent state via DB writes), and drains fire-and-forget bg writes
    # (respawn_tracker upserts, audit-log rows) — all needing the DB still
    # open. Closing the DB first silently dropped those final writes (the old
    # order, where only bootstrap's finally block called stop() AFTER lifespan
    # had already closed the DB). Best-effort: a stop error must not block the
    # resource teardown below. No-op when no orchestrator is wired (tests,
    # skip_orchestrator). bootstrap's finally block re-calls stop() as a safety
    # net; stop() is idempotent (guarded by the _stopped flag) so the second
    # call is a no-op.
    orchestrator = get_orchestrator_or_none()
    if orchestrator is not None:
        try:
            await orchestrator.stop()
        except Exception as e:
            logger.warning("Orchestrator stop failed during shutdown", error=str(e))

    # Close Phase 3 services
    await close_optimal_service()

    await close_db()

    logger.info("Shutdown complete")


def _mount_v1_routers(app: FastAPI) -> None:
    """Mount every API v1 (intent-verb + content-tool) router."""
    app.include_router(flow_dev_module.router)
    app.include_router(flow_qa_module.router)
    app.include_router(flow_doc_module.router)
    app.include_router(flow_cell_pm_module.router)
    app.include_router(flow_main_pm_module.router)
    app.include_router(flow_board_module.router)
    app.include_router(flow_auditor_module.router)
    app.include_router(flow_pr_reviewer_module.router)
    app.include_router(do_module.router)


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
        # Wraps the existing lifespan with fastapi-guard's when armed (drives the
        # middleware's redis/geo/agent init); returns it unchanged when off.
        lifespan=guarded_lifespan(lifespan),
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

    # fastapi-guard HTTP security layer — no-op unless ROBOCO_GUARD_ENABLED.
    # Mounted last so SecurityMiddleware is outermost and blocks hostile traffic
    # before it reaches the app (guard does its own request logging); order can
    # be tuned during passive-mode calibration.
    apply_guard(app)

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
        settings_router,
        prefix=f"{api_prefix}/settings",
        tags=["Settings"],
    )

    app.include_router(
        company_goals_router,
        prefix=f"{api_prefix}/company-goals",
        tags=["Company"],
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

    # Web research — pluggable external search/fetch for Board + PM agents.
    app.include_router(
        research_router,
        prefix=f"{api_prefix}/research",
        tags=["Research"],
    )

    # Cockpit — the CEO's read-only "is the business winning?" summary.
    app.include_router(
        cockpit_router,
        prefix=f"{api_prefix}/cockpit",
        tags=["Cockpit"],
    )

    # Release manager — the CEO approves/rejects a held release proposal.
    app.include_router(
        release_router,
        prefix=f"{api_prefix}/release",
        tags=["Release"],
    )

    # Playbooks — the Auditor (or CEO) curates the drafted playbook library.
    app.include_router(
        playbooks_router,
        prefix=f"{api_prefix}/playbooks",
        tags=["Playbooks"],
    )

    # X (Twitter) engine — the CEO approves/rejects held posts/replies and
    # manages credentials. Nothing here posts except an explicit approve.
    app.include_router(
        x_router,
        prefix=f"{api_prefix}/x",
        tags=["X"],
    )

    # Board roadmap engine — the CEO approves/rejects items within a held
    # roadmap cycle. Approving materializes a BACKLOG task; nothing auto-starts.
    app.include_router(
        roadmap_router,
        prefix=f"{api_prefix}/roadmap",
        tags=["Roadmap"],
    )

    # Video engine — the CEO requests an on-demand marketing video; the
    # release/spotlight triggers open the same UX/UI authoring task via their
    # own hooks. Nothing renders or posts from this route alone.
    app.include_router(
        video_router,
        prefix=f"{api_prefix}/video",
        tags=["Video"],
    )

    # Pitches — Board proposals + CEO approve -> auto-provision origination path.
    app.include_router(
        pitch_router,
        prefix=f"{api_prefix}/pitches",
        tags=["Pitches"],
    )

    # Secretary — the CEO's chief-of-staff: company-state reads + gated directives.
    app.include_router(
        secretary_router,
        prefix=f"{api_prefix}/secretary",
        tags=["Secretary"],
    )
    # Secretary live chat — panel <-> Secretary container bridge.
    app.include_router(
        secretary_live_router,
        prefix=f"{api_prefix}/secretary",
        tags=["Secretary"],
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

    # Prompter live chat — panel <-> spawned intake agent (SSE + relay)
    app.include_router(
        prompter_live_router,
        prefix=f"{api_prefix}/prompter",
        tags=["Prompter"],
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

    # Token Usage Analytics
    app.include_router(
        usage_router,
        prefix=f"{api_prefix}/usage",
        tags=["Usage Analytics"],
    )

    # System monitoring (rate-limits, etc.)
    app.include_router(
        system_router,
        prefix=f"{api_prefix}/system",
        tags=["System"],
    )

    # Cloud auth — /auth/status is always public; login/logout mount only
    # when ROBOCO_CLOUD_AUTH_ENABLED (mirrors apply_guard's conditional mount).
    mount_cloud_auth(app, f"{api_prefix}/auth")

    # API v1 — intent-verb flow + content-tool endpoints (each module owns
    # its own prefix); grouped into one helper to keep create_app's own
    # statement count from growing unbounded as roles are added.
    _mount_v1_routers(app)

    # ==========================================================================
    # WebSocket
    # ==========================================================================
    app.include_router(ws_router, prefix="/ws", tags=["WebSocket"])

    return app


# Create the default application instance
app = create_app()
