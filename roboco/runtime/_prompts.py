"""AgentOrchestrator mixin for Prompts.

Extracted from orchestrator.py to shrink the monolith.
Provides the ``AgentPromptsMixin`` mixin class.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


class AgentPromptsMixin:
    """Mixin for AgentOrchestrator: Prompts.
    """

    def _build_dev_prompt(self, task: dict[str, Any]) -> str:
        """Build state-aware initial prompt for a developer."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        status = task.get("status", "unknown")

        # Determine workflow state based on task attributes
        has_plan = bool(task.get("plan"))
        workflow_state = self._get_workflow_state(status, has_plan)
        instructions = self._get_workflow_instructions(workflow_state, task_id)

        return f"""You have been assigned a development task.

TASK ID: {task_id}
TITLE: {title}
STATUS: {status}
WORKFLOW STATE: {workflow_state}

{instructions}

Start by calling evidence(task_id="{task_id}") for full details and acceptance criteria.

When out of work: i_am_idle().
"""

    def _build_qa_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a QA agent."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        assigned_to = task.get("assigned_to", "unknown")
        team = task.get("team", "unknown")

        return f"""A task is ready for QA review.

TASK ID: {task_id}
TITLE: {title}
DEVELOPER: {assigned_to}
TEAM: {team}

== QA WORKFLOW ==

1. claim_review(task_id="{task_id}")
   — assigns the QA seat; returns inline diff + PR + commits as evidence.
   The PR is already open (dev opened it before submitting QA);
   review on GitHub if you need more context.
2. Review the implementation against EVERY acceptance criterion.
   Run/read tests; sanity-check the diff for regressions, security,
   and scope creep.
3. Decide:
   - PASS: pass(task_id="{task_id}",
            notes="<>=80 chars: what you verified, which AC, evidence>")
     — transitions awaiting_qa → awaiting_documentation.
   - FAIL: fail(task_id="{task_id}",
            issues=["concrete issue 1", "concrete issue 2", ...])
     — transitions to needs_revision; each issue must be specific and
     actionable.
4. note(scope='reflect'|'learning', task_id="{task_id}", text=...)
   for anything worth flagging.
5. give_me_work() to pick up the next QA item,
   or i_am_idle() if the queue is empty.
"""

    def _build_doc_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a documenter."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        return f"""A task is ready for documentation. The dev's PR is already open
— you're documenting alongside the QA-passed branch.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}

== DOC WORKFLOW ==

1. claim_doc_task(task_id="{task_id}")
   — assigns the doc seat and opens your workspace on the task's branch.
2. evidence(task_id="{task_id}") — read dev handoff notes, qa_notes,
   and the inline diff so the docs reflect what actually shipped.
3. Write/update docs in your workspace: README sections, API references,
   code comments, migration notes, or new docs files as the change requires.
4. commit("docs(scope): <subject, >=20 chars>") per logical doc chunk
   — auto-prefixes the task ID and stages tracked changes.
5. i_documented(task_id="{task_id}",
   notes="<>=20 chars: what you documented and where>",
   files=["docs/foo.md", "README.md", ...])
   — transitions awaiting_documentation → awaiting_pm_review.
6. give_me_work() for the next doc item,
   or i_am_idle() if the queue is empty.
"""

    def _build_pm_review_prompt(self, task: dict[str, Any]) -> str:
        """Prompt for PM reviewing a SUBTASK in awaiting_pm_review."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        return f"""A SUBTASK in your cell is awaiting your PM review.
It has passed QA and documentation; the leaf PR is open and ready to merge.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}

== PM REVIEW WORKFLOW (leaf subtask) ==

1. evidence(task_id="{task_id}")
   — review PR, commits, inline diff, dev_notes, qa_notes, doc files.
2. Spot-check that:
   - every acceptance criterion is satisfied,
   - QA's pass notes line up with the actual diff,
   - docs reflect what shipped.
3. note(scope='decision', task_id="{task_id}",
        text="<approve rationale or rejection reason>")
   — REQUIRED before complete().
