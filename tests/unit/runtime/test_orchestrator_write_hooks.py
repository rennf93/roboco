"""
Unit tests for orchestrator write-hooks:
    _finalize_spawn_session  — closes the agent_spawn_sessions DB row on stop
    _sweep_token_snapshots   — polls active agents and upserts token snapshots

These tests mock the httpx transport and the SQLAlchemy session factory so no
real network or database is required.

Coverage:
  1. _finalize_spawn_session success — SDK returns token data → DB update
     carries those exact values to calculate_cost and the UPDATE statement.
  2. _finalize_spawn_session HTTP error — SDK unreachable → DB update proceeds
     with all-zero token counts (finalization must not raise).
  3. _sweep_token_snapshots active agent — non-zero tokens → snapshot row
     inserted and session row updated.
  4. _sweep_token_snapshots per-agent HTTP error — ConnectError on one agent
     is caught; the sweep continues and the next agent is still processed.
"""

from __future__ import annotations

import json
from contextlib import ExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
from roboco.models.runtime import (
    AgentInstance,
    OrchestratorAgentConfig,
    OrchestratorAgentState,
    WaitingRecord,
)
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.utils.converters import require_uuid

# ---------------------------------------------------------------------------
# Module-level constants (ruff PLR2004: no magic values in comparisons)
# ---------------------------------------------------------------------------

_AGENT_ID = "be-dev-1"
_AGENT_ID_2 = "be-dev-2"

# Token counts used in success-path assertions
_TI = 111  # tokens_input
_TO = 222  # tokens_output
_TCR = 33  # tokens_cache_read
_TCW = 44  # tokens_cache_write

# Token counts for the snapshot test
_SNAP_TI = 50
_SNAP_TO = 100
_SNAP_TCR = 10
_SNAP_TCW = 5

# Token counts for the loop-continues test (agent-2)
_LOOP_TI = 25
_LOOP_TO = 75

# Expected number of DB execute() calls for a normal finalize (SELECT + UPDATE)
_FINALIZE_EXEC_CALLS = 2


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_orchestrator() -> AgentOrchestrator:
    """Minimal AgentOrchestrator — no background tasks, no real DB."""
    return AgentOrchestrator(mcp_config_dir=Path("/tmp"), project_root=Path("/tmp"))


def _make_instance(
    agent_id: str = _AGENT_ID,
    usage_session_id: UUID | None = None,
) -> AgentInstance:
    """Return an ACTIVE AgentInstance with a running container."""
    return AgentInstance(
        agent_id=agent_id,
        state=OrchestratorAgentState.ACTIVE,
        container_id="abc123def456",
        config=OrchestratorAgentConfig(
            agent_id=agent_id,
            blueprint_path=Path("/tmp/blueprint.md"),
            model="sonnet",
        ),
        usage_session_id=usage_session_id,
    )


def _mock_response(
    status: int = 200,
    json_data: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json = MagicMock(return_value=json_data or {})
    return resp


def _make_db_factory(
    session_row: Any = None,
    add_list: list[Any] | None = None,
    execute_list: list[Any] | None = None,
) -> Any:
    """Return a callable that acts like get_session_factory().

    The returned callable, when called with no arguments, returns an async
    context manager yielding a mock AsyncSession whose execute() returns a
    result whose scalar_one_or_none() returns *session_row*.
    """

    @asynccontextmanager
    async def _db_context() -> Any:
        db = MagicMock()

        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=session_row)

        async def _exec(stmt: Any) -> MagicMock:
            if execute_list is not None:
                execute_list.append(stmt)
            return result

        db.execute = AsyncMock(side_effect=_exec)
        db.commit = AsyncMock()

        def _add(obj: Any) -> None:
            if add_list is not None:
                add_list.append(obj)

        db.add = _add if add_list is not None else MagicMock()
        yield db

    return _db_context


class _FakeHTTPClient:
    """Drop-in replacement for ``httpx.AsyncClient`` in tests.

    Accepts a *handler* callable ``(url: str) -> httpx.Response | raises``
    that is invoked by ``get()``.  Supports the ``async with`` protocol.
    """

    def __init__(self, handler: Any, **_: Any) -> None:
        self._handler = handler

    async def __aenter__(self) -> _FakeHTTPClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def get(self, url: str, **_: Any) -> Any:
        return self._handler(url)


