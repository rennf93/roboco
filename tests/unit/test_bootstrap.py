"""bootstrap.py coverage — orchestration entrypoint with all I/O patched.

`main()` wires database init, event-bus init, websocket-bridge,
orchestrator startup, and the uvicorn API server. Every external call gets
patched so the tests run in milliseconds without a real Redis/Postgres/HTTP
stack. Helpers `_run_api_server` and `_wait_for_api_ready` get their own
isolated checks.
"""

from __future__ import annotations

import asyncio
import runpy
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.bootstrap import (
    _BootstrapHolder,
    _run_api_server,
    _wait_for_api_ready,
    main,
)

# ---------------------------------------------------------------------------
# _run_api_server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_api_server_starts_uvicorn() -> None:
    """_run_api_server constructs a uvicorn Config + Server and serves."""
    server_instance = MagicMock()
    server_instance.serve = AsyncMock()
    with (
        patch("roboco.bootstrap.uvicorn.Config") as cfg_cls,
        patch("roboco.bootstrap.uvicorn.Server", return_value=server_instance),
    ):
        await _run_api_server()
    cfg_cls.assert_called_once()
    server_instance.serve.assert_awaited_once()


# ---------------------------------------------------------------------------
# _wait_for_api_ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_api_ready_returns_when_health_ok() -> None:
    """200 from /health → return immediately."""
    response = MagicMock()
    response.status_code = HTTPStatus.OK
    client_instance = MagicMock()
    client_instance.get = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    with patch("roboco.bootstrap.httpx.AsyncClient", return_value=client_instance):
        await _wait_for_api_ready(max_wait=4)
    client_instance.get.assert_awaited()


@pytest.mark.asyncio
async def test_wait_for_api_ready_swallows_errors_and_times_out() -> None:
    """Connection errors are swallowed; loop times out and warns."""
    client_instance = MagicMock()
    client_instance.get = AsyncMock(side_effect=ConnectionError("boom"))
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    sleep_mock = AsyncMock()
    with (
        patch("roboco.bootstrap.httpx.AsyncClient", return_value=client_instance),
        patch("roboco.bootstrap.asyncio.sleep", sleep_mock),
    ):
        await _wait_for_api_ready(max_wait=4)
    # Loop ran a couple of iterations (2s each) before timing out.
    assert sleep_mock.await_count >= 1


@pytest.mark.asyncio
async def test_wait_for_api_ready_keeps_polling_on_non_200() -> None:
    """Non-200 status → keep polling until max_wait."""
    response = MagicMock()
    response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    client_instance = MagicMock()
    client_instance.get = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("roboco.bootstrap.httpx.AsyncClient", return_value=client_instance),
        patch("roboco.bootstrap.asyncio.sleep", AsyncMock()),
    ):
        await _wait_for_api_ready(max_wait=4)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _make_main_patches(*, raise_on_spawn: bool = False):
    """Bundle the heavy IO mocks main() needs.

    Returns a context manager and the orchestrator mock so the test can
    inspect calls afterward.
    """
    orchestrator_mock = MagicMock()
    orchestrator_mock.start = AsyncMock()
    orchestrator_mock.stop = AsyncMock()
    if raise_on_spawn:
        orchestrator_mock.spawn_agent = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        orchestrator_mock.spawn_agent = AsyncMock()

    event_bus_mock = MagicMock()
    event_bus_mock.start_listening = AsyncMock()
    event_bus_mock.disconnect = AsyncMock()

    api_task_mock = AsyncMock()  # api_task awaited inside main

    return orchestrator_mock, event_bus_mock, api_task_mock


@pytest.mark.asyncio
async def test_main_skip_orchestrator_returns_after_db_bootstrap() -> None:
    """skip_orchestrator=True → only DB bootstrap runs, then early return."""
    db_mock = AsyncMock()
    with (
        patch("roboco.bootstrap.bootstrap_database", db_mock),
        patch("roboco.bootstrap.init_event_bus") as bus,
    ):
        await main(skip_orchestrator=True)
    db_mock.assert_awaited_once()
    bus.assert_not_called()


@pytest.mark.asyncio
async def test_main_skip_db_skips_database_bootstrap() -> None:
    """skip_db=True → bootstrap_database not called, but rest still runs."""
    db_mock = AsyncMock()
    with (
        patch("roboco.bootstrap.bootstrap_database", db_mock),
        # Skip orchestrator so we exit early — we just want to verify db
        # bootstrap path.
        patch("roboco.bootstrap.init_event_bus") as bus,
    ):
        await main(skip_db=True, skip_orchestrator=True)
    db_mock.assert_not_called()
    bus.assert_not_called()