4. Decide:
   - APPROVE: complete(task_id="{task_id}", notes="<merge rationale>")
     — auto-merges the leaf PR and finalizes the subtask.
   - NEEDS REWORK: leave a clear note(scope='decision', text="...") and
     rely on the dispatcher to respawn the dev for revision.
     Use escalate_up only if the issue is truly outside your cell.
5. give_me_work() / triage() for the next item, or i_am_idle().

Never `commit`, never write code, never run `git`. PMs coordinate.
"""

    def _build_board_prompt(self, task: dict[str, Any]) -> str:
        """Prompt for a board agent (Product Owner / Head of Marketing) to
        review and SHAPE a strategic task. Board roles advise — they do not
        build, code, or delegate."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        description = task.get("description", "No description")

        return f"""\
You are on the Board. This strategic task is under board review.

TASK: {task_id}
TITLE: {title}
DESCRIPTION: {description}

THE BOARD REVIEWS AS A PAIR: the Product Owner AND the Head of Marketing both
review every board task before it reaches the CEO. The Product Owner owns
product requirements + acceptance scope; the Head of Marketing owns the UX /
user-facing / positioning dimension. The CEO only gets the handoff after BOTH
of you have recorded a review.

YOUR ROLE: review and shape this work. You do NOT build, code, claim, or
delegate — those verbs are not yours. Your deliverable is a recorded review.

== WHAT TO DO ==

1. triage()
     — see your board-level work and context.
2. note(text="<the product requirements and acceptance criteria you expect, the
        scope, the must-haves, and what 'done' looks like — Head of Marketing:
        the UX, user-facing impact, and how the feature is positioned>",
        scope='decision', task_id="{task_id}")
     — this recorded review is how the CEO and Main PM act on your input.
3. say(...) in your board channel to flag UX, positioning, or risk concerns and
     to coordinate with your fellow board reviewer.
4. i_am_idle()
     — when your review is recorded. Once both board reviewers are done, the
       CEO is notified the task is ready for Approve & Start, then routes it to
       Main PM for delegation to the cells; you do NOT hand it off yourself.

Do NOT attempt to claim, plan, complete, or delegate — the gateway will reject
those, and a substantive recorded note IS your job here.
"""

    def _build_marketing_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for head-marketing with a marketing task."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        description = task.get("description", "No description")

        return f"""You have been assigned a marketing task.

TASK ID: {task_id}
TITLE: {title}
DESCRIPTION: {description}

Begin work:

1. Review the task details above (full acceptance criteria arrive in your
   briefing / the give_me_work response)
2. Execute the marketing task (content, campaigns, research, etc.)
3. Coordinate with Product Owner or Main PM if needed
4. Call i_am_done() when done
5. Call give_me_work() to check for more marketing work
6. If no more work, call i_am_idle() to shutdown gracefully
"""

    def _build_pm_blocker_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a Cell PM handling a blocker."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        assigned_to = task.get("assigned_to", "unknown")
        blocker = task.get("blocker", {})
        reason = blocker.get("reason", "Unknown")
        what_needed = blocker.get("what_needed", "Unknown")

        return f"""A task in your cell is BLOCKED and needs your attention.

TASK ID: {task_id}
TITLE: {title}
ASSIGNED TO: {assigned_to}
BLOCKER REASON: {reason}
WHAT'S NEEDED: {what_needed}

Your job:

1. Understand the blocker by reviewing task details
2. Communicate with the blocked developer if needed
3. Resolve the blocker (coordinate resources, make decisions, escalate if needed)
4. Once resolved, call unblock("{task_id}") to release the task back to the developer
5. Call triage() to check for other blocked tasks in your cell
6. If no more blockers, call i_am_idle() to shutdown gracefully
"""

    def _build_escalation_prompt(self, notification: dict[str, Any]) -> str:
        """Build initial prompt for handling an escalation."""
        notif_id = notification.get("id", "unknown")
        from_agent = notification.get("from_agent", "unknown")
        subject = notification.get("subject", "No subject")
        priority = notification.get("priority", "normal")
        body = notification.get("body", "No details provided")

        return f"""You have received an ESCALATION that requires your attention.

FROM: {from_agent}
SUBJECT: {subject}
PRIORITY: {priority}

DETAILS:
{body}

Your job:

1. Acknowledge the notification with notify_ack("{notif_id}")
2. Assess the escalation and determine action needed
3. Communicate decisions via appropriate channels
4. If this requires further escalation, use escalate_up()
5. When resolved, call triage() for other work
6. If no more work, call i_am_idle() to shutdown gracefully
"""

    def _build_approval_prompt(self, notification: dict[str, Any]) -> str:
        """Build initial prompt for handling an approval request."""
        notif_id = notification.get("id", "unknown")
        from_agent = notification.get("from_agent", "unknown")
        subject = notification.get("subject", "No subject")
        related_task_id = notification.get("related_task_id", "None")
        body = notification.get("body", "No details provided")

        return f"""You have received an APPROVAL REQUEST.

FROM: {from_agent}
SUBJECT: {subject}
RELATED TASK: {related_task_id}

REQUEST:
{body}

Your job:

1. Review the approval request carefully
2. If related to a task, use the task context provided in your briefing
3. Make your decision and communicate it
4. Acknowledge with notify_ack("{notif_id}")
5. Call triage() for other work
6. If no more work, call i_am_idle() to shutdown gracefully
"""

    def _build_audit_prompt(self, alert: dict[str, Any] | None = None) -> str:
        """Build initial prompt for the auditor."""
        if alert:
            subject = alert.get("subject", "Quality issue detected")
            body = alert.get("body", "Review system quality metrics")

            return f"""QUALITY ALERT triggered your attention.

ALERT: {subject}
DETAILS: {body}

Your job:

1. Investigate the quality issue
2. Review relevant channels and task history (you have read access to all)
3. Compile your findings
4. Report to CEO via appropriate channel
5. Call i_am_idle() when complete
"""

        return """Periodic AUDIT requested.

Your job:

1. Review recent activity across all cells
2. Check quality metrics (QA pass/fail rates, blocker frequency, etc.)
3. Identify any concerns or patterns
4. Compile audit report for CEO
5. Call i_am_idle() when complete
"""

    def _build_a2a_prompt(self, notification: dict[str, Any]) -> str:
        """Build initial prompt for handling an A2A (Agent-to-Agent) request.

        Reads `priority` directly off the notification row (set by
        NotificationService.send_a2a_notification). Pre-Phase-3 this
        consumed a non-existent `metadata.urgent` and always rendered
        urgency_note=False; the column-level priority is now the source
        of truth.
        """
        notif_id = notification.get("id", "unknown")
        from_agent = notification.get("from_agent", "unknown")
        body = notification.get("body", "No message provided")
        related_task_id = notification.get("related_task_id")
        metadata = notification.get("metadata", {})
        skill = metadata.get("skill", "general")
        priority_raw = notification.get("priority", "normal")

        # URGENT gets the bold attention-grabber; HIGH gets a quieter
        # "higher priority" hint; NORMAL gets no prefix.
        if priority_raw == "urgent":
            urgency_note = "**URGENT** - This request has priority.\n\n"
        elif priority_raw == "high":
            urgency_note = "**HIGH PRIORITY** - Please handle promptly.\n\n"
        else:
            urgency_note = ""
        task_note = f"RELATED TASK: {related_task_id}\n" if related_task_id else ""

        return f"""You have received an A2A (Agent-to-Agent) REQUEST.

{urgency_note}FROM: {from_agent}
SKILL: {skill}
{task_note}
REQUEST:
{body}

Your job:

1. Acknowledge the notification with notify_ack("{notif_id}")
2. Process the request using your {skill} capabilities
3. Respond to {from_agent} using dm("{from_agent}", ...)
4. If you need task context, it is provided in your briefing for the related task
5. When done, call give_me_work() for other work
6. If no more work, call i_am_idle() to shutdown gracefully
"""
