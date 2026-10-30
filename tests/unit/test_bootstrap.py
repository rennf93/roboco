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
import sys
import types
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco import bootstrap
from roboco.bootstrap import (
    _BootstrapHolder,
    _run_api_server,
    _run_indexer_role,
    _start_indexer_task_if_available,
    _wait_for_api_ready,
    _warn_when_docker_bypasses_the_gate,
    main,
)
from roboco.config import settings
from roboco.services.workspace_git import GitUserDropError

if TYPE_CHECKING:
    from pathlib import Path

_INDEXER_MODULE = "roboco.services.optimal_brain.indexer_worker"


def _inject_fake_indexer_module(
    monkeypatch: pytest.MonkeyPatch, run_indexer: Any
) -> None:
    """Simulate the parallel leg's module landing: a real import of
    ``_INDEXER_MODULE`` would otherwise ImportError in this checkout."""
    fake_module = types.ModuleType(_INDEXER_MODULE)
    monkeypatch.setattr(fake_module, "run_indexer", run_indexer, raising=False)
    monkeypatch.setitem(sys.modules, _INDEXER_MODULE, fake_module)


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


def _make_main_patches(*, raise_on_spawn: bool = False) -> tuple[Any, Any, Any]:
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

    # api_task is awaited inside main.
    api_task_mock = AsyncMock()

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
    # Cleared in finally.
    assert _BootstrapHolder.orchestrator is None


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
    # Cleanup still ran.
    orch.stop.assert_awaited_once()


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


# ---------------------------------------------------------------------------
# ROBOCO_ROLE=indexer / =all: the indexer_worker seam
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_indexer_role_calls_run_indexer_when_module_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_indexer_mock = AsyncMock()
    _inject_fake_indexer_module(monkeypatch, run_indexer_mock)

    await _run_indexer_role()

    run_indexer_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_indexer_role_passes_dedicated_embedder_when_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dedicated indexer role seeds its own embedder: run_indexer is
    called with dedicated_embedder=True."""
    calls: list[dict[str, bool]] = []

    async def run_indexer(*, dedicated_embedder: bool = False) -> None:
        calls.append({"dedicated_embedder": dedicated_embedder})

    _inject_fake_indexer_module(monkeypatch, run_indexer)

    await _run_indexer_role()

    assert calls == [{"dedicated_embedder": True}]


@pytest.mark.asyncio
async def test_start_indexer_task_if_available_schedules_task_when_module_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_indexer_mock = AsyncMock()
    _inject_fake_indexer_module(monkeypatch, run_indexer_mock)

    task = _start_indexer_task_if_available()

    assert task is not None
    await task
    run_indexer_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_indexer_role_runs_only_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROBOCO_ROLE=indexer: no event bus, no orchestrator, no API server,
    just the worker entry point."""
    monkeypatch.setattr(settings, "role", "indexer")
    run_indexer_role_mock = AsyncMock()

    with (
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap._run_indexer_role", run_indexer_role_mock),
        patch("roboco.bootstrap.init_event_bus") as bus,
        patch("roboco.bootstrap.AgentOrchestrator") as orch_cls,
    ):
        await main()

    run_indexer_role_mock.assert_awaited_once()
    bus.assert_not_called()
    orch_cls.assert_not_called()


@pytest.mark.asyncio
async def test_main_all_role_starts_indexer_task_alongside_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROBOCO_ROLE=all (the default): the indexer worker also runs
    in-process, alongside the orchestrator's own loops, and the WebSocket
    bridge starts (it owns the real /ws/ connections in this role)."""
    monkeypatch.setattr(settings, "role", "all")
    orch, bus, _ = _make_main_patches()
    start_indexer_mock = MagicMock(return_value=None)

    with (
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap.init_event_bus", AsyncMock(return_value=bus)) as bus_fn,
        patch("roboco.bootstrap.register_default_handlers"),
        patch("roboco.bootstrap.start_websocket_bridge", AsyncMock()) as bridge,
        patch("roboco.bootstrap.AgentOrchestrator", return_value=orch),
        patch("roboco.bootstrap.set_orchestrator"),
        patch("roboco.bootstrap.NotificationService"),
        patch("roboco.bootstrap.set_event_context"),
        patch("roboco.bootstrap.set_reasoning_stream_callback"),
        patch("roboco.bootstrap._wait_for_api_ready", AsyncMock()),
        patch("roboco.bootstrap._run_api_server", AsyncMock(return_value=None)),
        patch("roboco.bootstrap._start_indexer_task_if_available", start_indexer_mock),
    ):
        await main()

    start_indexer_mock.assert_called_once()
    bridge.assert_awaited_once()
    assert bus_fn.await_args is not None
    consumer_name = bus_fn.await_args.kwargs["consumer_name"]
    assert consumer_name.startswith("orchestrator-all-")


@pytest.mark.asyncio
async def test_main_dispatcher_role_skips_websocket_bridge_and_indexer_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROBOCO_ROLE=dispatcher takes the same bootstrap.main() shape as 'all'
    (event bus + orchestrator + its own /health-serving API server; the
    fleet-loop split itself lives inside Orchestrator.start()'s role gate),
    but two things differ here in bootstrap.py: the WebSocket bridge is
    skipped (dispatcher owns no real /ws/ connections to forward to, and a
    second subscriber would just steal deliveries from the consumer group),
    and only 'all' fires the in-process indexer task."""
    monkeypatch.setattr(settings, "role", "dispatcher")
    orch, bus, _ = _make_main_patches()
    start_indexer_mock = MagicMock(return_value=None)

    with (
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap.init_event_bus", AsyncMock(return_value=bus)) as bus_fn,
        patch("roboco.bootstrap.register_default_handlers"),
        patch("roboco.bootstrap.start_websocket_bridge", AsyncMock()) as bridge,
        patch("roboco.bootstrap.AgentOrchestrator", return_value=orch) as orch_cls,
        patch("roboco.bootstrap.set_orchestrator"),
        patch("roboco.bootstrap.NotificationService"),
        patch("roboco.bootstrap.set_event_context"),
        patch("roboco.bootstrap.set_reasoning_stream_callback"),
        patch("roboco.bootstrap._wait_for_api_ready", AsyncMock()),
        patch("roboco.bootstrap._run_api_server", AsyncMock(return_value=None)),
        patch("roboco.bootstrap._start_indexer_task_if_available", start_indexer_mock),
    ):
        await main()

    bus_fn.assert_awaited_once()
    orch_cls.assert_called_once()
    orch.start.assert_awaited_once()
    # Only 'all' runs it in-process.
    start_indexer_mock.assert_not_called()
    # The dispatcher has no /ws/ connections to bridge.
    bridge.assert_not_awaited()
    assert bus_fn.await_args is not None
    consumer_name = bus_fn.await_args.kwargs["consumer_name"]
    assert consumer_name.startswith("orchestrator-dispatcher-")


