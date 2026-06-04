"""api.app coverage — FastAPI factory + lifespan.

`create_app()` wires every router. We assert the FastAPI instance comes back
with the expected metadata and that every prefixed route is mounted, then
exercise the lifespan with all heavy I/O patched out (init_db, transcription,
extraction, optimal-service).
"""

from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from roboco.api.app import app as default_app
from roboco.api.app import create_app, lifespan


def test_default_app_is_a_fastapi_instance() -> None:
    """Importing the module yields a configured FastAPI instance."""
    assert isinstance(default_app, FastAPI)
    assert default_app.title == "RoboCo API"


def test_create_app_returns_new_instance_each_call() -> None:
    a = create_app()
    b = create_app()
    assert a is not b
    assert isinstance(a, FastAPI)


def test_create_app_registers_all_router_prefixes() -> None:
    """Every router is mounted under its expected prefix."""
    app = create_app()
    paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
    # Spot-check a representative path from each prefix group.
    expected_prefixes = [
        "/api/agents",
        "/api/channels",
        "/api/groups",
        "/api/sessions",
        "/api/messages",
        "/api/notifications",
        "/api/stream",
        "/api/optimal",
        "/api/journals",
        "/api/tasks",
        "/api/kanban",
        "/api/dashboard",
        "/api/orchestrator",
        "/api/a2a",
        "/api/git",
        "/api/projects",
        "/api/providers",
        "/api/work-sessions",
        "/api/docs",
        "/ws",
    ]
    for prefix in expected_prefixes:
        assert any(p.startswith(prefix) for p in paths), (
            f"No routes registered under {prefix}"
        )


def test_create_app_includes_v1_flow_routes() -> None:
    """API v1 (intent-verb) routers from `routes/v1/*` are mounted."""
    app = create_app()
    paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
    # v1 routers register their own prefixes; we just confirm /api/v1 paths
    # exist after include_router.
    assert any(p.startswith("/api/v1") for p in paths)


def test_create_app_attaches_cors_middleware() -> None:
    app = create_app()
    middleware_classes = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" in middleware_classes


# ---------------------------------------------------------------------------
# Lifespan — startup + shutdown with heavy IO patched
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _stub_get_optimal():
    """Stand-in for the optimal-service factory."""
    yield MagicMock()


@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown_happy_path() -> None:
    """Lifespan does init_db + service starts + optimal init + clean shutdown."""
    transcription_mock = MagicMock()
    transcription_mock.start = AsyncMock()
    transcription_mock.stop = AsyncMock()

    with (
        patch("roboco.api.app.init_db", new=AsyncMock()),
        patch("roboco.api.app.close_db", new=AsyncMock()),
        patch("roboco.api.app.TranscriptionService", return_value=transcription_mock),
        patch("roboco.api.app.ExtractionService"),
        patch("roboco.api.app.ExtractionPipeline"),
        patch(
            "roboco.api.app.get_optimal_service",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("roboco.api.app.close_optimal_service", new=AsyncMock()),
    ):
        app = create_app()
        async with lifespan(app):
            # During startup transcription was started + state populated.
            transcription_mock.start.assert_awaited_once()
            assert app.state.transcription is transcription_mock
            assert app.state.extraction is not None
            assert app.state.optimal is not None
        # After yield, shutdown ran.
        transcription_mock.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_handles_optimal_init_failure_gracefully() -> None:
    """Optimal-service init failure → app.state.optimal=None, no raise."""
    transcription_mock = MagicMock()
    transcription_mock.start = AsyncMock()
    transcription_mock.stop = AsyncMock()

    with (
        patch("roboco.api.app.init_db", new=AsyncMock()),
        patch("roboco.api.app.close_db", new=AsyncMock()),
        patch("roboco.api.app.TranscriptionService", return_value=transcription_mock),
        patch("roboco.api.app.ExtractionService"),
        patch("roboco.api.app.ExtractionPipeline"),
        patch(
            "roboco.api.app.get_optimal_service",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch("roboco.api.app.close_optimal_service", new=AsyncMock()),
    ):
        app = create_app()
        async with lifespan(app):
            assert app.state.optimal is None


def _lifespan_io_patches() -> list:
    transcription_mock = MagicMock()
    transcription_mock.start = AsyncMock()
    transcription_mock.stop = AsyncMock()
    return [
        patch("roboco.api.app.init_db", new=AsyncMock()),
        patch("roboco.api.app.close_db", new=AsyncMock()),
        patch("roboco.api.app.TranscriptionService", return_value=transcription_mock),
        patch("roboco.api.app.ExtractionService"),
        patch("roboco.api.app.ExtractionPipeline"),
        patch(
            "roboco.api.app.get_optimal_service",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("roboco.api.app.close_optimal_service", new=AsyncMock()),
    ]


def _header_trust_warnings(logger_mock: MagicMock) -> list:
    return [
        c
        for c in logger_mock.warning.call_args_list
        if c.args and "HEADER-TRUST" in c.args[0]
    ]


async def _run_lifespan_with(*, auth_required: bool, logger_mock: MagicMock) -> None:
    """Run the lifespan with heavy I/O patched and the auth flag forced."""
    with ExitStack() as stack:
        for cm in _lifespan_io_patches():
            stack.enter_context(cm)
        stack.enter_context(
            patch("roboco.api.app._auth_required", return_value=auth_required)
        )
        stack.enter_context(patch("roboco.api.app.logger", logger_mock))
        app = create_app()
        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_lifespan_warns_in_header_trust_mode() -> None:
    """Startup warns when agent auth is not enforced (header-trust mode)."""
    logger_mock = MagicMock()
    await _run_lifespan_with(auth_required=False, logger_mock=logger_mock)
    assert _header_trust_warnings(logger_mock), (
        "header-trust startup warning expected when auth is not required"
    )


@pytest.mark.asyncio
async def test_lifespan_no_header_trust_warning_when_auth_required() -> None:
    """No header-trust warning when ROBOCO_AGENT_AUTH_REQUIRED enforces tokens."""
    logger_mock = MagicMock()
    await _run_lifespan_with(auth_required=True, logger_mock=logger_mock)
    assert not _header_trust_warnings(logger_mock)
