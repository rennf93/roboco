"""Gated release-manager engine — dormant by default.

Mirrors the self-heal engine's "detect → originate a CEO-gated artifact → hold"
shape, but for releases. At a logical point (accumulated unreleased changes past
the threshold AND a green gate) it runs the deterministic readiness sweep
(``ReleaseReadinessService``) and originates ONE release PROPOSAL held for the
CEO. It is deliberately conservative:

* **Default OFF.** ``release_manager_enabled`` is False, so the orchestrator loop
  never runs and nothing is proposed.
* **Never publishes.** The proposal is HELD (``confirmed_by_human=False``, owned
  by the Secretary, and explicitly skipped by every dispatcher) — it is acted on
  only by the CEO-gated release routes + the fail-closed executor, never by the
  loop. The loop NEVER calls start / approve / merge / publish.
* **Repo-singular.** It assesses RoboCo's own project (``self_heal_project_slug``,
  the canonical "this project IS RoboCo" pointer; defaults to ``roboco-api``).
* **Bounded.** At most one open proposal at a time (dedup by source).

Correctness is deterministic: the readiness audit lives in code
(``release_readiness``), not in agent judgment.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.notification import NotificationService
from roboco.services.project import get_project_service
from roboco.services.release_readiness import (
    ReleaseReadinessReport,
    assess,
    gather_snapshot,
    report_to_dict,
)
from roboco.services.task import (
    RELEASE_MANAGER_SOURCE,
    TaskCreateRequest,
    get_task_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

# An assessor produces the readiness report (or None when it can't assess). The
# default is the production git path; tests inject a synthetic one.
ReleaseAssessor = Callable[[], Awaitable[ReleaseReadinessReport | None]]


def _roboco_slug() -> str:
    """The registered project that IS RoboCo itself (reused from self-heal)."""
    return (settings.self_heal_project_slug or "roboco-api").strip()


def _past_threshold(report: ReleaseReadinessReport) -> bool:
    """A release is warranted past the commit floor, or for any feat/security."""
    n_commits = len(report.change_summary)
    significant = report.bump_kind != "patch" or any(
        summary.startswith("security:") for summary in report.change_summary
    )
    return n_commits >= settings.release_min_commits or significant


def _proposal_description(report: ReleaseReadinessReport) -> str:
    lines = [
        f"Proposed release: v{report.proposed_version} ({report.bump_kind} bump).",
        f"{len(report.change_summary)} change(s) since the last tag; "
        f"gate is {report.gate_state}.",
        "",
        "## Drafted CHANGELOG",
        report.drafted_changelog.rstrip(),
    ]
    if report.gaps:
        lines.append("")
        lines.append("## Gaps to resolve before publish")
        lines.extend(f"- [{gap.category}] {gap.detail}" for gap in report.gaps)
    if report.migration_notes:
        lines.append("")
        lines.append("## Migrations")
        lines.extend(f"- {note}" for note in report.migration_notes)
    return "\n".join(lines)


class ReleaseManagerEngine(BaseService):
    """Detect release-readiness and originate ONE CEO-gated proposal (never publish)."""

    service_name = "release_manager_engine"

    def __init__(
        self, session: AsyncSession, assessor: ReleaseAssessor | None = None
    ) -> None:
        super().__init__(session)
        self._assessor: ReleaseAssessor = assessor or self._production_assess

    async def run_cycle(self) -> TaskTable | None:
        """Assess and, if warranted, originate one held proposal. Else no-op.

        No-op unless ``release_manager_enabled``. Originates only past the
        threshold, with a green gate, and when no proposal is already open. The
        proposal is held for the CEO; this never starts/approves/publishes.
        """
        if not settings.release_manager_enabled:
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_release_proposals():
            return None  # one open proposal at a time
        report = await self._ready_report()
        if report is None:
            return None
        project = await get_project_service(self.session).get_by_slug(_roboco_slug())
        if project is None or project.id is None:
            self.log.warning(
                "release-manager: RoboCo project not resolvable; skipping",
                slug=_roboco_slug(),
            )
            return None
        return await self._originate(report, cast("UUID", project.id))

    async def _ready_report(self) -> ReleaseReadinessReport | None:
        """Assess and return a report only when a release is actually warranted.

        Returns None (propose nothing) when the assessor can't assess, the gate
        is not green, or the change set is below the threshold.
        """
        report = await self._assessor()
        if report is None:
            return None
        if report.gate_state != "green":
            self.log.info(
                "release-manager: gate not green; not proposing",
                gate=report.gate_state,
            )
            return None
        if not _past_threshold(report):
            return None
        return report

    async def _originate(
        self, report: ReleaseReadinessReport, project_id: UUID
    ) -> TaskTable:
        """Open ONE PENDING, HELD release proposal owned by the Secretary."""
        task_svc = get_task_service(self.session)
        task = await task_svc.create(
            TaskCreateRequest(
                title=f"Release proposal: v{report.proposed_version}",
                description=_proposal_description(report),
                acceptance_criteria=[
                    f"CEO approves cutting v{report.proposed_version}",
                    "All flagged gaps are resolved or accepted before publish",
                ],
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["secretary-1"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=RELEASE_MANAGER_SOURCE,
                confirmed_by_human=False,  # HELD for the CEO; never dispatched
            )
        )
        # Carry the machine-readable report so the CEO surface + executor can use
        # it without re-deriving (the description is the human-readable mirror).
        markers.set_release_report(task, report_to_dict(report))
        await self.session.flush()
        await self._notify_ceo(report, task)
        self.log.info(
            "release proposal opened (held for CEO)",
            task_id=str(task.id),
            version=report.proposed_version,
            bump=report.bump_kind,
            gaps=len(report.gaps),
        )
        return task

    async def _notify_ceo(
        self, report: ReleaseReadinessReport, task: TaskTable
    ) -> None:
        gaps = f" {len(report.gaps)} gap(s) to review." if report.gaps else ""
        body = (
            f"[release] Ready to cut v{report.proposed_version} "
            f"({report.bump_kind}). {len(report.change_summary)} change(s) "
            f"since the last tag.{gaps}\n\n"
            "Review the drafted CHANGELOG + version bumps and approve or "
            "reject-with-changes in the panel — nothing publishes until you do."
        )
        try:
            await NotificationService().send_ack_notification(
                from_agent="system", to_agent="ceo", body=body, task_id=str(task.id)
            )
        except Exception as exc:
            self.log.warning("release CEO notify failed (best-effort)", error=str(exc))

    async def _production_assess(self) -> ReleaseReadinessReport | None:
        """Real path: read-clone RoboCo, fetch CI, gather the snapshot, assess.

        Read-only and fail-safe: if the project / clone can't be resolved it
        returns None (propose nothing) rather than raising. The CI conclusion is
        injected into the snapshot (None → unknown gate → no proposal).
        """
        from roboco.services.git import get_git_service
        from roboco.services.workspace import get_workspace_service

        slug = _roboco_slug()
        project = await get_project_service(self.session).get_by_slug(slug)
        if project is None:
            return None
        try:
            root = await get_workspace_service(self.session).ensure_read_clone(slug)
        except Exception as exc:
            self.log.warning("release-manager: read clone failed", error=str(exc))
            return None
        ci = await get_git_service(self.session).get_latest_ci_conclusion(
            slug, workflow=(settings.self_heal_ci_workflow or None)
        )
        conclusion = (ci or {}).get("conclusion")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        snapshot = gather_snapshot(Path(root), master_ci_conclusion=conclusion)
        return assess(snapshot, today=today)


def get_release_manager_engine(
    session: AsyncSession, assessor: ReleaseAssessor | None = None
) -> ReleaseManagerEngine:
    """Build a ReleaseManagerEngine for ``session`` (optional injected assessor)."""
    return ReleaseManagerEngine(session, assessor=assessor)