@pytest.mark.asyncio
async def test_main_full_path_starts_orchestrator_and_shuts_down() -> None:
    """Happy path: bootstrap, init bus, start orchestrator, await api task."""
    orch, bus, _ = _make_main_patches()

    with (
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap.init_event_bus", AsyncMock(return_value=bus)),
        patch("roboco.bootstrap.register_default_handlers"),
        patch("roboco.bootstrap.start_websocket_bridge", AsyncMock()),
        patch("roboco.bootstrap.AgentOrchestrator", return_value=orch),
        patch("roboco.bootstrap.set_orchestrator"),
        patch("roboco.bootstrap.NotificationService"),
        patch("roboco.bootstrap.set_event_context"),
        patch("roboco.bootstrap.set_reasoning_stream_callback"),
        patch("roboco.bootstrap._wait_for_api_ready", AsyncMock()),
        # Replace _run_api_server with an AsyncMock that returns immediately;
        # create_task wraps the coroutine into a real Task that completes
        # without warnings.
        patch("roboco.bootstrap._run_api_server", AsyncMock(return_value=None)),
    ):
        await main()

    orch.start.assert_awaited_once()
    orch.stop.assert_awaited_once()
    bus.start_listening.assert_awaited_once()
    bus.disconnect.assert_awaited_once()
    assert _BootstrapHolder.orchestrator is None  # Cleared in finally.


@pytest.mark.asyncio
async def test_main_spawns_requested_agents() -> None:
    """spawn_agents list → orchestrator.spawn_agent called for each."""
    orch, bus, _ = _make_main_patches()

    with (
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap.init_event_bus", AsyncMock(return_value=bus)),
        patch("roboco.bootstrap.register_default_handlers"),
        patch("roboco.bootstrap.start_websocket_bridge", AsyncMock()),
        patch("roboco.bootstrap.AgentOrchestrator", return_value=orch),
        patch("roboco.bootstrap.set_orchestrator"),
        patch("roboco.bootstrap.NotificationService"),
        patch("roboco.bootstrap.set_event_context"),
        patch("roboco.bootstrap.set_reasoning_stream_callback"),
        patch("roboco.bootstrap._wait_for_api_ready", AsyncMock()),
        patch("roboco.bootstrap._run_api_server", AsyncMock(return_value=None)),
    ):
        await main(spawn_agents=["be-dev-1", "fe-dev-1"])

    _EXPECTED_SPAWNS = 2
    assert orch.spawn_agent.await_count == _EXPECTED_SPAWNS


@pytest.mark.asyncio
async def test_main_logs_and_continues_when_spawn_fails() -> None:
    """spawn_agent failure logs error but doesn't abort startup."""
    orch, bus, _ = _make_main_patches(raise_on_spawn=True)

    with (
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap.init_event_bus", AsyncMock(return_value=bus)),
        patch("roboco.bootstrap.register_default_handlers"),
        patch("roboco.bootstrap.start_websocket_bridge", AsyncMock()),
        patch("roboco.bootstrap.AgentOrchestrator", return_value=orch),
        patch("roboco.bootstrap.set_orchestrator"),
        patch("roboco.bootstrap.NotificationService"),
        patch("roboco.bootstrap.set_event_context"),
        patch("roboco.bootstrap.set_reasoning_stream_callback"),
        patch("roboco.bootstrap._wait_for_api_ready", AsyncMock()),
        patch("roboco.bootstrap._run_api_server", AsyncMock(return_value=None)),
    ):
        # Should not raise — error is caught + logged.
        await main(spawn_agents=["broken-agent"])

    orch.spawn_agent.assert_awaited_once()
    orch.stop.assert_awaited_once()  # Cleanup still ran.


def test_module_run_as_script_invokes_cli() -> None:
    """`python -m roboco.bootstrap` → calls `roboco.cli.cli()`."""
    cli_mock = MagicMock()
    with patch.dict(
        "sys.modules",
        {"roboco.cli": MagicMock(cli=cli_mock)},
    ):
        runpy.run_module("roboco.bootstrap", run_name="__main__")
    cli_mock.assert_called_once()


@pytest.mark.asyncio
async def test_main_handles_api_task_cancellation() -> None:
    """If api_task is cancelled, finally block still runs cleanup."""
    orch, bus, _ = _make_main_patches()

    async def _raise_cancelled() -> None:
        raise asyncio.CancelledError()

    with (
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap.init_event_bus", AsyncMock(return_value=bus)),
        patch("roboco.bootstrap.register_default_handlers"),
        patch("roboco.bootstrap.start_websocket_bridge", AsyncMock()),
        patch("roboco.bootstrap.AgentOrchestrator", return_value=orch),
        patch("roboco.bootstrap.set_orchestrator"),
        patch("roboco.bootstrap.NotificationService"),
        patch("roboco.bootstrap.set_event_context"),
        patch("roboco.bootstrap.set_reasoning_stream_callback"),
        patch("roboco.bootstrap._wait_for_api_ready", AsyncMock()),
        patch("roboco.bootstrap._run_api_server", _raise_cancelled),
    ):
        await main()

    # Cleanup ran despite cancellation.
    orch.stop.assert_awaited_once()
    bus.disconnect.assert_awaited_once()
