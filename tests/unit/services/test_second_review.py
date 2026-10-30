"""Cross-vendor second-review selection service (roboco/services/second_review.py).

Pins the two behaviors the in-path PR gate (a sibling unit) depends on:

  * the provider-difference rule never returns the authoring provider when
    multiple providers are enabled;
  * the single-provider-enabled path returns an explicit skip + evidence
    note, never an exception/block.

Plus the settings-driven risk-threshold classifier (`is_high_stakes` /
`task_is_high_stakes`), defaulting the flag OFF.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
from roboco.config import settings
from roboco.models.base import ModelProvider, Team
from roboco.models.task import Task
from roboco.services.second_review import (
    SecondReviewSelection,
    is_high_stakes,
    resolve_second_review_provider,
    task_is_high_stakes,
    task_is_high_stakes_with_decisions,
)


def _task(
    *,
    title: str = "Add user lookup endpoint",
    description: str = "Add GET /v1/users/{id} returning user JSON.",
    priority: int = 2,
    adds_migration: bool = False,
    touches_shared: bool = False,
) -> Task:
    return Task(
        title=title,
        description=description,
        acceptance_criteria=["returns 404 for unknown user"],
        created_by=uuid4(),
        team=Team.BACKEND,
        priority=priority,
        adds_migration=adds_migration,
        touches_shared=touches_shared,
    )


# ---------------------------------------------------------------------------
# resolve_second_review_provider — the provider-difference rule
# ---------------------------------------------------------------------------


def test_resolved_second_review_provider_never_equals_authoring_provider() -> None:
    """With multiple providers enabled, the resolved second reviewer always
    differs from whichever provider authored/QA'd the task."""
    enabled = [ModelProvider.ANTHROPIC, ModelProvider.GROK, ModelProvider.GEMINI]
    for authoring in enabled:
        result = resolve_second_review_provider(authoring, enabled)
        assert result.skipped is False
        assert result.skip_reason is None
        assert result.provider is not None
        assert result.provider != authoring


def test_resolve_picks_first_differing_candidate_deterministically() -> None:
    enabled = [ModelProvider.GROK, ModelProvider.ANTHROPIC, ModelProvider.GEMINI]
    result = resolve_second_review_provider(ModelProvider.ANTHROPIC, enabled)
    assert result == SecondReviewSelection.resolved(ModelProvider.GROK)


# ---------------------------------------------------------------------------
# resolve_second_review_provider — the single-provider skip path
# ---------------------------------------------------------------------------


def test_skips_with_evidence_note_when_only_one_provider_enabled() -> None:
    result = resolve_second_review_provider(
        ModelProvider.ANTHROPIC, [ModelProvider.ANTHROPIC]
    )
    assert result.skipped is True
    assert result.provider is None
    assert result.skip_reason is not None
    assert "anthropic" in result.skip_reason.lower()
    assert "skip" in result.skip_reason.lower()


def test_skips_when_no_providers_enabled_at_all() -> None:
    """An empty enabled-providers list (e.g. a bad read) never raises."""
    result = resolve_second_review_provider(ModelProvider.ANTHROPIC, [])
    assert result.skipped is True
    assert result.provider is None
    assert result.skip_reason


