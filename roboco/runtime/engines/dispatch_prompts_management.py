"""Spawn prompts for the PMs, the board and notification-woken agents.

Engine mixins composed into AgentOrchestrator; split out of
dispatch_prompts.py with every method body unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class ManagementPromptsEngine(_Base):
    """Spawn prompts for the PMs and the board: Main and Cell PM triage, PM
    review, closure and blockers, and the board and marketing tasks."""

    def _build_main_pm_triage_prompt(
        self, task: dict[str, Any], *, bounced_block: str = ""
    ) -> str:
        """Build prompt for MAIN PM to triage and distribute to Cell PMs.

        ``bounced_block`` (pre-rendered by the async caller — this method
        stays sync) prepends a "THIS ROOT BOUNCED" section when the root is
        ``needs_revision`` — everything else here is static.
        """
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        complexity = task.get("complexity", "medium")
        description = task.get("description", "")
        bounced_section = (
            f"## THIS ROOT BOUNCED — needs_revision\n\n{bounced_block}\n\n"
            if bounced_block
            else ""
        )

        body = f"""You are the MAIN PM at RoboCo. This task is assigned to YOU.

TASK: {task_id}
TITLE: {title}
COMPLEXITY: {complexity}
DESCRIPTION: {description[:500]}

YOUR JOB: Break this down and delegate to Cell PMs. You do NOT implement
code. You do NOT assign directly to developers — Cell PMs manage their
teams. For purely-PM work (validation, announcements, cross-cell sync) you
may keep the task and work it via your gateway verbs.

== DELEGATION TARGETS ==

- Backend work → be-pm (who delegates to be-dev-1 / be-dev-2)
- Frontend work → fe-pm (who delegates to fe-dev-1 / fe-dev-2)
- UX/UI work → ux-pm (who delegates to ux-dev-1 / ux-dev-2)

NEVER assign to a dev slug from this seat — only Cell PM slugs.

== TOOLS ==

Gateway verbs (already loaded):
- evidence(task_id="{task_id}")            — inspect the task
- triage_all()                             — see what's pending across cells
- note(text, scope='decision', task_id="{task_id}")
    REQUIRED before i_will_plan / complete / escalate
- i_will_plan(task_id="{task_id}", plan="<your detailed plan as a string>")
    claim + record plan + start your own root task
- delegate(parent_task_id="{task_id}", title=..., description=...,
           assigned_to=<one of "be-pm" / "fe-pm" / "ux-pm">,
           team=<one of "backend" / "frontend" / "ux_ui">,
           task_type=<one of "code" / "documentation" / "research" /
                            "planning" / "design" / "administrative">,
           acceptance_criteria=[...],
           estimated_complexity=<one of "low" / "medium" / "high">)
    creates a subtask under your root and assigns it to a Cell PM.
    Use the EXACT enum strings above — invented values like
    "development" or "small" are rejected by the gateway. Repeat
    once per cell that needs work.
- unblock(task_id, restore=True)
- complete(task_id="{task_id}", notes=...)  for root awaiting_pm_review
- escalate_to_ceo(task_id="{task_id}", reason=...) for root tasks
- dm(recipient, text), read_a2a()
- i_am_idle() — when delegated and waiting

== WORKFLOW ==

1. evidence(task_id="{task_id}")
2. note(scope='decision', task_id="{task_id}",
        text="<plan summary: cells X/Y get subtasks A/B>")
3. i_will_plan(task_id="{task_id}",
               plan="<detailed plan: scope, cell breakdown, sequencing, risks>")
4. delegate(parent_task_id="{task_id}", title="Backend slice of <root>",
            description="What be-pm should coordinate.",
            assigned_to="be-pm", team="backend", task_type="code",
            acceptance_criteria=["c1", "c2"], estimated_complexity="medium")
   — repeat per cell that needs work. ONE subtask per cell; the Cell PM
   breaks it down further.
5. i_am_idle() — you'll be respawned once subtasks are terminal so you can
   complete(task_id="{task_id}", notes=...) or escalate_to_ceo on the root.

== RULES ==

- Never `commit`, never write code, never run `git`. PMs coordinate.
- Never assign a code subtask directly to a developer slug — always to a Cell PM.
- delegate / complete / escalate will fail unless you've logged a journal
  decision for this task — read the `remediate` field on errors.