# ---------------------------------------------------------------------------
# _finalize_spawn_session — success path
# ---------------------------------------------------------------------------


async def test_finalize_spawn_session_success_calls_calculate_cost() -> None:
    """Token values returned by the SDK /usage/status are passed to calculate_cost.

    This verifies the full data-flow: SDK response → token vars → cost calc.
    """
    orch = _make_orchestrator()
    session_uuid = uuid4()
    orch._instances[_AGENT_ID] = _make_instance(usage_session_id=session_uuid)

    token_data = {
        "tokens_input": _TI,
        "tokens_output": _TO,
        "tokens_cache_read": _TCR,
        "tokens_cache_write": _TCW,
    }

    def _handler(_url: str) -> Any:
        return _mock_response(200, token_data)

    session_row = MagicMock()
    session_row.id = session_uuid
    db_factory = _make_db_factory(session_row=session_row)

    def _client_cls(**_kw: Any) -> _FakeHTTPClient:
        return _FakeHTTPClient(_handler)

    with (
        patch("roboco.runtime.orchestrator.httpx.AsyncClient", _client_cls),
        patch("roboco.db.base.get_session_factory", return_value=db_factory),
        patch("roboco.billing.pricing.calculate_cost", return_value=0.001) as mock_cost,
    ):
        await orch._finalize_spawn_session(_AGENT_ID, exit_reason="stopped")

    mock_cost.assert_called_once_with(
        model="sonnet",
        tokens_input=_TI,
        tokens_output=_TO,
        tokens_cache_read=_TCR,
        tokens_cache_write=_TCW,
    )


async def test_finalize_spawn_session_success_executes_select_and_update() -> None:
    """When a session row exists the function calls execute() twice: SELECT + UPDATE."""
    orch = _make_orchestrator()
    session_uuid = uuid4()
    orch._instances[_AGENT_ID] = _make_instance(usage_session_id=session_uuid)

    def _handler(_url: str) -> Any:
        return _mock_response(
            200,
            {
                "tokens_input": 10,
                "tokens_output": 20,
                "tokens_cache_read": 0,
                "tokens_cache_write": 0,
            },
        )

    session_row = MagicMock()
    session_row.id = session_uuid
    execute_calls: list[Any] = []
    db_factory = _make_db_factory(session_row=session_row, execute_list=execute_calls)

    def _client_cls(**_kw: Any) -> _FakeHTTPClient:
        return _FakeHTTPClient(_handler)

    with (
        patch("roboco.runtime.orchestrator.httpx.AsyncClient", _client_cls),
        patch("roboco.db.base.get_session_factory", return_value=db_factory),
        patch("roboco.billing.pricing.calculate_cost", return_value=0.0),
    ):
        await orch._finalize_spawn_session(_AGENT_ID, exit_reason="completed")

    # SELECT (find the row) + UPDATE (write the values) = 2 execute() calls
    assert len(execute_calls) == _FINALIZE_EXEC_CALLS


# ---------------------------------------------------------------------------
# _finalize_spawn_session — HTTP-error path
# ---------------------------------------------------------------------------


async def test_finalize_spawn_session_http_error_uses_zero_tokens() -> None:
    """When the SDK endpoint is unreachable, finalization uses zero tokens.

    The function must not raise; cost must be calculated with all-zero counts.
    """
    orch = _make_orchestrator()
    session_uuid = uuid4()
    orch._instances[_AGENT_ID] = _make_instance(usage_session_id=session_uuid)

    def _boom(_url: str) -> Any:
        raise httpx.ConnectError("container not reachable")

    session_row = MagicMock()
    session_row.id = session_uuid
    db_factory = _make_db_factory(session_row=session_row)

    def _client_cls(**_kw: Any) -> _FakeHTTPClient:
        return _FakeHTTPClient(_boom)

    with (
        patch("roboco.runtime.orchestrator.httpx.AsyncClient", _client_cls),
        patch.object(orch, "_usage_from_transcript", return_value=(0, 0, 0, 0, 0)),
        patch("roboco.db.base.get_session_factory", return_value=db_factory),
        patch("roboco.billing.pricing.calculate_cost", return_value=0.0) as mock_cost,
    ):
        # Must not raise even though the SDK is unreachable
        await orch._finalize_spawn_session(_AGENT_ID, exit_reason="stopped")

    mock_cost.assert_called_once_with(
        model="sonnet",
        tokens_input=0,
        tokens_output=0,
        tokens_cache_read=0,
        tokens_cache_write=0,
    )


