"""
Cross-vendor second-review selection service.

Backs the in-path PR gate's cross-vendor second-review step (a sibling unit):
a high-stakes task gets its PR gate review from a model provider that
DIFFERS from whichever provider authored/QA'd it, so a single vendor's blind
spot never reviews itself. Two independent pieces, both settings-driven:

  * `is_high_stakes` — the risk-threshold classifier (feature-flagged, OFF by
    default; every threshold parameter lives in `Settings`, nothing here is
    hardcoded).
  * `resolve_second_review_provider` — a pure resolver that picks an enabled
    provider different from the authoring one, using whatever the fleet's
    provider-routing seam (`ProviderService` / `model_assignments`) currently
    reports as enabled — never a hardcoded vendor list. When only one
    provider is enabled fleet-wide it returns an explicit skip carrying an
    evidence note; it never raises.

`SecondReviewService` is the thin DB-backed entry point the gate actually
calls: it reads the fleet's enabled providers via `ProviderService` (the same
table `ModelRoutingService` resolves agent routing from) and delegates to the
pure resolver above.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from roboco.config import settings
from roboco.services.base import BaseService
from roboco.services.llm import ModelRoutingService
from roboco.services.provider import ProviderService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.base import ModelProvider
    from roboco.models.task import Task


@dataclass(frozen=True)
class SecondReviewSelection:
    """Outcome of resolving a cross-vendor second-review provider.

    Exactly one of `provider` / `skip_reason` is meaningful: a resolved
    selection carries `provider` (skipped=False, skip_reason=None); a skip
    carries `skip_reason` (an evidence note explaining why, provider=None,
    skipped=True). Consumers should branch on `skipped`, not on `provider`
    being `None`.
    """

    provider: ModelProvider | None
    skipped: bool
    skip_reason: str | None = None

    @classmethod
    def resolved(cls, provider: ModelProvider) -> SecondReviewSelection:
        return cls(provider=provider, skipped=False, skip_reason=None)

    @classmethod
    def skip(cls, reason: str) -> SecondReviewSelection:
        return cls(provider=None, skipped=True, skip_reason=reason)


def is_high_stakes(
    *,
    priority: int,
    adds_migration: bool,
    touches_shared: bool,
    security_relevant: bool,
) -> bool:
    """Risk-threshold check: does this task qualify for cross-vendor review?

    Purely a function of the passed-in signals + `Settings` — every threshold
    (the flag itself, the priority cutoff, and which signals count) is
    settings-driven, never hardcoded here. Returns `False` unconditionally
    when `settings.cross_vendor_review_enabled` is off (the default).
    """
    if not settings.cross_vendor_review_enabled:
        return False
    if priority <= settings.cross_vendor_review_max_priority:
        return True
    if settings.cross_vendor_review_flag_migrations and adds_migration:
        return True
    if settings.cross_vendor_review_flag_protected_surface and touches_shared:
        return True
    return bool(settings.cross_vendor_review_flag_security and security_relevant)


def task_is_high_stakes(task: Task) -> bool:
    """Convenience wrapper of `is_high_stakes` for a real `Task` row.

    Security-relevance has no dedicated `Task` field, so it is derived from a
    settings-configurable keyword match against the title + description —
    same "settings-driven, not hardcoded" rule as every other threshold.
    """
    haystack = f"{task.title}\n{task.description}".lower()
    security_relevant = any(
        keyword in haystack
        for keyword in settings.cross_vendor_review_security_keywords
    )
    return is_high_stakes(
        priority=task.priority,
        adds_migration=task.adds_migration,
        touches_shared=task.touches_shared,
        security_relevant=security_relevant,
    )


def resolve_second_review_provider(
    authoring_provider: ModelProvider,
    enabled_providers: Sequence[ModelProvider],
) -> SecondReviewSelection:
    """Pick an enabled provider that differs from `authoring_provider`.

    Deterministic: picks the first differing candidate in `enabled_providers`
    order (callers pass a stably-ordered sequence — see
    `SecondReviewService.enabled_providers`). Never raises: when no differing
    enabled provider exists (the fleet runs a single vendor — the common
    default, since Anthropic ships enabled-by-default and every other
    provider ships disabled until configured), it returns an explicit skip
    with an evidence note instead.
    """
    for candidate in enabled_providers:
        if candidate != authoring_provider:
            return SecondReviewSelection.resolved(candidate)
    return SecondReviewSelection.skip(
        f"Only one model provider ({authoring_provider.value}) is enabled "
        "fleet-wide; cross-vendor second review is skipped."
    )


class SecondReviewService(BaseService):
    """DB-backed entry point: resolves the fleet's enabled providers via the
    existing provider-routing seam, then delegates to the pure resolver."""

    service_name: ClassVar[str] = "second_review"

    async def enabled_providers(self) -> list[ModelProvider]:
        """Distinct enabled provider types, in `ProviderService` list order
        (by name) — never a hardcoded vendor list."""
        rows = await ProviderService(self.session).list_providers(
            include_disabled=False
        )
        seen: list[ModelProvider] = []
        for row in rows:
            if row.type not in seen:
                seen.append(row.type)
        return seen

    async def resolve_second_reviewer(
        self, authoring_provider: ModelProvider
    ) -> SecondReviewSelection:
        """Resolve a second-review provider given the authoring provider."""
        enabled = await self.enabled_providers()
        return resolve_second_review_provider(authoring_provider, enabled)

    async def resolve_second_reviewer_for_agent(
        self, agent_slug: str, *, complexity: str | None = None
    ) -> SecondReviewSelection:
        """Convenience: resolve the authoring agent's provider via
        `ModelRoutingService` (the existing multi-provider routing seam),
        then pick a differing enabled provider (or skip)."""
        route = await ModelRoutingService(self.session).resolve_for_agent(
            agent_slug, complexity=complexity
        )
        return await self.resolve_second_reviewer(route.provider_type)


def get_second_review_service(session: AsyncSession) -> SecondReviewService:
    """Factory mirroring the other `get_*_service` helpers in this package."""
    return SecondReviewService(session)
