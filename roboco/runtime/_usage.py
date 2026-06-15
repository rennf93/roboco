"""AgentOrchestrator mixin for Usage.

Extracted from orchestrator.py to shrink the monolith.
Provides the ``AgentUsageMixin`` mixin class.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from fastapi import status as http_status

from roboco.agents_config import get_agent_role, get_agent_team
from roboco.models.runtime import OrchestratorAgentState as AgentState
from roboco.runtime._helpers import SDK_PORT

logger = structlog.get_logger()


class AgentUsageMixin:
    """Mixin for AgentOrchestrator: Usage."""

    async def _record_spawn_session(
        self,
        config: OrchestratorAgentConfig,
        task_id: str | None,
    ) -> UUID | None:
        """Insert a row into agent_spawn_sessions after a successful spawn.

        Returns the UUID of the created row so the caller can store it on
        the AgentInstance for later direct-by-id lookup in
        _finalize_spawn_session.  Returns None when the insert fails; a
        missing session row must never block the spawn path.
        """
        try:
            from uuid import uuid4 as _uuid4

            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentSpawnSessionTable

            agent_slug = config.agent_id
            team = get_agent_team(agent_slug) or "backend"
            role = get_agent_role(agent_slug) or "developer"

            session_id = _uuid4()
            session_factory = get_session_factory()
            async with session_factory() as db:
                row = AgentSpawnSessionTable(
                    id=session_id,
                    agent_slug=agent_slug,
                    team=team,
                    role=role,
                    model=config.model or "unknown",
                    task_id=task_id,
                    started_at=datetime.now(UTC),
                )
                db.add(row)
                await db.commit()
                logger.debug(
                    "Spawn session recorded",
                    agent_slug=agent_slug,
                    session_id=str(session_id),
                    task_id=task_id,
                )
            return session_id
        except Exception as exc:
            logger.warning(
                "Failed to record spawn session",
                agent_slug=config.agent_id,
                error=str(exc),
            )
            return None

    def _claude_session_id_for(self, agent_id: str) -> str | None:
        """The orchestrator-assigned Claude session id for a running agent."""
        instance = self._instances.get(agent_id)
        return (
            instance.config.claude_session_id if instance and instance.config else None
        )

    @staticmethod
    def _usage_from_transcript(
        agent_id: str, claude_session_id: str | None = None
    ) -> tuple[int, int, int, int]:
        """Sum token usage from the agent's Claude Code transcript.

        The host ``~/.claude`` is mounted into the orchestrator, so transcripts
        are readable here under ``projects/<cwd-dir>/<session-id>.jsonl``. When
        the orchestrator-assigned ``claude_session_id`` is known we locate the
        exact transcript by id across ANY project dir — review/coordinate roles
        run at cwd ``/app`` so theirs lands in ``projects/-app``, not in a
        per-agent ``projects/*-{slug}`` dir. Without an id we fall back to the
        newest transcript in the agent's own workspace dir. Durable fallback for
        the live SDK ``/usage/status`` fetch, which misses for short-lived or
        torn-down agents. Returns zeros when no transcript is found.
        """
        from roboco.agent_sdk.transcript_usage import sum_transcript_usage

        projects = Path.home() / ".claude" / "projects"
        try:
            if claude_session_id:
                by_id = list(projects.glob(f"*/{claude_session_id}.jsonl"))
                if by_id:
                    return sum_transcript_usage(by_id[0])
            jsonl = [
                f
                for d in projects.glob(f"*-{agent_id}")
                if d.is_dir()
                for f in d.glob("*.jsonl")
            ]
            if not jsonl:
                return (0, 0, 0, 0)
            newest = max(jsonl, key=lambda f: f.stat().st_mtime)
            return sum_transcript_usage(newest)
        except OSError:
            return (0, 0, 0, 0)

    async def _resolve_final_token_usage(
        self, agent_id: str
    ) -> tuple[int, int, int, int]:
        """Resolve final token counts for a stopping agent.

        Tries the live SDK ``/usage/status`` first; if that misses — the SDK's
        in-memory counts race container teardown for short-lived agents — it
        falls back to the agent's Claude Code transcript, which is durable and
        mounted into this container. Returns
        ``(input, output, cache_read, cache_write)``.
        """
        tokens = (0, 0, 0, 0)
        sdk_url = f"http://roboco-agent-{agent_id}:{SDK_PORT}/usage/status"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(sdk_url)
                if resp.status_code == http_status.HTTP_200_OK:
                    data = resp.json()
                    tokens = (
                        data.get("tokens_input", 0),
                        data.get("tokens_output", 0),
                        data.get("tokens_cache_read", 0),
                        data.get("tokens_cache_write", 0),
                    )
        except Exception as sdk_exc:
            logger.debug(
                "Could not fetch final token counts from SDK",
                agent_id=agent_id,
                error=str(sdk_exc),
            )

        if not tokens[0] and not tokens[1]:
            tin, tout, cr, cw = self._usage_from_transcript(
                agent_id, self._claude_session_id_for(agent_id)
            )
            if tin or tout:
                tokens = (tin, tout, cr, cw)
        return tokens

    async def _finalize_spawn_session(
        self,
        agent_id: str,
        exit_reason: str = "stopped",
    ) -> None:
        """Close the open agent_spawn_sessions row for this agent.

        Resolves final token counts (live SDK, with a durable transcript
        fallback), calculates cost via the pricing module, then updates the DB
        row with ended_at, token totals, exit_reason, and estimated_cost_usd.
        Errors are caught and logged — finalization must never block stop_agent.
        """
        try:
            from roboco.billing.pricing import calculate_cost
            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentSpawnSessionTable

            # Resolve final token counts (live SDK, with transcript fallback).
            (
                tokens_input,
                tokens_output,
                tokens_cache_read,
                tokens_cache_write,
            ) = await self._resolve_final_token_usage(agent_id)

            # Look up the model and usage_session_id from the running instance config.
            model = "unknown"
            instance = self._instances.get(agent_id)
            if instance and instance.config:
                model = instance.config.model or "unknown"
            usage_session_id = instance.usage_session_id if instance else None

            cost = calculate_cost(
                model=model,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                tokens_cache_read=tokens_cache_read,
                tokens_cache_write=tokens_cache_write,
            )

            session_factory = get_session_factory()
            async with session_factory() as db:
                from sqlalchemy import select, update

                # Prefer a direct lookup by the session UUID captured at spawn
                # time; fall back to the (agent_slug, ended_at IS NULL) query
                # for instances that pre-date the usage_session_id field.
                if usage_session_id is not None:
                    result = await db.execute(
                        select(AgentSpawnSessionTable).where(
                            AgentSpawnSessionTable.id == usage_session_id
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
                if session_row is not None:
                    await db.execute(
                        update(AgentSpawnSessionTable)
                        .where(AgentSpawnSessionTable.id == session_row.id)
                        .values(
                            ended_at=datetime.now(UTC),
                            tokens_input=tokens_input,
                            tokens_output=tokens_output,
                            tokens_cache_read=tokens_cache_read,
                            tokens_cache_write=tokens_cache_write,
                            exit_reason=exit_reason,
                            estimated_cost_usd=cost,
                        )
                    )
                    await db.commit()
                    logger.debug(
                        "Spawn session finalized",
                        agent_id=agent_id,
                        session_id=str(session_row.id),
                        tokens_input=tokens_input,
                        tokens_output=tokens_output,
                        estimated_cost_usd=cost,
                    )
        except Exception as exc:
            logger.warning(
                "Failed to finalize spawn session",
                agent_id=agent_id,
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
        source has any usage yet.
        """
        tokens = await self._fetch_agent_tokens(client, agent_id)
        if tokens is not None:
            return tokens
        transcript = self._usage_from_transcript(
            agent_id, self._claude_session_id_for(agent_id)
        )
        return transcript if any(transcript) else None

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

        async with httpx.AsyncClient(timeout=3.0) as client:
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

                    tokens_input, tokens_output = tokens[0], tokens[1]
                    model = instance.config.model if instance.config else "unknown"

                    # Accumulate per-agent data for the aggregate snapshot.
                    with contextlib.suppress(Exception):
                        from roboco.billing.pricing import calculate_cost

                        agent_cost = calculate_cost(
                            model=model,
                            tokens_input=tokens_input,
                            tokens_output=tokens_output,
                        )
                        _usage_by_agent.append(
                            {
                                "agent_id": agent_id,
                                "input_tokens": tokens_input,
                                "output_tokens": tokens_output,
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
