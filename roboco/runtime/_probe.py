"""AgentOrchestrator mixin for Probe.

Extracted from orchestrator.py to shrink the monolith.
Provides the ``AgentRateLimitMixin`` mixin class.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from roboco.config import settings
from roboco.runtime._helpers import (
    _ANTHROPIC_PROBE_BASE,
    _CEO_NOTIFY_THRESHOLD,
    _HTTP_TOO_MANY_REQUESTS,
    _PROBE_TIMEOUT_SECONDS,
)

logger = structlog.get_logger()


class AgentRateLimitMixin:
    """Mixin for AgentOrchestrator: Probe.
    """

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

    async def _sweep_rate_limit_probes(self) -> None:
        """One probe pass: check every rate-limited provider.

        For each provider whose estimated_lift_at has passed:
        - Call ``_do_probe(provider)`` to test connectivity.
        - **Success**: clear the tracker, resolve all parked agents, publish
          ``RATE_LIMIT_LIFTED``.
        - **Failure**: increment probe_failures; if the count reaches 10 and
          we haven't already sent a CEO notification for this episode, send
          one now.
        """
        from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

        try:
            providers = await RateLimitStateTracker.list_rate_limited_providers()
        except Exception as e:
            logger.warning("Failed to list rate-limited providers", error=str(e))
            return

        for provider, state in providers:
            try:
                await self._probe_one_provider(provider, state)
            except Exception as e:
                logger.error(
                    "Unhandled error probing provider",
                    provider=provider,
                    error=str(e),
                )

    def _make_tracker(self, provider: str) -> Any:
        """Return a RateLimitStateTracker for *provider*.

        Extracted as its own method so unit tests can monkeypatch it to
        return an async mock without needing to intercept lazy imports.
        """
        from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

        return RateLimitStateTracker(provider)

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
        """Count a failed probe; notify the CEO once at the failure threshold."""
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
        """Return True if ``provider`` is accepting requests again (not 429).

        Makes a free, unmetered liveness call — Anthropic ``GET /v1/models``
        or Ollama ``GET /api/tags`` — and treats any non-429 response as the
        rate limit having lifted. A 429 keeps the provider parked; a network
        error stays parked too (retry next sweep). When the provider can't be
        probed (no key / unknown), fall back to time-expiry optimism: the
        caller only reaches this after ``estimated_lift_at`` has passed.

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
                "Rate-limit probe request failed", provider=provider, error=str(exc)
            )
            return False  # unreachable — stay parked, retry on the next sweep
        return resp.status_code != _HTTP_TOO_MANY_REQUESTS

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
            from sqlalchemy import select as _select

            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentTable, NotificationTable
            from roboco.models.base import (
                AgentRole,
                NotificationPriority,
                NotificationType,
            )
            from roboco.services.notification_delivery import (
                get_notification_delivery_service,
            )
            from roboco.utils.converters import require_uuid

            # Compute human-friendly duration
            duration_desc = "unknown duration"
            try:
                activated_at = datetime.fromisoformat(activated_at_str)
                elapsed = datetime.now(UTC) - activated_at
                total_minutes = int(elapsed.total_seconds() / 60)
                if total_minutes < 60:  # noqa: PLR2004
                    duration_desc = f"{total_minutes} minute(s)"
                else:
                    duration_desc = f"{total_minutes // 60}h {total_minutes % 60}m"
            except (ValueError, TypeError):
                pass

            session_factory = get_session_factory()
            async with session_factory() as db:
                ceo_result = await db.execute(
                    _select(AgentTable).where(AgentTable.role == AgentRole.CEO)
                )
                ceo = ceo_result.scalar_one_or_none()
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

