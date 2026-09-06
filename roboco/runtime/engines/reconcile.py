"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: reconcile)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from roboco.agents_config import (
    get_agent_team,
)
from roboco.models.runtime import (
    AgentInstance,
    WaitingRecord,
)
from roboco.runtime.orchestrator import (
    _DOCKER_INSPECT_TIMEOUT_SECONDS,
    AGENT_IMAGES,
    AgentState,
    logger,
)
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from roboco.services.task import TaskService


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class ReconcileEngine(_Base):
    """Mixin holding the "reconcile" methods moved out of AgentOrchestrator."""

    async def _clear_respawn_record(self, agent_slug: str, task_id: str) -> None:
        """Delete one PM-respawn counter row (best-effort).

        Used by the startup loader to evict a row whose task is gone or
        terminal, so a stale counter never resurrects against a fixed task.
        """
        try:
            from uuid import UUID as _UUID

            from sqlalchemy import delete

            from roboco.db.base import get_session_factory
            from roboco.db.tables import RespawnTrackerTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                await db.execute(
                    delete(RespawnTrackerTable).where(
                        RespawnTrackerTable.agent_slug == agent_slug,
                        RespawnTrackerTable.task_id == _UUID(task_id),
                    )
                )
                await db.commit()
        except Exception as e:
            logger.error(
                "Failed to clear respawn record",
                agent_id=agent_slug,
                task_id=task_id,
                error=str(e),
            )

    async def restore_waiting_records(self) -> int:
        """Load persisted waiting records into memory on orchestrator start.

        Call this from `start()` so agents marked WAITING_LONG before the
        previous orchestrator exited can still be resolved.
        """
        try:
            from sqlalchemy import select

            from roboco.db.base import get_session_factory
            from roboco.db.tables import WaitingRecordTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                rows = await db.execute(select(WaitingRecordTable))
                count = 0
                for row in rows.scalars().all():
                    self._waiting_records[row.agent_id] = WaitingRecord(
                        agent_id=row.agent_id,
                        task_id=str(row.task_id) if row.task_id else None,
                        waiting_for=row.waiting_for,
                        waiting_since=row.waiting_since,
                        context=dict(row.context or {}),
                    )
                    count += 1
                if count:
                    logger.info(
                        "Restored waiting records from database",
                        count=count,
                    )
                return count
        except Exception as e:
            logger.error("Failed to restore waiting records", error=str(e))
            return 0

    @staticmethod
    def _partition_respawn_rows(
        rows: "Iterable[Any]",
        status_by_id: dict[Any, Any],
        now: datetime | None = None,
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], list[tuple[str, Any]]]:
        """Split persisted respawn rows into (restorable entries, stale keys).

        Pure: a row is **stale** when its task is missing from ``status_by_id``
        or terminal (completed/cancelled) — a stale counter must never resurrect
        against a fixed/deleted task. Restorable entries are keyed
        ``(agent_slug, str(task_id))`` to match the in-memory dict; stale keys
        carry the raw ``task_id`` for deletion.

        F034: ``last_check`` is re-stamped to ``now`` (the restore time) on
        every restorable entry. ``_pm_made_rule_following_retry`` reads
        ``since = record.get("last_check")`` to bound its tracing_gap audit
        lookup; a stale pre-restart ``last_check`` would match a pre-restart
        tracing_gap row and falsely reset the breaker on the first post-restart
        spawn. Re-stamping bounds the lookup to post-restart gaps only.
        """
        from roboco.models.base import TaskStatus

        restore_now = now or datetime.now(UTC)
        terminal = {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}
        restored: dict[tuple[str, str], dict[str, Any]] = {}
        stale: list[tuple[str, Any]] = []
        for r in rows:
            status = status_by_id.get(r.task_id)
            norm = getattr(status, "value", status)
            if status is None or norm in terminal:
                stale.append((r.agent_slug, r.task_id))
                continue
            restored[(r.agent_slug, str(r.task_id))] = {
                "count": r.count,
                # Re-stamp to the LIVE status (mirrors the last_check re-stamp
                # above): a pre-restart last_status is as stale w.r.t. post-restart
                # reality, and a status mismatch across the restart gap would
                # otherwise disarm the breaker on the first post-restart spawn and
                # re-burn the whole strike threshold against a still-wedged task.
                "last_status": norm,
                "last_check": restore_now,
                "tracing_resets": r.tracing_resets,
                "revisit_resets": r.revisit_resets,
                "notified": r.notified,
            }
        return restored, stale

    async def restore_respawn_tracker(self) -> int:
        """Load the persisted PM-respawn counter into memory on startup.

        Mirrors ``restore_waiting_records``: read every ``respawn_tracker`` row,
        keep only those whose task is still live and non-terminal, evict the
        rest, and populate ``_pm_respawn_tracker`` so a wedged-task counter trips
        at its persisted threshold instead of resetting to 1 and re-burning the
        whole budget. Best-effort — any failure starts with an empty tracker
        (exactly today's behaviour) and never blocks startup.
        """
        try:
            from sqlalchemy import select

            from roboco.db.base import get_session_factory
            from roboco.db.tables import RespawnTrackerTable, TaskTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                rows = (await db.execute(select(RespawnTrackerTable))).scalars().all()
                if not rows:
                    return 0
                ids = [r.task_id for r in rows]
                live = (
                    await db.execute(
                        select(TaskTable.id, TaskTable.status).where(
                            TaskTable.id.in_(ids)
                        )
                    )
                ).all()
                status_by_id = {row.id: row.status for row in live}
                restored, stale = self._partition_respawn_rows(rows, status_by_id)
            self._pm_respawn_tracker.update(restored)
            for agent_slug, task_id in stale:
                await self._clear_respawn_record(agent_slug, str(task_id))
            if restored:
                logger.info(
                    "Restored PM-respawn records from database",
                    count=len(restored),
                    evicted=len(stale),
                )
            return len(restored)
        except Exception as e:
            logger.error("Failed to restore respawn records", error=str(e))
            return 0

    @staticmethod
    async def _resolve_container_id(container_name: str) -> str | None:
        """Return the Docker container id for ``container_name`` via `docker inspect`.

        Used at startup re-adoption (F033) so a re-adopted ACTIVE instance
        carries the real container id — ``_check_health`` skips
        ``container_id is None`` instances, so without it a later container exit
        is invisible to the health loop and the task strands. Returns ``None``
        when the id can't be resolved (caller treats that as best-effort
        degraded re-adoption, still covered by the reaper's liveness fallback).
        """
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "-f",
            "{{.Id}}",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=_DOCKER_INSPECT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            proc.kill()
            raise
        cid = stdout.decode().strip()
        return cid or None

    async def _mark_running_and_beat(self) -> None:
        """Boot-time seam, called from `start()` before the loops launch.

        Marks this process as running so `_refresh_uptime` never reads its
        own broken audit writes as downtime, then fires a boot heartbeat
        that closes any pre-boot outage window at the boot instant. `_is_paused`
        opens its own session lazily, so this is safe to call before the
        rest of `start()`'s DB-dependent setup runs.
        """
        from roboco.services.uptime import mark_process_running

        mark_process_running()
        await self._emit_dispatcher_heartbeat()

    async def _reconcile_orphan_claims_on_startup(self) -> None:
        """Roll back tasks left in CLAIMED/IN_PROGRESS without a branch.

        A task in CLAIMED/IN_PROGRESS with ``branch_name IS NULL`` is an
        orphan: ``_apply_claim_fields`` committed the claim (status +
        claimant fields) before ``_provision_claim``'s branch creation
        committed, and that branch creation then failed (or claim rollback
        was not yet atomic). The next claim then fails non-idempotent on
        ``git checkout -b`` because the on-disk branch may exist while the
        DB state is stale.

        Opens its own session via the factory; the logic itself lives in
        ``_reconcile_with_service`` so tests can drive it against an
        injected session without the factory dance. Best-effort: if
        reconciliation fails, log and continue — startup must not be
        blocked by a single bad row.
        """
        from roboco.db.base import get_session_factory
        from roboco.services.task import TaskService

        factory = get_session_factory()
        try:
            async with factory() as db:
                svc = TaskService(db)
                await self._reconcile_with_service(svc)
                await db.commit()
        except Exception as exc:
            logger.error("startup reconcile failed; continuing", error=str(exc))

    async def _reconcile_orphan_spawn_sessions(self) -> int:
        """Close agent_spawn_sessions rows left open by a prior crash.

        usage.get_summary / get_time_series filter ``ended_at IS NOT NULL``,
        so an open row whose container is gone is permanently excluded from
        usage/cost rollups. Close each open session whose agent slug is NOT
        in ``self._instances`` (the re-adopted running set) with
        ``ended_at=now`` and ``exit_reason='abandoned'``. Running agents
        stay open for their live finalize. Best-effort; never blocks startup.
        """
        try:
            from sqlalchemy import select, update

            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentSpawnSessionTable
        except ImportError:
            return 0
        try:
            running = set(self._instances.keys())
            session_factory = get_session_factory()
            async with session_factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(AgentSpawnSessionTable).where(
                                AgentSpawnSessionTable.ended_at.is_(None)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                orphans = [r for r in rows if r.agent_slug not in running]
                if not orphans:
                    return 0
                now = datetime.now(UTC)
                await db.execute(
                    update(AgentSpawnSessionTable)
                    .where(AgentSpawnSessionTable.id.in_([r.id for r in orphans]))
                    .values(ended_at=now, exit_reason="abandoned")
                )
                await db.commit()
            return len(orphans)
        except Exception as exc:
            logger.warning(
                "Failed to reconcile orphan spawn sessions",
                error=str(exc),
            )
            return 0

    async def _reconcile_with_service(self, svc: "TaskService") -> None:
        """Inner reconcile loop, parameterised by the TaskService to use.

        Same shape as ``_reap_with_service`` — extracted so tests can
        bypass ``get_session_factory`` and drive the logic directly.
        """
        from roboco.utils.converters import require_uuid

        candidates = await svc.list_in_progress_or_claimed()
        orphans = [t for t in candidates if not t.branch_name]
        if not orphans:
            logger.info("startup reconcile: no orphan claims")
            return
        for t in orphans:
            task_id = require_uuid(t.id)
            try:
                await svc.unclaim_for_reaper(task_id)
                logger.warning(
                    "startup reconcile: orphan claim rolled back",
                    task_id=str(task_id),
                    had_status=str(t.status),
                )
            except Exception as exc:
                logger.error(
                    "startup reconcile: rollback failed",
                    task_id=str(t.id),
                    error=str(exc),
                )

    async def _agent_holds_live_claim(self, slug: str) -> bool | None:
        """Whether ``slug`` currently owns a non-terminal task.

        Used by ``_readopt_running_agents`` to tell a still-useful running
        container (the agent is mid-task) from a zombie left over after a prior
        orchestrator released the claim: registering a zombie ACTIVE would block
        the spawn gate from re-dispatching that slug until the stale container is
        eventually noticed (#72). Returns True when the slug owns a non-terminal
        task, False when it owns nothing (zombie), and None on a lookup error
        (indeterminate — the caller falls back to today's register behaviour so a
        startup DB hiccup can't regress the cold-start double-spawn protection).
        """
        from sqlalchemy import select

        from roboco.db.base import get_db_context
        from roboco.db.tables import TaskTable
        from roboco.models.base import TaskStatus

        agent_uuid = AGENT_UUIDS.get(slug)
        if agent_uuid is None:
            return False  # unknown slug owns nothing by definition
        try:
            async with get_db_context() as db:
                result = await db.execute(
                    select(TaskTable.id)
                    .where(
                        TaskTable.assigned_to == agent_uuid,
                        TaskTable.status.notin_(
                            (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
                        ),
                    )
                    .limit(1)
                )
                return result.first() is not None
        except Exception:
            logger.warning(
                "readopt live-claim lookup failed; falling back to register",
                slug=slug,
            )
            return None

    async def _read_container_auth_env(
        self, container_name: str
    ) -> tuple[str, str, str] | None:
        """Read (token, agent_id, role) from a running agent container's env.

        Returns None on any probe failure or missing var so the caller can
        skip the container (best-effort — the reaper still covers it).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                container_name,
                "printenv",
                "ROBOCO_AGENT_TOKEN",
                "ROBOCO_AGENT_ID",
                "ROBOCO_AGENT_ROLE",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        token: str | None = None
        agent_id_env: str | None = None
        role_env: str | None = None
        for line in stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("ROBOCO_AGENT_TOKEN="):
                token = line[len("ROBOCO_AGENT_TOKEN=") :]
            elif line.startswith("ROBOCO_AGENT_ID="):
                agent_id_env = line[len("ROBOCO_AGENT_ID=") :]
            elif line.startswith("ROBOCO_AGENT_ROLE="):
                role_env = line[len("ROBOCO_AGENT_ROLE=") :]
        if not token or not agent_id_env or not role_env:
            return None
        return token, agent_id_env, role_env

    async def _heal_stale_agent_tokens(self) -> int:
        """Kill running agent containers whose ROBOCO_AGENT_TOKEN no longer
        verifies against the current ``ROBOCO_AGENT_AUTH_SECRET``.

        A token is baked into the container env at spawn (``_append_agent_auth_env``
        signs with the orchestrator's secret at that moment). If the secret later
        drifts — a `.env` change, a compose recreate that reloads the
        orchestrator's env without recreating the agent containers, an image
        redeploy — the surviving agent keeps sending its old token and the
        middleware 401s every verb with "signature mismatch". The container stays
        alive (heartbeating), so the reaper never reclaims it and no fresh agent
        spawns: the fleet stalls. This self-heals it at startup by killing each
        stale-token container so the normal dispatch re-spawns it with a freshly
        signed token.

        Inert when the secret is unset (dev): ``verify_agent_token`` fails for
        every token without a secret, so the heal would kill the whole fleet —
        gated to prod-only. Best-effort: a probe failure leaves the container
        alone (the reaper's own liveness path still covers it).
        """
        from roboco.agents_config import _auth_secret, verify_agent_token

        if not _auth_secret():
            return 0
        killed = 0
        for slug in AGENT_IMAGES:
            try:
                is_running, _ = await self._inspect_container_state(
                    f"roboco-agent-{slug}"
                )
            except Exception:
                continue
            if not is_running:
                continue
            env = await self._read_container_auth_env(f"roboco-agent-{slug}")
            if env is None:
                continue
            token, agent_id_env, role_env = env
            team = get_agent_team(agent_id_env) or ""
            # Verify against the UUID the MCP servers actually send as
            # X-Agent-ID, not the container-env ROBOCO_AGENT_ID (a slug on
            # pre-fix containers). A stale container spawned before the
            # slug→UUID fix carries a slug-signed token + a slug
            # ROBOCO_AGENT_ID, so verifying against the slug would PASS and
            # leave the stale container running (its MCP server still 401s
            # sending the UUID). Resolving to the UUID makes the heal reject
            # the slug-signed token and kill the container so it respawns
            # with a UUID-signed one. AGENT_UUIDS is slug→UUID keyed, so a
            # UUID input falls back to itself.
            agent_uuid = AGENT_UUIDS.get(agent_id_env, agent_id_env)
            if verify_agent_token(token, agent_uuid, role_env, team):
                continue
            logger.warning(
                "Killing agent with a stale auth token at startup; the reaper "
                "will re-spawn it with a freshly signed token",
                slug=slug,
            )
            await self._remove_container(
                f"roboco-agent-{slug}",
                teardown_sandbox=False,
                stop_reason="stale_token_heal",
            )
            killed += 1
        if killed:
            logger.info("Healed stale agent tokens at startup", count=killed)
        return killed

    async def _readopt_running_agents(self) -> int:
        """Re-adopt still-running agent containers into ``_instances`` at startup.

        An orchestrator restart loses the in-memory ``_instances`` registry while
        the agent containers keep running. The reaper has a Docker-liveness
        fallback for that (``_assignee_container_running``), but the spawn gate's
        ``_is_agent_active`` does NOT — so after a restart it sees a live agent as
        inactive and can double-spawn it onto work its forgotten-but-running
        container is already doing. Probe each known agent slug's container (the
        same ``docker inspect`` the reaper uses) and register a minimal ACTIVE
        instance for any that is running, not already tracked, AND still holds a
        live (non-terminal) claim — a running container whose claim a prior
        orchestrator already released is a zombie and is skipped so it can't
        block the spawn gate from re-dispatching that slug (#72). Inert when
        nothing is running (degrades to today's cold start) and best-effort: a
        probe or claim-lookup error leaves that slot untracked / falls back to
        registering (the reaper's own fallback still covers it). Returns the
        number re-adopted.
        """
        readopted = 0
        for slug in AGENT_IMAGES:
            if slug in self._instances:
                continue
            try:
                is_running, _ = await self._inspect_container_state(
                    f"roboco-agent-{slug}"
                )
            except Exception:
                continue
            if not is_running:
                continue
            # Capture the real container id: ``_check_health`` skips instances
            # with ``container_id is None``, so a re-adopted instance without
            # the id would be invisible to the health loop and strand the task
            # under a phantom ACTIVE instance. Best-effort — a probe failure
            # degrades to None (reaper's Docker-liveness fallback covers it).
            container_id: str | None = None
            try:
                container_id = await self._resolve_container_id(f"roboco-agent-{slug}")
            except Exception:
                container_id = None
            # #72: a running container whose slug no longer holds a live claim is
            # a zombie from a prior orchestrator that released the claim — skip it
            # so it can't block re-dispatch of the slug. ``None`` (lookup error)
            # falls back to registering: a startup DB hiccup must not regress the
            # cold-start double-spawn protection this readopt exists to provide.
            holds_claim = await self._agent_holds_live_claim(slug)
            if holds_claim is False:
                logger.info(
                    "readopt skipped a running zombie container (no live claim)",
                    slug=slug,
                )
                continue
            self._instances[slug] = AgentInstance(
                agent_id=slug,
                state=AgentState.ACTIVE,
                container_id=container_id,
            )
            readopted += 1
        if readopted:
            logger.info(
                "re-adopted running agent containers at startup", count=readopted
            )
        return readopted

    def _check_loop_liveness(self) -> None:
        now = time.monotonic()
        heartbeats = getattr(self, "_loop_heartbeats", {})
        for name, (last_success, interval) in heartbeats.items():
            stall = now - last_success
            if stall > 2 * interval:
                logger.warning(
                    "engine loop stalled past 2x interval",
                    loop=name,
                    stall_seconds=int(stall),
                    interval=interval,
                )

    async def _health_loop(self) -> None:
        """Background health check loop."""
        while self._running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                await self._check_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Health check error", error=str(e))

    async def _check_health(self) -> None:
        """Check health of all running agents."""
        for agent_id, instance in list(self._instances.items()):
            if instance.state not in (AgentState.ACTIVE, AgentState.WAITING_SHORT):
                continue
            if instance.container_id is None:
                continue
            # A per-agent docker-inspect timeout (or any docker error) must skip
            # THIS agent, not abort the whole sweep — otherwise one hung daemon
            # call means no agent gets health-checked this tick. The reaper's
            # own liveness fallback still covers a genuinely-stopped container
            # next tick; skipping is the safe fail-direction.
            try:
                is_running, exit_code = await self._inspect_container_state(
                    f"roboco-agent-{agent_id}"
                )
            except Exception as exc:
                logger.debug(
                    "container inspect failed; skipping agent this tick",
                    agent_id=agent_id,
                    error=str(exc),
                )
                continue
            if not is_running:
                await self._handle_stopped_container(agent_id, instance, exit_code)
        self._check_loop_liveness()

    async def _board_program_loop(self) -> None:
        """Board Program engine: on an interval, originate a cycle for every
        enabled, due program (roadmap, x_feature, and every later registry
        entry) — replaces the old bespoke ``_roadmap_engine_loop`` /
        ``_x_feature_spotlight_loop``.

        Unlike those, this loop carries no single static disablement gate:
        each program's own enablement (legacy flag or settings-store
        override) is checked per-tick inside ``BoardProgramEngine``, so the
        loop always ticks and simply originates nothing when every program
        is off. The tick interval is a fixed floor, not a live setting — it
        only bounds how promptly a newly-due program is noticed; the actual
        due-check inside the engine still reads the live per-program
        interval override.
        """
        interval = self._board_program_interval_seconds()
        self._record_loop_heartbeat("board_program", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_board_program_cycle()
                self._record_loop_heartbeat("board_program", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("board-program cycle failed")

    def _board_program_interval_seconds(self) -> int:
        """Loop wake-up floor: the shortest registered program cadence,
        floored at 300s (so an idle deployment doesn't busy-poll) and capped
        at 3600s — due-ness staleness is bounded at 1h; ticks are cheap
        settings reads, so a slower program cadence never needs a slower
        tick."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        shortest = min(
            (p.default_interval_seconds for p in PROGRAMS.values()), default=300
        )
        return min(3600, max(300, shortest))

    async def _run_board_program_cycle(self) -> None:
        """One board-program pass: run the engine, commit. Testable w/o the sleep."""
        from roboco.db import get_db_context
        from roboco.services.board_programs import get_board_program_engine

        async with get_db_context(pool="background") as db:
            await get_board_program_engine(db).run_due_programs()
            await db.commit()