async def test_finalize_spawn_session_non_200_uses_zero_tokens() -> None:
    """A non-200 SDK response results in zero-token finalization, no exception."""
    orch = _make_orchestrator()
    session_uuid = uuid4()
    orch._instances[_AGENT_ID] = _make_instance(usage_session_id=session_uuid)

    def _handler(_url: str) -> Any:
        return _mock_response(503)

    session_row = MagicMock()
    session_row.id = session_uuid
    db_factory = _make_db_factory(session_row=session_row)

    def _client_cls(**_kw: Any) -> _FakeHTTPClient:
        return _FakeHTTPClient(_handler)

    with (
        patch("roboco.runtime.orchestrator.httpx.AsyncClient", _client_cls),
        patch.object(orch, "_usage_from_transcript", return_value=(0, 0, 0, 0, 0)),
        patch("roboco.db.base.get_session_factory", return_value=db_factory),
        patch("roboco.billing.pricing.calculate_cost", return_value=0.0) as mock_cost,
    ):
        await orch._finalize_spawn_session(_AGENT_ID, exit_reason="stopped")

    mock_cost.assert_called_once_with(
        model="sonnet",
        tokens_input=0,
        tokens_output=0,
        tokens_cache_read=0,
        tokens_cache_write=0,
    )


# ---------------------------------------------------------------------------
# _sweep_token_snapshots — active agent
# ---------------------------------------------------------------------------


async def test_sweep_token_snapshots_inserts_snapshot_for_active_agent() -> None:
    """An active agent with non-zero tokens gets a snapshot row added to the DB."""
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.state = OrchestratorAgentState.ACTIVE
    orch._instances[_AGENT_ID] = instance

    token_data = {
        "tokens_input": _SNAP_TI,
        "tokens_output": _SNAP_TO,
        "tokens_cache_read": _SNAP_TCR,
        "tokens_cache_write": _SNAP_TCW,
    }

    def _handler(_url: str) -> Any:
        return _mock_response(200, token_data)

    session_row = MagicMock()
    session_row.id = uuid4()
    added: list[Any] = []
    db_factory = _make_db_factory(session_row=session_row, add_list=added)

    def _client_cls(**_kw: Any) -> _FakeHTTPClient:
        return _FakeHTTPClient(_handler)

    with (
        patch("roboco.runtime.orchestrator.httpx.AsyncClient", _client_cls),
        patch("roboco.db.base.get_session_factory", return_value=db_factory),
    ):
        await orch._sweep_token_snapshots()

    # Exactly one snapshot row must have been passed to db.add()
    assert len(added) == 1
    snap = added[0]
    assert snap.tokens_input == _SNAP_TI
    assert snap.tokens_output == _SNAP_TO
    assert snap.tokens_cache_read == _SNAP_TCR
    assert snap.tokens_cache_write == _SNAP_TCW


async def test_sweep_token_snapshots_skips_zero_token_agents() -> None:
    """An agent whose SDK reports all-zero tokens is skipped (no DB writes)."""
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.state = OrchestratorAgentState.ACTIVE
    orch._instances[_AGENT_ID] = instance

    def _handler(_url: str) -> Any:
        return _mock_response(
            200,
            {
                "tokens_input": 0,
                "tokens_output": 0,
                "tokens_cache_read": 0,
                "tokens_cache_write": 0,
            },
        )

    added: list[Any] = []
    db_factory = _make_db_factory(add_list=added)

    def _client_cls(**_kw: Any) -> _FakeHTTPClient:
        return _FakeHTTPClient(_handler)

    with (
        patch("roboco.runtime.orchestrator.httpx.AsyncClient", _client_cls),
        patch.object(orch, "_usage_from_transcript", return_value=(0, 0, 0, 0, 0)),
        patch("roboco.db.base.get_session_factory", return_value=db_factory),
    ):
        await orch._sweep_token_snapshots()

    assert added == []


