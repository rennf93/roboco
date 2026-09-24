"""Per-call tier resolution for the Decisions service.

The resolution chain (spec section 1), evaluated cheaply and in-process on
every call: master flag off -> tier 3 (no verdict, the pre-Decisions
floor). Laya tier enabled (default) and the ``roboco-decisions`` sidecar healthy
-> tier 1. Laya disabled by config or unhealthy -> OpenRouter IF opted in
AND the AI Provider screen has a key -> tier 2. OpenRouter opted in but the
key MISSING -> ONE ack-required CEO notification per boot (a missing key is
config, not an incident) -> fall back to Laya if available, else the floor.

The selected tier is stamped into every ``session_id`` and every log line
so shadow data stays attributable per backend (Laya is the calibration
baseline; the OpenRouter fallback is stamped separately).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast

import httpx
import structlog
from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import ProviderConfigTable
from roboco.models.base import ModelProvider
from roboco.models.notification import CreateNotificationParams
from roboco.services.decisions.client import DecisionsEndpoint

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Cached sidecar health (spec 4): a GET /health probe with a 30s TTL instead
# of a probe per call. An unhealthy sidecar resolves to the OpenRouter tier
# (if opted in) or the floor for the cache window.
_HEALTH_TTL_SECONDS = 30.0
_health_cache: dict[str, tuple[float, bool]] = {}

# Cached OpenRouter fallback key (60s TTL): when the sidecar is unhealthy
# and the fallback is opted in, every decision call would otherwise pay a
# ProviderConfigTable read + Fernet decrypt. The key string lives only in
# process, the same trust domain as the boot-state dict below.
_API_KEY_TTL_SECONDS = 60.0
_API_KEY_CACHE_KEY = "openrouter"
_api_key_cache: dict[str, tuple[float, str | None]] = {}

# Once-per-boot marker for the OpenRouter-key-missing CEO notification
# (dict, not a bare global, to keep mutation explicit).
_BOOT_STATE = {"openrouter_key_warning_sent": False}


async def resolve_endpoint(session: AsyncSession) -> DecisionsEndpoint | None:
    """Pick the tier that should serve this call, or ``None`` for tier 3
    (every caller then does exactly what it did before Decisions existed)."""
    if not settings.decisions_enabled:
        return None

    if settings.decisions_tier_laya_enabled and await _sidecar_healthy():
        return _laya_endpoint()

    openrouter = await _openrouter_endpoint(session)
    if openrouter is not None:
        return openrouter

    # The fallback is opted in but has no key (config, not an incident): one
    # ack-required CEO notification per boot naming the exact fix screen,
    # then resolve to Laya for the process lifetime if it is available.
    if (
        settings.decisions_tier_openrouter_enabled
        and not _BOOT_STATE["openrouter_key_warning_sent"]
    ):
        _BOOT_STATE["openrouter_key_warning_sent"] = True
        await _notify_ceo_missing_openrouter_key(session)

    return None


async def check_openrouter_fallback_at_startup(session: AsyncSession) -> None:
    """Spec 4 startup key check: if the OpenRouter fallback is OPTED IN but
    the AI Provider screen has no OpenRouter key, fire the ONE ack-required
    CEO notification at boot, naming the exact fix screen, and resolve to
    the Laya tier for the process lifetime. Shares the lazy path's
    once-per-boot marker, so the two can never double-fire. Never raises
    (callers still wrap it: startup must never fail because of it)."""
    if not settings.decisions_enabled or not settings.decisions_tier_openrouter_enabled:
        return
    if _BOOT_STATE["openrouter_key_warning_sent"]:
        return
    api_key = await _openrouter_api_key(session)
    if api_key:
        return
    _BOOT_STATE["openrouter_key_warning_sent"] = True
    logger.warning(
        "decisions OpenRouter fallback opted in with no key at startup; "
        "resolving to the Laya tier for the process lifetime"
    )
    await _notify_ceo_missing_openrouter_key(session)


def _laya_endpoint() -> DecisionsEndpoint:
    return DecisionsEndpoint(
        tier="laya",
        base_url=settings.decisions_base_url,
        model=settings.decisions_model,
        timeout_s=settings.decisions_timeout_s,
        api_key=None,
    )


async def _openrouter_endpoint(session: AsyncSession) -> DecisionsEndpoint | None:
    if not settings.decisions_tier_openrouter_enabled:
        return None
    api_key = await _openrouter_api_key(session)
    if not api_key:
        return None
    return DecisionsEndpoint(
        tier="openrouter",
        base_url="https://openrouter.ai",
        model="typesafe/jev-1.13",
        timeout_s=settings.decisions_openrouter_timeout_s,
        api_key=api_key,
    )


async def _openrouter_api_key(session: AsyncSession) -> str | None:
    """Resolve the OpenRouter key exactly like the existing provider: the
    single seeded OPENROUTER row, Fernet-decrypted (spec 4). Cached with a
    60s TTL (same pattern as the health probe above) so an unhealthy-sidecar
    window does not turn every decision call into a config-table read plus
    a decrypt; ``reset_health_cache`` drops it in tests."""
    now = time.monotonic()
    cached = _api_key_cache.get(_API_KEY_CACHE_KEY)
    if cached is not None and now - cached[0] < _API_KEY_TTL_SECONDS:
        return cached[1]
    from roboco.services.provider import get_provider_service

    result = await session.execute(
        select(ProviderConfigTable).where(
            ProviderConfigTable.type == ModelProvider.OPENROUTER
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        _api_key_cache[_API_KEY_CACHE_KEY] = (now, None)
        return None
    try:
        api_key = await get_provider_service(session).get_decrypted_token(
            cast("UUID", row.id)
        )
    except Exception as exc:
        logger.warning(
            "could not decrypt OpenRouter key for the decisions fallback",
            error=str(exc),
        )
        api_key = None
    _api_key_cache[_API_KEY_CACHE_KEY] = (now, api_key)
    return api_key


async def _sidecar_healthy() -> bool:
    """Cached GET /health probe (30s TTL). Network trouble counts as
    unhealthy for the window; never raises."""
    base = settings.decisions_base_url.rstrip("/")
    now = time.monotonic()
    cached = _health_cache.get(base)
    if cached is not None and now - cached[0] < _HEALTH_TTL_SECONDS:
        return cached[1]
    healthy = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{base}/health")
            healthy = response.status_code == httpx.codes.OK
    except (httpx.HTTPError, OSError) as exc:
        logger.debug("roboco-decisions health probe failed", error=str(exc))
    _health_cache[base] = (now, healthy)
    if not healthy:
        logger.warning(
            "roboco-decisions sidecar unhealthy; decisions resolve past the Laya tier",
            base_url=base,
        )
    return healthy


def reset_health_cache() -> None:
    """Test hook: drop the cached probe, the cached fallback key, and the
    boot warning marker."""
    _health_cache.clear()
    _api_key_cache.clear()
    _BOOT_STATE["openrouter_key_warning_sent"] = False


async def _notify_ceo_missing_openrouter_key(session: AsyncSession) -> None:
    """One ack-required CEO notification per boot (spec 4/5). Deduped by the
    once-per-boot marker, never per call."""
    from roboco.models.base import NotificationPriority, NotificationType
    from roboco.services.notification import NotificationService

    try:
        await NotificationService()._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.HIGH,
                from_agent="system",
                to_agents=["ceo"],
                subject="Decisions: OpenRouter fallback opted in but no key",
                body=(
                    "The Decisions service's OpenRouter fallback tier is opted "
                    "in, but no OpenRouter key is configured, so decisions "
                    "resolve to the self-hosted sidecar only. Fix: set the "
                    "OpenRouter key on Settings -> AI Providers. This "
                    "notification fires once per orchestrator boot."
                ),
                requires_ack=True,
                bypass_purpose_dedup=True,
            ),
            db_session=session,
        )
    except Exception as exc:
        logger.warning(
            "failed to send the OpenRouter-key-missing CEO notification",
            error=str(exc),
        )
