"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: sweeps)."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
from fastapi import status as http_status

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers as _markers
from roboco.models import Team
from roboco.models.base import ModelProvider
from roboco.runtime.orchestrator import (
    _BRANCH_CUT_BACKOFF_BASE_SECONDS,
    _MAX_BRANCH_CUT_ATTEMPTS,
    INTAKE_AGENT_ID,
    SDK_PORT,
    SECRETARY_AGENT_ID,
    SUPERSEDE_PR_CLOSE_COMMENT,
    SUPERSEDE_PR_COMMENT,
    AgentState,
    _supersede_author_prefix,
    _system_api_headers,
    logger,
)
from roboco.services.task import (
    PR_REVIEW_SOURCES,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.runtime import (
        AgentInstance,
    )
    from roboco.models.sandbox import SandboxInfo
    from roboco.services.task import TaskService


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class SweepsEngine(_Base):
    """Mixin holding the "sweeps" methods moved out of AgentOrchestrator."""

    # Re-declared (not just inherited from _Base above): mypy's Protocol
    # attribute inference cannot determine the type of an inherited member
    # that a method both reads AND assigns within the same method body
    # (read-then-write in one scope; see _sweep_prune_dangling_images and
    # _sweep_prune_transcripts below) without a bare re-declaration
    # directly on the concrete class.
    if TYPE_CHECKING:
        _last_image_prune: datetime | None
        _last_transcript_prune: datetime | None

    async def reap_intake_session(self, session_id: str) -> None:
        """End a live chat: close the relay stream and stop the container."""
        from roboco.services.prompter_live import get_live_registry

        get_live_registry().close(session_id)
        await self.stop_agent(
            INTAKE_AGENT_ID, graceful=True, stop_reason="intake_session_reaped"
        )
        logger.info("Intake session reaped", session_id=session_id)

    async def reap_secretary_session(self, session_id: str) -> None:
        """End a live Secretary chat: close the relay and stop the container."""
        from roboco.services.prompter_live import get_live_registry

        get_live_registry().close(session_id)
        await self.stop_agent(
            SECRETARY_AGENT_ID, graceful=True, stop_reason="secretary_session_reaped"
        )
        logger.info("Secretary session reaped", session_id=session_id)

    async def _reap_idle_interactive_sessions(self) -> None:
        """Retire live intake/secretary chats idle past the configured threshold.

        An abandoned chat (the human closed the tab without confirming or
        stopping) otherwise leaks its container until the orchestrator restarts.
        Idle is measured by time-since-last-turn (push/deliver), NOT connection
        state, so an active or page-reloaded chat that keeps exchanging turns is
        never reaped; board-review-parked sessions are exempt. Provider-agnostic
        (Claude + Grok interactive). Disabled when the threshold is 0.
        """
        from roboco.services.prompter_live import get_live_registry

        threshold = float(settings.interactive_idle_reap_seconds)
        for session_id, agent_id in get_live_registry().idle_session_ids(threshold):
            try:
                if agent_id == INTAKE_AGENT_ID:
                    await self.reap_intake_session(session_id)
                elif agent_id == SECRETARY_AGENT_ID:
                    await self.reap_secretary_session(session_id)
                else:
                    continue
                logger.info(
                    "Reaped idle interactive session",
                    session_id=session_id,
                    agent_id=agent_id,
                    idle_threshold_s=threshold,
                )
            except Exception as exc:
                logger.warning(
                    "Idle interactive reap failed",
                    session_id=session_id,
                    error=str(exc),
                )

    async def stop_agent(
        self,
        agent_id: str,
        graceful: bool = True,
        exit_reason: str = "stopped",
        release_claim: bool = False,
        stop_reason: str = "stop_agent",
    ) -> None:
        """Stop an agent container.

        Finalization (the HTTP call to the agent SDK's /usage/status endpoint)
        is performed BEFORE acquiring self._lock so that the network I/O does
        not block other operations that need the lock.

        When ``release_claim`` is True the caller declares the stopped agent
        will not continue its task (budget kill, orchestrator shutdown) and the
        agent's claimed/in_progress task is handed back to the pool immediately
        instead of waiting up to ``stale_claim_reap_seconds`` for the
        stale-claim reaper to notice the dead heartbeat — closing the
        SIGTERM-mid-verb gap where a task sat CLAIMED/IN_PROGRESS with no
        running agent. Default False: the provider-park / waiting path
        (``mark_waiting_long``) and interactive stops manage their own claim
        lifecycle, so they opt out and the claim survives for the probe-resume
        loop. A provider-parked agent (``rate_limit_lifted`` WaitingRecord) is
        always skipped even when a caller opts in — its claim must survive so
        probe-success revives the same agent on the same task.

        ``stop_reason`` breadcrumbs the container as an expected stop (see
        ``_record_expected_stop``) BEFORE the docker stop/kill is issued —
        ``_check_health`` polls without holding ``self._lock``, so it can
        observe the container already gone while this call is still mid-flight;
        recording early (not just at ``_remove_container``) closes that race.
        """
        # Finalize the spawn-session row before the container is removed so we
        # can still query the SDK's /usage/status endpoint.  This must happen
        # outside self._lock — the HTTP round-trip would otherwise hold the
        # lock for the full network timeout.
        instance = self._instances.get(agent_id)
        if instance is None:
            return
        if instance.container_id:
            await self._finalize_spawn_session(agent_id, exit_reason=exit_reason)

        # Capture the task the agent was working on before the instance state
        # is mutated, so a release_claim stop can hand it back to the pool once
        # the container is gone. Only relevant when the caller opted in.
        stopped_task_id = instance.current_task_id if release_claim else None

        async with self._lock:
            if agent_id not in self._instances:
                return

            instance = self._instances[agent_id]

            if instance.container_id:
                self._record_expected_stop(agent_id, stop_reason)
                instance.state = AgentState.STOPPING
                container_name = f"roboco-agent-{agent_id}"

                if graceful:
                    # Graceful stop with timeout
                    proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "stop",
                        "-t",
                        "10",
                        container_name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                else:
                    # Force kill
                    proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "kill",
                        container_name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()

                # Remove container
                await self._remove_container(container_name)

            instance.state = AgentState.OFFLINE
            instance.container_id = None

            logger.info("Agent stopped", agent_id=agent_id)

        # Hand the stopped agent's claimed task back to the pool now, instead
        # of leaving it CLAIMED/IN_PROGRESS with no running agent for the
        # reaper's full heartbeat TTL. Done outside self._lock (DB I/O) and
        # best-effort: a failure logs a warning and the stale-claim reaper
        # remains the backstop. Skipped for a provider-parked agent — the
        # probe-resume loop owns its recovery and the claim must survive.
        if stopped_task_id and not self._is_rate_limit_parked(agent_id):
            await self._release_stopped_agent_claim(agent_id, stopped_task_id)

    def _is_rate_limit_parked(self, agent_id: str) -> bool:
        """True if the agent is provider-parked on a rate limit.

        Mirrors the reaper's ``_assignee_is_provider_parked`` guard but keyed
        by slug directly (no task row needed): a ``rate_limit_lifted``
        WaitingRecord means the probe-resume loop owns this agent's recovery
        and its claim must survive a stop. Defensive on a missing registry.
        """
        records = getattr(self, "_waiting_records", None)
        if not records:
            return False
        record = records.get(agent_id)
        return record is not None and record.waiting_for == "rate_limit_lifted"

    async def _release_stopped_agent_claim(
        self, agent_id: str, task_id_str: str
    ) -> None:
        """Force a stopped agent's claimed/in_progress task back to pending.

        Reuses the hardened, idempotent, status-checked
        ``TaskService.unclaim_for_reaper`` (the same path the stale-claim
        reaper uses) so a task that already moved on (e.g. submitted to QA
        before the stop) is a clean no-op. Opens its own short-lived session
        outside ``self._lock``. Best-effort: a DB failure logs and the reaper
        backstops on the next tick.
        """
        from roboco.db.base import get_session_factory
        from roboco.services.task import TaskService
        from roboco.utils.converters import InvalidIdentifierError, require_uuid

        try:
            task_id = require_uuid(task_id_str)
        except InvalidIdentifierError as exc:
            # A malformed task_id_str is a bad identifier, not a transient
            # failure — log it so the drop is visible instead of swallowed,
            # then no-op (nothing to release). Other exceptions still fall
            # through to the broad catch below (#25).
            logger.warning(
                "stopped agent claim had malformed task id",
                task_id_str=task_id_str,
                error=str(exc),
            )
            return
        try:
            factory = get_session_factory()
            async with factory() as db:
                svc = TaskService(db)
                await svc.unclaim_for_reaper(task_id)
                await db.commit()
            logger.info(
                "stopped agent claim released to pool",
                agent_id=agent_id,
                task_id=task_id_str,
            )
        except Exception as exc:
            logger.warning(
                "stop_agent claim release failed; reaper will backstop",
                agent_id=agent_id,
                task_id=task_id_str,
                error=str(exc),
            )

    @staticmethod
    async def _fetch_agent_tokens(
        client: httpx.AsyncClient, agent_id: str
    ) -> tuple[int, int, int, int] | None:
        """Fetch cumulative token counts from an agent's SDK usage endpoint.

        Returns ``(input, output, cache_read, cache_write)`` or ``None`` when the
        agent returns a non-200 status or has not accrued any tokens yet.
        """
        sdk_url = f"http://roboco-agent-{agent_id}:{SDK_PORT}/usage/status"
        resp = await client.get(sdk_url)
        if resp.status_code != http_status.HTTP_200_OK:
            return None
        data = resp.json()
        tokens = (
            data.get("tokens_input", 0),
            data.get("tokens_output", 0),
            data.get("tokens_cache_read", 0),
            data.get("tokens_cache_write", 0),
        )
        if sum(tokens) == 0:
            return None
        return tokens

    async def _resolve_active_tokens(
        self, client: httpx.AsyncClient, agent_id: str
    ) -> tuple[int, int, int, int] | None:
        """Resolve live token counts for an active agent.

        Tries the agent SDK's ``/usage/status`` first; on a zero/miss falls
        back to the durable transcript (the SDK can report zero mid-run, the
        same race the finalize path handles). Returns ``None`` when neither
        source has any usage yet. GROK / OPENAI (codex) / GEMINI / KIMI have no
        SDK server or Claude transcript, so each routes to its own
        ``usage.json`` — the same early return the finalize path uses, so live
        USAGE_SNAPSHOT reflects grok/codex/gemini/kimi agents mid-run too (in
        practice a one-shot run's usage.json is written only post-run, so
        this is a no-op ``None`` until the run ends).
        """
        instance = self._instances.get(agent_id)
        provider = (
            instance.config.provider_type
            if instance is not None and instance.config is not None
            else None
        )
        # One-shot CLIs (no SDK server / Claude transcript) each read their own
        # captured usage.json — collapsed into a lookup so a fourth such
        # provider is one dict entry, not another branch.
        usage_json_readers = {
            ModelProvider.GROK.value: self._grok_usage_tokens,
            ModelProvider.OPENAI.value: self._codex_usage_tokens,
            ModelProvider.GEMINI.value: self._gemini_usage_tokens,
            ModelProvider.KIMI.value: self._kimi_usage_tokens,
        }
        read_usage_json = usage_json_readers.get(provider) if provider else None
        if read_usage_json is not None:
            cli_tokens = read_usage_json(agent_id)
            return cli_tokens if any(cli_tokens) else None
        tokens = await self._fetch_agent_tokens(client, agent_id)
        if tokens is not None:
            return tokens
        tin, tout, cr, cw, _turns = self._usage_from_transcript(
            agent_id, self._claude_session_id_for(agent_id)
        )
        token_counts = (tin, tout, cr, cw)
        return token_counts if any(token_counts) else None

    @staticmethod
    async def _persist_token_snapshot(
        session_factory: Any,
        agent_id: str,
        instance: AgentInstance,
        tokens: tuple[int, int, int, int],
    ) -> bool:
        """Insert a token_usage_snapshots row and refresh the open session totals.

        Returns True when a snapshot was written; False when the agent has no
        open spawn-session row to attach it to.
        """
        from uuid import uuid4

        from sqlalchemy import select, update

        from roboco.db.tables import AgentSpawnSessionTable, TokenUsageSnapshotTable

        tokens_input, tokens_output, tokens_cache_read, tokens_cache_write = tokens
        async with session_factory() as db:
            # Prefer a direct lookup by the session UUID captured at spawn time;
            # fall back to the agent_slug heuristic for instances that pre-date
            # the usage_session_id field.
            if instance.usage_session_id is not None:
                result = await db.execute(
                    select(AgentSpawnSessionTable).where(
                        AgentSpawnSessionTable.id == instance.usage_session_id
                    )
                )
            else:
                result = await db.execute(
                    select(AgentSpawnSessionTable)
                    .where(
                        AgentSpawnSessionTable.agent_slug == agent_id,
                        AgentSpawnSessionTable.ended_at.is_(None),
                    )
                    .order_by(AgentSpawnSessionTable.started_at.desc())
                    .limit(1)
                )
            session_row = result.scalar_one_or_none()
            if session_row is None:
                return False

            db.add(
                TokenUsageSnapshotTable(
                    id=uuid4(),
                    agent_spawn_session_id=session_row.id,
                    snapshotted_at=datetime.now(UTC),
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                    tokens_cache_read=tokens_cache_read,
                    tokens_cache_write=tokens_cache_write,
                )
            )
            await db.execute(
                update(AgentSpawnSessionTable)
                .where(AgentSpawnSessionTable.id == session_row.id)
                .values(
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                    tokens_cache_read=tokens_cache_read,
                    tokens_cache_write=tokens_cache_write,
                )
            )
            await db.commit()
            return True

    async def _sweep_token_snapshots(self) -> None:
        """Write a token_usage_snapshots row for each active agent with non-zero tokens.

        Called from _run_sweep() every ~60 s. Also updates the cumulative
        token counts on the open agent_spawn_sessions row so the DB reflects
        current progress without waiting for session close.
        Errors per-agent are caught so one bad agent doesn't abort the whole sweep.

        Also publishes a USAGE_SNAPSHOT aggregate event after the loop so the
        /ws/system dashboard updates live for active agents.
        """
        if not self._instances:
            return

        try:
            from roboco.db.base import get_session_factory
        except ImportError:
            return

        session_factory = get_session_factory()

        # Accumulators for the post-loop USAGE_SNAPSHOT event.
        _usage_by_agent: list[dict[str, Any]] = []
        _usage_total_input = 0
        _usage_total_output = 0
        _usage_total_cost = 0.0

        async with httpx.AsyncClient(
            timeout=3.0, headers=_system_api_headers()
        ) as client:
            for agent_id, instance in list(self._instances.items()):
                if instance.state not in (
                    AgentState.ACTIVE,
                    AgentState.WAITING_SHORT,
                ):
                    continue

                try:
                    tokens = await self._resolve_active_tokens(client, agent_id)
                    if tokens is None:
                        continue

                    persisted = await self._persist_token_snapshot(
                        session_factory, agent_id, instance, tokens
                    )
                    if not persisted:
                        continue

                    tokens_input = tokens[0]
                    tokens_output = tokens[1]
                    tokens_cache_read = tokens[2]
                    tokens_cache_write = tokens[3]
                    model = instance.config.model if instance.config else "unknown"

                    # Accumulate per-agent data for the aggregate snapshot.
                    with contextlib.suppress(Exception):
                        from roboco.billing.pricing import calculate_cost

                        agent_cost = calculate_cost(
                            model=model,
                            tokens_input=tokens_input,
                            tokens_output=tokens_output,
                            tokens_cache_read=tokens_cache_read,
                            tokens_cache_write=tokens_cache_write,
                        )
                        _usage_by_agent.append(
                            {
                                "agent_id": agent_id,
                                "input_tokens": tokens_input,
                                "output_tokens": tokens_output,
                                "cache_read_tokens": tokens_cache_read,
                                "cache_write_tokens": tokens_cache_write,
                                "model": model,
                                "cost_estimate": agent_cost,
                            }
                        )
                        _usage_total_input += tokens_input
                        _usage_total_output += tokens_output
                        _usage_total_cost += agent_cost

                except Exception as agent_exc:
                    logger.debug(
                        "Token snapshot failed for agent",
                        agent_id=agent_id,
                        error=str(agent_exc),
                    )

        # Publish a USAGE_SNAPSHOT aggregate if any active agents had token data.
        if _usage_by_agent:
            with contextlib.suppress(Exception):
                from roboco.events import get_event_bus
                from roboco.services.usage_events import (
                    UsageSnapshot,
                    publish_usage_snapshot,
                )

                await publish_usage_snapshot(
                    get_event_bus(),
                    UsageSnapshot(
                        period="live",
                        totals={
                            "input_tokens": _usage_total_input,
                            "output_tokens": _usage_total_output,
                        },
                        cost_estimate=_usage_total_cost,
                        by_agent=_usage_by_agent,
                    ),
                )

    async def _sweep_daily_rollup(self) -> None:
        """Upsert daily_usage_rollups from closed agent_spawn_sessions.

        Groups ended sessions by (date, agent_slug, team, model) and sums
        their token counts + cost. Uses a Python-side upsert to stay
        compatible with asyncpg / SQLAlchemy without raw INSERT ... ON CONFLICT
        dialect-specific SQL.
        Errors are caught so a bad rollup doesn't abort the sweeper.
        """
        try:
            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentSpawnSessionTable
        except ImportError:
            return

        try:
            from uuid import uuid4 as _uuid4

            from sqlalchemy import func, select

            session_factory = get_session_factory()
            async with session_factory() as db:
                # Aggregate closed sessions by (date, agent_slug, team, model).
                # Limit to the last 7 days to avoid re-aggregating all-time
                # history on every sweep — older days are already stable.
                rollup_window_start = datetime.now(UTC) - timedelta(days=7)
                result = await db.execute(
                    select(
                        func.date(AgentSpawnSessionTable.started_at).label("date"),
                        AgentSpawnSessionTable.agent_slug,
                        AgentSpawnSessionTable.team,
                        AgentSpawnSessionTable.model,
                        func.sum(AgentSpawnSessionTable.tokens_input).label(
                            "tokens_input"
                        ),
                        func.sum(AgentSpawnSessionTable.tokens_output).label(
                            "tokens_output"
                        ),
                        func.sum(AgentSpawnSessionTable.tokens_cache_read).label(
                            "tokens_cache_read"
                        ),
                        func.sum(AgentSpawnSessionTable.tokens_cache_write).label(
                            "tokens_cache_write"
                        ),
                        func.sum(AgentSpawnSessionTable.estimated_cost_usd).label(
                            "total_cost_usd"
                        ),
                        func.count(AgentSpawnSessionTable.id).label("session_count"),
                    )
                    .where(
                        AgentSpawnSessionTable.ended_at.isnot(None),
                        AgentSpawnSessionTable.started_at >= rollup_window_start,
                    )
                    .group_by(
                        func.date(AgentSpawnSessionTable.started_at),
                        AgentSpawnSessionTable.agent_slug,
                        AgentSpawnSessionTable.team,
                        AgentSpawnSessionTable.model,
                    )
                )
                rows = result.fetchall()

                for row in rows:
                    await self._upsert_rollup_row(db, row, _uuid4)

                await db.commit()
                logger.debug("Daily usage rollup complete", rows_processed=len(rows))

        except Exception as exc:
            logger.warning("Daily usage rollup failed", error=str(exc))

    async def _upsert_rollup_row(self, db: Any, row: Any, uuid4: Any) -> None:
        """Insert or update a single daily_usage_rollups row from an aggregate.

        Looks up the existing rollup for (date, agent_slug, team, model) and
        either updates its summed columns or inserts a fresh row.
        """
        from sqlalchemy import select, update

        from roboco.db.tables import DailyUsageRollupTable

        key = {
            "date": row.date,
            "agent_slug": row.agent_slug,
            "team": row.team,
            "model": row.model,
        }
        values = {
            "tokens_input": int(row.tokens_input or 0),
            "tokens_output": int(row.tokens_output or 0),
            "tokens_cache_read": int(row.tokens_cache_read or 0),
            "tokens_cache_write": int(row.tokens_cache_write or 0),
            "total_cost_usd": float(row.total_cost_usd or 0.0),
            "session_count": int(row.session_count or 0),
        }

        existing_result = await db.execute(
            select(DailyUsageRollupTable).where(
                DailyUsageRollupTable.date == key["date"],
                DailyUsageRollupTable.agent_slug == key["agent_slug"],
                DailyUsageRollupTable.team == key["team"],
                DailyUsageRollupTable.model == key["model"],
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing is not None:
            await db.execute(
                update(DailyUsageRollupTable)
                .where(DailyUsageRollupTable.id == existing.id)
                .values(**values)
            )
        else:
            db.add(DailyUsageRollupTable(id=uuid4(), **key, **values))

    async def _sweep_member_performance(self) -> None:
        """Upsert member_performance_daily from spawn sessions + audit_log.

        Mirrors _sweep_daily_rollup: a trailing 7-day, overwrite-upsert sweep
        (idempotent — re-running overwrites, never accumulates). Aggregates each
        metric with one query keyed (date, agent_slug) into an accumulator, then
        upserts one row per member per day plus one CEO row per day. Wrapped in
        its own try/except so a bad member rollup never aborts the sweeper.
        """
        try:
            from roboco.db.base import get_session_factory
        except ImportError:
            return
        try:
            window_start = datetime.now(UTC) - timedelta(days=7)
            session_factory = get_session_factory()
            async with session_factory() as db:
                acc: dict[tuple[Any, str], dict[str, Any]] = {}
                await self._msweep_spawn(db, window_start, acc)
                await self._msweep_delivery(db, window_start, acc)
                await self._msweep_caused(db, window_start, acc)
                await self._msweep_qa(db, window_start, acc)
                await self._msweep_escalations(db, window_start, acc)
                await self._msweep_blocked_others(db, window_start, acc)
                await self._msweep_idle(db, window_start, acc)
                await self._msweep_blocked_seconds(db, window_start, acc)
                for (day, slug), fields in acc.items():
                    await self._upsert_member_perf_row(db, day, "agent", slug, fields)
                await self._msweep_ceo(db, window_start)
                await db.commit()
                logger.debug("Member performance rollup complete", rows=len(acc))
        except Exception as exc:
            logger.warning("Member performance rollup failed", error=str(exc))

    @staticmethod
    def _merge_member(
        acc: dict[tuple[Any, str], dict[str, Any]],
        day: Any,
        slug: str,
        **fields: Any,
    ) -> None:
        """Merge one metric's aggregate into the (date, slug) accumulator entry."""
        if not slug:
            return
        entry = acc.setdefault((day, slug), {})
        for key, value in fields.items():
            entry[key] = value

    async def _msweep_spawn(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """Effort / turns / tool_calls / tokens / cost from closed spawn sessions."""
        from sqlalchemy import text

        sql = text(
            """
            SELECT date(started_at) AS d, agent_slug AS slug, team, role,
              COALESCE(SUM(EXTRACT(epoch FROM
                (COALESCE(ended_at, now()) - started_at))), 0) AS active_s,
              COALESCE(SUM(turns), 0) AS turns,
              COALESCE(SUM(tool_calls), 0) AS tool_calls,
              COALESCE(SUM(tokens_input + tokens_output
                + tokens_cache_read + tokens_cache_write), 0) AS tokens,
              COALESCE(SUM(estimated_cost_usd), 0) AS cost
            FROM agent_spawn_sessions
            WHERE ended_at IS NOT NULL AND started_at >= :ws
            GROUP BY date(started_at), agent_slug, team, role
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            self._merge_member(
                acc,
                r.d,
                r.slug,
                team=r.team,
                role=r.role,
                active_runtime_seconds=int(r.active_s or 0),
                turns=int(r.turns or 0),
                tool_calls=int(r.tool_calls or 0),
                tokens=int(r.tokens or 0),
                cost_usd=float(r.cost or 0.0),
            )

    async def _msweep_delivery(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """Completed / first-pass / revisions-received per task owner per day."""
        from sqlalchemy import text

        sql = text(
            """
            SELECT date(t.completed_at) AS d, ag.slug AS slug,
              COUNT(*) AS completed,
              COUNT(*) FILTER (WHERE COALESCE(t.revision_count, 0) = 0) AS first_pass,
              COALESCE(SUM(t.revision_count), 0) AS received
            FROM tasks t JOIN agents ag ON ag.id = t.assigned_to
            WHERE t.status = 'completed' AND t.completed_at >= :ws
              AND t.assigned_to IS NOT NULL
            GROUP BY date(t.completed_at), ag.slug
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            self._merge_member(
                acc,
                r.d,
                r.slug,
                tasks_completed=int(r.completed or 0),
                tasks_first_pass=int(r.first_pass or 0),
                revisions_received=int(r.received or 0),
            )

    async def _msweep_caused(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """Revisions caused — qa/pr fail events attributed to the rejector."""
        from sqlalchemy import text

        sql = text(
            """
            SELECT date(al.timestamp) AS d, ag.slug AS slug, COUNT(*) AS caused
            FROM audit_log al JOIN agents ag ON ag.id = al.agent_id
            WHERE al.event_type IN ('task.qa_fail', 'task.pr_fail')
              AND al.timestamp >= :ws
            GROUP BY date(al.timestamp), ag.slug
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            self._merge_member(acc, r.d, r.slug, revisions_caused=int(r.caused or 0))

    async def _msweep_qa(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """QA pass-rate — passed (awaiting_documentation by qa) + failed (qa_fail)."""
        from sqlalchemy import text

        sql = text(
            """
            SELECT date(al.timestamp) AS d, ag.slug AS slug,
              COUNT(*) FILTER (
                WHERE al.event_type = 'task.awaiting_documentation') AS passed,
              COUNT(*) FILTER (WHERE al.event_type = 'task.qa_fail') AS failed
            FROM audit_log al JOIN agents ag ON ag.id = al.agent_id
            WHERE al.timestamp >= :ws AND (
              (al.event_type = 'task.awaiting_documentation'
                AND (al.details->>'agent_role') = 'qa')
              OR al.event_type = 'task.qa_fail'
            )
            GROUP BY date(al.timestamp), ag.slug
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            passed = int(r.passed or 0)
            failed = int(r.failed or 0)
            self._merge_member(
                acc,
                r.d,
                r.slug,
                qa_reviews_passed=passed,
                qa_reviews_total=passed + failed,
            )

    async def _msweep_escalations(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """Escalations raised per member (keyed on details.escalator_slug)."""
        from sqlalchemy import text

        sql = text(
            """
            SELECT date(timestamp) AS d,
              (details->>'escalator_slug') AS slug, COUNT(*) AS n
            FROM audit_log
            WHERE event_type = 'task.escalated' AND timestamp >= :ws
              AND (details->>'escalator_slug') IS NOT NULL
            GROUP BY date(timestamp), (details->>'escalator_slug')
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            self._merge_member(acc, r.d, r.slug, escalations=int(r.n or 0))

    async def _msweep_blocked_others(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """Downstream tasks a member's completed task was blocking."""
        from sqlalchemy import text

        sql = text(
            """
            SELECT date(al.timestamp) AS d, ag.slug AS slug,
              COALESCE(SUM((al.details->>'count')::int), 0) AS n
            FROM audit_log al
            JOIN tasks t ON t.id = al.target_id
            JOIN agents ag ON ag.id = t.assigned_to
            WHERE al.event_type = 'task.unblocked_dependents' AND al.timestamp >= :ws
            GROUP BY date(al.timestamp), ag.slug
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            self._merge_member(acc, r.d, r.slug, blocked_others=int(r.n or 0))

    async def _msweep_idle(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """Idle seconds — each idle mark to the member's next spawn (else now)."""
        from sqlalchemy import text

        sql = text(
            """
            WITH idle AS (
              SELECT date(al.timestamp) AS d,
                (al.details->>'agent_slug') AS slug, al.timestamp AS idle_at
              FROM audit_log al
              WHERE al.event_type = 'agent.idle' AND al.timestamp >= :ws
                AND (al.details->>'agent_slug') IS NOT NULL
            )
            SELECT i.d, i.slug,
              COALESCE(SUM(EXTRACT(epoch FROM (
                COALESCE((SELECT MIN(s.started_at) FROM agent_spawn_sessions s
                          WHERE s.agent_slug = i.slug AND s.started_at > i.idle_at),
                         now()) - i.idle_at))), 0) AS idle_s
            FROM idle i GROUP BY i.d, i.slug
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            self._merge_member(acc, r.d, r.slug, idle_seconds=int(r.idle_s or 0))

    async def _msweep_blocked_seconds(
        self,
        db: Any,
        window_start: datetime,
        acc: dict[tuple[Any, str], dict[str, Any]],
    ) -> None:
        """Wall-clock a member's tasks spent in `blocked`, per owner per day."""
        from sqlalchemy import text

        sql = text(
            """
            WITH ordered AS (
              SELECT a.target_id, a.timestamp AS entered,
                (a.details->>'to_status') AS status,
                LEAD(a.timestamp) OVER (
                  PARTITION BY a.target_id ORDER BY a.timestamp) AS exited
              FROM audit_log a
              WHERE a.event_type LIKE 'task.%'
                AND a.event_type = 'task.' || (a.details->>'to_status')
                AND a.timestamp >= :ws
            )
            SELECT date(o.entered) AS d, ag.slug AS slug,
              COALESCE(SUM(EXTRACT(epoch FROM
                (COALESCE(o.exited, now()) - o.entered))), 0) AS blocked_s
            FROM ordered o
            JOIN tasks t ON t.id = o.target_id
            JOIN agents ag ON ag.id = t.assigned_to
            WHERE o.status = 'blocked'
            GROUP BY date(o.entered), ag.slug
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            self._merge_member(acc, r.d, r.slug, blocked_seconds=int(r.blocked_s or 0))

    async def _msweep_ceo(self, db: Any, window_start: datetime) -> None:
        """Upsert one CEO row per day: approval/unblock dwell + god-mode count."""
        from sqlalchemy import text

        sql = text(
            """
            WITH events AS (
              SELECT target_id, timestamp, date(timestamp) AS d,
                (details->>'to_status') AS to_status,
                (details->>'agent_role') AS role
              FROM audit_log
              WHERE event_type LIKE 'task.%' AND timestamp >= :ws
            ),
            approvals AS (
              SELECT e.d, EXTRACT(epoch FROM ((
                SELECT MIN(x.timestamp) FROM events x
                WHERE x.target_id = e.target_id AND x.timestamp > e.timestamp
                  AND x.role = 'ceo'
                  AND x.to_status IN
                    ('completed', 'needs_revision', 'cancelled', 'pending')
              ) - e.timestamp)) AS latency
              FROM events e WHERE e.to_status = 'awaiting_ceo_approval'
            ),
            unblocks AS (
              SELECT e.d, EXTRACT(epoch FROM ((
                SELECT MIN(x.timestamp) FROM events x
                WHERE x.target_id = e.target_id AND x.timestamp > e.timestamp
                  AND x.role = 'ceo' AND x.to_status IN ('in_progress', 'pending')
              ) - e.timestamp)) AS latency
              FROM events e WHERE e.to_status = 'blocked'
            )
            SELECT d,
              COALESCE(SUM(approval_latency), 0) AS approval_s,
              COALESCE(SUM(unblock_latency), 0) AS unblock_s,
              COALESCE(SUM(godmode), 0) AS godmode
            FROM (
              SELECT d, latency AS approval_latency, 0 AS unblock_latency, 0 AS godmode
                FROM approvals WHERE latency IS NOT NULL
              UNION ALL
              SELECT d, 0, latency, 0 FROM unblocks WHERE latency IS NOT NULL
              UNION ALL
              SELECT d, 0, 0, 1 FROM events WHERE role = 'ceo'
            ) u GROUP BY d
            """
        )
        for r in (await db.execute(sql, {"ws": window_start})).all():
            await self._upsert_member_perf_row(
                db,
                r.d,
                "ceo",
                "",
                {
                    "ceo_approval_dwell_seconds": int(r.approval_s or 0),
                    "ceo_unblock_dwell_seconds": int(r.unblock_s or 0),
                    "godmode_actions": int(r.godmode or 0),
                },
            )

    async def _upsert_member_perf_row(
        self, db: Any, day: Any, member_kind: str, slug: str, fields: dict[str, Any]
    ) -> None:
        """Overwrite-upsert one member_performance_daily row on the natural key."""
        from uuid import uuid4 as _uuid4

        from sqlalchemy import select, update

        from roboco.db.tables import MemberPerformanceDailyTable

        existing = (
            await db.execute(
                select(MemberPerformanceDailyTable).where(
                    MemberPerformanceDailyTable.date == day,
                    MemberPerformanceDailyTable.member_kind == member_kind,
                    MemberPerformanceDailyTable.agent_slug == slug,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await db.execute(
                update(MemberPerformanceDailyTable)
                .where(MemberPerformanceDailyTable.id == existing.id)
                .values(**fields)
            )
        else:
            db.add(
                MemberPerformanceDailyTable(
                    id=_uuid4(),
                    date=day,
                    member_kind=member_kind,
                    agent_slug=slug,
                    **fields,
                )
            )

    async def _sweeper_loop(self) -> None:
        """Background sweeper for stale notifications + runtime maintenance.

        Addresses a silent-failure surface (NotificationTable.expires_at existed
        but no job ever acted on it) and drives the budget kill-switch, token
        rollups, transcript retention, and dangling-image pruning.

        Runs on its own interval so a slow sweep can't delay agent dispatch.
        """
        sweep_interval = 60  # seconds
        while self._running:
            try:
                await asyncio.sleep(sweep_interval)
                await self._run_sweep()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Sweeper loop error", error=str(e))

    async def _run_sweep(self) -> None:
        """Run one pass of the notification sweeper + runtime maintenance."""
        from roboco.db.base import get_session_factory
        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        session_factory = get_session_factory()
        async with session_factory() as db:
            # This loop doesn't run through _dispatch_all_work, so it keeps
            # the ledger warm on its own tick too (_refresh_uptime no-ops
            # past its own throttle either way).
            await self._refresh_uptime(db)
            deliv_svc = get_notification_delivery_service(db)
            try:
                # Resolve terminal-task escalations FIRST: sweep_expired_
                # notifications runs its own independent query right after,
                # so a row acked here is already excluded from that query's
                # unacked/re-escalation path in the same tick.
                resolved = await deliv_svc.resolve_terminal_task_escalations()
                expired = await deliv_svc.sweep_expired_notifications()
                if resolved or expired:
                    await db.commit()
            except Exception as e:
                await db.rollback()
                logger.warning("Notification sweep failed", error=str(e))

        # Retire abandoned live intake/secretary chats (idle past the threshold)
        # so a closed-tab session doesn't leak its container until restart.
        await self._reap_idle_interactive_sessions()

        # Budget kill-switch — runs every sweep. Any agent whose SDK reports
        # halt=true has breached its per-session tool-call cap; terminate the
        # container so the next dispatcher tick doesn't waste tokens on the
        # same session.
        await self._sweep_budget_exceeded()

        # Token-usage instrumentation: snapshot active agents and roll up
        # closed sessions into the daily aggregation table.
        await self._sweep_token_snapshots()
        await self._sweep_daily_rollup()
        # Granular per-member performance rollup (own try/except inside).
        await self._sweep_member_performance()

        # Prune old agent transcripts (throttled internally to ~hourly) so the
        # operator's bind-mounted ~/.claude doesn't grow without bound.
        await self._sweep_transcript_retention()

        # Prune dangling (<none>) Docker images left by agent-image rebuilds
        # (throttled internally to ~6h) so deploys don't pile up orphaned layers.
        await self._sweep_dangling_images()

        # Close-on-land for landed supersedes — runs here (always-on sweeper)
        # rather than the default-off external-PR poll loop, so a supersede that
        # lands after external_pr_enabled is toggled off is still reconciled.
        await self._sweep_superseded_prs()

    async def _sweep_superseded_prs(self) -> None:
        """Retire the contributor PR for any supersede umbrella that landed,
        and reconcile branch_pending umbrellas stranded by an orchestrator
        restart.

        Dormant in a standard deployment: when no ``external_pr_supersede``
        umbrellas exist the lookup returns nothing and no GitHub call is made,
        so this is safe to run unconditionally on every sweep.

        Each landed umbrella's contributor-PR close runs in its OWN fresh
        session (``_close_one_superseded_pr``), never one session held across
        the whole pending list: this sweep ticks every 60s forever, so a
        single session held across N sequential GitHub close calls (each an
        HTTP round trip) was the highest-frequency pool-hold offender of the
        2026-07-29 pool-exhaustion incident class. Mirrors
        ``_reconcile_umbrella_row``'s per-row isolation two methods below.
        """
        from roboco.db.base import get_session_factory

        system_id = _foundation.AGENTS["system"].uuid
        session_factory = get_session_factory()
        pending = await self._collect_supersede_close_pending(session_factory)
        for uid, replacement_pr in pending:
            await self._close_one_superseded_pr(
                uid, replacement_pr, system_id, session_factory
            )

        # Reconcile branch_pending / branch_cut_failed umbrellas (restart-safe).
        to_reconcile = await self._collect_supersede_reconciliations(session_factory)
        for uid, slug, pr_num, pid, branch in to_reconcile:
            # F2: claim the in-flight slot before spawning.
            if uid in self._supersede_cuts_in_flight:
                continue
            self._supersede_cuts_in_flight.add(uid)
            logger.info(
                "supersede: reconciling branch_pending umbrella",
                umbrella_id=uid,
                pr_number=pr_num,
            )
            bg = asyncio.create_task(
                self._cut_supersede_branch(
                    umbrella_id=uid,
                    project_slug=slug,
                    pr_number=pr_num,
                    project_id=pid,
                    branch_name=branch,
                )
            )
            self._bg_tasks.add(bg)
            bg.add_done_callback(self._bg_tasks.discard)

    async def _collect_supersede_close_pending(
        self, session_factory: Any
    ) -> list[tuple[str, int]]:
        """(umbrella id, replacement PR) pairs pending a contributor-PR close.

        Read in its own short session, closed before the per-row close loop
        in ``_sweep_superseded_prs``. A lookup failure here is best-effort
        and returns [] (mirrors ``_collect_supersede_reconciliations``).
        """
        from roboco.services.task import get_task_service

        try:
            async with session_factory() as db:
                task_service = get_task_service(db)
                pending = await task_service.supersede_umbrellas_pending_close()
                return [(str(u.id), pr) for u, pr in pending]
        except Exception as e:
            logger.warning("Supersede close-on-land sweep lookup failed", error=str(e))
            return []

    async def _close_one_superseded_pr(
        self,
        uid: str,
        replacement_pr: int,
        system_id: "UUID",
        session_factory: Any,
    ) -> None:
        """Close ONE landed umbrella's contributor PR in its own fresh session.

        Per-row session + per-row commit, isolated from siblings (mirrors
        ``_reconcile_umbrella_row``): one bad PR (deleted, revoked PAT) is
        logged and skipped rather than aborting every other umbrella queued
        this tick, and the pool connection this row checks out for the
        GitHub HTTP call below is released the moment the row finishes.
        """
        from roboco.services.git import GitService
        from roboco.services.task import get_task_service
        from roboco.utils.converters import require_uuid

        try:
            async with session_factory() as db:
                task_service = get_task_service(db)
                umbrella = await task_service.get(require_uuid(uid))
                if umbrella is None:
                    return
                marker = _markers.get_external_pr_supersede(umbrella) or ""
                pr_number = self._parse_supersede_pr(marker)
                if pr_number is None:
                    return
                author = self._parse_supersede_author(marker)
                comment = _supersede_author_prefix(
                    author
                ) + SUPERSEDE_PR_CLOSE_COMMENT.format(replacement_pr=replacement_pr)
                git = GitService(db)
                try:
                    await git.close_pull_request(
                        pr_number,
                        comment=comment,
                        delete_branch=False,
                        actor_agent_id=system_id,
                        # PR numbers are per-repo; scope the close to THIS
                        # umbrella's project so a same-numbered PR in another
                        # project's repo is never resolved (and closed) by
                        # mistake.
                        project_id=cast("UUID", umbrella.project_id),
                    )
                except Exception:
                    # A permanent close failure (deleted PR, revoked PAT) would
                    # otherwise re-fire + re-log every tick forever; keep it a
                    # single warning rather than a per-tick stack trace.
                    logger.warning("close-on-land failed", pr_number=pr_number)
                    return
                await task_service.mark_supersede_pr_closed(cast("UUID", umbrella.id))
                await db.commit()
        except Exception as e:
            logger.warning(
                "Supersede close-on-land sweep row failed; umbrella skipped",
                umbrella_id=uid,
                error=str(e),
            )

    async def _collect_supersede_reconciliations(
        self, session_factory: Any
    ) -> list[tuple[str, str, int, Any, str]]:
        """Build the (uid, slug, pr, pid, branch) list for the sweep to spawn.

        The candidate id list is looked up in its own session (a lookup
        failure here is best-effort and returns []); each umbrella is then
        reconciled in its OWN fresh session via ``_reconcile_umbrella_row``
        so one persistently-failing umbrella cannot starve every sibling on
        the sweep tick (mirrors the notification re-escalation sweep's
        per-row isolation, #721/#730).
        """
        import time

        from roboco.services.task import get_task_service

        now = time.time()
        try:
            async with session_factory() as db:
                task_service = get_task_service(db)
                pending = await task_service.supersede_umbrellas_branch_pending()
                umbrella_ids = [str(u.id) for u in pending]
        except Exception as e:
            logger.warning("Supersede branch-pending sweep lookup failed", error=str(e))
            return []
        to_reconcile: list[tuple[str, str, int, Any, str]] = []
        for uid in umbrella_ids:
            entry = await self._reconcile_umbrella_row(uid, now, session_factory)
            if entry is not None:
                to_reconcile.append(entry)
        return to_reconcile

    async def _reconcile_umbrella_row(
        self, uid: str, now: float, session_factory: Any
    ) -> tuple[str, str, int, Any, str] | None:
        """Reconcile ONE umbrella in its own session, isolated from siblings.

        A fresh session per row means a lookup error, or a commit/flush
        failure inside ``_reconcile_one_umbrella``'s CEO-unblock reset, can
        never poison or roll back any other row's session; the failing
        umbrella is logged and skipped this tick, retried on the next.
        """
        from roboco.foundation.policy.content import markers as _sm
        from roboco.services.project import get_project_service
        from roboco.services.task import get_task_service
        from roboco.utils.converters import require_uuid

        try:
            async with session_factory() as db:
                task_service = get_task_service(db)
                project_service = get_project_service(db)
                umbrella = await task_service.get(require_uuid(uid))
                if umbrella is None:
                    return None
                return await self._reconcile_one_umbrella(
                    umbrella, now, project_service, db, _sm
                )
        except Exception as e:
            logger.warning(
                "Supersede branch-pending sweep row failed; umbrella skipped",
                umbrella_id=uid,
                error=str(e),
            )
            return None

    async def _reconcile_one_umbrella(
        self, umbrella: Any, now: float, project_service: Any, db: Any, _sm: Any
    ) -> tuple[str, str, int, Any, str] | None:
        """Check one umbrella for sweep reconciliation. Returns the tuple to
        spawn, or None if it should be skipped (in-flight, backoff, no PR)."""
        uid = str(umbrella.id)
        if uid in self._supersede_cuts_in_flight:
            return None
        retry_at = _sm.get_branch_cut_next_retry_at(umbrella)
        if retry_at is not None and now < retry_at:
            return None
        # branch_cut_failed but NOT branch_pending: CEO unblocked after
        # exhaustion. Re-arm for a fresh cut.
        if _sm.is_branch_cut_failed(umbrella) and not _sm.is_branch_pending(umbrella):
            _sm.mark_branch_pending(umbrella)
            _sm.clear_branch_cut_failed(umbrella)
            _sm.clear_branch_cut_next_retry_at(umbrella)
            await db.flush()
            await db.commit()
        # The marker lives in orchestration_markers (migration 041), NOT
        # quick_context: reading quick_context here returned None after the
        # marker refactor, so a stranded branch_pending umbrella was never
        # reconciled (same bug class as close-on-land).
        pr_number = self._parse_supersede_pr(
            _sm.get_external_pr_supersede(umbrella) or ""
        )
        if pr_number is None:
            return None
        project = await project_service.get(cast("UUID", umbrella.project_id))
        if project is None:
            return None
        return (
            uid,
            project.slug,
            pr_number,
            cast("UUID", umbrella.project_id),
            umbrella.branch_name or "",
        )

    async def _sweep_dangling_images(self) -> None:
        """Prune dangling (<none>) Docker images left by agent-image rebuilds.

        Each rebuild of an agent image orphans the prior build's layers as an
        untagged ``<none>`` image; over many deploys these pile up (the operator
        saw ~80). Pruning only DANGLING images is safe — a tagged image, or one
        backing a running container, is never dangling. Throttled to
        ``settings.image_prune_interval_seconds`` (default 6h) and gated by
        ``settings.image_prune_enabled`` (default on). Best-effort: any failure
        is logged, never raised into the sweeper.
        """
        if not settings.image_prune_enabled:
            return
        now = datetime.now(UTC)
        last = self._last_image_prune
        if (
            last is not None
            and (now - last).total_seconds() < settings.image_prune_interval_seconds
        ):
            return
        self._last_image_prune = now
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "prune",
                "-f",
                "--filter",
                "dangling=true",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                summary = stdout.decode().strip().splitlines()[-1:] if stdout else []
                logger.info(
                    "pruned dangling images", reclaimed=summary[0] if summary else ""
                )
            else:
                logger.warning("dangling-image prune returned non-zero")
        except Exception as e:
            logger.warning("dangling-image prune failed (best-effort)", error=str(e))

    async def _sweep_transcript_retention(self) -> None:
        """Prune agent transcripts older than the retention window.

        Throttled to ``settings.transcript_prune_interval_seconds``. Reads the
        window from the ``system_settings`` store (panel-editable), falling back
        to ``settings.transcript_retention_days``. Only agent-owned project dirs
        (``-app`` + per-workspace dirs) are touched — never the operator's own
        Claude sessions. Best-effort: any failure is logged, never raised.
        """
        if not settings.transcript_prune_enabled:
            return
        now = datetime.now(UTC)
        last = self._last_transcript_prune
        if (
            last is not None
            and (now - last).total_seconds()
            < settings.transcript_prune_interval_seconds
        ):
            return
        self._last_transcript_prune = now

        retention_days = settings.transcript_retention_days
        with contextlib.suppress(Exception):
            from roboco.db.base import get_session_factory
            from roboco.services.settings import get_settings_service

            session_factory = get_session_factory()
            async with session_factory() as db:
                retention_days = await get_settings_service(db).get_int(
                    "transcript_retention_days", settings.transcript_retention_days
                )

        from roboco.runtime.transcript_retention import select_prunable_transcripts

        projects_root = Path.home() / ".claude" / "projects"
        cutoff = (now - timedelta(days=retention_days)).timestamp()
        prunable = select_prunable_transcripts(
            projects_root, settings.workspaces_root, cutoff
        )
        pruned = 0
        for transcript in prunable:
            try:
                transcript.unlink()
                pruned += 1
            except OSError as exc:
                logger.debug(
                    "Transcript prune failed", path=str(transcript), error=str(exc)
                )
        if pruned:
            logger.info(
                "Pruned old agent transcripts",
                count=pruned,
                retention_days=retention_days,
            )

    @staticmethod
    async def _fetch_budget_status(
        client: httpx.AsyncClient, url: str, agent_id: str
    ) -> dict[str, Any] | None:
        """Read an agent's SDK budget status; None if unreachable/not-JSON.

        The SDK being unreachable is benign (container not yet started, already
        gone, or a transient blip) and the health loop covers genuine failures,
        so the failure is swallowed — but logged at debug so it is observable
        rather than silent (the bare try/except/continue it replaced was not).
        """
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            logger.debug(
                "Budget status unreachable; skipping agent this sweep",
                agent_id=agent_id,
                error=str(exc),
            )
            return None
        if resp.status_code != http_status.HTTP_200_OK:
            return None
        try:
            data = resp.json()
        except ValueError as exc:
            logger.debug(
                "Budget status not JSON; skipping agent this sweep",
                agent_id=agent_id,
                error=str(exc),
            )
            return None
        return data if isinstance(data, dict) else None

    async def _task_budget_breach(self, task_id_str: str) -> tuple[float, float] | None:
        """``(cap_usd, spend_usd)`` if this task's own $ budget is breached.

        ``None`` when unbreached, when the task carries no explicit
        ``budget_usd`` (budgets are explicit-input only — an unset budget
        means no cap; ``effective_task_budget_usd`` is the shared resolver
        ``unblock``'s re-check uses), OR the task has already left claimed/
        in_progress (a stale re-check racing the task's own progress — not a
        breach). Spend is
        ``TaskService.task_spend_usd`` (closed-session cost + open-session
        live-token pricing via ``calculate_cost`` — a DB-only read off
        ``_sweep_token_snapshots``'s periodically-refreshed token columns, no
        fresh SDK round-trip needed for "cheaply available").
        """
        from roboco.db.base import get_db_context
        from roboco.foundation.policy.agent_loop import effective_task_budget_usd
        from roboco.models.base import TaskStatus
        from roboco.services.task import TaskService
        from roboco.utils.converters import InvalidIdentifierError, require_uuid

        try:
            task_id = require_uuid(task_id_str)
        except InvalidIdentifierError:
            return None
        async with get_db_context() as db:
            svc = TaskService(db)
            task = await svc.get(task_id)
            if task is None or task.status not in (
                TaskStatus.CLAIMED,
                TaskStatus.IN_PROGRESS,
            ):
                return None
            cap_usd = effective_task_budget_usd(task)
            if cap_usd is None:
                return None
            spend_usd = await svc.task_spend_usd(task_id)
        if spend_usd < cap_usd:
            return None
        return cap_usd, spend_usd

    async def _handle_task_budget_breach(
        self, task_id_str: str, *, cap_usd: float, spend_usd: float
    ) -> None:
        """Block a task whose own $ budget is breached and notify the CEO.

        Runs BEFORE ``stop_agent`` so its ``release_claim`` unclaim (which
        only fires from claimed/in_progress — see ``_force_unclaim_to_pending``)
        finds the task already ``BLOCKED`` and no-ops: the task never bounces
        through ``pending`` for an instant re-claim to re-burn the same
        budget. ``blocker_resolver_type=HUMAN`` keeps the dispatcher from ever
        respawning onto it (``_is_hitl_blocked``) — only the CEO raising the
        cap (or cancelling) moves it forward. Best-effort: a failure here logs
        and lets the caller's ``stop_agent`` proceed regardless — one more
        tick of a live over-budget agent is the safer failure mode than
        crashing the sweep.
        """
        from roboco.db.base import get_db_context
        from roboco.foundation.policy.content import markers
        from roboco.models.base import BlockerResolverType, TaskStatus
        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )
        from roboco.services.task import TaskService
        from roboco.utils.converters import InvalidIdentifierError, require_uuid

        try:
            task_id = require_uuid(task_id_str)
        except InvalidIdentifierError as exc:
            logger.warning(
                "task budget breach had malformed task id",
                task_id_str=task_id_str,
                error=str(exc),
            )
            return
        try:
            async with get_db_context() as db:
                svc = TaskService(db)
                task = await svc.get(task_id)
                if task is None or task.status not in (
                    TaskStatus.CLAIMED,
                    TaskStatus.IN_PROGRESS,
                ):
                    return
                task.blocker_resolver_type = BlockerResolverType.HUMAN
                markers.mark_budget_blocked(task)
                await svc.admin_set_status(
                    task_id, TaskStatus.BLOCKED, actor_role="system"
                )
                delivery = get_notification_delivery_service(db)
                await delivery.notify_ceo_of_budget_breach(
                    task=task,
                    task_id=task_id,
                    cap_usd=cap_usd,
                    spend_usd=spend_usd,
                )
                await self._fire_coroner_budget_hook(db, task_id)
        except Exception as exc:
            logger.warning(
                "task budget-breach block/notify failed",
                task_id=task_id_str,
                error=str(exc),
            )

    @staticmethod
    async def _fire_coroner_budget_hook(db: Any, task_id: "UUID") -> None:
        """Coroner (Board Program) budget-blocked trigger (spec §4).

        Runs on the SAME already-open session as the rest of
        ``_handle_task_budget_breach`` — ``get_db_context()`` commits on clean
        ``async with`` exit and ROLLS BACK on any exception, so this catches
        its OWN failures internally rather than letting them propagate and
        undo the block/notify that already succeeded this call.
        """
        from roboco.services.coroner_engine import get_coroner_engine

        try:
            await get_coroner_engine(db).open_for_incident(task_id, kind="budget")
        except Exception as exc:
            logger.warning(
                "coroner: budget hook failed (best-effort)",
                task_id=str(task_id),
                error=str(exc),
            )

    async def _sweep_budget_exceeded(self) -> None:
        """Stop agents whose per-session SDK budget reports halt=true, OR
        (when ``ROBOCO_TASK_BUDGETS_ENABLED`` is on) whose active task's own
        $ budget is breached.

        Tool-call halt: each agent's SDK server is reachable at
        `http://roboco-agent-{agent_id}:9000/budget/status` on the shared
        agent network; the task is already being auto-substituted by the
        post-tool hook on the agent side, so a release_claim stop suffices.

        Task $ budget: `_task_budget_breach` compares accumulated spend
        (agent_spawn_sessions) against task.budget_usd / the TaskType
        default. Unlike the tool-call path this explicitly BLOCKs the task
        (see `_handle_task_budget_breach`) before the agent is stopped, and
        notifies the CEO — a breached $ cap needs a human decision (raise
        the cap or leave it blocked), not just a silent re-queue.
        """
        if not self._instances:
            return
        from roboco.config import settings as _settings

        task_budgets_on = _settings.task_budgets_enabled
        async with httpx.AsyncClient(
            timeout=3.0, headers=_system_api_headers()
        ) as client:
            for agent_id, instance in list(self._instances.items()):
                if instance.state not in (
                    AgentState.ACTIVE,
                    AgentState.WAITING_SHORT,
                ):
                    continue
                await self._check_budget_for_agent(
                    client, agent_id, instance, task_budgets_on
                )

    async def _check_budget_for_agent(
        self,
        client: httpx.AsyncClient,
        agent_id: str,
        instance: Any,
        task_budgets_on: bool,
    ) -> None:
        """Stop one agent if its tool-call budget halted OR its task's $
        budget breached. Extracted from ``_sweep_budget_exceeded`` (xenon
        complexity budget) — see that method's docstring for the two triggers.
        """
        url = f"http://roboco-agent-{agent_id}:{SDK_PORT}/budget/status"
        data = await self._fetch_budget_status(client, url, agent_id)
        task_breach = await self._maybe_task_budget_breach(instance, task_budgets_on)
        tool_call_halt = bool(data and data.get("halt"))
        if not tool_call_halt and task_breach is None:
            return
        await self._stop_budget_exceeded_agent(agent_id, instance, data, task_breach)

    async def _maybe_task_budget_breach(
        self, instance: Any, task_budgets_on: bool
    ) -> tuple[float, float] | None:
        """``_task_budget_breach`` for the instance's task, or None inert."""
        if not task_budgets_on or not instance.current_task_id:
            return None
        return await self._task_budget_breach(instance.current_task_id)

    async def _resolve_budget_stop_reason(
        self,
        agent_id: str,
        instance: Any,
        data: dict[str, Any] | None,
        task_breach: tuple[float, float] | None,
    ) -> str:
        """Block + notify on a task breach, else log the tool-call halt.

        Either way returns the ``stop_reason`` the caller passes to
        ``stop_agent``.
        """
        if task_breach is None or not instance.current_task_id:
            logger.warning(
                "Agent budget exceeded; terminating container",
                agent_id=agent_id,
                total_calls=data.get("total") if data else None,
                halt_threshold=data.get("halt_threshold") if data else None,
            )
            return "budget_sweep"
        cap_usd, spend_usd = task_breach
        logger.warning(
            "Task budget exceeded; blocking task and terminating container",
            agent_id=agent_id,
            task_id=instance.current_task_id,
            cap_usd=cap_usd,
            spend_usd=round(spend_usd, 4),
        )
        await self._handle_task_budget_breach(
            instance.current_task_id, cap_usd=cap_usd, spend_usd=spend_usd
        )
        return "budget_exceeded_task"

    async def _stop_budget_exceeded_agent(
        self,
        agent_id: str,
        instance: Any,
        data: dict[str, Any] | None,
        task_breach: tuple[float, float] | None,
    ) -> None:
        """Resolve the stop reason (blocking + notifying on a task breach
        first) then gracefully stop the agent, releasing its claim.

        release_claim=True mirrors the tool-call-halt path. On a task breach
        the task is already BLOCKED (`_resolve_budget_stop_reason` ran the
        block+notify first), so stop_agent's own release-to-pending unclaim
        finds it out of claimed/in_progress and no-ops — it never bounces
        through pending for an instant re-claim to re-burn the same budget.
        """
        stop_reason = await self._resolve_budget_stop_reason(
            agent_id, instance, data, task_breach
        )
        try:
            await self.stop_agent(
                agent_id,
                graceful=True,
                release_claim=True,
                stop_reason=stop_reason,
            )
        except Exception as e:
            logger.warning(
                "Failed to stop budget-exceeded agent",
                agent_id=agent_id,
                error=str(e),
            )

    @staticmethod
    def _parse_supersede_pr(marker_line: str) -> int | None:
        """Extract the contributor PR number from a supersede umbrella marker.

        The marker value is ``pr={n} review={uuid} [author={login}] [closed=1]``
        stored under the ``external_pr_supersede`` key in ``orchestration_markers``
        (migration 041). It used to ride in ``quick_context`` prefixed with the key
        name; the refactor moved it to the typed column but left this parser on
        ``quick_context``, so close-on-land never found the PR number and never
        fired. ``pr=`` is unique among the tokens (``review=`` / ``author=`` /
        ``closed=1`` don't start with ``pr=``), so a whitespace split + prefix match
        is exact.
        """
        for part in marker_line.split():
            if part.startswith("pr="):
                try:
                    return int(part[3:])
                except ValueError:
                    return None
        return None

    @staticmethod
    def _parse_supersede_author(marker_line: str) -> str:
        """Extract the contributor login from a supersede umbrella marker, or ""."""
        for part in marker_line.split():
            if part.startswith("author="):
                return part[len("author=") :]
        return ""

    async def _cut_supersede_branch(
        self,
        *,
        umbrella_id: str,
        project_slug: str,
        pr_number: int,
        project_id: "UUID",
        branch_name: str,
    ) -> None:
        """Background branch cut for a supersede umbrella.

        Resolves the workspace (skip_refresh: the branch is cut from
        ``refs/pull/{n}/head``, not an existing local branch), posts the
        contributor PR comment if it was not posted in the fast path, fetches
        the PR head ref, and pushes the roboco-owned branch. On success
        clears ``branch_pending`` and wakes the dispatcher. On failure
        increments the ``branch_cut_failed`` attempt count and applies a
        backoff so the reconciliation sweep retries; after
        ``_MAX_BRANCH_CUT_ATTEMPTS`` failures the umbrella is BLOCKED (HUMAN
        resolver) and the CEO is notified. Called from
        ``supersede_external_pr`` (asyncio task) and from the reconciliation
        sweep (restart-safe).
        """
        from roboco.db import get_db_context
        from roboco.foundation.policy.content import markers
        from roboco.models.base import TaskStatus
        from roboco.services.git import GitService
        from roboco.services.task import get_task_service
        from roboco.utils.converters import require_uuid

        try:
            async with get_db_context() as db:
                task_service = get_task_service(db)
                umbrella = await task_service.get(require_uuid(umbrella_id))
                if umbrella is None or umbrella.status == TaskStatus.CANCELLED:
                    return
                # If branch_cut_failed but NOT branch_pending, this was
                # unblocked by the CEO after exhaustion — reset for a fresh
                # cut. (The sweep also does this, but guard here too in case
                # the spawn came from supersede_external_pr's retry path.)
                # Commit (not flush) the instant the reset lands: the git
                # work below (workspace resolve + branch cut) can genuinely
                # raise, and get_db_context's except clause rolls back the
                # WHOLE session on any exception, so a flush-only reset would
                # be discarded along with it, so if the comment was already
                # posted on an earlier attempt (skipping that block's own
                # commit) a raise here would revert branch_pending to False
                # and make _fail_supersede_branch_cut's own guard silently
                # drop this attempt's failure bookkeeping.
                if markers.is_branch_cut_failed(
                    umbrella
                ) and not markers.is_branch_pending(umbrella):
                    markers.mark_branch_pending(umbrella)
                    markers.clear_branch_cut_failed(umbrella)
                    markers.clear_branch_cut_next_retry_at(umbrella)
                    await db.commit()
                # Already completed by a prior run (race with sweep).
                if not markers.is_branch_pending(umbrella):
                    return
                git = GitService(db)
                system_id = _foundation.AGENTS["system"].uuid
                # Post the contributor comment if the fast path did not.
                if not markers.is_supersede_comment_posted(umbrella):
                    try:
                        author = self._parse_supersede_author(
                            markers.get_external_pr_supersede(umbrella) or ""
                        )
                        comment = _supersede_author_prefix(
                            author
                        ) + SUPERSEDE_PR_COMMENT.format(branch=branch_name)
                        await git.comment_pull_request(
                            pr_number,
                            project_id=project_id,
                            comment=comment,
                        )
                        markers.mark_supersede_comment_posted(umbrella)
                        # Commit (not flush) the instant the comment lands: the
                        # branch-cut step below is real network git and can
                        # genuinely raise, and get_db_context's except clause
                        # rolls back the WHOLE session on any exception, so a
                        # flush-only marker would be discarded along with it,
                        # and the retry would repost a duplicate comment on the
                        # contributor's public PR.
                        await db.commit()
                    except Exception as exc:
                        logger.warning(
                            "supersede: background contributor comment failed",
                            pr_number=pr_number,
                            error=str(exc),
                        )
                logger.warning(
                    "supersede: cutting roboco branch off untrusted fork PR head",
                    branch=branch_name,
                    pr_number=pr_number,
                    project=project_slug,
                )
                workspace = await git.get_workspace(
                    project_slug, agent_id=system_id, skip_refresh=True
                )
                await git.create_branch_from_pr_head(
                    workspace, project_slug, pr_number, branch_name
                )
                # Success: clear the gate, clear any prior failure markers,
                # and wake the dispatcher.
                markers.clear_branch_pending(umbrella)
                markers.clear_branch_cut_failed(umbrella)
                markers.clear_branch_cut_next_retry_at(umbrella)
                await db.commit()
            self._dispatch_wake.set()
        except Exception as exc:
            logger.error(
                "supersede: background branch cut failed",
                umbrella_id=umbrella_id,
                branch=branch_name,
                pr_number=pr_number,
                error=str(exc),
            )
            await self._fail_supersede_branch_cut(umbrella_id, branch_name, exc)
        finally:
            # F2: release the in-flight slot so the sweep can retry.
            self._supersede_cuts_in_flight.discard(umbrella_id)

    async def _fail_supersede_branch_cut(
        self, umbrella_id: str, branch_name: str, exc: Exception
    ) -> None:
        """Handle a supersede branch-cut failure with retry + backoff.

        On failures below ``_MAX_BRANCH_CUT_ATTEMPTS``: keep
        ``branch_pending``, increment ``branch_cut_failed`` attempt count,
        and set a ``branch_cut_next_retry_at`` backoff so the sweep retries
        without hammering every 60s. On the final failure: clear
        ``branch_pending``, set BLOCKED (HUMAN resolver), and notify the CEO.
        The commit happens BEFORE the notification (F7) so a notify failure
        can't roll back the status transition.
        """
        import time

        from roboco.db import get_db_context
        from roboco.foundation.policy.content import markers
        from roboco.models.base import BlockerResolverType, TaskStatus
        from roboco.services.task import get_task_service
        from roboco.utils.converters import require_uuid

        try:
            async with get_db_context() as db:
                task_service = get_task_service(db)
                umbrella = await task_service.get(require_uuid(umbrella_id))
                if umbrella is None:
                    return
                # If the marker was already cleared (a concurrent sweep
                # succeeded), do not block a finished umbrella.
                if not markers.is_branch_pending(umbrella):
                    return
                attempts = markers.get_branch_cut_attempts(umbrella) + 1
                if attempts < _MAX_BRANCH_CUT_ATTEMPTS:
                    # Retry with backoff: keep branch_pending so the sweep
                    # re-runs the cut after the backoff window expires.
                    backoff = _BRANCH_CUT_BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
                    markers.mark_branch_cut_failed(umbrella, attempts)
                    markers.set_branch_cut_next_retry_at(
                        umbrella, time.time() + backoff
                    )
                    await db.commit()
                    logger.warning(
                        "supersede: branch cut failed, will retry",
                        umbrella_id=umbrella_id,
                        attempts=attempts,
                        backoff_seconds=backoff,
                        error=str(exc),
                    )
                    return
                # Exhausted retries: escalate to BLOCKED.
                umbrella.blocker_resolver_type = BlockerResolverType.HUMAN
                markers.mark_branch_cut_failed(umbrella, attempts)
                markers.clear_branch_pending(umbrella)
                markers.clear_branch_cut_next_retry_at(umbrella)
                await task_service.admin_set_status(
                    require_uuid(umbrella_id),
                    TaskStatus.BLOCKED,
                    actor_role="system",
                )
                await db.commit()
            # F7: notify CEO AFTER the commit so a notify failure can't
            # roll back the BLOCKED transition. Best-effort in its own
            # session.
            try:
                async with get_db_context() as notify_db:
                    from roboco.services.notification_delivery import (
                        get_notification_delivery_service,
                    )

                    notify_task_service = get_task_service(notify_db)
                    umbrella_fresh = await notify_task_service.get(
                        require_uuid(umbrella_id)
                    )
                    if umbrella_fresh is not None:
                        delivery = get_notification_delivery_service(notify_db)
                        await delivery.notify_ceo_of_supersede_branch_cut_failure(
                            task=umbrella_fresh,
                            task_id=require_uuid(umbrella_id),
                            branch=branch_name,
                            error=str(exc),
                        )
                        await notify_db.commit()
            except Exception as notify_exc:
                logger.warning(
                    "supersede: CEO notify failed after branch cut failure",
                    umbrella_id=umbrella_id,
                    error=str(notify_exc),
                )
        except Exception as inner:
            logger.warning(
                "supersede: failed to handle branch cut failure",
                umbrella_id=umbrella_id,
                error=str(inner),
            )

    async def _sweep_rate_limit_probes(self) -> None:
        """One probe pass: check every rate-limited provider.

        For each provider whose estimated_lift_at has passed:
        - Call ``_do_probe(provider)`` to test connectivity.
        - **Success**: clear the tracker, resolve all parked agents, publish
          ``RATE_LIMIT_LIFTED``.
        - **Failure**: increment probe_failures; if the count reaches 10 and
          we haven't already sent a CEO notification for this episode, send
          one now.

        F045: the loop is tracker-driven, but an ``activate()`` failure in the
        in-verb ``i_am_blocked(rate_limited)`` path (or a Redis hiccup) can
        leave agents parked in ``_waiting_records`` for a provider the tracker
        never learned about — so the tracker-listed loop above never probes it
        and the parked agents strand in WAITING_LONG forever. After probing the
        tracker-listed set, scan the in-memory records for any
        ``rate_limit_lifted`` provider the loop did NOT cover and probe it via
        the time-expiry fallback (empty state → ``_too_early_to_probe`` returns
        False → probe now) so ``_on_probe_success`` can resume them. The
        fallback reads only local memory, so it works even when Redis is down.
        """
        from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

        try:
            providers = await RateLimitStateTracker.list_rate_limited_providers()
        except Exception as e:
            logger.warning("Failed to list rate-limited providers", error=str(e))
            providers = []

        probed_providers: set[str] = set()
        for provider, state in providers:
            probed_providers.add(provider)
            try:
                await self._probe_one_provider(provider, state)
            except Exception as e:
                logger.error(
                    "Unhandled error probing provider",
                    provider=provider,
                    error=str(e),
                )

        # Orphan fallback: resume agents parked for a provider the
        # tracker-listed loop above did not cover (activate failed silently or
        # Redis was down at park time). On probe success ``_on_probe_success``
        # clears the tracker (self-healing) and resumes the parked agents.
        orphan_providers: set[str] = set()
        for record in self._waiting_records.values():
            if record.waiting_for != "rate_limit_lifted":
                continue
            prov = record.context.get("provider")
            if prov and prov not in probed_providers:
                orphan_providers.add(prov)
        for provider in orphan_providers:
            try:
                await self._probe_one_provider(provider, {})
            except Exception as e:
                logger.error(
                    "Unhandled error probing orphaned rate-limited provider",
                    provider=provider,
                    error=str(e),
                )

    async def _sandbox_available_services(self, project_slug: str) -> list[str]:
        """Which sandbox services this project's spawn may request on-demand.

        Off (flag or project) => [], byte-for-byte identical to today (the
        legacy `_append_gate_env` prod-creds injection stays active). The
        project lookup is best-effort (a DB hiccup degrades to "no sandbox"
        rather than blocking the spawn). Provisioning itself no longer
        happens here — it is on-demand via the `request_sandbox` do-verb
        (see `ensure_sandbox`), so a spawn never fails on sandbox
        infrastructure.
        """
        if not settings.sandbox_db_enabled:
            return []
        from roboco.db.base import get_db_context
        from roboco.services.project import get_project_service

        try:
            async with get_db_context() as db:
                project = await get_project_service(db).get_by_slug(project_slug)
        except Exception as e:
            logger.warning(
                "sandbox project lookup failed; no sandbox available this spawn",
                project_slug=project_slug,
                error=str(e),
            )
            return []
        return list(project.sandbox_services or []) if project else []

    async def ensure_sandbox(
        self,
        agent_slug: str,
        requested: list[str],
        opted: list[str],
        features: dict[str, list[str]] | None = None,
    ) -> SandboxInfo:
        """Idempotent on-demand provision, called by the `request_sandbox` verb.

        DEVIATION (full-set provisioning): always provisions ``requested |
        opted`` — effectively the project's whole opted-in set, since
        ``opted`` is already a superset of ``requested`` by the verb's own
        guard — rather than only what this particular call named. That makes
        any later subset/superset request within the same opted set a
        guaranteed cache hit; it can never fall through to `provision()`,
        whose pre-clear `teardown()` would otherwise kill a live, mid-use
        container out from under the agent and rotate its creds. The union
        (rather than trusting the caller to always pass the full set) is
        belt-and-suspenders — bounded by the project's own opt-in either way.

        ``features`` (per-service extensions/modules) is the union the verb
        already computed (project standing union per-call, bounded by the opted
        set + the allowlist). The cache-hit check extends to it: a cached
        entry satisfies a new call iff the services are a subset AND every
        requested feature per service is already cached — a feature superset
        re-provisions (rotates creds), mirroring the services-superset case.

        A cache hit is verified live (`SandboxProvisioner.is_live`) before
        being trusted: a container OOM-killed or removed out-of-band evicts
        the stale entry and falls through to a fresh full-set provision
        (new creds — that's the recovery).

        The whole check-cache -> provision -> store section runs under a
        per-agent-slug lock so two concurrent calls for the same agent can't
        race provision()/teardown() on the same containers.
        """
        full = sorted(set(requested) | set(opted))
        feat_map = features or {}
        # Lazily-allocated (no __init__ statement) to keep AgentOrchestrator's
        # constructor under the statement-count gate; getattr guards bare
        # __new__() test doubles that never ran __init__ — same convention
        # as _sandbox_info's own test-double guards elsewhere in this class.
        locks = getattr(self, "_sandbox_locks", None)
        if locks is None:
            locks = {}
            self._sandbox_locks = locks
        lock = locks.setdefault(agent_slug, asyncio.Lock())
        async with lock:
            cached = self._sandbox_info.get(agent_slug)
            if cached is not None and set(full) <= set(cached.services):
                features_covered = all(
                    set(feat_map.get(svc, [])) <= set(cached.services[svc].features)
                    for svc in full
                )
                if features_covered and await self._sandbox.is_live(
                    agent_slug, sorted(cached.services)
                ):
                    return cached
                self._sandbox_info.pop(agent_slug, None)
            info = await self._sandbox.provision(
                agent_slug, full, features=feat_map or None
            )
            self._sandbox_info[agent_slug] = info
            return info

    async def release_sandbox(self, agent_slug: str) -> None:
        """Best-effort teardown at the end of the caller's task engagement.

        Called by the Choreographer's post-verb hook (i_am_done, unclaim,
        i_am_idle, pass_review/fail_review, i_documented) so a
        `request_sandbox`-provisioned sidecar doesn't outlive the work
        that asked for it, instead of only dying with the agent container.
        Idempotent and never raises (`SandboxProvisioner.teardown`'s own
        contract) — the container-removal teardown + janitor sweep remain
        the backstop.

        The overwhelmingly common call has no sandbox at all, so the cache
        dict is checked BEFORE taking the per-agent lock or touching
        docker — the fast path is a single dict lookup, no lock, no
        subprocess.
        """
        if agent_slug not in self._sandbox_info:
            return
        locks = getattr(self, "_sandbox_locks", None)
        if locks is None:
            locks = {}
            self._sandbox_locks = locks
        lock = locks.setdefault(agent_slug, asyncio.Lock())
        async with lock:
            if agent_slug not in self._sandbox_info:
                return
            await self._sandbox.teardown(agent_slug)
            self._sandbox_info.pop(agent_slug, None)

    async def _sandbox_janitor_sweep(self) -> None:
        """Best-effort: remove sandbox containers whose owner agent is gone.

        Cheap (a couple of docker calls) and error-isolated — the provisioner
        itself never raises out of ``janitor_sweep``, so a hiccup here never
        blocks the reaper tick it rides alongside.
        """
        if not settings.sandbox_db_enabled:
            return
        with contextlib.suppress(Exception):
            await self._sandbox.janitor_sweep()
        # Evict ensure_sandbox cache entries for agents the sweep just reaped
        # (owner container gone). getattr guards bare __new__() test doubles.
        cache = getattr(self, "_sandbox_info", None)
        if cache:
            live = getattr(self, "_instances", {})
            for slug in set(cache) - set(live):
                cache.pop(slug, None)

    async def _external_pr_poll_loop(self) -> None:
        """Engine 3: discover inbound PRs and open review tasks.

        Dormant by default — returns immediately unless ``external_pr_enabled``
        OR ``internal_pr_enabled``, so a standard deployment makes no inbound
        GitHub call. This only lists open PRs and records a review task per
        newly-seen reviewable one (external/fork PRs, and — when internal review
        is on — org-repo PRs not tied to an active task); it never fetches or
        runs contributor code (that waits on a human confirmation downstream).
        New review tasks wake the dispatcher.
        """
        if not (settings.external_pr_enabled or settings.internal_pr_enabled):
            return
        from roboco.db import get_db_context

        interval = settings.external_pr_poll_interval_seconds
        while self._running:
            try:
                await asyncio.sleep(interval)
                async with get_db_context(pool="background") as db:
                    ingested = await self._poll_external_prs_once(db)
                if ingested:
                    self._dispatch_wake.set()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("external-PR poll cycle failed")

    @classmethod
    def _projects_one_per_repo(cls, projects: list[Any]) -> list[Any]:
        """One canonical project per distinct repo.

        Many projects can point at the SAME repo — a monorepo product's
        backend/frontend/ux cells each have their own Project mapping to one
        git_url. Polling per-project would then ingest one review task per cell
        for a single external PR (the per-(project,pr) dedup can't see across
        projects). Collapse to one canonical project per repo (deterministic by
        slug so the pick is stable across polls); genuinely separate repos
        (multi-repo) each keep their own. Projects without a git_url are skipped.

        Used by the external-PR discovery path (one review per PR per repo). The
        CI-watch and dep-update loaders use :meth:`_projects_one_per_key` with a
        finer (repo, workflow) / (repo, command) key so a monorepo's per-cell
        workflow / lockfile-command overrides are each sampled once instead of
        collapsing to the canonical cell's value.
        """
        return cls._projects_one_per_key(
            projects,
            key_fn=lambda p: (cls._repo_key(str(getattr(p, "git_url", "") or "")),),
        )

    @staticmethod
    async def _release_pool_connection(db: "AsyncSession") -> None:
        """End ``db``'s current transaction so its pool connection is returned.

        Mirrors content_actions.evidence()'s pool-release commit / the
        background engines' own ``_release_pool_connection`` (2026-07-29
        pool-exhaustion incident): the next read/write reopens a fresh
        transaction on demand. A poisoned session rolls back instead - ending
        the transaction is the point, either way works.
        """
        from sqlalchemy.exc import PendingRollbackError

        try:
            await db.commit()
        except PendingRollbackError:
            await db.rollback()

    async def _poll_external_prs_once(self, db: "AsyncSession") -> int:
        """One discovery pass across active repos; returns tasks ingested.

        Repo-aware: collapses active projects to one canonical project per
        distinct repo (so a monorepo product yields ONE review per PR, not one
        per cell-project), lists each repo's open PRs, and ingests a de-duped
        review task for each reviewable one. A same-repo PR whose head branch
        an active task owns (repo-wide, sibling cell-projects included) is the
        org's own and never ingested, regardless of author — the rest split
        into external/fork PRs and (when internal review is on) org-repo PRs
        opened outside the task flow. Commits once at the end.

        The pool connection is released (``_release_pool_connection``) right
        before each project's PR fetch below, a GitHub HTTP call - per-project,
        not once for the whole batch, so the hold never accumulates across
        every active repo in one poll tick. The resolve (project row + token,
        both DB reads) runs BEFORE the release, not via ``list_open_prs``
        itself: that convenience method does its own ``get_by_slug``/token
        DB reads as its first statements, so calling it right after a release
        would re-check-out a connection and hold it through the very HTTP
        call the release exists to free it for. ``resolve_repo_and_token`` +
        ``list_open_prs_for`` split the DB-resolve from the IO so nothing
        DB-backed runs between the release and the HTTP call.
        """
        from roboco.services.git import GitService
        from roboco.services.project import get_project_service
        from roboco.services.task import get_task_service

        git = GitService(db)
        task_service = get_task_service(db)
        projects = await get_project_service(db).list_all(active_only=True)
        system_id = _foundation.AGENTS["system"].uuid
        allowlist = {a.lower() for a in settings.external_pr_author_allowlist}
        ingested = 0
        for project in self._projects_one_per_repo(projects):
            resolved = await git.resolve_repo_and_token(project.slug)
            await self._release_pool_connection(db)
            if resolved is None:
                continue
            repo_ref, git_token = resolved
            for pr in await git.list_open_prs_for(project.slug, repo_ref, git_token):
                if await self._ingest_pr_if_reviewable(
                    task_service, project, pr, system_id, allowlist
                ):
                    ingested += 1
        await db.commit()
        return ingested

    async def _ingest_pr_if_reviewable(
        self,
        task_service: "TaskService",
        project: Any,
        pr: dict[str, Any],
        system_id: "UUID",
        allowlist: set[str],
    ) -> bool:
        """Ingest a review task for one open PR if it qualifies; True if ingested.

        External/fork PRs (when external review is on and the author is allowed)
        are ingested as ``external_pr``. Org-repo PRs whose head branch no active
        task owns (when internal review is on) are ingested as ``internal_pr`` —
        the org's own in-flight integration PRs are skipped, since a live task
        owns their branch and they already pass QA + PM review.
        """
        if pr.get("number") is None:
            return False
        # The reviewer reviews PRs the org did NOT author. Skip PRs opened by the
        # repo-owner account: a self-review can't post REQUEST_CHANGES (GitHub
        # 422), and re-reviewing the org's own in-flight PRs every poll is noise.
        if pr.get("author_is_owner"):
            return False
        # The org's own in-flight PRs are recognized by BRANCH OWNERSHIP, not
        # author identity: with a GitHub App bound, fleet PRs are authored by
        # <app-slug>[bot] whose author_association is NONE, which the external
        # heuristic below reads as an outsider (2026-07-23 live incident: a
        # same-repo dev-stream PR was ingested as external_pr and adversarially
        # reviewed). A same-repo head branch owned by an active task is ours
        # regardless of who authored the PR, so this check must run BEFORE the
        # author-based classification. Residual: an org PR whose task went
        # terminal with the PR left open falls through to the author heuristics.
        if not pr.get("is_fork") and await task_service.active_task_owns_branch(
            str(pr.get("head_ref") or ""),
            cast("UUID", project.id),
        ):
            return False
        if self._is_external_pr(pr):
            if not settings.external_pr_enabled or not self._pr_author_allowed(
                pr, allowlist
            ):
                return False
            source = "external_pr"
        else:
            if not settings.internal_pr_enabled:
                return False
            source = "internal_pr"
        created = await task_service.ingest_external_pr(
            project_id=cast("UUID", project.id),
            pr=pr,
            created_by=system_id,
            team=Team.BOARD,
            source=source,
        )
        return created is not None

    @staticmethod
    def _pr_author_allowed(pr: dict[str, Any], allowlist: set[str]) -> bool:
        """With a non-empty allowlist, only those GitHub authors are reviewed.

        An empty allowlist (the default) reviews every external PR — the review
        is read-only, so it is safe; the ``confirmed_by_human`` gate still
        protects any later supersede that would run the contributor's code.
        """
        if not allowlist:
            return True
        return (pr.get("user_login") or "").lower() in allowlist

    @staticmethod
    def _is_external_pr(pr: dict[str, Any]) -> bool:
        """A PR the org did not author: a fork head or a non-member author."""
        if pr.get("is_fork"):
            return True
        trusted = {"OWNER", "MEMBER", "COLLABORATOR"}
        assoc = (pr.get("author_association") or "").upper()
        return assoc not in trusted

    async def supersede_external_pr(self, review_task_id: "UUID") -> dict[str, Any]:
        """CEO-authorized takeover of a reviewed external PR.

        Confirms the review task (this CEO action is the human confirmation that
        authorizes running the contributor's code), creates the supersede
        umbrella (committed with a ``branch_pending`` marker), and returns
        immediately. The branch cut (workspace resolve + fetch
        ``refs/pull/{n}/head`` + push) runs in a background task so the CEO
        does not hit the 60s client timeout. The dispatcher skips a
        ``branch_pending`` umbrella so Main PM is not routed until the branch
        is ready. On success the marker is cleared and the dispatcher is
        woken; on failure the sweep retries with exponential backoff up to
        ``_MAX_BRANCH_CUT_ATTEMPTS`` times, after which the umbrella is
        BLOCKED (HUMAN resolver) and the CEO is notified with the recovery
        action (unblock to re-run the cut, or cancel). A reconciliation sweep
        on the sweep tick recovers umbrellas stranded by an orchestrator
        restart and retries failed cuts after the backoff window.
        """
        from roboco.db import get_db_context
        from roboco.foundation.policy.content import markers
        from roboco.models.base import TaskStatus
        from roboco.services.git import GitService
        from roboco.services.project import get_project_service
        from roboco.services.task import get_task_service

        # Serialize concurrent CEO calls (double-click) - the dedup check and
        # the umbrella/branch creation are not atomic across DB sessions.
        async with self._supersede_lock, get_db_context() as db:
            task_service = get_task_service(db)
            review = await task_service.get(review_task_id)
            if review is None or getattr(review, "source", "") not in PR_REVIEW_SOURCES:
                return {"ok": False, "error": "not a PR-review task"}
            if not review.project_id or not review.pr_number:
                return {
                    "ok": False,
                    "error": "review task missing project or pr_number",
                }
            # Review-first: only supersede a PR the org has actually reviewed.
            if review.status != TaskStatus.COMPLETED:
                return {
                    "ok": False,
                    "error": "review not complete - review the PR first",
                }
            project = await get_project_service(db).get(cast("UUID", review.project_id))
            if project is None:
                return {"ok": False, "error": "project not found"}
            pr_number = int(review.pr_number)
            project_id = cast("UUID", review.project_id)
            # Idempotent: a repeat call returns the existing umbrella - no second
            # branch cut, no duplicate cell takeover. Covers branch_pending
            # umbrellas too (a retry during the cut short-circuits here).
            existing = await task_service.find_supersede_umbrella(project_id, pr_number)
            if existing is not None:
                return {
                    "ok": True,
                    "supersede_task_id": str(existing.id),
                    "branch": existing.branch_name,
                    "already_superseded": True,
                }
            system_id = _foundation.AGENTS["system"].uuid
            branch_name = f"feature/main_pm/supersede-pr-{pr_number}"
            # The CEO authorized fetching + finishing the contributor's code.
            review.confirmed_by_human = True
            # Create the umbrella with branch_pending marker BEFORE any git op.
            # The commit here is the point of no return - the umbrella exists
            # and the dispatcher gate (branch_pending) holds it until the
            # background branch cut clears the marker.
            umbrella = await task_service.create_supersede_umbrella(
                review_task_id=review_task_id,
                branch_name=branch_name,
                created_by=system_id,
            )
            # create_supersede_umbrella returns None only if the review task
            # is missing or not a PR-review source - both already validated
            # above. If a race deletes the review between the check and the
            # create, the .id access raises (500, acceptable for that race).
            umbrella = cast("Any", umbrella)
            umbrella_id = str(umbrella.id)
            markers.mark_branch_pending(umbrella)
            await db.flush()
            # Try the contributor comment in the fast path (skip_refresh: the
            # comment only needs the remote URL, not fresh refs). If it fails
            # (workspace not cloned yet, forge error), the background task
            # retries via the supersede_comment_posted marker.
            git = GitService(db)
            try:
                author = self._parse_supersede_author(
                    markers.get_external_pr_supersede(umbrella) or ""
                )
                comment = _supersede_author_prefix(
                    author
                ) + SUPERSEDE_PR_COMMENT.format(branch=branch_name)
                await git.comment_pull_request(
                    pr_number,
                    project_id=project_id,
                    comment=comment,
                )
                markers.mark_supersede_comment_posted(umbrella)
                await db.flush()
            except Exception as exc:
                logger.warning(
                    "supersede: fast-path contributor comment failed, "
                    "deferring to background task",
                    pr_number=pr_number,
                    error=str(exc),
                )
            await db.commit()
            # Claim the in-flight slot BEFORE releasing the supersede lock so a
            # reconciliation sweep tick landing between the commit and the
            # spawn cannot double-spawn a concurrent branch cut.
            self._supersede_cuts_in_flight.add(umbrella_id)
        # Background the slow git ops (workspace clone up to 300s, fetch +
        # checkout + push, each 30s). Fire-and-forget; the reconciliation
        # sweep recovers on restart.
        bg = asyncio.create_task(
            self._cut_supersede_branch(
                umbrella_id=umbrella_id,
                project_slug=project.slug,
                pr_number=pr_number,
                project_id=project_id,
                branch_name=branch_name,
            )
        )
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._bg_tasks.discard)
        return {
            "ok": True,
            "supersede_task_id": umbrella_id,
            "branch": branch_name,
            "status": "cutting_branch",
        }