async def test_sweep_token_snapshots_per_agent_error_does_not_abort_loop() -> None:
    """A ConnectError for one agent is caught; the next agent is still processed."""
    orch = _make_orchestrator()

    # Agent 1: HTTP error
    inst1 = _make_instance(_AGENT_ID)
    inst1.state = OrchestratorAgentState.ACTIVE
    orch._instances[_AGENT_ID] = inst1

    # Agent 2: success with non-zero tokens
    inst2 = _make_instance(_AGENT_ID_2)
    inst2.state = OrchestratorAgentState.ACTIVE
    orch._instances[_AGENT_ID_2] = inst2

    def _handler(url: str) -> Any:
        if _AGENT_ID in url and _AGENT_ID_2 not in url:
            raise httpx.ConnectError("agent-1 unreachable")
        return _mock_response(
            200,
            {
                "tokens_input": _LOOP_TI,
                "tokens_output": _LOOP_TO,
                "tokens_cache_read": 0,
                "tokens_cache_write": 0,
            },
        )

    session_row = MagicMock()
    session_row.id = uuid4()
    added: list[Any] = []
    db_factory = _make_db_factory(session_row=session_row, add_list=added)

    def _client_cls(**_kw: Any) -> _FakeHTTPClient:
        return _FakeHTTPClient(_handler)

    with (
        patch("roboco.runtime.orchestrator.httpx.AsyncClient", _client_cls),
        patch("roboco.db.base.get_session_factory", return_value=db_factory),
    ):
        await orch._sweep_token_snapshots()

    # Only agent-2's snapshot should be present; agent-1's error was caught.
    assert len(added) == 1
    assert added[0].tokens_input == _LOOP_TI
    assert added[0].tokens_output == _LOOP_TO


# ---------------------------------------------------------------------------
# _sweep_daily_rollup — inserts new row when none exists
# ---------------------------------------------------------------------------

# Token counts for the rollup test
_ROLLUP_TI = 200
_ROLLUP_TO = 300
_ROLLUP_TCR = 20
_ROLLUP_TCW = 10