def test_skip_result_is_a_normal_return_value_not_an_exception() -> None:
    """The gate treats a skip as a normal outcome — calling this must never
    raise, for either the multi-provider or single-provider case."""
    try:
        resolve_second_review_provider(
            ModelProvider.ANTHROPIC, [ModelProvider.ANTHROPIC]
        )
        resolve_second_review_provider(
            ModelProvider.ANTHROPIC, [ModelProvider.ANTHROPIC, ModelProvider.GROK]
        )
    except Exception as exc:  # pragma: no cover - the assertion is the point
        pytest.fail(f"resolve_second_review_provider raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# is_high_stakes / task_is_high_stakes — settings-driven risk threshold
# ---------------------------------------------------------------------------


def test_high_stakes_check_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag defaults OFF — even a P0 migration-adding task is never
    high-stakes while the master switch is off."""
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", False)
    assert (
        is_high_stakes(
            priority=0,
            adds_migration=True,
            touches_shared=True,
            security_relevant=True,
        )
        is False
    )


def test_high_stakes_check_flags_p0_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_max_priority", 1)
    assert (
        is_high_stakes(
            priority=0,
            adds_migration=False,
            touches_shared=False,
            security_relevant=False,
        )
        is True
    )


def test_high_stakes_check_does_not_flag_low_priority_routine_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    assert (
        is_high_stakes(
            priority=3,
            adds_migration=False,
            touches_shared=False,
            security_relevant=False,
        )
        is False
    )


def test_high_stakes_check_flags_migration_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_flag_migrations", True)
    assert (
        is_high_stakes(
            priority=3,
            adds_migration=True,
            touches_shared=False,
            security_relevant=False,
        )
        is True
    )


def test_high_stakes_check_migration_signal_is_toggleable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_flag_migrations", False)
    assert (
        is_high_stakes(
            priority=3,
            adds_migration=True,
            touches_shared=False,
            security_relevant=False,
        )
        is False
    )


def test_task_is_high_stakes_matches_security_keyword_in_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    task = _task(
        title="Rotate the encryption key",
        description="Rotate the Fernet secret used to encrypt git tokens.",
        priority=3,
    )
    assert task_is_high_stakes(task) is True


def test_task_is_high_stakes_false_for_routine_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    task = _task(
        title="Add user lookup endpoint",
        description="Add GET /v1/users/{id} returning user JSON.",
        priority=3,
    )
    assert task_is_high_stakes(task) is False


# ---------------------------------------------------------------------------
# B9 task_is_high_stakes_with_decisions: the deterministic classifier is
# authoritative; the decisions screen may only ADD eligibility, and never
# re-enables a flag-off pass.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b9_flag_off_never_consults_the_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With cross_vendor_review_enabled off, deterministic False means the
    whole pass is disabled: the decisions screen is never consulted, so a
    classifier verdict cannot implicitly re-enable the feature."""
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", False)
    pilot = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "roboco.services.decisions.pilots_infra.second_review_high_stakes",
        pilot,
    )
    assert (
        await task_is_high_stakes_with_decisions(cast("AsyncSession", None), _task())
        is False
    )
    pilot.assert_not_awaited()


@pytest.mark.asyncio
async def test_b9_screen_may_add_eligibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag on, deterministic classifier below threshold, decisions screen
    ON-confident: the task qualifies (may only ADD)."""
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_max_priority", 0)
    monkeypatch.setattr(
        "roboco.services.decisions.pilots_infra.second_review_high_stakes",
        AsyncMock(return_value=True),
    )
    assert (
        await task_is_high_stakes_with_decisions(cast("AsyncSession", None), _task())
        is True
    )


@pytest.mark.asyncio
async def test_b9_screen_false_keeps_deterministic_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_max_priority", 0)
    monkeypatch.setattr(
        "roboco.services.decisions.pilots_infra.second_review_high_stakes",
        AsyncMock(return_value=False),
    )
    assert (
        await task_is_high_stakes_with_decisions(cast("AsyncSession", None), _task())
        is False
    )


@pytest.mark.asyncio
async def test_b9_deterministic_true_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classifier saying True wins without a decisions call (may only
    ADD, never need)."""
    monkeypatch.setattr(settings, "cross_vendor_review_enabled", True)
    monkeypatch.setattr(settings, "cross_vendor_review_max_priority", 1)
    pilot = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "roboco.services.decisions.pilots_infra.second_review_high_stakes",
        pilot,
    )
    assert (
        await task_is_high_stakes_with_decisions(
            cast("AsyncSession", None), _task(priority=0)
        )
        is True
    )
    pilot.assert_not_awaited()
