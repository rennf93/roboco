"""Unit tests for the rate-limited path in Choreographer.i_am_blocked.

Behaviours verified here:
- i_am_blocked(reason='rate_limited') calls RateLimitStateTracker.activate()
  and stores affected agent IDs; all active agents on the rate-limited
  provider are subsequently marked waiting-long.
- POST /v1/i_am_blocked with reason='rate_limited' does NOT transition the
  task to 'blocked'; the task remains in its current status (in_progress) and
  the calling agent is parked via mark_waiting_long(waiting_for='rate_limit_lifted').
- mark_waiting_long is called for every orchestrator-tracked active agent
  sharing the affected provider — call count equals active agent count.
- A RATE_LIMIT_HIT event is published to the StreamEventBus with fields
  provider, affectedAgents, retryAfterSeconds, and timestamp.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from roboco.models.events import EventType
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from structlog.testing import capture_logs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACTIVE_AGENTS = ["be-dev-1", "be-dev-2", "be-qa"]
_PROVIDER = "anthropic"


def _make_evidence_repo() -> AsyncMock:
    repo = AsyncMock()
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    return repo


def _make_task_svc(agent_id: object, task_id: object) -> AsyncMock:
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=agent_id,
        pre_block_state=None,
        task_type="code",
        team="backend",
        # Avoid issues with spec iteration in claim guards
        dependency_ids=[],
        # acceptance_criteria needed by some paths
        acceptance_criteria=[],
        quick_context=None,
    )
    task_svc = AsyncMock()
    task_svc.session = MagicMock()
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        id=agent_id,
        role="developer",
        team="backend",
        slug="be-dev-1",  # calling agent's slug
    )
    return task_svc


def _make_orchestrator(
    active_agents: list[str] | None = None,
    provider: str = _PROVIDER,
) -> MagicMock:
    """Build a synchronous/async orchestrator mock."""
    agents = active_agents if active_agents is not None else _ACTIVE_AGENTS
    orch = MagicMock()
    orch.get_provider_for_agent = MagicMock(return_value=provider)
    orch.get_active_agent_slugs_for_provider = MagicMock(return_value=agents)
    orch.mark_waiting_long = AsyncMock(return_value=None)
    return orch


def _make_stream_bus() -> AsyncMock:
    bus = AsyncMock()
    bus.publish = AsyncMock(return_value="msg-id-1")
    return bus


def _make_deps(
    agent_id: object,
    task_id: object,
    orchestrator: MagicMock | None = None,
    stream_bus: AsyncMock | None = None,
) -> ChoreographerDeps:
    return ChoreographerDeps(
        task=_make_task_svc(agent_id, task_id),
        work_session=AsyncMock(),
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        audit=AsyncMock(),
        evidence_repo=_make_evidence_repo(),
        orchestrator=orchestrator,
        stream_bus=stream_bus,
    )


# ---------------------------------------------------------------------------
# Task stays in in_progress, agent parked via mark_waiting_long
# ---------------------------------------------------------------------------


class TestRateLimitedDoesNotBlockTask:
    async def test_task_status_remains_in_progress(self) -> None:
        """reason='rate_limited' must NOT transition the task to 'blocked'."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator()
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        env = await c.i_am_blocked(agent_id, task_id, "rate_limited")

        assert env.error is None
        assert env.status == "in_progress"

    async def test_verb_runner_block_action_not_called(self) -> None:
        """The `block` action (task.escalate) must NOT run on rate_limited path."""
        agent_id = uuid4()
        task_id = uuid4()
        deps = _make_deps(agent_id, task_id)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        # The VerbRunner calls task.escalate for the normal block path.
        # In the rate-limited path this must NOT happen.
        deps.task.escalate.assert_not_awaited()

    async def test_calling_agent_parked_via_mark_waiting_long(self) -> None:
        """mark_waiting_long must be called with waiting_for='rate_limit_lifted'."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"])
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        # Verify that at least one mark_waiting_long call uses the right reason.
        # The implementation calls mark_waiting_long(slug, waiting_for=..., ...)
        # so waiting_for is always a keyword argument.
        waiting_for_values = [
            c.kwargs.get("waiting_for") for c in orch.mark_waiting_long.call_args_list
        ]
        assert "rate_limit_lifted" in waiting_for_values

    async def test_case_insensitive_reason_match(self) -> None:
        """reason='Rate_Limited' (any case) should trigger the special path."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"])
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        env = await c.i_am_blocked(agent_id, task_id, "Rate_Limited")

        assert env.error is None
        assert env.status == "in_progress"

    async def test_struggle_journal_still_written(self) -> None:
        """journal.write_struggle must still be written on the rate_limited path."""
        agent_id = uuid4()
        task_id = uuid4()
        deps = _make_deps(agent_id, task_id)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        deps.journal.write_struggle.assert_awaited_once()


