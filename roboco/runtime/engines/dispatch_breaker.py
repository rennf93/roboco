"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: dispatch_breaker)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.runtime.orchestrator import (
    _DOCKER_EXEC_TIMEOUT_SECONDS,
    INTAKE_AGENT_ID,
    SECRETARY_AGENT_ID,
    AgentState,
    logger,
)
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from uuid import UUID

    import httpx

    from roboco.services.task import TaskService


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class DispatchBreakerEngine(_Base):
    """Mixin holding the "dispatch_breaker" methods moved out of AgentOrchestrator."""

    # Re-declared (not just inherited from _Base above): mypy's Protocol
    # attribute inference cannot determine the type of an inherited member
    # that a method both reads AND assigns within the same method body
    # (read-then-write in one scope; see _prune_notification_spawn_maps and
    # _emit_dispatcher_heartbeat below) without a bare re-declaration
    # directly on the concrete class. Without it a fresh run rooted at
    # orchestrator.py alone infers `datetime` from the heartbeat assignment
    # and rejects __init__'s `datetime | None`.
    if TYPE_CHECKING:
        _last_dispatch_heartbeat: datetime | None
        _notification_spawn_at: dict[tuple[str, str], float]
        _notification_spawn_count: dict[tuple[str, str], int]

    async def _notification_spawn_cooled(
        self, agent_slug: str, notification_id: str | None
    ) -> bool:
        """True when this (agent, notification) spawn is suppressed.

        Two guards: the cross-tick cooldown (one spawn per window), AND a hard
        cap — past ``notification_spawn_max_attempts`` spawns for the same
        notification without it being acknowledged, stop re-spawning entirely.
        Without the cap a wedged escalation/alert whose recipient never resolves
        it re-spawns that recipient every window forever (these dispatchers
        carry no task_id, so the PM respawn breaker never sees them). The cap is
        id-scoped: a fresh escalation (new notification id) is unaffected, and a
        resolved one stops being fetched and prunes out.

        Returns False — and stamps + counts the pair — when a spawn is allowed.
        A notification with no id is never damped (fail-open: better one extra
        spawn than a silently dropped escalation).
        """
        if not notification_id:
            return False
        # Lazy init keeps the damper working on partially-constructed
        # instances (tests build the orchestrator via __new__).
        store: dict[tuple[str, str], float] = self.__dict__.setdefault(
            "_notification_spawn_at", {}
        )
        counts: dict[tuple[str, str], int] = self.__dict__.setdefault(
            "_notification_spawn_count", {}
        )
        key = (agent_slug, str(notification_id))
        now = time.monotonic()
        cooldown = settings.notification_spawn_cooldown_seconds
        last = store.get(key)
        if last is not None and (now - last) < cooldown:
            return True
        if await self._notification_spawn_over_cap(key, store, counts, now):
            return True
        counts[key] = counts.get(key, 0) + 1
        store[key] = now
        if len(store) > self._NOTIFICATION_COOLDOWN_PRUNE_AT:
            self._prune_notification_spawn_maps(now - cooldown)
        return False

    async def _notification_spawn_over_cap(
        self,
        key: tuple[str, str],
        store: dict[tuple[str, str], float],
        counts: dict[tuple[str, str], int],
        now: float,
    ) -> bool:
        """True (and suppresses the spawn) once ``key`` has spawned
        ``notification_spawn_max_attempts`` times without the notification being
        acknowledged — the no-task_id analogue of the PM respawn breaker.
        Re-stamps so the capped entry survives pruning (which would otherwise
        drop the count and reset the cap); logs and notifies the CEO exactly
        once at the trip (matching ``_notify_stuck_agent``'s best-effort
        pattern) — never re-fires on later suppressed spawns for the same key.
        """
        max_attempts = settings.notification_spawn_max_attempts
        attempts = counts.get(key, 0)
        if not (max_attempts and attempts >= max_attempts):
            return False
        store[key] = now
        if attempts == max_attempts:
            counts[key] = attempts + 1
            logger.warning(
                "escalation respawn loop broken — a notification kept "
                "respawning its target without being acknowledged; "
                "suppressing further spawns until it is resolved",
                agent_slug=key[0],
                notification_id=key[1],
                attempts=attempts,
                max_attempts=max_attempts,
            )
            await self._notify_notification_spawn_capped(
                agent_slug=key[0], notification_id=key[1], attempts=attempts
            )
        return True

    async def _notify_notification_spawn_capped(
        self, agent_slug: str, notification_id: str, attempts: int
    ) -> None:
        """One-shot alert to the CEO that the notification-spawn cap tripped.

        Best-effort, mirroring ``_notify_stuck_agent``: a notification
        failure must not wedge dispatch, so any error is logged and
        swallowed.
        """
        from roboco.services.notification import NotificationService

        try:
            await NotificationService().send_notification_spawn_cap_notification(
                agent_slug=agent_slug,
                notification_id=notification_id,
                to_agent="ceo",
                attempts=attempts,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send notification-spawn-cap notification",
                agent_slug=agent_slug,
                notification_id=notification_id,
                error=str(exc),
            )

    def _prune_notification_spawn_maps(self, cutoff: float) -> None:
        """Drop cooldown/count entries stamped before ``cutoff`` (both maps
        stay aligned so a surviving cap keeps its count)."""
        self._notification_spawn_at = {
            k: v for k, v in self._notification_spawn_at.items() if v >= cutoff
        }
        survivors = set(self._notification_spawn_at)
        self._notification_spawn_count = {
            k: v for k, v in self._notification_spawn_count.items() if k in survivors
        }

    async def _notification_has_live_work(
        self, client: httpx.AsyncClient, notif: dict[str, Any]
    ) -> bool:
        """False when a notification has no live work behind it — the 'is there
        actually something to do' gate for notification-triggered spawns. Four
        obvious markers: it has expired, it is stale past the spawn-age window
        (wedged / reloaded from before a restart), its related task is already
        terminal (the work is done), or that task is HITL-blocked (a human,
        not a respawn, resolves it — e.g. the oscillation breaker tripped and
        force-blocked it; the task-status dispatchers already skip these via
        ``_is_hitl_blocked``, but this notification-driven path carries no
        task-status gate of its own). Fail-open: an unparseable field or a
        failed task fetch never suppresses a spawn. The stale-past-spawn-age
        check reads active time (`_active_age`), so a notification minted
        right before a CEO pause isn't already "wedged" the moment the fleet
        wakes up.
        """
        now = datetime.now(UTC)
        expires = self._parse_iso_dt(notif.get("expires_at"))
        if expires is not None and expires <= now:
            return False
        max_age = settings.notification_spawn_max_age_seconds
        ts = self._parse_iso_dt(notif.get("timestamp"))
        if (
            max_age
            and ts is not None
            and self._active_age(ts, now).total_seconds() > max_age
        ):
            return False
        task_id = notif.get("related_task_id")
        if task_id:
            fields = await self._fetch_task_fields(client, str(task_id))
            if fields is not None:
                if fields.get("status") in ("completed", "cancelled"):
                    return False
                if self._is_hitl_blocked(fields):
                    return False
        return True

    def _is_parallel_phase_claim(
        self, task: dict[str, Any], dev_uuid: str | None
    ) -> bool:
        """True if a `claimed` task is actually in the doc/PR parallel phase.

        The `original_developer:` quick_context marker is set pre-QA by
        `open_pr`, so it alone cannot distinguish a QA-claimed
        awaiting_qa task (wrong) from a doc-claimed awaiting_documentation
        task (right). Require the claimant to be a documenter.
        """
        if not dev_uuid:
            return False
        claimed_by = task.get("claimed_by")
        if not claimed_by:
            return False
        claimed_slug = self._resolve_agent_slug(claimed_by)
        return bool(claimed_slug) and "doc" in claimed_slug

    async def _respawn_dev_for_pr_half(
        self, task: dict[str, Any], dev_uuid: str | None
    ) -> None:
        """Respawn the original developer if they still owe the PR half.

        `pr_number` is set by the PR-create handler as soon as GitHub
        confirms the PR, even if the status-gated `pr_created` flag never
        flips — without that second check we'd respawn the dev forever
        after they've already created the PR (the handler refuses to set
        pr_created=True when the doc's claim moved status out of
        awaiting_documentation).
        """
        if not dev_uuid or task.get("pr_created") or task.get("pr_number"):
            return
        dev_slug = self._resolve_agent_slug(dev_uuid)
        if dev_slug and await self._pm_respawn_should_gate(dev_slug, task):
            # Respawn circuit breaker — the PR-half respawn loops exactly like
            # the doc half when the dev can never finish (progress resets it).
            return
        if not dev_slug or self._is_agent_active(dev_slug):
            return
        await self.spawn_agent(
            agent_id=dev_slug,
            task_id=task["id"],
            initial_prompt=await self._build_dev_prompt(task),
            git_context=self._task_git_context(task),
            spawned_by="_respawn_dev_for_pr_half",
        )

    def _schedule_respawn_persist(
        self, agent_slug: str, task_id: str, record: dict[str, Any]
    ) -> None:
        """Fire-and-forget a write-through of one PM-respawn counter row.

        Copies ``record`` so a later in-place mutation can't race the background
        write, then schedules it on the strong-ref ``_bg_tasks`` set — the
        dispatcher hot path never blocks on the DB, and a write failure degrades
        to in-memory-only (today's behaviour).
        """
        self._schedule_bg(
            self._persist_respawn_record(agent_slug, task_id, dict(record))
        )

    async def _persist_respawn_record(
        self, agent_slug: str, task_id: str, record: dict[str, Any]
    ) -> None:
        """Write-through one PM-respawn counter row (atomic upsert).

        Best-effort, mirroring ``_persist_waiting_record``: a persistence failure
        must never gate or un-gate a spawn, so any error is logged and swallowed.
        The counter stays authoritative in memory regardless.

        Unlike ``_persist_waiting_record`` (inline-awaited, one row per agent),
        this is scheduled fire-and-forget per gate mutation, and a respawn loop
        fires several persists for the same ``(agent_slug, task_id)`` in quick
        succession. A delete-then-insert raced under that concurrency: two
        transactions for the same key overlapped, the loser's INSERT hit
        ``pk_respawn_tracker`` UniqueViolation, the durable count stuck at the
        first INSERT's value, and a restart re-burned the strike threshold — the
        exact re-burn this feature was built to stop (2026-06-27 live meltdown).
        The single ``ON CONFLICT DO UPDATE`` upsert is race-free at row level, BUT
        fire-and-forget tasks can still COMMIT out of order: a slow stale persist
        (count=2) scheduled first can resolve AFTER a fast fresh one (count=4)
        scheduled second, leaving the durable row at the stale low count (same
        re-burn on restart). The ``_respawn_persist_lock`` is acquired as the
        FIRST await below, so acquisition order = task creation order = logical
        schedule order, and commits land in that order — the durable row always
        ends at the latest logical value.
        """
        async with self._respawn_persist_lock:
            try:
                from uuid import UUID as _UUID

                from sqlalchemy.dialects.postgresql import insert as pg_insert

                from roboco.db.base import get_session_factory
                from roboco.db.tables import RespawnTrackerTable

                tid = _UUID(task_id)
                now = datetime.now(UTC)
                stmt = pg_insert(RespawnTrackerTable).values(
                    agent_slug=agent_slug,
                    task_id=tid,
                    count=int(record["count"]),
                    last_status=record.get("last_status"),
                    last_check=record["last_check"],
                    tracing_resets=int(record.get("tracing_resets", 0)),
                    revisit_resets=int(record.get("revisit_resets", 0)),
                    notified=bool(record.get("notified", False)),
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        RespawnTrackerTable.agent_slug,
                        RespawnTrackerTable.task_id,
                    ],
                    set_={
                        "count": stmt.excluded.count,
                        "last_status": stmt.excluded.last_status,
                        "last_check": stmt.excluded.last_check,
                        "tracing_resets": stmt.excluded.tracing_resets,
                        "revisit_resets": stmt.excluded.revisit_resets,
                        "notified": stmt.excluded.notified,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                session_factory = get_session_factory()
                async with session_factory() as db:
                    await db.execute(stmt)
                    await db.commit()
            except Exception as e:
                logger.error(
                    "Failed to persist respawn record",
                    agent_id=agent_slug,
                    task_id=task_id,
                    error=str(e),
                )

    def _grok_cost_usd(self, agent_id: str) -> float:
        """A GROK agent's captured notional cost from its ``usage.json`` (0 if none)."""
        data = self._grok_usage_json(agent_id)
        if not data:
            return 0.0
        try:
            return float(data.get("cost_usd", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _gemini_cost_usd(self, agent_id: str) -> float:
        """A GEMINI agent's captured cost from its ``usage.json`` (0 if none)."""
        data = self._gemini_usage_json(agent_id)
        if not data:
            return 0.0
        try:
            return float(data.get("cost_usd", 0.0))
        except (TypeError, ValueError):
            return 0.0

    async def _enforce_grok_cost_budget(self) -> None:
        """Kill a live GROK container whose captured cost exceeds the cap.

        The grok CLI exposes no live token/budget hook, so the budget kill-switch
        (Claude Code parity for runaway token burn — a loop that keeps firing
        verbs evades the idle watchdog but still burns cost) reads each ACTIVE
        GROK container's captured cost from its ``usage.json`` and kills + evicts
        it past ``ROBOCO_GROK_MAX_COST_USD``. The reaper then releases the freed
        task. This bites on the interactive sessions (the driver rewrites
        usage.json every turn, so a runaway chat is caught between turns); a
        one-shot ``grok -p`` writes usage.json only post-run and is bounded by its
        ``--max-turns`` cap instead. Disabled (no-op) when the cap is <= 0.
        """
        cap = getattr(self, "_grok_max_cost_usd", 0.0)
        if cap <= 0:
            return
        from roboco.models.base import ModelProvider

        for agent_id, instance in list(self._instances.items()):
            config = instance.config
            if (
                config is None
                or config.provider_type != ModelProvider.GROK.value
                or instance.state != AgentState.ACTIVE
            ):
                continue
            cost = self._grok_cost_usd(agent_id)
            if cost <= cap:
                continue
            try:
                await self._remove_container(
                    f"roboco-agent-{agent_id}", stop_reason="grok_cost_cap"
                )
            except Exception as exc:
                logger.error(
                    "grok cost-cap kill failed; will retry next tick",
                    agent_id=agent_id,
                    error=str(exc),
                )
                continue
            # Finalize the spawn session BEFORE popping the instance so the
            # captured usage/cost is recorded; popping first would lose the
            # model + usage_session_id and leave the session row open.
            with contextlib.suppress(Exception):
                await self._finalize_spawn_session(agent_id, exit_reason="cost_cap")
            self._instances.pop(agent_id, None)
            # Interactive roles (intake/secretary) have an open panel relay; a
            # raw kill would leave the SSE hanging (frozen chat). Close it with a
            # reason so the panel reports why the chat ended.
            if agent_id in (INTAKE_AGENT_ID, SECRETARY_AGENT_ID):
                from roboco.services.prompter_live import get_live_registry

                get_live_registry().close_by_agent(
                    agent_id, error="Chat ended: the Grok cost cap was exceeded."
                )
            logger.warning(
                "grok container killed: cost ceiling exceeded",
                agent_id=agent_id,
                cost_usd=round(cost, 4),
                cap_usd=cap,
            )

    @staticmethod
    async def _probe_gateway_health(slug: str) -> bool | None:
        """Probe an agent container's gateway out-of-band: healthy / broken / unknown.

        The heartbeat only proves a verb fired recently; it cannot tell a quiet-
        but-healthy agent from one whose MCP gateway is broken (e.g. a corrupted
        ``/app/.venv`` so every gateway tool import raises) yet whose container is
        still up. This asks the container directly whether the gateway venv imports
        its core deps. Returns True (healthy), False (the import failed => broken
        gateway), or None when the probe itself could not run (no docker, container
        gone) so the caller declines to act on an inconclusive probe.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                f"roboco-agent-{slug}",
                "/app/.venv/bin/python",
                "-c",
                "import httpx, mcp",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            return None
        try:
            rc = await asyncio.wait_for(
                proc.wait(), timeout=_DOCKER_EXEC_TIMEOUT_SECONDS
            )
        except TimeoutError:
            # A hung docker exec is inconclusive (the probe could not run to
            # completion): kill the child and decline to act, matching the
            # existing probe-failure contract. The next grace tick retries.
            proc.kill()
            return None
        except Exception:
            return None
        return rc == 0

    async def _emit_dispatcher_heartbeat(self) -> None:
        """Periodic dispatcher.alive audit row — a dead loop becomes detectable.

        The dispatch loop can die silently (task cancelled, unhandled exit) and
        its stdout dies with the container; audit_log survives both. Absence of
        a fresh heartbeat row = loop dead, distinguishable from "no work". The
        `dispatch_paused` flag lets the uptime ledger treat a deliberate
        maintenance pause as downtime instead of counting it as an outage.
        """
        now = datetime.now(UTC)
        last = getattr(self, "_last_dispatch_heartbeat", None)
        if last is not None and (now - last).total_seconds() < (
            self._DISPATCH_HEARTBEAT_SECONDS
        ):
            return
        self._last_dispatch_heartbeat = now
        from roboco.services.maintenance_pause import PauseScope

        paused = await self._is_paused(PauseScope.DISPATCH)
        self._fire_audit(
            event_type="dispatcher.alive",
            agent_slug="orchestrator",
            details={
                "interval_seconds": self._DISPATCH_HEARTBEAT_SECONDS,
                "dispatch_paused": paused,
            },
        )

    async def _refresh_grok_auth(self) -> None:
        """Keep the host SuperGrok token live so grok agents never mount a dead one.

        The grok access token has a ~6h server-set TTL and headless grok cannot
        self-refresh — on an expired token it hangs at an interactive login
        prompt. The per-agent mount is read-only, so the orchestrator refreshes
        the host ``auth.json`` itself (refresh-token grant) before expiry; agents
        then mount a fresh credential. Best-effort, throttled, and serial (run
        once per dispatch tick) so concurrent refreshes can't rotate the
        refresh-token out from under each other. Never breaks the loop.
        """
        now = datetime.now(UTC)
        next_check = getattr(self, "_grok_auth_next_check", None)
        if next_check is not None and now < next_check:
            return
        self._grok_auth_next_check = now + timedelta(seconds=60)
        try:
            from roboco.llm.providers import grok_auth
            from roboco.llm.providers.grok import GROK_AUTH_HOST_PATH

            auth_path = Path(GROK_AUTH_HOST_PATH) / "auth.json"
            status = await asyncio.to_thread(grok_auth.refresh_if_stale, auth_path)
            if status == "refreshed":
                logger.info("grok auth token refreshed")
            elif status == "failed":
                logger.warning(
                    "grok auth refresh failed; agents may hit an expired token"
                )
        except Exception as exc:
            logger.error("grok auth refresh hook error", error=str(exc))

    async def _refresh_codex_auth(self) -> None:
        """Keep the host Codex CLI credential live (parity with ``_refresh_grok_auth``).

        Same rationale as the grok refresh: the per-agent mount is read-only,
        so the orchestrator refreshes the host ``auth.json`` itself before the
        access JWT expires. Best-effort, throttled, and serial (run once per
        dispatch tick). Never breaks the loop.
        """
        now = datetime.now(UTC)
        next_check = getattr(self, "_codex_auth_next_check", None)
        if next_check is not None and now < next_check:
            return
        self._codex_auth_next_check = now + timedelta(seconds=60)
        try:
            from roboco.llm.providers import codex_auth
            from roboco.llm.providers.codex import CODEX_AUTH_HOST_PATH

            auth_path = Path(CODEX_AUTH_HOST_PATH) / "auth.json"
            status = await asyncio.to_thread(codex_auth.refresh_if_stale, auth_path)
            if status == "refreshed":
                logger.info("codex auth token refreshed")
            elif status == "failed":
                logger.warning(
                    "codex auth refresh failed; agents may hit an expired token"
                )
        except Exception as exc:
            logger.error("codex auth refresh hook error", error=str(exc))

    async def _reap_stale_claims(self) -> None:
        """Release claimed/in_progress tasks whose holder hasn't heart-beat in TTL.

        Closes the "dead container squats task forever" failure mode that
        the schema hinted at (``last_heartbeat_at`` since migration 006) but
        no code enforced. The runtime decision (cutoff, iteration) lives
        here in the orchestrator; the actual UPDATE statements live in
        ``TaskService.unclaim_for_reaper``.

        Opens a fresh per-tick session — short-lived because the reaper
        runs on every dispatch cycle and the work is cheap (one SELECT
        plus N UPDATEs for the typically-empty stale set). Tests that
        need to inject a mock service do so by building an instance via
        ``__new__`` (bypassing this method) and calling
        ``_reap_with_service`` directly.

        THE TRAP: while ``dispatch`` is paused, agents legitimately stop
        bumping their gateway heartbeat (no new turn is being dispatched for
        them), so an otherwise-idle-but-fine claim would read as wedged past
        the TTL and get unclaimed + respawned, the exact opposite of paused.
        But the pause must not blind the fleet to a genuinely wedged/stuck/
        broken-gateway container: those kills are liveness-based, not
        dispatch-based, and a paused fleet can still burn spend on a runaway
        agent for up to ``MAX_PAUSE_HOURS``. So the reap still runs every
        tick; only its DB-level unclaim fallback (the part that only helps
        if a respawn can follow, which a paused fleet won't do) is gated on
        ``dispatch_paused`` -- see ``_reap_with_service``.
        """
        from roboco.db.base import get_session_factory
        from roboco.services.maintenance_pause import PauseScope, is_paused
        from roboco.services.task import TaskService

        factory = get_session_factory()
        async with factory() as db:
            dispatch_paused = await is_paused(db, PauseScope.DISPATCH)
            svc = TaskService(db)
            await self._reap_with_service(svc, dispatch_paused=dispatch_paused)
            await db.commit()
        await self._sandbox_janitor_sweep()

    def _assignee_has_active_instance(self, task: Any) -> bool:
        """True if the task's assignee currently holds a live (ACTIVE) container.

        The heartbeat only approximates liveness. A developer deep in an
        edit/test cycle can go longer than the heartbeat TTL between gateway
        calls, so a heartbeat-only reaper releases claims out from under agents
        that are alive and working — churning the task (and risking a double
        spawn against the still-running container). The agent-instance registry
        is the ground truth; defer to it when present. Defensive on missing
        fields so a heartbeat-only caller (and the reaper's own unit tests)
        behave exactly as before.
        """
        owner = getattr(task, "assigned_to", None) or getattr(task, "claimed_by", None)
        if not owner:
            return False
        instances = getattr(self, "_instances", None)
        if not instances:
            return False
        instance = instances.get(self._resolve_agent_slug(str(owner)))
        return instance is not None and instance.state == AgentState.ACTIVE

    def _assignee_is_provider_parked(self, task: Any) -> bool:
        """True if the task's assignee is parked waiting for a provider to recover.

        A provider-parked agent (session-limit / overload / grok-429) is OFFLINE
        with a dead container and a ``rate_limit_lifted`` WaitingRecord; the
        probe-resume loop owns its recovery. The stale-claim reaper must skip it
        so the claim survives until the probe revives the agent — reaping would
        release the claim to pending and probe-success would then respawn the
        agent on a task it no longer owns. Defensive on a missing registry.
        """
        owner = getattr(task, "assigned_to", None) or getattr(task, "claimed_by", None)
        if not owner:
            return False
        records = getattr(self, "_waiting_records", None)
        if not records:
            return False
        slug = self._resolve_agent_slug(str(owner))
        record = records.get(slug)
        return record is not None and record.waiting_for == "rate_limit_lifted"

    async def _assignee_container_running(self, task: Any) -> bool:
        """Docker-liveness fallback for the reaper on an instance-registry MISS.

        ``_assignee_has_active_instance`` reads the in-memory ``_instances``
        registry, which is lost on an orchestrator restart while the agent's
        container keeps running. Without a fallback the heartbeat-stale reaper
        then releases a task out from under a live agent the orchestrator has
        merely forgotten — registry amnesia, the over-reap that hit be-dev-1.
        This asks Docker directly, but ONLY on a true registry miss: a known
        instance (ACTIVE or stopped) is authoritative and not second-guessed,
        and an uninitialised registry (``None`` — e.g. a unit-test harness) is
        left to the existing behaviour. Any error (no docker binary, inspect
        fails) yields False, so non-Docker test/dev contexts are unaffected.
        """
        instances = getattr(self, "_instances", None)
        if instances is None:
            return False
        owner = getattr(task, "assigned_to", None) or getattr(task, "claimed_by", None)
        if not owner:
            return False
        slug = self._resolve_agent_slug(str(owner))
        if slug in instances:
            return False
        try:
            is_running, _ = await self._inspect_container_state(f"roboco-agent-{slug}")
        except Exception:
            return False
        return is_running

    def _wedged_grok_slug(
        self, task: Any, last_heartbeat: "datetime | None"
    ) -> str | None:
        """Slug of an ACTIVE GROK container holding ``task`` and idle past the kill TTL.

        ``_assignee_has_active_instance`` shields a live container from the
        reaper — correct for a Claude agent quiet during a long edit/test cycle.
        A wedged GROK container is the one case that breaks: ACTIVE *and*
        silent (an idle model call fires no gateway verb), so its heartbeat never
        advances and the skip would protect it forever. Returns the slug only for
        a GROK instance idle past the grok-kill TTL — a recent heartbeat, no
        owner, a non-GROK provider, or a non-ACTIVE instance all yield ``None``.
        """
        from roboco.models.base import ModelProvider

        ttl = timedelta(seconds=getattr(self, "_grok_idle_kill_ttl", 900))
        if last_heartbeat is not None and self._active_age(last_heartbeat) < ttl:
            return None
        owner = getattr(task, "assigned_to", None) or getattr(task, "claimed_by", None)
        if not owner:
            return None
        slug = self._resolve_agent_slug(str(owner))
        instance = (getattr(self, "_instances", None) or {}).get(slug)
        config = getattr(instance, "config", None)
        is_active_grok = (
            instance is not None
            and instance.state == AgentState.ACTIVE
            and config is not None
            and config.provider_type == ModelProvider.GROK.value
        )
        return slug if is_active_grok else None

    async def _maybe_kill_wedged_grok(
        self, task: Any, last_heartbeat: "datetime | None"
    ) -> bool:
        """Kill + evict a wedged GROK container so this tick's reaper frees its task.

        On a kill the container is removed (its logs dumped to disk first) and
        dropped from ``_instances``. Returns True only when a container was
        actually killed; see :meth:`_wedged_grok_slug` for the eligibility rule.
        """
        slug = self._wedged_grok_slug(task, last_heartbeat)
        if slug is None:
            return False
        try:
            await self._remove_container(
                f"roboco-agent-{slug}", stop_reason="reaper_wedged_grok"
            )
        except Exception as exc:
            logger.error(
                "wedged-grok kill failed; will retry next tick",
                agent_id=slug,
                error=str(exc),
            )
            return False
        self._instances.pop(slug, None)
        logger.warning(
            "wedged grok container killed and evicted",
            agent_id=slug,
            task_id=str(getattr(task, "id", "")),
        )
        return True

    def _stuck_claude_slug(
        self, task: Any, last_heartbeat: "datetime | None"
    ) -> str | None:
        """Slug of an ACTIVE non-GROK container holding ``task``, stuck past the TTL.

        The reaper's live-container skip shields a quiet agent during a long
        edit/test cycle — correct for a working agent (it fires gateway verbs
        every few minutes, advancing its heartbeat). A non-GROK agent stuck in a
        non-verb loop is ACTIVE yet silent, so the skip would protect its claim
        forever (#73). Returns the slug only for a non-GROK ACTIVE instance whose
        heartbeat has been stale longer than ``claude_stuck_kill_seconds`` — a
        recent heartbeat, no owner, a GROK provider (handled by the wedged-grok
        path), or a non-ACTIVE instance all yield ``None``. The bucket is
        provider-agnostic by construction (it excludes GROK specifically, not
        an allowlist of what it includes) — GEMINI (a one-shot CLI runtime with
        no SDK server either) falls into this same generic "non-GROK" bucket
        for free, with no dedicated wedge-kill path of its own.
        """
        from roboco.models.base import ModelProvider

        ttl = timedelta(seconds=getattr(self, "_claude_stuck_kill_ttl", 3600))
        if last_heartbeat is not None and self._active_age(last_heartbeat) < ttl:
            return None
        owner = getattr(task, "assigned_to", None) or getattr(task, "claimed_by", None)
        if not owner:
            return None
        slug = self._resolve_agent_slug(str(owner))
        instance = (getattr(self, "_instances", None) or {}).get(slug)
        config = getattr(instance, "config", None)
        is_active_non_grok = (
            instance is not None
            and instance.state == AgentState.ACTIVE
            and config is not None
            and config.provider_type != ModelProvider.GROK.value
        )
        return slug if is_active_non_grok else None

    async def _maybe_kill_stuck_claude(
        self, task: Any, last_heartbeat: "datetime | None"
    ) -> bool:
        """Kill + evict a stuck non-GROK container so the reaper frees its task.

        On a kill the container is removed and dropped from ``_instances``.
        Returns True only when a container was actually killed; see
        :meth:`_stuck_claude_slug` for the eligibility rule (#73).
        """
        slug = self._stuck_claude_slug(task, last_heartbeat)
        if slug is None:
            return False
        try:
            await self._remove_container(
                f"roboco-agent-{slug}", stop_reason="reaper_stuck_claude"
            )
        except Exception as exc:
            logger.error(
                "stuck-claude kill failed; will retry next tick",
                agent_id=slug,
                error=str(exc),
            )
            return False
        self._instances.pop(slug, None)
        logger.warning(
            "stuck non-grok container killed and evicted",
            agent_id=slug,
            task_id=str(getattr(task, "id", "")),
        )
        return True

    async def _maybe_recover_broken_gateway(self, task: Any) -> bool:
        """Kill + evict a live agent whose gateway is broken past the grace window.

        The reaper's live-skip protects a running container from a stale-heartbeat
        reap — right for a healthy agent quiet during a long edit/test cycle, but
        it would shield a broken-but-alive agent (a corrupted gateway firing no
        verb) forever. This probes the gateway out-of-band and, once it has been
        broken longer than ``gateway_health_grace_seconds`` (so a transient probe
        miss is tolerated), kills + evicts the container so the reaper falls
        through to release + respawn. Returns True only on a kill; a healthy
        gateway, an inconclusive probe, or a still-within-grace breakage returns
        False (the live container is spared). Gated by ``gateway_health_enabled``.
        """
        if not settings.gateway_health_enabled:
            return False
        owner = getattr(task, "assigned_to", None) or getattr(task, "claimed_by", None)
        if not owner:
            return False
        slug = self._resolve_agent_slug(str(owner))
        if not await self._gateway_broken_past_grace(slug):
            return False
        try:
            await self._remove_container(
                f"roboco-agent-{slug}", stop_reason="gateway_health_recovery"
            )
        except Exception as exc:
            logger.error(
                "broken-gateway kill failed; will retry next tick",
                agent_id=slug,
                error=str(exc),
            )
            return False
        self._instances.pop(slug, None)
        self._gateway_broken_since.pop(slug, None)
        logger.warning(
            "broken-gateway agent killed and evicted",
            agent_id=slug,
            task_id=str(getattr(task, "id", "")),
        )
        return True

    async def _gateway_broken_past_grace(self, slug: str) -> bool:
        """True when ``slug``'s gateway has probed broken longer than the grace.

        Probe-inconclusive (None) or healthy clears the grace mark and returns
        False; the first broken sighting records the mark and returns False (one
        grace tick); a breakage older than ``gateway_health_grace_seconds`` (or a
        test-injected ``_gateway_health_grace``) returns True.
        """
        healthy = await self._probe_gateway_health(slug)
        if healthy is None or healthy:
            self._gateway_broken_since.pop(slug, None)
            return False
        now = datetime.now(UTC)
        first_seen = self._gateway_broken_since.get(slug)
        if first_seen is None:
            self._gateway_broken_since[slug] = now
            return False
        grace = getattr(self, "_gateway_health_grace", None)
        if grace is None:
            grace = settings.gateway_health_grace_seconds
        return (now - first_seen).total_seconds() >= grace

    async def _should_skip_live_reap(self, t: Any, ts: Any) -> bool:
        """True when a live container should be spared from reaping.

        A live container normally protects its task; on a registry MISS (e.g. the
        orchestrator restarted and forgot a still-running container) fall back to
        asking Docker. A live container is spared UNLESS it is wedged (grok) or
        its gateway is broken-but-alive past the grace window — both checks kill +
        evict it (returning False here) so the caller falls through to release +
        respawn. Short-circuits like the original ``and``: when not live, neither
        kill nor recovery check is awaited.
        """
        live = self._assignee_has_active_instance(
            t
        ) or await self._assignee_container_running(t)
        return (
            live
            and not await self._maybe_kill_wedged_grok(t, ts)
            and not await self._maybe_kill_stuck_claude(t, ts)
            and not await self._maybe_recover_broken_gateway(t)
        )

    async def _reap_with_service(
        self, svc: "TaskService", *, dispatch_paused: bool = False
    ) -> None:
        """Inner reap loop, parameterized by the TaskService to use.

        Each candidate is delegated to ``_reap_one_stale_claim``, which
        decides skip-vs-reap and, when a reap is due, hands off to
        ``_unclaim_stale_claim`` -- wrapped there in try/except so a single
        bad row doesn't abort the dispatch tick. See
        ``_reap_one_stale_claim`` for the pause-ordering guarantee: the
        liveness/kill checks always run; only the final DB unclaim is gated
        on ``dispatch_paused``. ``candidates`` is threaded through so the
        parked-claim check can look at an agent's OTHER claimed/in_progress
        rows without a second query.
        """
        candidates = await svc.list_in_progress_or_claimed()
        for t in candidates:
            await self._reap_one_stale_claim(svc, t, dispatch_paused, candidates)

    def _heartbeat_is_stale(self, ts: Any, ttl_seconds: int) -> bool:
        """True when a claim's heartbeat is missing, or its active-time age
        (fleet downtime discounted) has reached ``ttl_seconds``."""
        if ts is None:
            return True
        return self._active_age(ts) >= timedelta(seconds=ttl_seconds)

    def _is_fresher_activity(self, agent_slug: str, this_task: Any, other: Any) -> bool:
        """True if `other` is a different row for the same agent that proves
        it is alive and working elsewhere: in_progress, or a heartbeat
        fresher than `this_task`'s own."""
        from roboco.models.base import TaskStatus

        if other is this_task or getattr(other, "id", None) == getattr(
            this_task, "id", None
        ):
            return False
        owner = getattr(other, "assigned_to", None) or getattr(
            other, "claimed_by", None
        )
        if not owner or self._resolve_agent_slug(str(owner)) != agent_slug:
            return False
        status = getattr(other, "status", None)
        if getattr(status, "value", status) == TaskStatus.IN_PROGRESS.value:
            return True
        other_ts = getattr(other, "last_heartbeat_at", None)
        this_ts = getattr(this_task, "last_heartbeat_at", None)
        return other_ts is not None and (this_ts is None or other_ts > this_ts)

    def _agent_busy_elsewhere(
        self, agent_slug: str, this_task: Any, candidates: list[Any]
    ) -> bool:
        """True if `agent_slug` holds another claimed/in_progress row - proof
        it is alive and working elsewhere, so `this_task`'s claim is parked
        rather than genuinely in flight."""
        return any(
            self._is_fresher_activity(agent_slug, this_task, other)
            for other in candidates
        )

    async def _maybe_release_parked_claim(
        self, svc: "TaskService", t: Any, candidates: list[Any]
    ) -> bool:
        """Release a `claimed` row whose heartbeat never advanced past its own
        claim, when the claiming agent is provably alive and busy on another
        task - the 2026-09-05 incident where be-dev-1 piled up four such
        claims (up to an hour parked) while its container worked a different
        task and be-dev-2 idled. The live-container skip in
        ``_should_skip_live_reap`` would otherwise spare this row forever,
        since it only checks whether the agent has ANY live container, not
        whether that container is working THIS task.

        Never fires when the row's own heartbeat has advanced past its claim
        (genuinely working, not parked), when the claim is still being
        finalized (``_is_claim_in_flight``), or when this is the agent's only
        claim (nothing fresher to prove it's alive elsewhere - left to the
        existing dead-run logic below). Returns True once it releases.
        """
        from roboco.models.base import TaskStatus

        status = getattr(t, "status", None)
        claimed_at = getattr(t, "claimed_at", None)
        ts = getattr(t, "last_heartbeat_at", None)
        owner = getattr(t, "assigned_to", None) or getattr(t, "claimed_by", None)
        # "Never advanced past claim" implies claimed_at is not None, so the
        # later _heartbeat_is_stale(claimed_at, ...) call is only reached
        # once that is already guaranteed - short-circuit evaluation order
        # matters here, not just the boolean result.
        never_advanced = claimed_at is not None and (ts is None or ts <= claimed_at)
        eligible = (
            getattr(status, "value", status) == TaskStatus.CLAIMED.value
            and never_advanced
            and owner is not None
            and self._heartbeat_is_stale(claimed_at, self._claim_heartbeat_ttl)
        )
        if not eligible:
            return False
        agent_slug = self._resolve_agent_slug(str(owner))
        if self._is_claim_in_flight(agent_slug) or not self._agent_busy_elsewhere(
            agent_slug, t, candidates
        ):
            return False
        parked_seconds = round(self._active_age(claimed_at).total_seconds())
        logger.warning(
            "parked claim released; agent busy elsewhere",
            task_id=str(getattr(t, "id", "")),
            agent=agent_slug,
            parked_seconds=parked_seconds,
        )
        await self._unclaim_stale_claim(svc, t, ts)
        return True

    async def _reap_one_stale_claim(
        self,
        svc: "TaskService",
        t: Any,
        dispatch_paused: bool,
        candidates: list[Any] | None = None,
    ) -> None:
        """Decide skip-vs-reap for one candidate claim.

        ``dispatch_paused`` narrows the ``dispatch``-scope maintenance pause
        to just the final DB-level unclaim: the wedged-grok / stuck-claude /
        broken-gateway kill checks nested in ``_should_skip_live_reap`` are
        liveness/heartbeat based, not dispatch based, and always run FIRST --
        a genuinely wedged container must never idle unmonitored for the
        length of a pause. Unclaiming only helps a task that a respawn can
        follow, which a paused fleet won't do this tick, so that step alone
        is deferred until the pause lifts. A claim whose assignee still has a
        live container is otherwise skipped entirely: the heartbeat is a
        stale proxy there, and reaping a working agent only churns the task
        -- UNLESS ``_maybe_release_parked_claim`` proves the claim is merely
        parked (see that method), which runs first and pause-gated the same
        way.
        """
        if not dispatch_paused and await self._maybe_release_parked_claim(
            svc, t, candidates or []
        ):
            return
        ts = t.last_heartbeat_at
        if not self._heartbeat_is_stale(ts, self._claim_heartbeat_ttl):
            return
        # A live container is spared unless it is wedged (grok) or its
        # gateway is broken-but-alive past the grace window - see
        # _should_skip_live_reap, which kills + evicts those so we fall
        # through to release + respawn. This check always runs, paused
        # or not.
        if await self._should_skip_live_reap(t, ts):
            return
        # A provider-parked agent (session-limit / overload / grok-429)
        # is OFFLINE with a dead container and a ``rate_limit_lifted``
        # WaitingRecord. The probe-resume loop owns its recovery - do
        # NOT reap the claim, or probe-success would respawn the agent
        # on a task it no longer owns.
        if self._assignee_is_provider_parked(t):
            return
        if dispatch_paused:
            # The pause-sensitive step: no respawn will follow this
            # tick, so leave the claim as-is (any wedged container
            # above was already killed regardless).
            return
        await self._unclaim_stale_claim(svc, t, ts)

    async def _unclaim_stale_claim(
        self, svc: "TaskService", t: Any, ts: datetime | None
    ) -> None:
        """Release one stale claim to pending.

        try/except so a single bad row doesn't abort the dispatch tick - the
        reaper must keep ticking even if one task's release somehow fails.
        """
        from roboco.utils.converters import require_uuid

        task_id = require_uuid(t.id)
        reaped_agent = getattr(t, "assigned_to", None) or getattr(t, "claimed_by", None)
        try:
            await svc.unclaim_for_reaper(task_id)
            logger.warning(
                "stale claim reaped",
                task_id=str(task_id),
                last_heartbeat=ts.isoformat() if ts else None,
            )
        except Exception as exc:
            logger.error(
                "stale-claim reap failed; continuing",
                task_id=str(task_id),
                error=str(exc),
            )
        else:
            await self._notify_stale_claim_reaped(
                task_id, reaped_agent, ts, getattr(t, "title", None)
            )

    async def _notify_stale_claim_reaped(
        self,
        task_id: "UUID",
        reaped_agent: Any,
        last_heartbeat: datetime | None,
        task_title: str | None = None,
    ) -> None:
        """Best-effort coordination notification for a reaped stale claim.

        Best-effort: a notification failure must not wedge the reaper tick,
        so any error is logged and swallowed. A reaped task leaves
        ``list_in_progress_or_claimed`` once released to pending, so a later
        reaper tick never re-considers the same claim and cannot double-fire.
        """
        if reaped_agent is None:
            return
        from roboco.services.notification import NotificationService

        try:
            await NotificationService().send_stale_claim_reaped_notification(
                task_id=str(task_id),
                reaped_agent=str(reaped_agent),
                last_heartbeat=last_heartbeat.isoformat() if last_heartbeat else None,
                task_title=task_title,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send stale-claim-reaped notification",
                task_id=str(task_id),
                error=str(exc),
            )

    def _respawn_status_change_resets(
        self,
        key: tuple[str, Any],
        record: dict[str, Any],
        current_status: Any,
        now: datetime,
    ) -> bool:
        """Handle a status CHANGE; True when it resets the strike counter.

        A status never seen on this (agent, task) is genuine forward progress
        and fully resets, exactly as before. A REVISITED status — the A<->B
        oscillation (blocked <-> in_progress) that changes status on every
        spawn while advancing nothing (2026-07-02: a dev looped 2h/8 spawns
        without tripping the gate) — gets a bounded reset budget mirroring
        tracing_resets, after which strikes accrue. seen_statuses is
        in-memory only (not a tracker column): after a restart it rebuilds
        from observed statuses, which can only under-gate briefly — never
        over-gate.
        """
        agent_slug, task_id = key
        seen = record.get("seen_statuses") or [record.get("last_status")]
        if current_status not in seen:
            self._pm_respawn_tracker[key] = {
                "count": 1,
                "last_status": current_status,
                "last_check": now,
                "seen_statuses": [*seen, current_status],
            }
            self._schedule_respawn_persist(
                agent_slug, str(task_id), self._pm_respawn_tracker[key]
            )
            # Genuine forward progress: clear any durable stalled marker set
            # by a prior trip on this task. Fire-and-forget, same discipline
            # as the counter write-through above — the hot dispatch path
            # never blocks on this DB write.
            self._schedule_bg(self._clear_task_stalled_marker(agent_slug, str(task_id)))
            return True
        record["last_status"] = current_status
        revisits = record.get("revisit_resets", 0)
        if revisits < self._PM_RESPAWN_MAX_REVISIT_RESETS:
            record["revisit_resets"] = revisits + 1
            record["count"] = 1
            record["last_check"] = now
            record["notified"] = False
            self._schedule_respawn_persist(agent_slug, str(task_id), record)
            return True
        logger.warning(
            "PM respawn status ping-pong budget exhausted — "
            "revisited statuses no longer reset the strike counter",
            agent_id=agent_slug,
            task_id=str(task_id),
            task_status=current_status,
            revisit_resets=revisits,
        )
        return False

    async def _pm_tracing_gap_reset(
        self,
        agent_slug: str,
        task_id: Any,
        record: dict[str, Any],
        current_status: Any,
        now: datetime,
    ) -> bool:
        """Reset the strike counter when the PM made a rule-following retry.

        A tracing_gap normally means the agent is advancing through a verb
        chain, so reset — but only up to ``_PM_RESPAWN_MAX_TRACING_RESETS``.
        A task whose every respawn trips the same gap is wedged, not
        progressing, so cap the resets and let strikes accrue once the
        budget is exhausted. Returns True when the counter was reset.
        """
        if not await self._pm_made_rule_following_retry(agent_slug, task_id, record):
            return False
        resets = record.get("tracing_resets", 0)
        if resets < self._PM_RESPAWN_MAX_TRACING_RESETS:
            record["tracing_resets"] = resets + 1
            record["count"] = 1
            record["last_check"] = now
            record["notified"] = False
            self._schedule_respawn_persist(agent_slug, str(task_id), record)
            return True
        logger.warning(
            "PM respawn tracing_gap reset budget exhausted — "
            "treating recurring gap as a stuck loop",
            agent_id=agent_slug,
            task_id=task_id,
            task_status=current_status,
            tracing_resets=resets,
        )
        return False

    def _pm_cooldown_gate(
        self,
        agent_slug: str,
        task_id: Any,
        record: dict[str, Any],
        now: datetime,
    ) -> bool | None:
        """Self-heal a previously-tripped gate after a cooldown.

        Returns True to keep gating, False to let the spawn through after a
        cooldown reset, or None when the gate hasn't tripped yet (caller
        continues to the increment path). last_check is frozen at the trip
        tick; this branch returns before the increment below updates it.
        """
        if not (
            record["count"] > self._PM_RESPAWN_MAX_UNPRODUCTIVE
            and record.get("notified")
        ):
            return None
        elapsed: bool = (now - record["last_check"]).total_seconds() > (
            self._PM_RESPAWN_TRIP_COOLDOWN_SECONDS
        )
        if elapsed:
            record["count"] = 1
            record["last_check"] = now
            record["notified"] = False
            self._schedule_respawn_persist(agent_slug, str(task_id), record)
        return not elapsed

    async def _pm_respawn_should_gate(
        self, agent_slug: str, task: dict[str, Any]
    ) -> bool:
        """Return True when the respawn should be skipped (loop detected).

        Tracks (agent_slug, task_id) -> count of consecutive spawns where
        the task's status did not advance. When the task status changes,
        the counter resets. Once the count hits the threshold, the spawn
        is skipped and a warning logged; operators must intervene.

        Tracing-gap reset
        -----------------
        With the gateway claim-time gates installed, a rule-following PM
        will hit ``PARENT_NOT_CLAIMED`` (a ``tracing_gap`` envelope) and
        the prompt will tell it to call the prerequisite verb first.
        Each retry leaves the task status unchanged but the agent IS
        making progress through the verb chain. Counting that as a
        strike kills rule-followers.

        Solution: before incrementing on a same-status spawn, check
        ``audit_log`` for a ``gateway.rejected`` row tagged
        ``reason == "tracing_gap"`` from this (agent, task) since the
        last check. If found, reset the counter — the agent followed
        the rules, not stuck.

        Audit lookup is best-effort: any failure falls through to the
        legacy strike behavior so audit problems don't break the gate.
        """
        task_id = task.get("id")
        if not task_id:
            return False
        key = (agent_slug, task_id)
        current_status = task.get("status")
        record = self._pm_respawn_tracker.get(key)
        now = datetime.now(UTC)
        if record is None:
            self._pm_respawn_tracker[key] = {
                "count": 1,
                "last_status": current_status,
                "last_check": now,
                "seen_statuses": [current_status],
            }
            self._schedule_respawn_persist(
                agent_slug, str(task_id), self._pm_respawn_tracker[key]
            )
            return False
        if record.get("last_status") != current_status and (
            self._respawn_status_change_resets(key, record, current_status, now)
        ):
            return False
        # ponytail: helpers hold the two resettable sub-loops (tracing-gap,
        # cooldown); main fn just routes. Inline again if either grows a
        # second distinct reset path.
        if await self._pm_tracing_gap_reset(
            agent_slug, task_id, record, current_status, now
        ):
            return False
        # Already tripped on a PREVIOUS tick (notified flipped): the count is
        # frozen past the threshold and last_check is frozen at the trip tick,
        # so a deploy that fixed the underlying loop (auth/prompt/schema) can
        # self-heal after a cooldown instead of wedging until manual DB
        # surgery. A still-wedged task re-trips after the threshold (bounded
        # re-burn: ~3 spawns per cooldown window); a fixed one advances and
        # the status-change path fully resets the counter.
        gate = self._pm_cooldown_gate(agent_slug, task_id, record, now)
        if gate is not None:
            return gate
        record["count"] += 1
        record["last_check"] = now
        self._schedule_respawn_persist(agent_slug, str(task_id), record)
        tripped: bool = record["count"] > self._PM_RESPAWN_MAX_UNPRODUCTIVE
        if tripped:
            logger.warning(
                "PM respawn loop detected — skipping spawn",
                agent_id=agent_slug,
                task_id=task_id,
                task_status=current_status,
                spawn_attempts=record["count"],
                threshold=self._PM_RESPAWN_MAX_UNPRODUCTIVE,
                hint=(
                    "Agent repeatedly spawned without advancing task state. "
                    "Investigate prompt/schema drift or escalate manually."
                ),
            )
            # A skipped spawn pauses the loop but can't advance the task; alert
            # an overseer once so a wedged agent isn't silently stranded, and
            # record a durable marker on the task itself (readable without
            # container logs) alongside that one-shot notification. Both are
            # one-shot per trip, gated by the same `notified` flag.
            if not record.get("notified"):
                record["notified"] = True
                self._schedule_respawn_persist(agent_slug, str(task_id), record)
                await self._mark_task_stalled(task_id)
                await self._notify_stuck_agent(agent_slug, task_id, current_status)
        return tripped

    async def _mark_task_stalled(self, task_id: str) -> None:
        """Record a durable stalled marker on the task (breaker-tripped path).

        Best-effort: a write failure must not wedge dispatch, so any error is
        logged and swallowed — the CEO notification still fires regardless.
        """
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.models.base import StalledReason
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                await TaskService(db).mark_stalled(
                    UUID(task_id), reason=StalledReason.BREAKER_TRIPPED.value
                )
        except Exception as exc:
            logger.warning(
                "Failed to record stalled marker",
                task_id=task_id,
                error=str(exc),
            )

    async def _clear_task_stalled_marker(self, agent_slug: str, task_id: str) -> None:
        """Clear the durable stalled marker on genuine forward progress.

        Fire-and-forget (scheduled via `_schedule_bg`) so the hot dispatch
        path never blocks on this DB write — mirroring
        `_schedule_respawn_persist`'s write-through discipline. Best-effort:
        a write failure is logged and swallowed.
        """
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                await TaskService(db).clear_stalled_marker(UUID(task_id))
        except Exception as exc:
            logger.warning(
                "Failed to clear stalled marker",
                agent_id=agent_slug,
                task_id=task_id,
                error=str(exc),
            )

    async def _pm_made_rule_following_retry(
        self,
        agent_slug: str,
        task_id: str,
        record: dict[str, Any],
    ) -> bool:
        """Did the agent emit a ``tracing_gap`` envelope since the last check?

        Returns ``False`` for unknown slugs (defensive — the audit query
        needs an agent UUID, and we'd rather fall through to the legacy
        strike behavior than crash). Returns ``False`` if the audit
        lookup raises — observability must never block the gate.
        """
        agent_uuid_str = AGENT_UUIDS.get(agent_slug)
        if not agent_uuid_str:
            return False
        from uuid import UUID

        try:
            agent_uuid = UUID(agent_uuid_str)
            task_uuid = UUID(task_id)
        except (ValueError, TypeError):
            return False
        since = record.get("last_check") or datetime.now(UTC)

        from roboco.services.audit import get_audit_service

        audit = get_audit_service()
        try:
            return await audit.has_recent_tracing_gap(
                agent_id=agent_uuid,
                task_id=task_uuid,
                since=since,
            )
        except Exception as exc:
            logger.debug(
                "audit.has_recent_tracing_gap failed; falling back to strike count",
                agent_slug=agent_slug,
                task_id=task_id,
                error=str(exc),
            )
            return False

    async def _respawn_dev_if_inactive(
        self, task: dict[str, Any], agent_slug: str
    ) -> None:
        """Respawn a dev agent on an existing task when it isn't running."""
        if self._is_agent_active(agent_slug):
            return
        await self.spawn_agent(
            agent_id=agent_slug,
            task_id=task["id"],
            initial_prompt=await self._build_dev_prompt(task),
            git_context=self._task_git_context(task),
            spawned_by="_respawn_dev_if_inactive",
        )
