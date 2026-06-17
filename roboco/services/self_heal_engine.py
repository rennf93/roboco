"""Production self-healing engine ("engine 4") — dormant by default.

RoboCo heals ITSELF. This watches RoboCo's OWN repo for a regression (a failing
CI run on its default branch, via the telemetry source) and, when it sees one,
surfaces it to the CEO — and, behind a second opt-in, opens a PENDING fix task
into RoboCo's own delivery lifecycle and STOPS. It is deliberately conservative:

* **Default OFF.** ``self_heal_enabled`` is False, so the orchestrator loop never
  starts and the existing system is completely unaffected.
* **Never self-deploys.** Even fully enabled, the loop only NOTIFIES and, at
  most, OPENS a PENDING task. It never starts, approves, merges, or deploys work
  — every downstream step stays a human/CEO decision (the task waits for the
  CEO's Approve-&-Start and terminates at ``awaiting_ceo_approval``).
* **Repo-singular.** It targets only RoboCo's own project
  (``self_heal_project_slug``); the org's repo-agnostic delivery work is separate.
* **Bounded + deduped.** One pass per interval; at most one open fix task per
  signal fingerprint; per-cycle and rolling open-task caps; the notification
  layer's purpose-dedup suppresses repeat CEO pings until acknowledged.

This slice is detect + notify only; task origination is layered on next, behind
the second flag.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.services.base import BaseService
from roboco.services.notification import NotificationService
from roboco.services.telemetry import get_ci_telemetry_source

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.services.telemetry import TelemetrySource


@dataclass(frozen=True)
class RegressionObservation:
    """One regression the engine detected in RoboCo's own repo."""

    fingerprint: str  # stable per signal — the dedupe key for open fix tasks
    signal_name: str
    repo_hint: str
    summary: str
    detail: str
    raw_ref: str


def _fingerprint(signal_name: str) -> str:
    """Stable short hash of the signal (which already encodes the repo)."""
    return hashlib.sha256(signal_name.encode("utf-8")).hexdigest()[:16]


class SelfHealEngine(BaseService):
    """Detect a regression in RoboCo's own repo; surface it (and later open a fix)."""

    service_name = "self_heal_engine"

    def __init__(
        self, session: AsyncSession, source: TelemetrySource | None = None
    ) -> None:
        super().__init__(session)
        self._source: TelemetrySource = source or get_ci_telemetry_source(session)

    async def assess(self) -> list[RegressionObservation]:
        """Read telemetry and return observations. Pure — no side effects."""
        observations: list[RegressionObservation] = []
        for sample in await self._source.fetch():
            if not sample.is_breach:
                continue
            observations.append(
                RegressionObservation(
                    fingerprint=_fingerprint(sample.signal_name),
                    signal_name=sample.signal_name,
                    repo_hint=sample.repo_hint,
                    summary=f"Regression detected on {sample.repo_hint}.",
                    detail=sample.detail
                    or f"{sample.signal_name} breached its threshold.",
                    raw_ref=sample.raw_ref,
                )
            )
        return observations

    async def run_cycle(self) -> list[RegressionObservation]:
        """Assess and notify the CEO. No-op unless self-healing is enabled.

        Detect + notify only; never starts, merges, or deploys anything.
        """
        if not settings.self_heal_enabled:
            return []
        observations = await self.assess()
        if not observations:
            return []
        notifier = NotificationService()
        for obs in observations:
            body = f"[self-heal] {obs.summary}\n\n{obs.detail}"
            if obs.raw_ref:
                body += f"\n\nEvidence: {obs.raw_ref}"
            await notifier.send_ack_notification(
                from_agent="system", to_agent="ceo", body=body
            )
        return observations


def get_self_heal_engine(
    session: AsyncSession, source: TelemetrySource | None = None
) -> SelfHealEngine:
    """Construct a SelfHealEngine bound to ``session`` (optionally a test source)."""
    return SelfHealEngine(session, source=source)
