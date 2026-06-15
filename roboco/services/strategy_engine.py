"""Autonomous strategy engine ("engine 2") — dormant by default.

Engine 1 is the delivery lifecycle (agents shipping tasks). Engine 2 watches
the company against its standing goals and, when something needs attention,
surfaces it to the CEO. It is deliberately conservative:

* **Default OFF.** ``strategy_engine_enabled`` is False, so the orchestrator
  loop never starts and the existing system is completely unaffected.
* **Human-in-the-loop.** Even when enabled it only *notifies* the CEO — it
  never spends, builds, or auto-approves. Originating actual work stays a CEO
  decision (e.g. approving a pitch). This keeps a clear boundary around the
  autonomous surface.
* **Bounded + deduped.** One pass per interval, at most one notification per
  observation kind; the notification layer's purpose-dedup suppresses repeats
  until the CEO acknowledges.

Observations today: the company is idle while goals stand (drift toward doing
nothing), and tasks stranded in ``blocked`` past a threshold (work that needs a
human decision). Auto-origination (e.g. drafting pitches) is intentionally a
further opt-in, not part of this dormant baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.services.base import BaseService
from roboco.services.company_goals import get_company_goals_service
from roboco.services.notification import NotificationService
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class StrategyObservation:
    """One thing the engine noticed about company state."""

    kind: str  # "idle" | "stranded_blocked"
    summary: str
    detail: str


class StrategyEngine(BaseService):
    """Assess company state against goals; surface what needs the CEO."""

    service_name = "strategy_engine"

    async def assess(self) -> list[StrategyObservation]:
        """Read company state and return observations (no side effects)."""
        observations: list[StrategyObservation] = []
        task_svc = get_task_service(self.session)

        in_flight = await task_svc.list_in_progress_or_claimed()
        goals = await get_company_goals_service(self.session).get()
        objectives = goals.get("objectives") or []
        north_star = (goals.get("north_star") or "").strip()
        has_direction = bool(objectives or north_star)

        if not in_flight and has_direction:
            observations.append(
                StrategyObservation(
                    kind="idle",
                    summary="The company is idle but has standing goals.",
                    detail=(
                        "No delivery work is in progress or claimed, yet the "
                        "charter defines goals to pursue. Consider authoring a "
                        "pitch or starting work toward an objective."
                    ),
                )
            )

        stranded = await task_svc.list_long_running_blocked(
            threshold_minutes=settings.strategy_stranded_blocked_minutes
        )
        if stranded:
            observations.append(
                StrategyObservation(
                    kind="stranded_blocked",
                    summary=f"{len(stranded)} task(s) have been blocked a long time.",
                    detail=(
                        "These tasks have sat in 'blocked' beyond the threshold "
                        "and likely need a human decision to move forward."
                    ),
                )
            )
        return observations

    async def run_cycle(self) -> list[StrategyObservation]:
        """Assess and notify the CEO. No-op unless the engine is enabled."""
        if not settings.strategy_engine_enabled:
            return []
        observations = await self.assess()
        if not observations:
            return []
        notifier = NotificationService()
        for obs in observations:
            await notifier.send_ack_notification(
                from_agent="system",
                to_agent="ceo",
                body=f"[strategy engine] {obs.summary}\n\n{obs.detail}",
            )
        return observations


def get_strategy_engine(session: AsyncSession) -> StrategyEngine:
    """Construct a StrategyEngine bound to ``session``."""
    return StrategyEngine(session)