async def test_sweep_daily_rollup_inserts_new_row() -> None:
    """When no existing DailyUsageRollupTable row exists, db.add() is called
    with the correct aggregated token values."""
    orch = _make_orchestrator()

    # Build a fake aggregate result row
    agg_row = MagicMock()
    agg_row.date = "2026-06-10"
    agg_row.agent_slug = _AGENT_ID
    agg_row.team = "backend"
    agg_row.model = "sonnet"
    agg_row.tokens_input = _ROLLUP_TI
    agg_row.tokens_output = _ROLLUP_TO
    agg_row.tokens_cache_read = _ROLLUP_TCR
    agg_row.tokens_cache_write = _ROLLUP_TCW
    agg_row.total_cost_usd = 0.0
    agg_row.session_count = 1

    added: list[Any] = []
    call_count = 0

    @asynccontextmanager
    async def _db_context() -> Any:
        nonlocal call_count

        db = MagicMock()
        db.commit = AsyncMock()

        def _add(obj: Any) -> None:
            added.append(obj)

        db.add = _add

        async def _exec(_stmt: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # First call: aggregate SELECT — return one agg_row via fetchall()
                result.fetchall = MagicMock(return_value=[agg_row])
                result.scalar_one_or_none = MagicMock(return_value=None)
            else:
                # Second call: lookup SELECT for existing row — return None
                result.fetchall = MagicMock(return_value=[])
                result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        db.execute = AsyncMock(side_effect=_exec)
        yield db

    with patch("roboco.db.base.get_session_factory", return_value=_db_context):
        await orch._sweep_daily_rollup()

    # Exactly one new DailyUsageRollupTable row must have been added
    assert len(added) == 1
    row = added[0]
    assert row.tokens_input == _ROLLUP_TI
    assert row.tokens_output == _ROLLUP_TO
    assert row.tokens_cache_read == _ROLLUP_TCR
    assert row.tokens_cache_write == _ROLLUP_TCW


# ---------------------------------------------------------------------------
# stop_agent — _finalize_spawn_session is awaited before acquiring the lock
# ---------------------------------------------------------------------------


async def test_stop_agent_finalizes_before_lock() -> None:
    """stop_agent awaits _finalize_spawn_session when the instance has a
    running container_id (the finalization must happen before the lock)."""
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.container_id = "abc123def456"  # non-None → finalize must be called
    orch._instances[_AGENT_ID] = instance

    finalized: list[str] = []

    async def _fake_finalize(agent_id: str, **_kwargs: object) -> None:
        finalized.append(agent_id)

    # Stub out the Docker subprocess so stop_agent doesn't actually run Docker
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock()

    with (
        patch.object(orch, "_finalize_spawn_session", side_effect=_fake_finalize),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
        patch.object(orch, "_remove_container", AsyncMock()),
    ):
        await orch.stop_agent(_AGENT_ID, graceful=True)

    # _finalize_spawn_session must have been called exactly once with our agent id
    assert finalized == [_AGENT_ID]


# ---------------------------------------------------------------------------
# stop_agent — release_claim (F120): hand a stopped agent's claimed task back
# to the pool immediately instead of waiting for the stale-claim reaper's TTL.
# ---------------------------------------------------------------------------


def _stop_agent_patches(orch: AgentOrchestrator) -> Any:
    """Stub Docker + finalize so stop_agent runs without real Docker/DB."""
    mock_proc = MagicMock()
    mock_proc.wait = AsyncMock()
    return (
        patch.object(orch, "_finalize_spawn_session", AsyncMock()),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
        patch.object(orch, "_remove_container", AsyncMock()),
    )


async def test_stop_agent_releases_claim_when_release_claim_true() -> None:
    """stop_agent(release_claim=True) releases the agent's claimed task to the
    pool immediately, so a mid-verb SIGTERM/budget-kill doesn't strand the task
    CLAIMED/IN_PROGRESS until the reaper's heartbeat TTL expires."""
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.current_task_id = str(uuid4())
    orch._instances[_AGENT_ID] = instance

    released: list[str] = []

    async def _fake_release(agent_id: str, task_id: str) -> None:
        released.append((agent_id, task_id))  # type: ignore[arg-type]

    with ExitStack() as stack:
        for cm in _stop_agent_patches(orch):
            stack.enter_context(cm)
        stack.enter_context(
            patch.object(
                orch,
                "_release_stopped_agent_claim",
                side_effect=_fake_release,
                create=True,
            )
        )
        await orch.stop_agent(_AGENT_ID, graceful=True, release_claim=True)

    assert released == [(_AGENT_ID, instance.current_task_id)]


async def test_stop_agent_does_not_release_claim_by_default() -> None:
    """Default stop_agent (release_claim=False) must NOT release the claim —
    preserves the existing behavior for the provider-park / waiting path
    (mark_waiting_long) and the interactive stops, which manage their own
    claim lifecycle via the reaper's provider-park guard. No regression."""
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.current_task_id = str(uuid4())
    orch._instances[_AGENT_ID] = instance

    released: list[str] = []

    async def _fake_release(agent_id: str, task_id: str) -> None:
        released.append((agent_id, task_id))  # type: ignore[arg-type]

    with ExitStack() as stack:
        for cm in _stop_agent_patches(orch):
            stack.enter_context(cm)
        stack.enter_context(
            patch.object(
                orch,
                "_release_stopped_agent_claim",
                side_effect=_fake_release,
                create=True,
            )
        )
        # Default — release_claim omitted.
        await orch.stop_agent(_AGENT_ID, graceful=True)

    assert released == [], "default stop_agent must not release the claim"


async def test_stop_agent_skips_release_for_provider_parked_agent() -> None:
    """A provider-parked agent (rate_limit_lifted WaitingRecord) must NOT have
    its claim released even when release_claim=True — the probe-resume loop
    revives the SAME agent on the SAME task, so reaping would lose the claim."""
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.current_task_id = str(uuid4())
    orch._instances[_AGENT_ID] = instance
    # Parked on a rate limit — the claim must survive.
    orch._waiting_records[_AGENT_ID] = WaitingRecord(
        agent_id=_AGENT_ID,
        task_id=instance.current_task_id,
        waiting_for="rate_limit_lifted",
        waiting_since=datetime.now(UTC),
    )

    released: list[str] = []

    async def _fake_release(agent_id: str, task_id: str) -> None:
        released.append((agent_id, task_id))  # type: ignore[arg-type]

    with ExitStack() as stack:
        for cm in _stop_agent_patches(orch):
            stack.enter_context(cm)
        stack.enter_context(
            patch.object(
                orch,
                "_release_stopped_agent_claim",
                side_effect=_fake_release,
                create=True,
            )
        )
        await orch.stop_agent(_AGENT_ID, graceful=True, release_claim=True)

    assert released == [], "provider-parked agent's claim must not be released"


async def test_release_stopped_agent_claim_calls_unclaim_for_reaper() -> None:
    """The release helper opens a fresh session and routes through the hardened
    TaskService.unclaim_for_reaper (status-checked + idempotent), committing."""
    orch = _make_orchestrator()
    task_id = str(uuid4())

    svc = MagicMock()
    svc.unclaim_for_reaper = AsyncMock()

    @asynccontextmanager
    async def _factory_ctx() -> Any:
        db = MagicMock()
        db.commit = AsyncMock()
        yield db

    fake_factory = MagicMock(return_value=_factory_ctx())

    with (
        patch("roboco.db.base.get_session_factory", return_value=fake_factory),
        patch("roboco.services.task.TaskService", return_value=svc),
    ):
        await orch._release_stopped_agent_claim(_AGENT_ID, task_id)

    svc.unclaim_for_reaper.assert_awaited_once_with(require_uuid(task_id))


# ---------------------------------------------------------------------------
# _handle_stopped_container — self-exits finalize (stop_agent was not called)
# ---------------------------------------------------------------------------


async def test_handle_stopped_container_graceful_finalizes() -> None:
    """A graceful self-exit (exit 0) finalizes the spawn session.

    The agent calls i_am_idle and its container exits 0 without stop_agent
    being invoked, so _handle_stopped_container must finalize to capture the
    token usage; otherwise the session row is left open with zero tokens.
    """
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.container_id = "abc123def456"
    orch._instances[_AGENT_ID] = instance

    calls: list[tuple[str, str]] = []

    async def _fake_finalize(agent_id: str, exit_reason: str = "stopped") -> None:
        calls.append((agent_id, exit_reason))

    with patch.object(orch, "_finalize_spawn_session", side_effect=_fake_finalize):
        await orch._handle_stopped_container(_AGENT_ID, instance, 0)

    assert calls == [(_AGENT_ID, "completed")]
    assert instance.state is OrchestratorAgentState.OFFLINE


async def test_handle_stopped_container_crash_finalizes_then_restarts() -> None:
    """A non-zero exit finalizes (exit_reason='crashed') before auto-restart."""
    orch = _make_orchestrator()
    instance = _make_instance(_AGENT_ID)
    instance.container_id = "abc123def456"
    instance.error_count = 0
    orch._instances[_AGENT_ID] = instance

    calls: list[tuple[str, str]] = []

    async def _fake_finalize(agent_id: str, exit_reason: str = "stopped") -> None:
        calls.append((agent_id, exit_reason))

    with (
        patch.object(orch, "_finalize_spawn_session", side_effect=_fake_finalize),
        patch.object(orch, "spawn_agent", AsyncMock()) as mock_spawn,
    ):
        await orch._handle_stopped_container(_AGENT_ID, instance, 1)

    assert calls == [(_AGENT_ID, "crashed")]
    mock_spawn.assert_awaited_once()


# ---------------------------------------------------------------------------
# _resolve_active_tokens — SDK first, transcript fallback for live agents
# ---------------------------------------------------------------------------


async def test_resolve_active_tokens_falls_back_to_transcript() -> None:
    """When the SDK reports all-zero, live resolution uses the transcript."""
    orch = _make_orchestrator()

    def _handler(_url: str) -> Any:
        return _mock_response(
            200,
            {
                "tokens_input": 0,
                "tokens_output": 0,
                "tokens_cache_read": 0,
                "tokens_cache_write": 0,
            },
        )

    client = _FakeHTTPClient(_handler)
    with patch.object(
        orch, "_usage_from_transcript", return_value=(6, 514, 100, 50, 3)
    ):
        tokens = await orch._resolve_active_tokens(
            cast("httpx.AsyncClient", client), _AGENT_ID
        )

    assert tokens == (6, 514, 100, 50)


async def test_resolve_active_tokens_prefers_sdk() -> None:
    """A non-zero SDK response is used directly — no transcript fallback."""
    orch = _make_orchestrator()

    def _handler(_url: str) -> Any:
        return _mock_response(
            200,
            {
                "tokens_input": 10,
                "tokens_output": 20,
                "tokens_cache_read": 0,
                "tokens_cache_write": 0,
            },
        )

    client = _FakeHTTPClient(_handler)
    with patch.object(
        orch, "_usage_from_transcript", return_value=(999, 999, 999, 999, 0)
    ) as mock_tx:
        tokens = await orch._resolve_active_tokens(
            cast("httpx.AsyncClient", client), _AGENT_ID
        )

    assert tokens == (10, 20, 0, 0)
    mock_tx.assert_not_called()


async def test_resolve_final_turns_tools_from_sdk() -> None:
    """turns + tool_calls come from the SDK /usage/status when present."""
    orch = _make_orchestrator()

    def _handler(_url: str) -> Any:
        return _mock_response(200, {"turns": 7, "tool_calls": 42, "tokens_input": 1})

    with patch(
        "roboco.runtime.orchestrator.httpx.AsyncClient",
        lambda **_kw: _FakeHTTPClient(_handler),
    ):
        turns, tool_calls = await orch._resolve_final_turns_tools(_AGENT_ID)

    assert (turns, tool_calls) == (7, 42)


async def test_resolve_final_turns_tools_transcript_fallback_for_turns() -> None:
    """When the SDK reports 0 turns, fall back to the transcript turn count.

    tool_calls has no transcript equivalent and stays 0 ("n/a").
    """
    orch = _make_orchestrator()

    def _handler(_url: str) -> Any:
        return _mock_response(200, {"turns": 0, "tool_calls": 0})

    transcript_turns = 9
    with (
        patch(
            "roboco.runtime.orchestrator.httpx.AsyncClient",
            lambda **_kw: _FakeHTTPClient(_handler),
        ),
        patch.object(
            orch,
            "_usage_from_transcript",
            return_value=(1, 2, 3, 4, transcript_turns),
        ),
    ):
        turns, tool_calls = await orch._resolve_final_turns_tools(_AGENT_ID)

    assert turns == transcript_turns  # recovered from the transcript
    assert tool_calls == 0


# ---------------------------------------------------------------------------
# _usage_from_transcript — locate by session id across any project dir
# ---------------------------------------------------------------------------


def test_usage_from_transcript_finds_by_session_id_in_shared_app_dir(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A session id locates the transcript even in the shared -app dir.

    Review/coordinate roles run at cwd /app, so their transcript lands in
    projects/-app, not a per-agent projects/*-{slug} dir. The session id finds
    it regardless; the slug glob (no *-main-pm dir here) would return zeros.
    """
    app_dir = tmp_path / ".claude" / "projects" / "-app"
    app_dir.mkdir(parents=True)
    sid = "11111111-1111-1111-1111-111111111111"
    exp_in, exp_out, exp_cr, exp_cw = 12, 34, 5, 6
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "usage": {
                    "input_tokens": exp_in,
                    "output_tokens": exp_out,
                    "cache_read_input_tokens": exp_cr,
                    "cache_creation_input_tokens": exp_cw,
                },
            },
        }
    )
    (app_dir / f"{sid}.jsonl").write_text(line + "\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = AgentOrchestrator._usage_from_transcript("main-pm", sid)
    assert result == (exp_in, exp_out, exp_cr, exp_cw, 1)  # one message => 1 turn


def test_usage_from_transcript_without_session_id_uses_slug_glob(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Without a session id it still finds the agent's own workspace transcript."""
    slug_dir = tmp_path / ".claude" / "projects" / "-data-ws-roboco-backend-be-dev-1"
    slug_dir.mkdir(parents=True)
    exp_in, exp_out = 7, 8
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "usage": {"input_tokens": exp_in, "output_tokens": exp_out},
            },
        }
    )
    (slug_dir / "sess.jsonl").write_text(line + "\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = AgentOrchestrator._usage_from_transcript("be-dev-1")
    assert result == (exp_in, exp_out, 0, 0, 1)  # one message => 1 turn