# ---------------------------------------------------------------------------
# mark_waiting_long called for every active agent on affected provider
# ---------------------------------------------------------------------------


class TestMarkWaitingLongCallCount:
    async def test_call_count_equals_active_agent_count(self) -> None:
        """mark_waiting_long must be called once per active agent."""
        agent_id = uuid4()
        task_id = uuid4()
        active = ["be-dev-1", "be-dev-2", "be-dev-3"]
        orch = _make_orchestrator(active_agents=active)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        assert orch.mark_waiting_long.call_count == len(active)

    async def test_call_count_with_single_active_agent(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"])
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        assert orch.mark_waiting_long.call_count == 1

    async def test_no_calls_when_no_active_agents(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=[])
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        assert orch.mark_waiting_long.call_count == 0

    async def test_no_calls_when_orchestrator_is_none(self) -> None:
        """When orchestrator is not wired in, no parking happens but no crash."""
        agent_id = uuid4()
        task_id = uuid4()
        deps = _make_deps(agent_id, task_id, orchestrator=None)
        c = Choreographer(deps)

        env = await c.i_am_blocked(agent_id, task_id, "rate_limited")

        # Should still succeed; no orchestrator = no parking
        assert env.error is None
        assert env.status == "in_progress"

    async def test_mark_waiting_long_receives_waiting_for_arg(self) -> None:
        """Every mark_waiting_long call must carry waiting_for='rate_limit_lifted'."""
        agent_id = uuid4()
        task_id = uuid4()
        active = ["be-dev-1", "be-qa"]
        orch = _make_orchestrator(active_agents=active)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        for c_args in orch.mark_waiting_long.call_args_list:
            # mark_waiting_long(slug, waiting_for=..., ...) — waiting_for is a kwarg
            assert c_args.kwargs.get("waiting_for") == "rate_limit_lifted"


# ---------------------------------------------------------------------------
# RATE_LIMIT_HIT event published with correct payload structure
# ---------------------------------------------------------------------------


class TestRateLimitHitEventPublished:
    async def test_stream_bus_publish_called_once(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator()
        bus = _make_stream_bus()
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=bus)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        bus.publish.assert_awaited_once()

    async def test_event_type_is_rate_limit_hit(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator()
        bus = _make_stream_bus()
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=bus)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        event = bus.publish.call_args.args[0]
        assert event.type == EventType.RATE_LIMIT_HIT

    async def test_event_data_has_provider_field(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(provider="anthropic")
        bus = _make_stream_bus()
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=bus)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        event = bus.publish.call_args.args[0]
        assert "provider" in event.data
        assert event.data["provider"] == "anthropic"

    async def test_event_data_has_affected_agents_list(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        active = ["be-dev-1", "be-dev-2"]
        orch = _make_orchestrator(active_agents=active)
        bus = _make_stream_bus()
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=bus)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        event = bus.publish.call_args.args[0]
        assert "affectedAgents" in event.data
        assert isinstance(event.data["affectedAgents"], list)
        assert event.data["affectedAgents"] == active

    async def test_event_data_has_retry_after_seconds_null_by_default(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator()
        bus = _make_stream_bus()
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=bus)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        event = bus.publish.call_args.args[0]
        assert "retryAfterSeconds" in event.data
        assert event.data["retryAfterSeconds"] is None

    async def test_event_data_retry_after_parsed_from_what_needed(self) -> None:
        """If what_needed is a numeric string, it becomes retryAfterSeconds."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator()
        bus = _make_stream_bus()
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=bus)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited", what_needed="30")

        event = bus.publish.call_args.args[0]
        assert event.data["retryAfterSeconds"] == float("30")

    async def test_event_data_has_timestamp_iso_string(self) -> None:
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator()
        bus = _make_stream_bus()
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=bus)
        c = Choreographer(deps)

        await c.i_am_blocked(agent_id, task_id, "rate_limited")

        event = bus.publish.call_args.args[0]
        assert "timestamp" in event.data
        # ISO string: must be a non-empty string
        ts = event.data["timestamp"]
        assert isinstance(ts, str) and len(ts) > 0

    async def test_no_publish_when_stream_bus_is_none(self) -> None:
        """When stream_bus is not wired in, no publish is attempted."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator()
        # stream_bus=None: no bus
        deps = _make_deps(agent_id, task_id, orchestrator=orch, stream_bus=None)
        c = Choreographer(deps)

        env = await c.i_am_blocked(agent_id, task_id, "rate_limited")

        # Should still succeed
        assert env.error is None
        assert env.status == "in_progress"


# ---------------------------------------------------------------------------
# RateLimitStateTracker.activate() called on rate_limited path
# ---------------------------------------------------------------------------

_TRACKER_PATCH = "roboco.services.gateway.rate_limit_tracker.RateLimitStateTracker"


class TestRateLimitTrackerActivateOnParking:
    """Verify that _handle_rate_limited_parking() calls activate()."""

    async def test_activate_called_when_provider_known(self) -> None:
        """activate() must be called once when provider != 'unknown'."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"], provider=_PROVIDER)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        mock_tracker = AsyncMock()
        mock_tracker.activate = AsyncMock(return_value=None)
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

        with patch(_TRACKER_PATCH, mock_tracker_cls):
            await c.i_am_blocked(agent_id, task_id, "rate_limited")

        mock_tracker_cls.assert_called_once_with(_PROVIDER)
        mock_tracker.activate.assert_awaited_once()

    async def test_activate_receives_affected_agents(self) -> None:
        """activate() must be called with the affected_agents list."""
        agent_id = uuid4()
        task_id = uuid4()
        active = ["be-dev-1", "be-dev-2"]
        orch = _make_orchestrator(active_agents=active, provider=_PROVIDER)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        mock_tracker = AsyncMock()
        mock_tracker.activate = AsyncMock(return_value=None)
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

        with patch(_TRACKER_PATCH, mock_tracker_cls):
            await c.i_am_blocked(agent_id, task_id, "rate_limited")

        call_kwargs = mock_tracker.activate.call_args.kwargs
        assert call_kwargs.get("affected_agents") == active

    async def test_activate_receives_retry_after_from_what_needed(self) -> None:
        """activate() must receive retry_after parsed from what_needed."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"], provider=_PROVIDER)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        mock_tracker = AsyncMock()
        mock_tracker.activate = AsyncMock(return_value=None)
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

        with patch(_TRACKER_PATCH, mock_tracker_cls):
            await c.i_am_blocked(agent_id, task_id, "rate_limited", what_needed="45")

        call_kwargs = mock_tracker.activate.call_args.kwargs
        assert call_kwargs.get("retry_after") == float("45")

    async def test_activate_retry_after_none_when_what_needed_not_numeric(self) -> None:
        """activate() must receive retry_after=None when what_needed is not a number."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"], provider=_PROVIDER)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        mock_tracker = AsyncMock()
        mock_tracker.activate = AsyncMock(return_value=None)
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

        with patch(_TRACKER_PATCH, mock_tracker_cls):
            await c.i_am_blocked(
                agent_id, task_id, "rate_limited", what_needed="retry soon"
            )

        call_kwargs = mock_tracker.activate.call_args.kwargs
        assert call_kwargs.get("retry_after") is None

    async def test_activate_skipped_when_provider_unknown(self) -> None:
        """activate() must NOT be called when provider resolves to 'unknown'."""
        agent_id = uuid4()
        task_id = uuid4()
        # get_provider_for_agent returns None → provider stays 'unknown'
        orch = MagicMock()
        orch.get_provider_for_agent = MagicMock(return_value=None)
        orch.get_active_agent_slugs_for_provider = MagicMock(return_value=[])
        orch.mark_waiting_long = AsyncMock(return_value=None)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        mock_tracker = AsyncMock()
        mock_tracker.activate = AsyncMock(return_value=None)
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

        with patch(_TRACKER_PATCH, mock_tracker_cls):
            env = await c.i_am_blocked(agent_id, task_id, "rate_limited")

        # No crash, no activate call
        assert env.error is None
        mock_tracker.activate.assert_not_awaited()

    async def test_activate_failure_does_not_crash_path(self) -> None:
        """If activate() raises, _handle_rate_limited_parking must still succeed."""
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"], provider=_PROVIDER)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        mock_tracker = AsyncMock()
        mock_tracker.activate = AsyncMock(side_effect=RuntimeError("redis down"))
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

        with patch(_TRACKER_PATCH, mock_tracker_cls):
            env = await c.i_am_blocked(agent_id, task_id, "rate_limited")

        assert env.error is None
        assert env.status == "in_progress"

    async def test_activate_failure_is_logged_not_silent(self) -> None:
        """An activate() failure must be logged loudly, not bare-suppressed —
        the probe-resume loop is tracker-driven, so a silent failure strands
        every parked agent in WAITING_LONG with no probe ever running.
        """
        agent_id = uuid4()
        task_id = uuid4()
        orch = _make_orchestrator(active_agents=["be-dev-1"], provider=_PROVIDER)
        deps = _make_deps(agent_id, task_id, orchestrator=orch)
        c = Choreographer(deps)

        mock_tracker = AsyncMock()
        mock_tracker.activate = AsyncMock(side_effect=RuntimeError("redis down"))
        mock_tracker_cls = MagicMock(return_value=mock_tracker)

        with patch(_TRACKER_PATCH, mock_tracker_cls), capture_logs() as logs:
            env = await c.i_am_blocked(agent_id, task_id, "rate_limited")

        assert env.error is None
        assert any(
            "activate" in str(e.get("event", "")).lower()
            and e.get("log_level") == "error"
            for e in logs
        ), f"expected an error log about activate failure; got {logs!r}"
