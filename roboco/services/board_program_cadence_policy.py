"""Applies the cadence-slowing policy to live metrics and builds the CEO review."""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.foundation.policy.board_program_cadence import (
    CadenceLimits,
    SlowdownDecision,
    decide_slowdown,
    effective_interval_seconds,
)
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.models.board_program_cadence_review import ProgramCadenceReview
from roboco.services.base import BaseService
from roboco.services.board_program_cadence import get_board_program_cadence_service
from roboco.services.settings import get_settings_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.board_program_cadence import ProgramCadenceMetric


def _disabled_key(key: str) -> str:
    return f"board_program.{key}.cadence_slowdown_disabled"


def _decide(metric: ProgramCadenceMetric) -> SlowdownDecision:
    return decide_slowdown(
        cost_per_cycle_usd=metric.cost_per_cycle_usd,
        items_proposed=metric.items_proposed,
        items_approved=metric.items_approved,
        cycles_counted=metric.cycles_counted,
        limits=CadenceLimits(
            min_proposals=settings.board_cadence_min_proposals,
            cost_threshold_usd=settings.board_cadence_cost_threshold_usd,
        ),
    )


class BoardProgramCadencePolicyService(BaseService):
    """Resolves effective intervals and the CEO cadence review."""

    service_name = "board_program_cadence_policy"

    async def slowdown_disabled(self, key: str) -> bool:
        """Whether the CEO reversed this program's slowdown."""
        return await get_settings_service(self.session).get_bool(
            _disabled_key(key), False
        )

    async def set_slowdown_disabled(self, key: str, disabled: bool) -> None:
        """Persist the CEO's reversal (or restore) for ``key``. Caller commits."""
        await get_settings_service(self.session).set(
            _disabled_key(key), "true" if disabled else "false"
        )

    async def effective_interval(self, key: str, base_seconds: int) -> int:
        """``base_seconds`` stretched when ``key`` is slowed; never shorter.

        Fails open to ``base_seconds``: a metrics error must not stall a
        program's cadence.
        """
        try:
            if await self.slowdown_disabled(key):
                return base_seconds
            metric = (
                await get_board_program_cadence_service(
                    self.session
                ).program_cadence_metrics()
            )[key]
            return effective_interval_seconds(
                base_seconds,
                slowed=_decide(metric).slowed,
                multiplier=settings.board_cadence_slowdown_multiplier,
            )
        except Exception:
            self.log.exception("cadence slowdown lookup failed", program=key)
            return base_seconds

    async def review(self) -> list[ProgramCadenceReview]:
        """Every program's cost, approval, sample and slowdown numbers."""
        metrics = await get_board_program_cadence_service(
            self.session
        ).program_cadence_metrics()
        multiplier = settings.board_cadence_slowdown_multiplier
        rows: list[ProgramCadenceReview] = []
        for key, program in PROGRAMS.items():
            metric = metrics[key]
            decision = _decide(metric)
            disabled = await self.slowdown_disabled(key)
            slowed = decision.slowed and not disabled
            base = program.default_interval_seconds
            rows.append(
                ProgramCadenceReview(
                    program_key=key,
                    cost_per_cycle_usd=metric.cost_per_cycle_usd,
                    approval_rate=metric.approval_rate,
                    sample_size=metric.items_proposed,
                    items_approved=metric.items_approved,
                    slowed=slowed,
                    slowdown_disabled=disabled,
                    original_interval_seconds=base,
                    effective_interval_seconds=effective_interval_seconds(
                        base, slowed=slowed, multiplier=multiplier
                    ),
                    reason=decision.reason,
                    cost_threshold_usd=decision.cost_threshold_usd,
                    min_proposals=decision.min_proposals,
                    slowdown_multiplier=multiplier,
                    cost_per_approved_usd=decision.cost_per_approved_usd,
                )
            )
        return rows


def get_board_program_cadence_policy_service(
    session: AsyncSession,
) -> BoardProgramCadencePolicyService:
    """Construct a BoardProgramCadencePolicyService bound to ``session``."""
    return BoardProgramCadencePolicyService(session)