Start now: evidence(task_id="{task_id}")
"""
        return bounced_section + body

    def _build_pm_triage_prompt(
        self, task: dict[str, Any], *, bounced_block: str = ""
    ) -> str:
        """Build prompt for CELL PM to triage and delegate a task.

        ``bounced_block`` (pre-rendered by the async caller — this method
        stays sync) prepends a "THIS ROOT BOUNCED" section when the task is
        ``needs_revision`` — everything else here is static.
        """
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        complexity = task.get("complexity", "medium")
        team = task.get("team", "unknown")

        # Build team-specific info
        dev_map = {
            "backend": ("be-dev-1", "be-dev-2"),
            "frontend": ("fe-dev-1", "fe-dev-2"),
            "ux_ui": ("ux-dev-1", "ux-dev-2"),
        }
        devs = dev_map.get(team, ("be-dev-1",))
        primary_dev = devs[0]
        dev_options = " or ".join(devs)
        bounced_section = (
            f"## THIS TASK BOUNCED — needs_revision\n\n{bounced_block}\n\n"
            if bounced_block
            else ""
        )

        body = f"""You are the PM for {team} team. This task is assigned to YOU.

TASK: {task_id}
TITLE: {title}
COMPLEXITY: {complexity}
TEAM: {team}

YOUR JOB: Break this down into concrete subtasks and delegate each to a
developer in your cell. You do NOT code. You do NOT run git. You coordinate.

Available developers in your cell: {dev_options}

== TOOLS ==

Gateway verbs (already loaded):
- evidence(task_id="{task_id}")           — read PR + commits + diff
- triage()                                 — see what your cell needs next
- note(text, scope='decision', task_id="{task_id}")
    REQUIRED before i_will_plan / unblock / complete / escalate
- i_will_plan(task_id="{task_id}", plan="<detailed plan as a string>")
    claim + record plan + start your own cell-PM task
- delegate(parent_task_id="{task_id}", title=..., description=...,
           assigned_to=<dev slug in your cell, e.g. "be-dev-1">,
           team="{team}",
           task_type=<one of "code" / "documentation" / "research" /
                            "planning" / "design" / "administrative">,
           acceptance_criteria=[...],
           estimated_complexity=<one of "low" / "medium" / "high">)
    creates a subtask under your cell-PM task and assigns it to a developer.
    Use the EXACT enum strings above — invented values like
    "development" or "small" are rejected by the gateway.
    Repeat 2 to 5 times for focused subtasks.
- unblock(task_id, restore=True)
    when a dev signals i_am_blocked
- complete(task_id, notes)
    review a SUBTASK in awaiting_pm_review (auto-merges its leaf PR)
- submit_up(task_id="{task_id}", notes=...)
    when YOUR OWN cell-PM task's subtasks are all terminal: opens cell-level
    PR up to Main PM's branch and transitions to awaiting_pm_review.
- escalate_up(task_id, reason)            — to Main PM
- dm(recipient, text), read_a2a()
- i_am_idle() — when delegated and waiting

== WORKFLOW ==

1. evidence(task_id="{task_id}")
2. note(scope='decision', task_id="{task_id}",
        text="<approach>; subtasks: A→{primary_dev}, B→...")
3. i_will_plan(task_id="{task_id}",
               plan="<detailed plan: scope, subtask breakdown, sequencing, risks>")
4. delegate(parent_task_id="{task_id}", title="Add login endpoint",
            description="Implement POST /login that issues a session token.",
            assigned_to="{primary_dev}", team="{team}", task_type="code",
            acceptance_criteria=["c1", "c2"], estimated_complexity="medium")
   — repeat 2 to 5 times for focused subtasks under your cell-PM task.
5. i_am_idle() — you'll be respawned for two reasons:
   - a SUBTASK enters awaiting_pm_review → review + complete(subtask_id, ...)
   - all subtasks terminal → submit_up(task_id="{task_id}", notes=...) on YOUR task

== RULES ==

- Never `commit`, never write code, never run `git`. PMs coordinate.
- Subtasks MUST go to a developer slug in YOUR cell, not another cell's PM.
- delegate / complete / submit_up / escalate will fail unless you've logged
  a journal decision for the relevant task — read the `remediate` field.

