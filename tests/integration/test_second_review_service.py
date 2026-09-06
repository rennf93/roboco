"""``SecondReviewService`` against a real, seeded ``provider_configs`` table
(round-1 pr_gate finding F-75ec4502) — every existing gate test monkeypatches
``get_second_review_service`` out, so the actual DB-backed selection code
(``enabled_providers`` / ``resolve_second_reviewer`` /
``resolve_second_reviewer_for_agent`` / ``get_second_review_service``) had
never run under test. This also covers the resolver ``_authoring_providers``
(pr_gate.py) now calls via ``resolve_for_agent`` for finding F-8b364f1c.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.db.tables import ProviderConfigTable
from roboco.models.base import ModelProvider
from roboco.services.second_review import (
    SecondReviewService,
    get_second_review_service,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# A real static agent slug (roboco/foundation/identity.py) with no seeded
# model_assignments row — ModelRoutingService.resolve_for_agent falls back
# to the legacy Anthropic path for it, exactly as it does in production for
# any agent with no explicit assignment.
_UNASSIGNED_AGENT_SLUG = "be-dev-1"


async def _seed_provider(
    session: AsyncSession,
    *,
    name: str,
    provider_type: ModelProvider,
    enabled: bool = True,
) -> ProviderConfigTable:
    row = ProviderConfigTable(name=name, type=provider_type, enabled=enabled)
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_enabled_providers_dedupes_by_type_in_name_order(
    db_session: AsyncSession,
) -> None:
    """Two enabled rows of the SAME provider type collapse to one entry;
    a disabled row is excluded; ordering follows ProviderService's own
    name-ascending list order."""
    await _seed_provider(
        db_session, name="z-anthropic", provider_type=ModelProvider.ANTHROPIC
    )
    await _seed_provider(
        db_session, name="a-anthropic", provider_type=ModelProvider.ANTHROPIC
    )
    await _seed_provider(db_session, name="m-grok", provider_type=ModelProvider.GROK)
    await _seed_provider(
        db_session, name="b-gemini", provider_type=ModelProvider.GEMINI, enabled=False
    )

    result = await SecondReviewService(db_session).enabled_providers()

    # name order: a-anthropic, m-grok, z-anthropic -> types deduped in that
    # first-seen order, disabled gemini excluded entirely.
    assert result == [ModelProvider.ANTHROPIC, ModelProvider.GROK]


@pytest.mark.asyncio
async def test_resolve_second_reviewer_picks_a_differing_enabled_provider(
    db_session: AsyncSession,
) -> None:
    await _seed_provider(
        db_session, name="anthropic", provider_type=ModelProvider.ANTHROPIC
    )
    await _seed_provider(db_session, name="grok", provider_type=ModelProvider.GROK)

    selection = await SecondReviewService(db_session).resolve_second_reviewer(
        ModelProvider.ANTHROPIC
    )

    assert selection.skipped is False
    assert selection.provider == ModelProvider.GROK


@pytest.mark.asyncio
async def test_resolve_second_reviewer_skips_when_only_authoring_provider_enabled(
    db_session: AsyncSession,
) -> None:
    await _seed_provider(
        db_session, name="anthropic", provider_type=ModelProvider.ANTHROPIC
    )

    selection = await SecondReviewService(db_session).resolve_second_reviewer(
        ModelProvider.ANTHROPIC
    )

    assert selection.skipped is True
    assert selection.provider is None
    assert selection.skip_reason is not None


@pytest.mark.asyncio
async def test_resolve_second_reviewer_for_agent_resolves_authoring_via_routing(
    db_session: AsyncSession,
) -> None:
    """An agent with no model_assignments row routes through the legacy
    Anthropic path (ModelRoutingService's own fallback) -- with a second
    enabled provider in the fleet, the second reviewer differs from it."""
    await _seed_provider(
        db_session, name="anthropic", provider_type=ModelProvider.ANTHROPIC
    )
    await _seed_provider(db_session, name="gemini", provider_type=ModelProvider.GEMINI)

    selection = await SecondReviewService(db_session).resolve_second_reviewer_for_agent(
        _UNASSIGNED_AGENT_SLUG
    )

    assert selection.skipped is False
    assert selection.provider == ModelProvider.GEMINI


@pytest.mark.asyncio
async def test_resolve_second_reviewer_for_agent_skips_single_vendor_fleet(
    db_session: AsyncSession,
) -> None:
    await _seed_provider(
        db_session, name="anthropic", provider_type=ModelProvider.ANTHROPIC
    )

    selection = await SecondReviewService(db_session).resolve_second_reviewer_for_agent(
        _UNASSIGNED_AGENT_SLUG
    )

    assert selection.skipped is True
    assert selection.provider is None


def test_get_second_review_service_factory_returns_a_service(
    db_session: AsyncSession,
) -> None:
    service = get_second_review_service(db_session)
    assert isinstance(service, SecondReviewService)
    assert service.session is db_session