# ---------------------------------------------------------------------------
# The git-user drop self-test gates the api and dispatcher (and "all").
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["api", "dispatcher", "all"])
async def test_main_refuses_to_start_when_the_drop_fails(
    monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    monkeypatch.setattr(settings, "role", role)
    db_mock = AsyncMock()
    with (
        patch(
            "roboco.bootstrap.verify_git_user_drop",
            side_effect=GitUserDropError("as-git-user: refused"),
        ),
        patch("roboco.bootstrap.bootstrap_database", db_mock),
        pytest.raises(SystemExit) as exited,
    ):
        await main(skip_orchestrator=True)
    assert exited.value.code == 1
    db_mock.assert_not_called()


@pytest.mark.asyncio
async def test_main_runs_the_self_test_before_anything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "role", "api")
    order: list[str] = []
    with (
        patch(
            "roboco.bootstrap.verify_git_user_drop",
            side_effect=lambda: order.append("self-test"),
        ),
        patch(
            "roboco.bootstrap.bootstrap_database",
            AsyncMock(side_effect=lambda: order.append("db")),
        ),
    ):
        await main(skip_orchestrator=True)
    assert order == ["self-test", "db"]


@pytest.mark.asyncio
async def test_main_indexer_role_runs_no_self_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The indexer starts no workspace process."""
    monkeypatch.setattr(settings, "role", "indexer")
    self_test = MagicMock()
    with (
        patch("roboco.bootstrap.verify_git_user_drop", self_test),
        patch("roboco.bootstrap.bootstrap_database", AsyncMock()),
        patch("roboco.bootstrap._run_indexer_role", AsyncMock()),
    ):
        await main()
    self_test.assert_not_called()


# ---------------------------------------------------------------------------
# A compose file from before the Docker gate gets a warning (trail #34).
# ---------------------------------------------------------------------------


def _raw_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, present: bool
) -> None:
    sock = tmp_path / "docker.sock"
    if present:
        sock.touch()
    monkeypatch.setattr(bootstrap, "HOST_DOCKER_SOCKET", sock)


def _patched_logger(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    # A Mock, not capture_logs: setup_logging elsewhere in the suite
    # reconfigures structlog after this module's logger is cached.
    fake_logger = MagicMock()
    monkeypatch.setattr(bootstrap, "logger", fake_logger)
    return fake_logger


@pytest.mark.parametrize("role", ["api", "dispatcher"])
def test_a_raw_socket_without_docker_host_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, role: str
) -> None:
    monkeypatch.setattr(settings, "role", role)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    _raw_socket(monkeypatch, tmp_path, present=True)
    logger = _patched_logger(monkeypatch)
    _warn_when_docker_bypasses_the_gate()
    logger.warning.assert_called_once()
    assert "update the compose file" in logger.warning.call_args.args[0]


@pytest.mark.parametrize(
    ("role", "docker_host", "present"),
    [
        # The gate's compose file: DOCKER_HOST names the gate's socket.
        ("api", "unix:///run/roboco-docker/blue.sock", True),
        ("dispatcher", "unix:///run/roboco-docker/blue.sock", False),
        # No socket mounted at all.
        ("api", "", False),
        # A host run talks to its own host's daemon.
        ("all", "", True),
        # The indexer never reaches Docker.
        ("indexer", "", True),
    ],
)
def test_no_warning_otherwise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: str,
    docker_host: str,
    present: bool,
) -> None:
    monkeypatch.setattr(settings, "role", role)
    monkeypatch.setenv("DOCKER_HOST", docker_host)
    _raw_socket(monkeypatch, tmp_path, present=present)
    logger = _patched_logger(monkeypatch)
    _warn_when_docker_bypasses_the_gate()
    logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_main_warns_after_the_self_test_and_before_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "role", "api")
    order: list[str] = []
    with (
        patch(
            "roboco.bootstrap.verify_git_user_drop",
            side_effect=lambda: order.append("self-test"),
        ),
        patch(
            "roboco.bootstrap._warn_when_docker_bypasses_the_gate",
            side_effect=lambda: order.append("gate-check"),
        ),
        patch(
            "roboco.bootstrap.bootstrap_database",
            AsyncMock(side_effect=lambda: order.append("db")),
        ),
    ):
        await main(skip_orchestrator=True)
    assert order == ["self-test", "gate-check", "db"]
