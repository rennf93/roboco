"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: rate_limit_probe)."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from roboco.config import settings
from roboco.runtime.orchestrator import (
    _ANTHROPIC_PROBE_BASE,
    _CEO_NOTIFY_THRESHOLD,
    _HTTP_MULTIPLE_CHOICES,
    _HTTP_OK,
    _MINUTES_PER_HOUR,
    _PROBE_GIVE_UP_THRESHOLD,
    _PROBE_TIMEOUT_SECONDS,
    AgentState,
    logger,
)

if TYPE_CHECKING:
    from roboco.models.runtime import (
        AgentInstance,
        WaitingRecord,
    )


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class RateLimitProbeEngine(_Base):
    """Mixin holding the "rate_limit_probe" methods moved out of AgentOrchestrator."""

    async def _delete_waiting_record(self, agent_id: str) -> None:
        """Delete a persisted waiting record when its wait resolves."""
        try:
            from sqlalchemy import delete

            from roboco.db.base import get_session_factory
            from roboco.db.tables import WaitingRecordTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                await db.execute(
                    delete(WaitingRecordTable).where(
                        WaitingRecordTable.agent_id == agent_id
                    )
                )
                await db.commit()
        except Exception as e:
            logger.error(
                "Failed to delete waiting record",
                agent_id=agent_id,
                error=str(e),
            )

    async def resolve_wait(
        self,
        agent_id: str,
        resolution: dict[str, Any],
    ) -> AgentInstance | None:
        """
        Resolve a wait condition and respawn the agent.

        Args:
            agent_id: The waiting agent
            resolution: Details about the resolution

        Returns:
            Respawned AgentInstance or None
        """
        if agent_id not in self._waiting_records:
            return None

        # #71: a lingering record (a prior resume whose liveness confirmation
        # hasn't torn it down yet) must not double-spawn an already-active agent.
        if self._is_agent_active(agent_id):
            return None

        record = self._waiting_records[agent_id]

        # Generate resume prompt
        resume_prompt = self._generate_resume_prompt(record, resolution)

        # Preserve the original git_context from the prior instance so the
        # respawned agent keeps the same workspace mount path.
        prior = self._instances.get(agent_id)
        prior_git_context = prior.config.git_context if prior and prior.config else None

        # Respawn FIRST, then tear down the record only once a container actually
        # launched. The old order deleted the record (in-memory + durable) before
        # the spawn: a re-park during the resume window — the provider's rate limit
        # lifts then immediately re-limits, or a second provider limit lands —
        # bails spawn with an OFFLINE instance (the parked-provider short-circuit),
        # and deleting the record first orphaned the agent. With no record the
        # probe-resume loop can never revive it and the spawn gate bails every
        # tick, so the agent is lost until the operator intervenes. Keeping the
        # record through a bail lets the next probe-success re-attempt the resume.
        try:
            instance = await self.spawn_agent(
                agent_id=agent_id,
                initial_prompt=resume_prompt,
                task_id=record.task_id,
                git_context=prior_git_context,
                spawned_by="resolve_wait",
            )
        except Exception:
            # Spawn failed (e.g. readiness refused → task auto-blocked). Tear
            # down the record so the probe loop doesn't keep re-resuming a task
            # that has moved to a different state; the blocked-task path takes
            # over. This matches the pre-fix behavior where the record was
            # deleted before the spawn attempt.
            del self._waiting_records[agent_id]
            await self._delete_waiting_record(agent_id)
            raise
        if instance is None or instance.state == AgentState.OFFLINE:
            # Spawn bailed without launching (provider re-parked). Keep the record
            # so the probe-resume loop re-attempts on the next clear.
            return instance
        if record.waiting_for == "rate_limit_lifted":
            # #71: don't tear down the record on a bare launch — a container that
            # launches then dies immediately would orphan the task until the
            # reaper's TTL. Keep the record past the launch and confirm liveness
            # in the background; if the container dies the probe-resume orphan
            # fallback re-resumes within a tick instead of waiting the full TTL.
            self._schedule_bg(self._confirm_resume_liveness(agent_id))
            return instance
        del self._waiting_records[agent_id]
        await self._delete_waiting_record(agent_id)
        return instance

    async def _confirm_resume_liveness(self, agent_id: str) -> None:
        """Tear down a resumed agent's WaitingRecord once it is confirmed alive.

        A container that launches then dies immediately must not strand its task
        until the reaper's TTL: the record is kept past the launch (``resolve_wait``
        schedules this) and deleted only once the agent is still active past a
        short confirmation window. If the container died, the record survives so
        the probe-resume orphan fallback re-resumes on the next tick (#71). The
        confirmation reads ``_is_agent_active`` — the same signal the spawn gate
        trusts — so a container the health loop has marked dead keeps its record.
        Best-effort: a delete error is swallowed (the in-memory record is gone
        either way once the process exits, and the orphan fallback is in-memory).
        """
        if agent_id not in self._waiting_records:
            return
        await asyncio.sleep(self._resume_confirm_delay)
        if not self._is_agent_active(agent_id):
            return  # container died — keep the record for the orphan fallback
        del self._waiting_records[agent_id]
        try:
            await self._delete_waiting_record(agent_id)
        except Exception:
            logger.warning(
                "resume-liveness confirm failed to delete the durable record",
                agent_id=agent_id,
            )

    def _generate_resume_prompt(
        self,
        record: WaitingRecord,
        resolution: dict[str, Any],
    ) -> str:
        """Generate a resume prompt for a respawning agent."""
        if record.waiting_for == "blocker_resolution":
            return f"""
You were working on TASK-{record.task_id} and got blocked.
The blocker has been resolved: {resolution.get("details", "Resolved")}

Resume by:
1. Reading your checkpoint from .tasks/active/TASK-{record.task_id}/
2. Call unblock("{record.task_id}")
3. Continue from where you left off
"""

        elif record.waiting_for == "qa_result":
            if resolution.get("passed"):
                return f"""
TASK-{record.task_id} has passed QA review.
The task is now awaiting documentation.
You may return to scanning for new work with give_me_work().
"""
            else:
                return f"""
TASK-{record.task_id} needs revision based on QA feedback.
QA notes: {resolution.get("notes", "See task for details")}

Resume by:
1. Reading the QA feedback
2. Updating your TODOs to address each issue
3. Making the fixes
4. Re-submitting for QA
"""

        elif record.waiting_for == "answer":
            return f"""
You asked a question about TASK-{record.task_id}:
Your question: {record.context.get("question", "Unknown")}
Answer received: {resolution.get("answer", "Unknown")}

Resume by incorporating this information and continuing from where you stopped.
"""

        elif record.waiting_for == "assignment":
            return f"""
You have been assigned a new task: TASK-{resolution.get("task_id")}

Start by:
1. Review the task details provided in your briefing / context_briefing
2. Follow the standard workflow: UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES
"""

        else:
            return f"Resuming. Wait condition '{record.waiting_for}' resolved."

    async def _rate_limit_probe_loop(self) -> None:
        """Background loop: probe rate-limited providers every ~30 seconds.

        Runs independently of the 60-second session/notification sweeper so
        rate limits can be cleared on their own cadence without blocking
        other sweep work.
        """
        probe_interval = 30  # seconds
        while self._running:
            try:
                await asyncio.sleep(probe_interval)
                await self._sweep_rate_limit_probes()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Rate-limit probe loop error", error=str(e))

    @staticmethod
    def _too_early_to_probe(state: dict[str, Any]) -> bool:
        """True while the estimated lift time (activated_at + retry_after) is future.

        Missing or malformed timestamps fall through to allow the probe.
        """
        activated_at_raw = state.get("activated_at")
        retry_after = state.get("retry_after")
        if not activated_at_raw or retry_after is None:
            return False
        try:
            activated_at = datetime.fromisoformat(activated_at_raw)
        except (ValueError, TypeError):
            return False
        return datetime.now(UTC) < activated_at + timedelta(seconds=retry_after)

    def _parked_agents_for(self, provider: str) -> list[str]:
        """Agent slugs parked waiting for *provider*'s rate limit to lift."""
        return [
            agent_id
            for agent_id, record in list(self._waiting_records.items())
            if record.waiting_for == "rate_limit_lifted"
            and record.context.get("provider") == provider
        ]

    async def _on_probe_success(self, provider: str, tracker: Any) -> None:
        """Clear the limit, resume parked agents, publish RATE_LIMIT_LIFTED."""
        logger.info("Rate-limit probe succeeded; clearing provider", provider=provider)
        await tracker.clear()
        # New episodes should get a fresh CEO notification.
        self._rate_limit_ceo_notified.discard(provider)
        resumed = self._parked_agents_for(provider)
        for agent_id in resumed:
            with contextlib.suppress(Exception):
                await self.resolve_wait(
                    agent_id,
                    {
                        "reason": "rate_limit_lifted",
                        "provider": provider,
                        "lifted_at": datetime.now(UTC).isoformat(),
                    },
                )
        with contextlib.suppress(Exception):
            from roboco.events import get_event_bus
            from roboco.models.events import Event, EventType

            await get_event_bus().publish(
                Event(
                    type=EventType.RATE_LIMIT_LIFTED,
                    data={
                        "provider": provider,
                        "resumedAgents": resumed,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
            )
        logger.info(
            "RATE_LIMIT_LIFTED published",
            provider=provider,
            resumed_agents=len(resumed),
        )

    async def _on_probe_failure(
        self, provider: str, tracker: Any, activated_at_raw: str | None
    ) -> None:
        """Count a failed probe; notify the CEO once at the failure threshold.

        F094 escape hatch: past ``_PROBE_GIVE_UP_THRESHOLD`` persistent failures
        the probe endpoint itself is the problem (misconfigured URL / removed API
        key / network partition to the probe host) while the provider may be fine
        for real workloads. Holding the park any longer strands every agent on
        the provider forever with only a one-shot CEO notification. Fall back to
        the same time-expiry optimism the unprobeable-provider path uses: clear
        the park and resume. If the provider is genuinely still down the real
        workload attempts re-park via the 429/5xx path, so this is bounded burn —
        strictly better than a silent forever-strand. ``_on_probe_success``
        clears the tracker (so the loop won't probe this provider again until a
        real 429 re-parks) and discards the CEO-notified flag (a fresh episode
        later gets a fresh notification).
        """
        failure_count = await tracker.increment_probe_failures()
        logger.debug(
            "Rate-limit probe failed", provider=provider, probe_failures=failure_count
        )
        if (
            failure_count >= _CEO_NOTIFY_THRESHOLD
            and provider not in self._rate_limit_ceo_notified
        ):
            self._rate_limit_ceo_notified.add(provider)
            await self._notify_rate_limit_ceo(
                provider=provider,
                activated_at_str=activated_at_raw or "unknown",
                paused_agent_count=len(self._parked_agents_for(provider)),
            )
        if failure_count >= _PROBE_GIVE_UP_THRESHOLD:
            logger.warning(
                "Rate-limit probe persistently failing; giving up on the probe "
                "and falling back to time-expiry optimism (clearing the park + "
                "resuming parked agents). If the provider is genuinely still down "
                "they will re-park via the real 429/5xx path.",
                provider=provider,
                probe_failures=failure_count,
            )
            await self._on_probe_success(provider, tracker)

    async def _probe_one_provider(self, provider: str, state: dict[str, Any]) -> None:
        """Probe a single rate-limited provider and handle the outcome."""
        if self._too_early_to_probe(state):
            return  # Wait until after the estimated lift time.
        tracker = self._make_tracker(provider)
        if await self._do_probe(provider):
            await self._on_probe_success(provider, tracker)
        else:
            await self._on_probe_failure(provider, tracker, state.get("activated_at"))

    @staticmethod
    def _probe_target(provider: str) -> tuple[str | None, dict[str, str]]:
        """Resolve the (url, headers) for a free liveness probe of ``provider``.

        Returns ``(None, {})`` when the provider can't be probed — an unknown
        provider, or Anthropic with no API key configured. The caller then
        falls back to time-expiry optimism rather than parking forever.
        """
        p = provider.lower()
        if p == "anthropic":
            key = settings.anthropic_api_key
            if not key:
                return None, {}
            return (
                f"{_ANTHROPIC_PROBE_BASE}/v1/models",
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
            )
        if p.startswith("ollama"):
            return f"{settings.ollama_base_url.rstrip('/')}/api/tags", {}
        return None, {}

    async def _do_probe(self, provider: str) -> bool:
        """Return True if ``provider`` is accepting requests again.

        Makes a free, unmetered liveness call — Anthropic ``GET /v1/models``
        or Ollama ``GET /api/tags`` — and treats only a 2xx response as
        recovered. Any error status keeps the provider parked: a 429 (still
        rate-limited) **and** a 5xx (still overloaded) alike — resuming on a
        non-2xx would march parked agents straight back into the failure. A
        network error stays parked too (retry next sweep). When the provider
        can't be probed (no key / unknown), fall back to time-expiry optimism:
        the caller only reaches this after ``estimated_lift_at`` has passed.

        Injectable boundary — tests monkeypatch this to force outcomes.
        """
        url, headers = self._probe_target(provider)
        if url is None:
            return True  # cannot probe — trust the elapsed retry_after window
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.debug(
                "Provider-recovery probe request failed",
                provider=provider,
                error=str(exc),
            )
            return False  # unreachable — stay parked, retry on the next sweep
        return _HTTP_OK <= resp.status_code < _HTTP_MULTIPLE_CHOICES

    async def _notify_rate_limit_ceo(
        self,
        provider: str,
        activated_at_str: str,
        paused_agent_count: int,
    ) -> None:
        """Send a high-priority notification to the CEO about a persistent rate limit.

        Fires once per rate-limit episode. Follows the same pattern as
        ``_notify_stranded_agent`` — direct DB insert + delivery.deliver().
        """
        try:
            from roboco.db.base import get_session_factory
            from roboco.db.tables import NotificationTable
            from roboco.models.base import (
                AgentRole,
                NotificationPriority,
                NotificationType,
            )
            from roboco.services.notification_delivery import (
                get_notification_delivery_service,
            )
            from roboco.services.repositories.query_helpers import get_agent_by_role
            from roboco.utils.converters import require_uuid

            # Compute human-friendly duration
            duration_desc = "unknown duration"
            try:
                activated_at = datetime.fromisoformat(activated_at_str)
                elapsed = datetime.now(UTC) - activated_at
                total_minutes = int(elapsed.total_seconds() / 60)
                if total_minutes < _MINUTES_PER_HOUR:
                    duration_desc = f"{total_minutes} minute(s)"
                else:
                    duration_desc = (
                        f"{total_minutes // _MINUTES_PER_HOUR}h "
                        f"{total_minutes % _MINUTES_PER_HOUR}m"
                    )
            except (ValueError, TypeError):
                pass

            session_factory = get_session_factory()
            async with session_factory() as db:
                ceo = await get_agent_by_role(db, AgentRole.CEO)
                if ceo is None:
                    logger.warning(
                        "CEO agent not found; skipping rate-limit CEO notification",
                        provider=provider,
                    )
                    return
                notification = NotificationTable(
                    type=NotificationType.ALERT,
                    priority=NotificationPriority.HIGH,
                    from_agent=ceo.id,
                    to_agents=[ceo.id],
                    subject=f"Rate limit persisting: {provider}",
                    body=(
                        f"Provider '{provider}' has been rate-limited for "
                        f"{duration_desc}. "
                        f"{paused_agent_count} agent(s) are currently paused. "
                        f"10 consecutive probe attempts have failed. "
                        f"Manual intervention may be required."
                    ),
                    requires_ack=True,
                )
                db.add(notification)
                await db.flush()
                delivery = get_notification_delivery_service(db)
                await delivery.deliver(require_uuid(notification.id))
                await db.commit()
            logger.info(
                "Rate-limit CEO notification sent",
                provider=provider,
                paused_agents=paused_agent_count,
            )
        except Exception as e:
            logger.error(
                "Failed to send rate-limit CEO notification",
                provider=provider,
                error=str(e),
            )
