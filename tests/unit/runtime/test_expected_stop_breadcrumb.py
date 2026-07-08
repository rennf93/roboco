"""Expected-stop breadcrumb registry.

Production containers can exit 143 (SIGTERM) with the orchestrator's exit
monitor logging only "Agent container stopped unexpectedly" — no line
identifies who stopped it, and containers are gone by the time anyone looks
(docker events empty). Every orchestrator-initiated stop/kill path now
breadcrumbs the agent_id (_record_expected_stop) before it acts; the monitor
consumes it (_consume_expected_stop) when the container turns up dead and
downgrades an attributed death to an info "(expected)" line, keeping the
warning meaningful for genuinely unexplained SIGTERMs/crashes.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState
from structlog.testing import capture_logs


def _make_orchestrator() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = MagicMock()
    return orch


def _instance() -> MagicMock:
    inst = MagicMock()
    inst.state = AgentState.ACTIVE
    inst.container_id = "deadbeef1234"
    inst.current_task_id = None
    inst.error_count = 0
    inst.config = MagicMock(git_context=None)
    return inst


# ---------------------------------------------------------------------------
# _record_expected_stop / _consume_expected_stop
# ---------------------------------------------------------------------------


def test_record_then_consume_returns_the_reason() -> None:
    orch = _make_orchestrator()
    orch._record_expected_stop("be-dev-1", "budget_sweep")
    assert orch._consume_expected_stop("be-dev-1") == "budget_sweep"


def test_consume_pops_the_entry() -> None:
    """A second consume for the same agent finds nothing — one-shot breadcrumb."""
    orch = _make_orchestrator()
    orch._record_expected_stop("be-dev-1", "budget_sweep")
    orch._consume_expected_stop("be-dev-1")
    assert orch._consume_expected_stop("be-dev-1") == "none_recorded"


def test_no_breadcrumb_is_none_recorded() -> None:
    orch = _make_orchestrator()
    assert orch._consume_expected_stop("be-dev-1") == "none_recorded"


def test_stale_breadcrumb_is_ignored() -> None:
    """A breadcrumb older than the freshness window can't attribute a later,
    unrelated exit — treated the same as never having been recorded."""
    orch = _make_orchestrator()
    orch._record_expected_stop("be-dev-1", "budget_sweep")
    reason, _ts = orch._expected_stops["be-dev-1"]
    orch._expected_stops["be-dev-1"] = (reason, time.monotonic() - 121.0)
    assert orch._consume_expected_stop("be-dev-1") == "none_recorded"


def test_registry_defensive_on_bare_new_instance() -> None:
    """A __new__-constructed instance (many existing test fixtures across the
    suite bypass __init__ this way) has no _expected_stops attribute until
    first use — both helpers must self-heal it rather than raise
    AttributeError."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert orch._consume_expected_stop("be-dev-1") == "none_recorded"
    orch._record_expected_stop("be-dev-1", "stop_agent_api")
    assert orch._consume_expected_stop("be-dev-1") == "stop_agent_api"


# ---------------------------------------------------------------------------
# _check_health / _handle_stopped_container attribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_path_breadcrumb_downgrades_the_monitor_log() -> None:
    """A kill path (e.g. the budget sweep) records a breadcrumb; when the
    monitor later observes the same container gone, it logs "(expected)" at
    info with the recorded reason instead of "unexpectedly" at warning."""
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _instance()
    # The exact call a kill path makes (_sweep_budget_exceeded -> stop_agent)
    # before it issues its own docker stop/kill.
    orch._record_expected_stop("be-dev-1", "budget_sweep")

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"false 137\n", b""))

    with (
        patch.object(orch, "spawn_agent", new=AsyncMock()),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        capture_logs() as logs,
    ):
        await orch._check_health()

    expected = [e for e in logs if e["event"] == "Agent container stopped (expected)"]
    assert expected, logs
    assert expected[0]["log_level"] == "info"
    assert expected[0]["expected_stop_reason"] == "budget_sweep"
    assert not [e for e in logs if e["event"] == "Agent container stopped unexpectedly"]


@pytest.mark.asyncio
async def test_no_breadcrumb_stays_a_warning() -> None:
    """No breadcrumb recorded: the death is genuinely unattributed, so the
    line stays a warning carrying expected_stop_reason="none_recorded"."""
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _instance()

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"false 137\n", b""))

    with (
        patch.object(orch, "spawn_agent", new=AsyncMock()),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        capture_logs() as logs,
    ):
        await orch._check_health()

    unexpected = [
        e for e in logs if e["event"] == "Agent container stopped unexpectedly"
    ]
    assert unexpected, logs
    assert unexpected[0]["log_level"] == "warning"
    assert unexpected[0]["expected_stop_reason"] == "none_recorded"


@pytest.mark.asyncio
async def test_stale_breadcrumb_does_not_suppress_the_warning() -> None:
    """A breadcrumb from a much older stop must not attribute an unrelated,
    later exit — the warning line still fires with none_recorded."""
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _instance()
    orch._record_expected_stop("be-dev-1", "budget_sweep")
    orch._expected_stops["be-dev-1"] = ("budget_sweep", time.monotonic() - 121.0)

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"false 137\n", b""))

    with (
        patch.object(orch, "spawn_agent", new=AsyncMock()),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        capture_logs() as logs,
    ):
        await orch._check_health()

    unexpected = [
        e for e in logs if e["event"] == "Agent container stopped unexpectedly"
    ]
    assert unexpected, logs
    assert unexpected[0]["expected_stop_reason"] == "none_recorded"


@pytest.mark.asyncio
async def test_inspect_diagnostics_failure_is_tolerated() -> None:
    """A failed/timed-out extra `docker inspect` for OOMKilled/StartedAt/etc
    must not break the monitor — the log line still emits, just without
    those fields (best-effort, never blocks the log)."""
    orch = _make_orchestrator()
    orch._instances["be-dev-1"] = _instance()

    async def _create_subprocess_exec(*args: object, **_kw: object) -> MagicMock:
        if any(isinstance(a, str) and "OOMKilled" in a for a in args):
            raise RuntimeError("docker daemon unreachable")
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"false 137\n", b""))
        return proc

    with (
        patch.object(orch, "spawn_agent", new=AsyncMock()),
        patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=_create_subprocess_exec),
        ),
        capture_logs() as logs,
    ):
        await orch._check_health()

    unexpected = [
        e for e in logs if e["event"] == "Agent container stopped unexpectedly"
    ]
    assert unexpected, logs
    assert "oom_killed" not in unexpected[0]
