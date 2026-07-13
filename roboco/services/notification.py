"""
Notification Service

Sends notifications through the API with proper enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import select

from roboco.db.base import get_db_context
from roboco.db.tables import AgentTable, NotificationTable
from roboco.foundation.policy.communications import ACK_REQUIRED_BY_TYPE
from roboco.models import NotificationPriority, NotificationType
from roboco.models.notification import CreateNotificationParams
from roboco.services.notification_dedup import all_recipients_recently_notified
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


async def _resolve_agent_uuid(
    db: AsyncSession, value: str | UUID | None
) -> UUID | None:
    """Turn an agent slug or UUID (any case / any form) into a real UUID.

    `notifications.from_agent` is UUID-typed in the DB + FK to agents.id.
    Callers across the codebase pass slugs ("be-doc", "system", etc.) —
    this resolver does the slug→UUID translation. "system" resolves to
    the seeded system agent (stable UUID) so orchestrator-generated
    notifications always have a valid sender.

    Returns None only for truly absent values (None, empty string, or a
    slug we can't find). The caller in `_create_notification` logs +
    skips in that case rather than crashing on FK violation.
    """
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        pass
    result = await db.execute(select(AgentTable).where(AgentTable.slug == str(value)))
    agent = result.scalar_one_or_none()
    return UUID(str(agent.id)) if agent else None


class NotificationService:
    """Service for sending system-generated notifications."""

    async def send_blocker_notification(
        self,
        task_id: str,
        blocker_reason: str,
        from_agent: str | None,
        to_pm: str,
    ) -> None:
        """Send notification about a blocked task."""
        logger.info(
            "Sending blocker notification",
            task_id=task_id,
            to_pm=to_pm,
        )

        # System notifications bypass normal permission checks
        body = (
            f"Task {task_id} has been blocked.\n\n"
            f"Reason: {blocker_reason}\n\n"
            "Please investigate and help resolve."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.BLOCKER_ESCALATION,
                priority=NotificationPriority.HIGH,
                from_agent=from_agent or "system",
                to_agents=[to_pm],
                subject=f"Task {task_id} is blocked",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_stuck_agent_notification(
        self,
        task_id: str,
        agent_slug: str,
        task_status: str,
        to_agent: str,
    ) -> None:
        """Alert an overseer that an agent is wedged in an unproductive loop.

        Raised when the dispatcher's respawn circuit-breaker pauses further
        spawns: the agent was respawned repeatedly without advancing the task,
        so automatic recovery has given up and a human needs to intervene.
        """
        logger.info(
            "Sending stuck-agent notification",
            task_id=task_id,
            agent=agent_slug,
            to_agent=to_agent,
        )
        body = (
            f"Agent {agent_slug} was repeatedly spawned on task {task_id} "
            f"(status: {task_status}) without advancing it, so further automatic "
            "spawns have been paused. Please investigate and intervene manually."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.BLOCKER_ESCALATION,
                priority=NotificationPriority.HIGH,
                from_agent="system",
                to_agents=[to_agent],
                subject=f"Agent {agent_slug} stuck on task {task_id}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_qa_ready_notification(
        self,
        task_id: str,
        from_agent: str | None,
        to_qa: str,
    ) -> None:
        """Send notification that task is ready for QA."""
        logger.info(
            "Sending QA ready notification",
            task_id=task_id,
            to_qa=to_qa,
        )

        body = (
            f"Task {task_id} is ready for QA review.\n\n"
            "Please review and provide feedback."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.REVIEW_REQUEST,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_qa],
                subject=f"Task {task_id} ready for QA",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_docs_ready_notification(
        self,
        task_id: str,
        from_agent: str | None,
        to_documenter: str,
    ) -> None:
        """Send notification that task is ready for documentation."""
        logger.info(
            "Sending docs ready notification",
            task_id=task_id,
            to_documenter=to_documenter,
        )

        body = (
            f"Task {task_id} has passed QA and is ready for documentation.\n\n"
            "Please create the required documentation."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.DOCUMENTATION_REQUEST,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_documenter],
                subject=f"Task {task_id} needs documentation",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_handoff_notification(
        self,
        task_id: str,
        handoff_id: str,
        from_agent: str | None,
        to_documenter: str,
    ) -> None:
        """Send notification that task needs handoff documentation."""
        logger.info(
            "Sending handoff notification",
            task_id=task_id,
            handoff_id=handoff_id,
            to_documenter=to_documenter,
        )

        body = (
            f"Task {task_id} is ready for handoff (ID: {handoff_id}).\n\n"
            "Please review and create handoff documentation."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.DOCUMENTATION_REQUEST,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_documenter],
                subject=f"Handoff required: Task {task_id}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_qa_failed_notification(
        self,
        task_id: str,
        qa_notes: str,
        to_developer: str,
    ) -> None:
        """Send notification that task failed QA."""
        logger.info(
            "Sending QA failed notification",
            task_id=task_id,
            to_developer=to_developer,
        )

        body = (
            f"Task {task_id} has failed QA review.\n\n"
            f"Notes: {qa_notes}\n\n"
            "Please address the issues and resubmit."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.REVIEW_REQUEST,
                priority=NotificationPriority.HIGH,
                from_agent="system",
                to_agents=[to_developer],
                subject=f"QA Failed: Task {task_id}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_board_review_complete_notification(
        self,
        task_id: str,
        from_agent: str | None = None,
        to_ceo: str = "ceo",
    ) -> None:
        """Tell the CEO a board review is complete and ready for Approve & Start.

        Board-reviewed coordination tasks stay ``pending`` and wait for the
        CEO's Approve & Start gate (``TaskService.approve_and_start``). The
        Product Owner + Head of Marketing record their review via journal
        notes, but that left the CEO with no actionable
        signal — only buried chatter. This emits a
        formal APPROVAL notification (ack-required) carrying ``related_task_id``
        so the handoff is a real signal the panel can surface, not buried
        noise. Board roles are exactly the senders permitted to notify, so the
        orchestrator emits it as ``system`` on their behalf once BOTH board
        reviewers (PO + Head of Marketing) have finished.
        """
        logger.info(
            "Sending board-review-complete notification to CEO",
            task_id=task_id,
            to_ceo=to_ceo,
        )

        body = (
            f"Board review complete for task {task_id}.\n\n"
            "The Product Owner and Head of Marketing have both reviewed and "
            "recorded their requirements. The task is ready for your "
            "Approve & Start decision (hand to Main PM) or rejection."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.APPROVAL,
                priority=NotificationPriority.HIGH,
                from_agent=from_agent or "system",
                to_agents=[to_ceo],
                subject=f"Board review complete: Task {task_id}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_weekly_report_notification(
        self,
        week: str,
        note_path: str,
        summary_line: str,
        to_ceo: str = "ceo",
    ) -> None:
        """Ping the CEO once the vault janitor materializes the weekly
        org-report note (``roboco.services.vault_janitor``). Best-effort by
        design — the caller swallows any failure, since a missed ping never
        invalidates the note that's already on disk.
        """
        body = (
            f"Weekly org report for {week} is ready in the vault "
            f"({note_path}).\n\n{summary_line}"
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.NORMAL,
                from_agent="system",
                to_agents=[to_ceo],
                subject=f"Weekly org report: {week}",
                body=body,
            )
        )

    async def send_external_pr_reviewed_notification(
        self,
        task_id: str,
        pr_number: int,
        pr_url: str,
        from_agent: str | None = None,
        to_ceo: str = "ceo",
    ) -> None:
        """Tell the CEO an inbound external PR has been reviewed — their call.

        The PR reviewer is read-only: it posts one change-request and stops. The
        CEO is the gate on what happens next (supersede the PR — the org takes it
        over and finishes it — or dismiss it). A passive ping is not enough, so
        this emits a formal APPROVAL notification carrying ``related_task_id`` so
        the panel's PR-review decision queue can surface it as an actionable
        signal. Emitted server-side as ``system`` (the reviewer has no notify
        verb).
        """
        logger.info(
            "Sending external-PR-reviewed notification to CEO",
            task_id=task_id,
            pr_number=pr_number,
            to_ceo=to_ceo,
        )
        body = (
            f"External PR #{pr_number} has been reviewed and a change-request "
            f"posted ({pr_url}).\n\nYour call: supersede it (the org takes the "
            "contribution over and finishes it to our standards) or dismiss it."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.APPROVAL,
                priority=NotificationPriority.HIGH,
                from_agent=from_agent or "system",
                to_agents=[to_ceo],
                subject=f"External PR #{pr_number} reviewed — your decision",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_reassignment_notification(
        self,
        task_id: str,
        previous_assignee: str | None,
        new_assignee: str | None,
        from_agent: str | None = None,
        to_ceo: str = "ceo",
    ) -> None:
        """Tell the outgoing + incoming owner (and the CEO) a task moved.

        Skipped by the caller when ``new_assignee == previous_assignee`` —
        ``TaskService.reassign`` runs even on a no-op redirect, and a
        same-owner "reassignment" is not a coordination event.
        """
        recipients = list(
            dict.fromkeys(r for r in (previous_assignee, new_assignee, to_ceo) if r)
        )
        if not recipients:
            return
        logger.info(
            "Sending reassignment notification",
            task_id=task_id,
            previous_assignee=previous_assignee,
            new_assignee=new_assignee,
        )
        body = (
            f"Task {task_id} was reassigned from "
            f"{previous_assignee or 'unassigned'} to {new_assignee or 'unassigned'}."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=recipients,
                subject=f"Task {task_id} reassigned",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_collision_sequencing_notification(
        self,
        held_back_task_id: str,
        blocking_task_id: str,
        held_back_assignee: str | None,
        from_agent: str | None = None,
        to_ceo: str = "ceo",
    ) -> None:
        """Tell the held-back task's owner (+ CEO) it now waits on a sibling.

        Fired only for a newly-created collision-sequencing edge (see
        ``wire_sibling_collision_dag`` — a repeat wiring pass over an
        already-wired pair contributes no edge, so this cannot double-fire).
        """
        recipients = list(dict.fromkeys(r for r in (held_back_assignee, to_ceo) if r))
        if not recipients:
            return
        logger.info(
            "Sending collision-sequencing notification",
            held_back_task_id=held_back_task_id,
            blocking_task_id=blocking_task_id,
        )
        body = (
            f"Task {held_back_task_id} was held back by the collision-sequencing "
            f"analyzer: it now depends on task {blocking_task_id}, which surfaced "
            "an overlapping file/migration/shared-surface collision. It will "
            "resume once that task reaches a terminal state."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=recipients,
                subject=f"Task {held_back_task_id} sequenced behind a sibling",
                body=body,
                related_task_id=held_back_task_id,
            )
        )

    async def send_unblock_notification(
        self,
        task_id: str,
        restored_owner: str | None,
        from_agent: str | None = None,
        to_ceo: str = "ceo",
    ) -> None:
        """Tell the restored owner (+ CEO) a blocked task is workable again.

        Fired from ``TaskService.unblock`` / ``unblock_with_restore`` — both
        only act on a task whose status is currently ``BLOCKED``, so a
        repeated call against the same (already-unblocked) task is a no-op
        upstream and this cannot double-fire.
        """
        recipients = list(dict.fromkeys(r for r in (restored_owner, to_ceo) if r))
        if not recipients:
            return
        logger.info(
            "Sending unblock notification",
            task_id=task_id,
            restored_owner=restored_owner,
        )
        body = (
            f"Task {task_id} has been unblocked and handed back to "
            f"{restored_owner or 'its owner'}. It is ready to resume."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=recipients,
                subject=f"Task {task_id} unblocked",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_dependency_revival_notification(
        self,
        task_id: str,
        assignee: str | None,
        completed_dependency_id: str,
        from_agent: str | None = None,
        to_ceo: str = "ceo",
    ) -> None:
        """Tell the revived task's owner (+ CEO) its last dependency landed.

        Distinct event from ``send_unblock_notification``: that one fires
        when a resolver explicitly calls ``unblock``/``unblock_with_restore``
        on a task blocked by escalation. This one fires from
        ``TaskService._unblock_dependents`` when the LAST outstanding
        dependency of a task blocked ON THAT DEPENDENCY completes — no
        resolver acted, the trigger is upstream task completion, so the
        notification names which dependency unblocked it rather than who
        resolved it. ``_unblock_dependents`` prunes ``dependency_ids`` before
        this fires, so a repeated call for the same completed dependency
        finds no matching dependent and cannot double-fire.
        """
        recipients = list(dict.fromkeys(r for r in (assignee, to_ceo) if r))
        if not recipients:
            return
        logger.info(
            "Sending dependency-revival notification",
            task_id=task_id,
            completed_dependency_id=completed_dependency_id,
        )
        body = (
            f"Task {task_id} was revived: its dependency "
            f"{completed_dependency_id} just completed and no other "
            "dependencies remain. It is ready to resume."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=recipients,
                subject=f"Task {task_id} revived by dependency completion",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_stale_claim_reaped_notification(
        self,
        task_id: str,
        reaped_agent: str | None,
        last_heartbeat: str | None = None,
        from_agent: str = "system",
        to_ceo: str = "ceo",
    ) -> None:
        """Tell the reaped agent (+ CEO) its stale claim was released.

        Fired from the orchestrator's ``_reap_with_service`` alongside
        ``unclaim_for_reaper``. A reaped task leaves
        ``list_in_progress_or_claimed`` once released to pending, so a
        subsequent reaper tick never re-considers the same claim and this
        cannot double-fire.
        """
        recipients = list(dict.fromkeys(r for r in (reaped_agent, to_ceo) if r))
        if not recipients:
            return
        logger.info(
            "Sending stale-claim-reaped notification",
            task_id=task_id,
            reaped_agent=reaped_agent,
        )
        body = (
            f"Task {task_id}'s claim went stale "
            f"(last heartbeat: {last_heartbeat or 'unknown'}) and was reaped "
            f"back to pending, releasing it from {reaped_agent or 'its holder'}."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.HIGH,
                from_agent=from_agent,
                to_agents=recipients,
                subject=f"Task {task_id}: stale claim reaped",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_ack_notification(
        self,
        *,
        from_agent: UUID | str,
        to_agent: str,
        body: str,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        task_id: UUID | str | None = None,
    ) -> None:
        """Send a free-form ack-required notification (PM/Board only).

        Used by the gateway `notify` content-tool. Distinguishes from
        the typed `send_*_notification` helpers above, which carry
        lifecycle semantics (blocker, qa-ready, etc.). Here the caller
        supplies the body verbatim. ALERT type is used so consumers
        treat it as a high-attention formal signal rather than
        conflating with task-state-driven notifications. The subject
        is derived from the first line of `body` (truncated), matching
        how `dm` derives a subject from free text.
        """
        subject = body.split("\n", 1)[0][:200] or "Notification"
        related_task_id = str(task_id) if task_id is not None else None
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=priority,
                from_agent=str(from_agent),
                to_agents=[to_agent],
                subject=subject,
                body=body,
                related_task_id=related_task_id,
            )
        )

    async def send_broadcast_notification(
        self,
        *,
        from_agent: UUID | str,
        text: str,
        subject: str | None = None,
    ) -> None:
        """Broadcast a company-wide announcement to every agent's inbox.

        The channel-era ANNOUNCE / RELAY_MESSAGE directives posted to a channel
        no agent read; this delivers to the notification inbox agents DO drain.
        Recipients are every non-human agent in the roster.
        """
        from roboco.foundation.identity import AGENTS

        recipients = [slug for slug, row in AGENTS.items() if not row.is_human]
        subject = (subject or text.split("\n", 1)[0])[:200] or "Announcement"
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.BROADCAST,
                priority=NotificationPriority.NORMAL,
                from_agent=str(from_agent),
                to_agents=recipients,
                subject=subject,
                body=text,
            )
        )

    async def send_a2a_notification(
        self,
        task_id: str,
        a2a_context: dict[str, Any],
    ) -> None:
        """Send notification for A2A request (when recipient is busy or offline).

        Args:
            task_id: Related task ID
            a2a_context: Dict with from_agent, to_agent, skill, message,
                priority. `priority` is a `NotificationPriority` (full
                tristate: NORMAL / HIGH / URGENT). This key used to be
                `urgent: bool`, which collapsed HIGH to NORMAL —
                A2AService now sends Priority directly.
        """
        from_agent = a2a_context.get("from_agent", "unknown")
        to_agent = a2a_context.get("to_agent", "")
        skill = a2a_context.get("skill", "general")
        message = a2a_context.get("message", "")
        priority = a2a_context.get("priority", NotificationPriority.NORMAL)
        # Defensive coerce — accept enum, str, or a stray bool from a
        # legacy caller. The point is that HIGH survives, so only collapse
        # to URGENT/NORMAL if the input is genuinely a bool.
        if isinstance(priority, bool):
            priority = (
                NotificationPriority.URGENT if priority else NotificationPriority.NORMAL
            )
        elif not isinstance(priority, NotificationPriority):
            try:
                priority = NotificationPriority(str(priority))
            except ValueError:
                priority = NotificationPriority.NORMAL

        logger.info(
            "Sending A2A notification",
            task_id=task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            skill=skill,
            priority=priority.value,
        )

        # Cosmetic [URGENT] prefix stays urgent-only. HIGH is recorded at
        # the NotificationTable.priority column but gets no body/subject
        # prefix — the column is the source of truth for routing, the
        # label is just an attention hint for the human-readable body.
        urgency_label = "[URGENT] " if priority == NotificationPriority.URGENT else ""
        body = (
            f"{urgency_label}A2A request from {from_agent}.\n\n"
            f"Skill: {skill}\n\n"
            f"Message: {message}"
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.A2A_REQUEST,
                priority=priority,
                from_agent=from_agent,
                to_agents=[to_agent],
                subject=f"{urgency_label}A2A: {skill}",
                body=body,
                related_task_id=task_id,
            )
        )

    @staticmethod
    def _notification_type_label(params: CreateNotificationParams) -> str:
        """Render the notification_type for a log line."""
        nt = params.notification_type
        return nt.value if hasattr(nt, "value") else str(nt)

    async def _resolve_recipients(
        self, db: Any, params: CreateNotificationParams
    ) -> list[UUID]:
        """Resolve to_agents (slugs/UUIDs) to UUID list. Drops unresolvable.

        notifications.to_agents is UUID[] — callers across the codebase
        pass slugs ("be-dev-1", "be-qa"). Resolve every recipient before
        insert; drop (with warn) any that don't resolve instead of
        letting asyncpg crash with "invalid UUID 'be-dev-1'".
        """
        to_agents_uuids: list[UUID] = []
        unresolved: list[str] = []
        for recipient in params.to_agents:
            resolved = await _resolve_agent_uuid(db, recipient)
            if resolved is None:
                unresolved.append(str(recipient))
            else:
                to_agents_uuids.append(resolved)
        if unresolved:
            logger.warning(
                "Dropping unresolved notification recipients",
                unresolved=unresolved,
                type=self._notification_type_label(params),
                subject=params.subject[:80],
            )
        return to_agents_uuids

    async def _duplicate_unacked_exists(
        self,
        db: AsyncSession,
        *,
        from_agent_uuid: UUID,
        params: CreateNotificationParams,
        to_agents_uuids: list[UUID],
    ) -> bool:
        """True when an unacked same-purpose notification already exists.

        Purpose-based dedup (CEO directive, 2026-06-10): same sender, type,
        task, EQUAL recipient set, while a prior one is still unacked —
        agents re-send the same signal (often reworded) and each copy inflates
        the recipient's unacked set, soft-blocking i_am_idle and driving respawn
        churn. Body text is NOT compared. Dedup applies only to ACTION-REQUIRED
        types; informational carries distinct content per send and acking is
        voluntary, so deduping them would silently drop broadcasts.

        Recipient set must be EXACTLY equal — overlapping-but-not-equal sets
        do NOT suppress. A blocker sent to {be-pm, main-pm} after an unacked
        one to {be-pm} alone must reach main-pm (the prior's recipients are a
        strict subset). Overlap is the SQL filter; the exact-set-equality check
        runs in Python against the fetched candidate rows.
        """
        related = params.related_task_id
        if not ACK_REQUIRED_BY_TYPE.get(params.notification_type, True):
            return False
        new_set = set(to_agents_uuids)
        dup_q = (
            select(NotificationTable.id, NotificationTable.to_agents)
            .where(NotificationTable.from_agent == from_agent_uuid)
            .where(NotificationTable.type == params.notification_type)
            .where(NotificationTable.to_agents.overlap(to_agents_uuids))
            .where(~NotificationTable.acked_by.contains(to_agents_uuids))
            .where(
                NotificationTable.related_task_id == related
                if related is not None
                else NotificationTable.related_task_id.is_(None)
            )
        )
        result = await db.execute(dup_q)
        for row in result.all():
            if set(row[1]) == new_set:
                logger.info(
                    "Suppressed duplicate notification (same purpose, unacked)",
                    from_agent=str(from_agent_uuid),
                    type=params.notification_type.value,
                    related_task_id=str(related) if related is not None else None,
                    to_agents=[str(a) for a in to_agents_uuids],
                )
                return True
        return False

    async def _create_notification(self, params: CreateNotificationParams) -> None:
        """Create a notification via the database and deliver it."""
        async with get_db_context() as db:
            from_agent_uuid = await _resolve_agent_uuid(db, params.from_agent)
            if from_agent_uuid is None:
                # notifications.from_agent is NOT NULL + FK to agents.id, so
                # we cannot insert. Skip-with-warn rather than crash the
                # upstream request.
                logger.warning(
                    "Skipping notification: from_agent unresolvable",
                    from_agent_input=str(params.from_agent),
                    type=self._notification_type_label(params),
                    subject=params.subject[:80],
                    to_agents=[str(a) for a in params.to_agents],
                )
                return
            to_agents_uuids = await self._resolve_recipients(db, params)
            if not to_agents_uuids:
                logger.warning(
                    "Skipping notification: no resolvable recipients",
                    to_agents_input=[str(a) for a in params.to_agents],
                    type=self._notification_type_label(params),
                    subject=params.subject[:80],
                )
                return
            # Re-fire guard for loop-prone types: a 60s Redis SET-NX window
            # coalesces the per-tick re-notify storm the DB dedup below skips
            # (these types are requires_ack=False). Fail-open on Redis down.
            if await all_recipients_recently_notified(
                ntype=params.notification_type,
                from_agent=from_agent_uuid,
                recipients=to_agents_uuids,
                related_task_id=params.related_task_id,
                subject=params.subject,
            ):
                logger.info(
                    "Suppressed re-fire notification (loop-prone, recent window)",
                    from_agent=str(from_agent_uuid),
                    type=params.notification_type.value,
                    related_task_id=str(params.related_task_id)
                    if params.related_task_id is not None
                    else None,
                    to_agents=[str(a) for a in to_agents_uuids],
                )
                return
            # Purpose-based dedup (CEO directive, 2026-06-10): suppress a second
            # notification for the SAME purpose while a prior one is unacked. See
            # ``_duplicate_unacked_exists`` for the rationale + the action-only
            # scope (informational types carry distinct content per send).
            if await self._duplicate_unacked_exists(
                db,
                from_agent_uuid=from_agent_uuid,
                params=params,
                to_agents_uuids=to_agents_uuids,
            ):
                return
            notification = NotificationTable(
                type=params.notification_type,
                priority=params.priority,
                from_agent=from_agent_uuid,
                to_agents=to_agents_uuids,
                subject=params.subject,
                body=params.body,
                related_task_id=params.related_task_id,
                # requires_ack follows ACK_REQUIRED_BY_TYPE (action-required vs
                # informational), not the column's True default; default True
                # for an unmapped type preserves the safe action-required bias.
                requires_ack=ACK_REQUIRED_BY_TYPE.get(params.notification_type, True),
            )
            db.add(notification)
            await db.flush()

            # Deliver via Redis Streams for real-time push
            from roboco.services.notification_delivery import (
                get_notification_delivery_service,
            )

            delivery_service = get_notification_delivery_service(db)
            await delivery_service.deliver(require_uuid(notification.id))

            await db.commit()

            logger.info(
                "Notification created and delivered",
                notification_id=str(notification.id),
                type=params.notification_type.value,
            )