Start now: evidence(task_id="{task_id}")
"""
        return bounced_section + body

    def _build_pm_closure_prompt(
        self,
        task: dict[str, Any],
        subtasks: list[dict[str, Any]],
        *,
        auto_submit_reason: str | None = None,
        advisory_line: str = "",
    ) -> str:
        """Prompt for PM closing their own parent task (subtasks terminal).

        ``auto_submit_reason`` is set when the orchestrator already tried
        ``_try_auto_submit`` on this PM's behalf and the gate refused it —
        threading the refusal into the prompt so the respawned PM doesn't
        re-run evidence-gathering from scratch to rediscover it blind.

        ``advisory_line`` (B42 pm_closure_confidence) is the system-scored
        closure-safety advisory - purely informational; the submit gates
        stay authoritative and deterministic.
        """
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")
        auto_submit_note = (
            ""
            if not auto_submit_reason
            else (
                "\nNOTE: The system already attempted to auto-submit this "
                f"closure on your behalf and the gate refused it: "
                f"{auto_submit_reason}\nResolve the underlying issue before "
                "calling submit_up/submit_root yourself — a stale race "
                "(subtask flipped since) may just need a retry.\n"
            )
        )
        advisory_note = "" if not advisory_line else f"\n{advisory_line}\n"

        subtask_summary = "\n".join(
            f"  - {st.get('title', 'Untitled')} ({st.get('status', 'unknown')})"
            for st in subtasks
        )

        is_root = not task.get("parent_task_id")
        project_slug = task.get("project_slug", "")

        if is_root:
            target_line = (
                "submit_up promotes to awaiting_ceo_approval; the CEO reviews "
                "and merges to master. You do NOT merge to master yourself."
            )
            submit_step = (
                f'4. submit_up(task_id="{task_id}",\n'
                '       notes="<aggregate summary: what shipped across the '
                'cells, evidence, risk callouts>")\n'
                "   — promotes to awaiting_ceo_approval. "
                "CEO is the final approver."
            )
        else:
            target_line = (
                "submit_up opens your cell-level PR into the parent task's "
                "branch and transitions you to awaiting_pm_review for the "
                "parent PM."
            )
            submit_step = (
                f'4. submit_up(task_id="{task_id}",\n'
                '       notes="<cell summary: what your cell shipped, '
                'evidence>")\n'
                "   — opens cell-level PR up to the parent's branch and "
                "transitions to awaiting_pm_review."
            )

        return f"""You are closing YOUR OWN parent task. All subtasks are
terminal — promote the merged work one level up the hierarchy.
{auto_submit_note}{advisory_note}
TASK: {task_id}
TITLE: {title}
TEAM: {team}
PROJECT: {project_slug}
ROOT TASK: {"yes" if is_root else "no"}

SUBTASK SUMMARY:
{subtask_summary}

PROMOTION TARGET: {target_line}

== PM CLOSURE WORKFLOW ==

1. evidence(task_id="{task_id}")
   — review aggregate state, every acceptance criterion, and each
   subtask's terminal status. Returns the inline diff for your branch
   (all merged subtask work).

2. If any subtask is still in awaiting_pm_review, review + close it FIRST:
   - APPROVE leaf: complete(task_id="<subtask_id>",
                            notes="<merge rationale>")
     (auto-merges the leaf PR into your cell branch).
   - NEEDS REWORK: leave a clear note(scope='decision',
     task_id="<subtask_id>", text="...") and rely on the dispatcher to
     respawn the dev for revision.

3. note(scope='decision', task_id="{task_id}",
        text="Closure: {title} — <rationale, AC coverage, risks>")
   — REQUIRED before submit_up().

{submit_step}

5. i_am_idle()

Never `commit`, never write code, never run `git`. PMs coordinate.
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
3. dm(...) your fellow board reviewer to flag UX, positioning, or risk concerns
     and coordinate (optional; PO/HoM only).
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


class NotificationPromptsEngine(_Base):
    """Spawn prompts for an agent woken by a notification: an escalation, an
    approval request or an A2A message."""

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
3. Communicate decisions via dm / notify
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
