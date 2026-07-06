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
from typing import TYPE_CHECKING, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.notification import NotificationService
from roboco.services.project import get_project_service
from roboco.services.task import (
    SELF_HEAL_SOURCE,
    TaskCreateRequest,
    extract_self_heal_fingerprint,
    get_task_service,
)
from roboco.services.telemetry import get_ci_telemetry_source

if TYPE_CHECKING:
    from uuid import UUID

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
    """Stable short hash of the signal (which already encodes the repo).

    By-design per-signal, NOT per-regression-run: the dedup's purpose is one
    open fix task per regression signal, so a persistently-red CI signal
    doesn't open a second task every cycle. A second distinct regression that
    shares the same ``signal_name`` folds into the first's open task —
    accepted because they share a signal root; widening to per-run (e.g.
    mixing in a timestamp) would defeat the dedup and re-spam the backlog
    each tick. Do not "fix" this into a per-run hash.
    """
    return hashlib.sha256(signal_name.encode("utf-8")).hexdigest()[:16]


_NOTIFY_DEDUPE_KEY_PREFIX = "self_heal:notified:"


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
        """Assess, notify the CEO, and (if originate is on) open fix tasks.

        No-op unless ``self_heal_enabled``. It always NOTIFIES on a regression;
        when ``self_heal_originate_enabled`` it also opens a PENDING fix task per
        new regression and STOPS. It never starts, approves, merges, or deploys.
        Writes (any opened task) are flushed here; the caller (the orchestrator
        loop) owns the commit.

        #43: the notify loop dedupes per fingerprint — a regression that stays
        red across cycles pings the CEO once per episode, not every tick (the
        notification layer's purpose-dedup only holds while unacked; without
        this guard a post-ack persistent red state re-fires each cycle). The
        CEO alert links the open self-heal fix task when one exists so the panel
        can route the CEO to the fix. The dedupe check fails open: a Redis
        outage still lets the notify through (better a duplicate ping than a
        swallowed regression).
        """
        if not settings.self_heal_enabled:
            return []
        observations = await self.assess()
        if not observations:
            return []
        fp_to_task = await self._open_self_heal_task_ids_by_fp()
        notifier = NotificationService()
        for obs in observations:
            if await self._already_notified(obs.fingerprint):
                continue
            body = f"[self-heal] {obs.summary}\n\n{obs.detail}"
            if obs.raw_ref:
                body += f"\n\nEvidence: {obs.raw_ref}"
            await notifier.send_ack_notification(
                from_agent="system",
                to_agent="ceo",
                body=body,
                task_id=fp_to_task.get(obs.fingerprint),
            )
            await self._mark_notified(obs.fingerprint)
        if settings.self_heal_originate_enabled:
            await self._originate(observations)
        return observations

    async def _open_self_heal_task_ids_by_fp(self) -> dict[str, UUID]:
        """Map each open self-heal task's fingerprint to its task id.

        Best-effort: a DB/query error returns ``{}`` so the notify loop still
        runs (task_id simply stays None — the alert floats free, which is the
        pre-fix behaviour, never a crash). Used both to link the CEO alert to
        the fix task and as a durable "already notified" corroboration.
        """
        try:
            task_svc = get_task_service(self.session)
            open_tasks = await task_svc.list_open_self_heal_tasks()
        except Exception:
            self.log.exception("self-heal open-task lookup failed; notify floats")
            return {}
        mapping: dict[str, UUID] = {}
        for existing in open_tasks:
            fp = extract_self_heal_fingerprint(existing)
            if fp and existing.id is not None:
                mapping[fp] = cast("UUID", existing.id)
        return mapping

    async def _already_notified(self, fingerprint: str) -> bool:
        """True if this fingerprint was already CEO-notified this episode.

        Fail-open: a Redis error returns False (notify anyway) — a dedupe
        outage must never swallow a regression alert.
        """
        try:
            import redis.asyncio  # local: redis is an agent-runtime dep

            r = redis.asyncio.from_url(settings.redis_url)
            try:
                return bool(await r.get(self._dedupe_key(fingerprint)))
            finally:
                await r.aclose()
        except Exception:
            self.log.exception("self-heal notify-dedupe check failed; failing open")
            return False

    async def _mark_notified(self, fingerprint: str) -> None:
        """Record that this fingerprint was CEO-notified, with a TTL window.

        Best-effort: a Redis error is logged and swallowed — the notify already
        fired, so a missed mark can at worst cause one duplicate on the next
        cycle, never a lost alert.
        """
        try:
            import redis.asyncio  # local: redis is an agent-runtime dep

            r = redis.asyncio.from_url(settings.redis_url)
            try:
                await r.set(
                    self._dedupe_key(fingerprint),
                    "1",
                    ex=settings.self_heal_notify_dedupe_seconds,
                )
            finally:
                await r.aclose()
        except Exception:
            self.log.exception("self-heal notify-dedupe mark dropped")

    @staticmethod
    def _dedupe_key(fingerprint: str) -> str:
        return f"{_NOTIFY_DEDUPE_KEY_PREFIX}{fingerprint}"

    async def _originate(self, observations: list[RegressionObservation]) -> int:
        """Open a PENDING fix task per NEW regression, then STOP. Returns count.

        Bounded + deduped: skips a regression that already has an open self-heal
        task (by fingerprint), honors the per-cycle and rolling open-task caps,
        and resolves the repo to RoboCo's own project. Each task is created
        PENDING + assigned to the Main PM agent (not merely team=main_pm) so
        that, once the CEO approves it, the orchestrator dispatches it straight
        to that agent via the assigned-PM path.

        The fix task is HELD for the CEO's Approve-&-Start
        (``confirmed_by_human=False``) — the documented safety invariant: the
        loop only OPENS the task; it never starts, approves, merges, or deploys.
        The orchestrator + ``give_me_work`` hold a ``confirmed_by_human=False``
        self-heal task out of dispatch until the CEO's ``approve_and_start``
        flips the flag True. The fix then ships through the normal gates
        (dev -> QA -> PR review -> the CEO's merge). The loop NEVER calls
        start / approve / merge / deploy. Flushes; the caller commits.
        """
        task_svc = get_task_service(self.session)
        project_svc = get_project_service(self.session)
        open_tasks = await task_svc.list_open_self_heal_tasks()
        open_fps: set[str] = set()
        for existing in open_tasks:
            fp = extract_self_heal_fingerprint(existing)
            if fp:
                open_fps.add(fp)
        open_count = len(open_tasks)
        created = 0
        for obs in observations:
            if created >= settings.self_heal_max_per_cycle:
                break
            if open_count >= settings.self_heal_max_open_tasks:
                self.log.info(
                    "self-heal open-task cap reached; not originating",
                    cap=settings.self_heal_max_open_tasks,
                )
                break
            if obs.fingerprint in open_fps:
                continue
            project = await project_svc.get_by_slug(obs.repo_hint)
            if project is None or project.id is None:
                self.log.warning(
                    "self-heal could not resolve project; notify-only",
                    repo=obs.repo_hint,
                )
                continue
            task = await task_svc.create(
                TaskCreateRequest(
                    title=f"Self-heal: fix the CI regression on {obs.repo_hint}",
                    description=(
                        f"RoboCo's own CI regressed.\n\n{obs.detail}\n\n"
                        f"Evidence: {obs.raw_ref}\n\n"
                        "This is a Main-PM coordination root: decompose the fix "
                        "and delegate the code work to a cell dev — the Main PM "
                        "does not write the fix itself. This task was opened "
                        "automatically by the self-heal loop and is HELD for the "
                        "CEO's Approve-&-Start — it will not dispatch until the "
                        "CEO approves it. Once approved, the delegated fix ships "
                        "through the normal gates (QA, PR review, and the CEO's "
                        "merge)."
                    ),
                    acceptance_criteria=[
                        "The CI regression is decomposed into a code-fix "
                        "subtask delegated to a cell developer",
                        f"CI on {obs.repo_hint}'s default branch is green again "
                        "and the fix merged through the normal gates",
                    ],
                    team=Team.MAIN_PM,
                    assigned_to=_foundation.AGENTS["main-pm"].uuid,
                    created_by=_foundation.AGENTS["system"].uuid,
                    task_type=TaskType.PLANNING,
                    nature=TaskNature.TECHNICAL,
                    estimated_complexity=Complexity.MEDIUM,
                    project_id=cast("UUID", project.id),
                    status=TaskStatus.PENDING,
                    source=SELF_HEAL_SOURCE,
                    confirmed_by_human=False,
                )
            )
            # Carry the fingerprint so a later cycle sees this regression already
            # has an open fix task (parsed by extract_self_heal_fingerprint).
            markers.set_self_heal_fingerprint(task, obs.fingerprint)
            await self.session.flush()
            open_fps.add(obs.fingerprint)
            open_count += 1
            created += 1
            self.log.info(
                "self-heal fix task opened (PENDING; held for CEO Approve-&-Start)",
                task_id=str(task.id),
                repo=obs.repo_hint,
                fingerprint=obs.fingerprint,
            )
        return created


def get_self_heal_engine(
    session: AsyncSession, source: TelemetrySource | None = None
) -> SelfHealEngine:
    """Construct a SelfHealEngine bound to ``session`` (optionally a test source)."""
    return SelfHealEngine(session, source=source)
