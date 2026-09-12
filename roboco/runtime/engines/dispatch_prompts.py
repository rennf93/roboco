"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: dispatch_prompts)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.agents_config import (
    get_agent_role,
)
from roboco.config import settings
from roboco.foundation.policy.content import markers as _markers
from roboco.runtime.orchestrator import (
    _PROMPT_FINDINGS_CAP,
    _format_barfly_candidates,
    _format_rejected_spotlights,
    _format_seen_features,
    _format_shipped_since,
    _render_open_finding_prompt_line,
    _shipped_digest_block,
    logger,
)
from roboco.services.task import (
    VIDEO_SOURCE,
)

if TYPE_CHECKING:
    from collections.abc import Callable


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class DispatchPromptsEngine(_Base):
    """Mixin holding the "dispatch_prompts" methods moved out of AgentOrchestrator."""

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
    ) -> str:
        """Prompt for PM closing their own parent task (subtasks terminal).

        ``auto_submit_reason`` is set when the orchestrator already tried
        ``_try_auto_submit`` on this PM's behalf and the gate refused it —
        threading the refusal into the prompt so the respawned PM doesn't
        re-run evidence-gathering from scratch to rediscover it blind.
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
{auto_submit_note}
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

    async def _get_prompt_for_agent(self, agent_slug: str, task: dict[str, Any]) -> str:
        """Get the prompt appropriate to the agent's ACTUAL role.

        A respawn must hand each role the prompt it can act on — a PM or board
        agent handed the developer prompt is told to write code and call verbs
        it does not own. Reuses the same per-role prompt builders the role
        dispatchers use so a respawn matches a fresh dispatch:

          developer      → dev prompt (REVISION_REQUIRED findings inline)
          qa             → QA prompt
          documenter     → doc prompt
          cell_pm        → cell-PM triage prompt (+ bounced-block if needs_revision)
          main_pm        → main-PM triage prompt (+ bounced-block if needs_revision)
          product_owner  → board-review prompt
          head_marketing → marketing prompt for a marketing task, else board
          auditor        → audit prompt

        Unknown roles fall back to the dev prompt (safe default for an
        executable task).
        """
        role = get_agent_role(agent_slug)
        # head_marketing is the one role whose prompt depends on the task, so it
        # is resolved before the static role→builder table.
        if role == "head_marketing":
            if task.get("team") == "marketing":
                return self._build_marketing_prompt(task)
            return self._build_board_prompt(task)
        if role == "cell_pm":
            return self._build_pm_triage_prompt(
                task, bounced_block=await self._revision_bounced_block(task)
            )
        if role == "main_pm":
            return self._build_main_pm_triage_prompt(
                task, bounced_block=await self._revision_bounced_block(task)
            )
        builders: dict[str, Callable[[dict[str, Any]], str]] = {
            "qa": self._build_qa_prompt,
            "documenter": self._build_doc_prompt,
            "product_owner": self._build_board_prompt,
            "auditor": lambda _task: self._build_audit_prompt(),
            "pr_reviewer": self._build_pr_review_prompt,
        }
        if role in builders:
            return builders[role](task)
        # "developer" plus every unknown role: the dev prompt is the safe
        # default for an executable task.
        return await self._build_dev_prompt(task)

    async def _open_findings_prompt_block(self, task_id: str) -> str:
        """Render up to ``_PROMPT_FINDINGS_CAP`` open revision-ledger findings
        for a dispatch prompt: one ``[F-<id8>] file:line — expected -> actual
        -> fix`` line each, plus a "+N more" overflow line past the cap.

        Returns "" for no task_id, no open findings, or any DB error
        (fail-open — the agent still has evidence()/triage() to read the
        full ledger).
        """
        if not task_id:
            return ""
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.repositories.review_findings import (
            STATUS_OPEN,
            ReviewFindingsRepository,
        )

        try:
            async with get_db_context() as db:
                rows = await ReviewFindingsRepository(db).list_for_task(
                    UUID(task_id), status=STATUS_OPEN, limit=_PROMPT_FINDINGS_CAP + 1
                )
        except Exception:
            logger.warning("open findings prompt fetch failed", task_id=task_id)
            return ""
        if not rows:
            return ""
        shown = rows[:_PROMPT_FINDINGS_CAP]
        lines = [_render_open_finding_prompt_line(row) for row in shown]
        if len(rows) > _PROMPT_FINDINGS_CAP:
            lines.append(f"... +{len(rows) - _PROMPT_FINDINGS_CAP} more via evidence()")
        return "\n".join(lines)

    async def _revision_bounced_block(self, task: dict[str, Any]) -> str:
        """The rendered open-findings block for a bounced PM prompt, or ""
        when the task isn't needs_revision (skips the DB fetch otherwise)."""
        if task.get("status") != "needs_revision":
            return ""
        return await self._open_findings_prompt_block(str(task.get("id") or ""))

    def _get_workflow_state(
        self,
        status: str,
        has_plan: bool,
    ) -> str:
        """Determine developer workflow state from task attributes.

        Args:
            status: Task status (claimed, in_progress, needs_revision, etc.)
            has_plan: Whether task has a plan submitted

        Returns:
            Workflow state string (NEEDS_PLAN, READY_TO_START, EXECUTING, etc.)
        """
        # Direct status mappings
        status_map = {
            "in_progress": "EXECUTING",
            "needs_revision": "REVISION_REQUIRED",
            "verifying": "VERIFYING",
        }

        if status in status_map:
            return status_map[status]

        # Handle claimed status with sub-states
        if status == "claimed":
            if not has_plan:
                return "NEEDS_PLAN"
            return "READY_TO_START"

        return status.upper()

    def _get_workflow_instructions(
        self, state: str, task_id: str, open_findings_block: str = ""
    ) -> str:
        """Get workflow instructions for the given state.

        Args:
            state: Workflow state (NEEDS_PLAN, READY_TO_START, etc.)
            task_id: Task ID for tool call examples
            open_findings_block: pre-rendered open-findings text for
                REVISION_REQUIRED (fetched by the async caller — this method
                stays sync and just threads the string through).

        Returns:
            Markdown-formatted instructions for the current state
        """
        findings_section = (
            f"\nOpen findings:\n{open_findings_block}\n"
            if open_findings_block
            else "\n(no findings on the ledger for this task)\n"
        )
        instructions = {
            "NEEDS_PLAN": f"""## NEXT STEP: Claim + Plan + Start

Call i_will_work_on(task_id="{task_id}",
    plan="<approach, ordered steps, risks, open questions>").

This single verb claims the task, records your plan, and transitions
to in_progress.
""",
            "READY_TO_START": f"""## NEXT STEP: Start Work

Call i_will_work_on(task_id="{task_id}", plan="<your plan as a string>")
to begin.
""",
            "EXECUTING": """## IN PROGRESS

Continue development. Required gates before i_am_done() will succeed
(enforced server-side — `remediate` tells you what's missing):
1. commit("<type(scope): subject, >=20 chars>")
   — makes the git commit, auto-prefixes task ID, records progress.
   Repeat per meaningful chunk.
2. note(scope='decision'|'learning'|'reflect', task_id="...", text=...)
   as you make trade-offs.

When acceptance criteria are met, call
open_pr(task_id="...") to push your branch and open the PR,
then i_am_done(task_id="...", notes="<self-verification summary>")
to submit for QA review.

If you hit something you can't unblock yourself:
i_am_blocked(task_id="...",
    reason="<blocked_external|low_context|...>").
""",
            "REVISION_REQUIRED": f"""## REVISION REQUESTED
{findings_section}
1. i_will_work_on(task_id="{task_id}",
   plan="<revised plan addressing each finding>")
2. commit() the fixes, then
   i_am_done(task_id="{task_id}", notes="<what was fixed>",
   resolved_findings=[{{"finding_id": "<id>", "commit": "<sha>",
   "note": "<what changed>"}}, ...])

evidence(task_id="{task_id}") for the full diff + revision_findings if you
need more than the excerpt above.
""",
            "VERIFYING": f"""## SELF-VERIFICATION

Run the project's quality checks against acceptance criteria:
1. Run tests, lint, type checks in your workspace.
2. evidence(task_id="{task_id}") — sanity-check inline diff + commits.
3. If everything passes:
   i_am_done(task_id="{task_id}", notes="<verification summary>")
   — chains submit_verification + push + create_pr + submit_qa.
4. If issues found: commit() the fixes and retry.
""",
            "WORK_ALREADY_DONE": f"""## WORK ALREADY DONE

Your task already has commits and an open PR — the work appears complete. The
server's fast path runs the slimmed gate set (ownership / commits / PR / open
findings / acceptance criteria / branch-pushed / not-behind-base / conventions /
CI-green) for you, so skip re-verification and submit directly:

  i_am_done(task_id="{task_id}", notes="<one-line summary of what was done>")

If the fast path refuses (a gate it checks is not actually met), the
`remediate` field names exactly what's missing — fix it and retry i_am_done.
""",
        }
        return instructions.get(
            state, f'Call evidence(task_id="{task_id}") to check status.'
        )

    def _video_prompt_block(self) -> str:
        """Video-authoring instructions appended for a ``source=VIDEO_SOURCE``
        task: build + propose the composition, then render it and eyeball
        every keyframe — a clean source review is not evidence the RENDERED
        clip is right; the frames are."""
        return """
## VIDEO TASK — verify the RENDERED clip, not just the source

1. Build/extend the composition under motion/compositions/<id>/ per the brief.
2. propose_video(composition_id="<id>", x_caption="...", tiktok_caption="...",
   platforms=[...], input_props={<real facts from the brief>}) — once.
3. request_render() renders your actual composition and
   returns keyframe PNG paths — Read every one. Confirm each scene/feature
   named in the brief appears fully and legibly, and that the authored
   data-duration actually covers all scenes (nothing cut off or rushed).
4. A frame doesn't prove it: fix the composition, then request_render()
   again. Repeat until every frame checks out.

Craft bar (motion/README.md "Cinematography & rhythm"): storyboard a shot
list before building — camera moves (pk-camera data-shots) and a cursor
that behaves like a hand (pk-cursor data-waypoints). A locked-off camera,
a frozen or popping cursor, or metronomic identical beats are automatic
revision. Clip windows are for structural layers ONLY — drive beats with
base-hidden delayed CSS animations, or the renderer drops your tail scenes.
Read the vendored renderer doctrine in motion/skills/ (hyperframes-core,
-keyframes, -creative) before authoring. You have browser tools (playwright
MCP) on this task: open your composition HTML (file://.../vertical.html)
and watch it live while iterating — do not build blind between renders.

Captions (x_caption / tiktok_caption, see motion/README.md "Captions"):
- X: hook line naming the concrete capability, then 1-2 lines of specifics
  (real feature names/numbers, not adjectives), short outro. No hashtag
  spam. Target well under 240 characters.
- TikTok: hook + 2-3 short punchy lines + 3-5 relevant niche hashtags max
  (never generic filler tags).
- Same slop-ban as the design bar (motion/README.md "AI tells to avoid"):
  no em dashes, no filler verbs ("Elevate", "Seamless", "Unleash").

i_am_done() refuses without a stamped render preview — a clean self-review of
the source is not enough; the rendered frames are the evidence.
"""

    async def _build_dev_prompt(self, task: dict[str, Any]) -> str:
        """Build state-aware initial prompt for a developer."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        status = task.get("status", "unknown")
        description = task.get("description") or ""

        # Determine workflow state based on task attributes
        has_plan = bool(task.get("plan"))
        workflow_state = self._get_workflow_state(status, has_plan)
        # Possibilities-matrix prompt proxy: when the flag is armed and the
        # task already has commits + an open PR, steer the dev to submit in one
        # turn instead of re-deriving (re-running gates, re-reading the diff).
        # This is a cheap sync proxy — the async DB gates (AC coverage, open
        # findings) are NOT re-checked here; the server fast path
        # (_i_am_done_fast_path) is the authority and runs them. The prompt
        # just collapses the 3-5-turn re-derivation to a single i_am_done call.
        if (
            settings.possibilities_matrix_enabled
            and status in ("claimed", "in_progress")
            and task.get("pr_created")
            and task.get("commits")
        ):
            workflow_state = "WORK_ALREADY_DONE"
        open_findings_block = ""
        if workflow_state == "REVISION_REQUIRED":
            open_findings_block = await self._open_findings_prompt_block(str(task_id))
        instructions = self._get_workflow_instructions(
            workflow_state, task_id, open_findings_block
        )
        video_block = (
            self._video_prompt_block() if task.get("source") == VIDEO_SOURCE else ""
        )

        # The task spec travels with the prompt so the dev starts with the
        # actual ask (file:line targets, constraints, the intake's rationale)
        # instead of hunting in the fog. evidence() carries the full upstream
        # ancestor chain on top; this is the leaf's own brief.
        desc_block = f"DESCRIPTION:\n{self._description_body(description)}"

        return f"""You have been assigned a development task.

TASK ID: {task_id}
TITLE: {title}
STATUS: {status}
WORKFLOW STATE: {workflow_state}

{desc_block}

Treat the description and any upstream technical detail you receive via
evidence() as authoritative ground truth — file:line targets, code examples,
and constraints come from the intake analysis and PM decomposition. Re-articulate
only the HOW (the solution); the WHAT is already decided upstream.

{instructions}
{video_block}
Start by calling evidence(task_id="{task_id}") for full details, acceptance
criteria, and the upstream parent/ancestor context (the original intake analysis).

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

    def _build_pr_review_prompt(self, task: dict[str, Any]) -> str:
        """Build the initial prompt for the PR reviewer on an external PR."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        pr_number = task.get("pr_number", "?")
        pr_url = task.get("pr_url", "")

        return f"""An external contributor opened a pull request. Review it.

TASK ID: {task_id}
TITLE: {title}
EXTERNAL PR: #{pr_number}  {pr_url}

== TRUST BOUNDARY ==
This PR is from OUTSIDE the org — the code is untrusted. The review is
READ-ONLY: you read the diff, you do NOT fetch, check out, build, or run the
contributor's code. Do not push to their fork. You never merge.

== REVIEW WORKFLOW ==

1. claim_pr_review(task_id="{task_id}")
   — starts the review; returns the contributor's unified diff inline.
2. Review the diff adversarially: correctness, security (injection, secret
   leaks, supply-chain/dependency risk), scope, and the codebase's standards.
   Reason about it from the diff alone — do not run it.
3. note(scope="learning", task_id="{task_id}", text="<what the review surfaced>")
   — required before you can post.
4. post_pr_review(task_id="{task_id}",
        body="<one complete change-request: per-finding file + line + expected
        vs actual; be specific and actionable>",
        event="REQUEST_CHANGES")
   — posts ONE complete review to the PR and finishes the task. Use
   event="APPROVE" only if the PR is genuinely ready as-is.
5. i_am_idle() when done.
"""

    def _build_pr_gate_prompt(self, task: dict[str, Any]) -> str:
        """Build the prompt for a reviewer on an in-path assembled-PR gate task."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")
        pr_number = task.get("pr_number", "?")
        pr_url = task.get("pr_url", "")
        criteria = task.get("acceptance_criteria") or []
        crit_block = (
            "\n".join(f"  - {c}" for c in criteria) if criteria else "  (none recorded)"
        )
        return f"""\
An assembled pull request is ready for review before the PM merges it.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}
ASSEMBLED PR: #{pr_number}  {pr_url}

== WHAT YOU ARE REVIEWING ==
This is the gate BEFORE the merge — the merge-level review QA does not do. You
review the ASSEMBLED diff (the whole cell→root or root→master PR), not a single
leaf, against the original intent and the contract between cells. The bug class
this catches lives in the seam (e.g. a frontend that sends a string where the
backend requires a UUID) — invisible to any single-cell QA. Read-only: you
never push or merge.

ACCEPTANCE CRITERIA (the assembled work must satisfy ALL of these):
{crit_block}

== REVIEW WORKFLOW ==

1. claim_gate_review(task_id="{task_id}")
   — claims the review; returns the assembled diff + acceptance criteria inline.
2. Review the diff against the objective + every acceptance criterion + the
   FE↔BE / cross-cell contract. Do not lose scope: the assembled thing must
   actually do what was asked.
3. note(scope="learning", task_id="{task_id}", text="<what the review surfaced>")
   — required before you pass or fail.
4a. pr_pass(task_id="{task_id}", notes="<how you verified the assembled work>")
    — if correct and complete: moves it to the PM to merge.
4b. pr_fail(task_id="{task_id}", issues=["<concrete, actionable gap>", ...])
    — if anything is wrong: sends it back to the PM for revision, like a QA fail.
5. i_am_idle() when done.
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
3. dm(...) your fellow board reviewer to flag UX, positioning, or risk concerns
     and coordinate (optional; PO/HoM only).
4. i_am_idle()
     — when your review is recorded. Once both board reviewers are done, the
       CEO is notified the task is ready for Approve & Start, then routes it to
       Main PM for delegation to the cells; you do NOT hand it off yourself.

Do NOT attempt to claim, plan, complete, or delegate — the gateway will reject
those, and a substantive recorded note IS your job here.
"""

    def _build_roadmap_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        market_brief_context: str = "",
        digest_context: str = "",
    ) -> str:
        """Prompt for the Product Owner's one-shot roadmap-exploration cycle.

        Unlike the two-reviewer board-review prompt, this is PO-solo (v1 —
        see the roadmap spec's non-goals): explore, author ONE themed cycle,
        then idle. No claim/plan/delegate/complete — those verbs aren't the
        Product Owner's. ``prior_context`` is the LEARN rendering of the last
        closed cycles (``BoardProgramEngine.prior_cycle_context``) — empty
        when none exist yet. ``market_brief_context`` is Periscope's latest
        filed market brief (spec §4: "its brief is Printer's cross-role
        input") — empty when no brief has ever been filed; never blocks.
        ``digest_context`` is the server-assembled shipped-this-week digest
        (``shipped_work_digest``) — empty when the digest could not be
        assembled; never blocks."""
        task_id = task.get("id", "unknown")
        min_items = settings.roadmap_min_items_per_cycle
        max_items = settings.roadmap_max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        brief_block = (
            f"\n## Head of Marketing's latest market brief (Periscope)\n"
            f"{market_brief_context}\n"
            if market_brief_context
            else ""
        )
        digest_block = _shipped_digest_block(digest_context)
        return f"""\
You are the Product Owner. It's time for your periodic roadmap exploration.

TASK: {task_id}

Explore the company's projects and propose ONE themed cycle of roadmap items
for the CEO to review — you author this alone. The Head of Marketing is not
involved in this cycle.
{brief_block}{digest_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Explore: read the company charter, recent releases, metrics, and each
   project's current state (read-only git). Check the knowledge base for open
   threads. Optionally run web research for market/competitive signal.
3. Pick ONE theme/goal for this cycle — a one-line focus that ties the items
   together (e.g. "close onboarding friction" or "harden the payments path").
4. propose_roadmap(cycle_goal="<the theme>", items=[...])
     — call this EXACTLY ONCE with {min_items}-{max_items} item drafts. Each
       item is an object with: title, description, acceptance_criteria (list
       of strings), project_slug, team ('backend'|'frontend'|'ux_ui'),
       priority (1-4, default 2), rationale (why this, why now).

   If nothing you explored is worth a themed cycle at all this week, call
   nothing_to_propose(task_id="{task_id}", reason="<what you explored and
   why none of it warranted a cycle>") instead — a forced, thin cycle is
   worse than an honest skip, and the next cycle's briefing will see your
   reason.
5. i_am_idle() — once proposed (or declined). The CEO reviews and approves/
   rejects each item individually in the roadmap queue; an approved item
   lands in BACKLOG for normal PM activation — nothing here auto-starts.

Do NOT claim, plan, delegate, or attempt to start any of the items yourself —
that is not your job here, and the gateway will reject those verbs.
"""

    def _build_pest_control_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        evidence_context: str = "",
        digest_context: str = "",
    ) -> str:
        """Prompt for the Product Owner's one-shot Pest Control exploration.

        Unlike the two-reviewer board-review prompt, this is PO-solo: hunt
        latent defects, author up to 5 evidence-backed bug drafts, then idle.
        ``prior_context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``); ``evidence_context`` is
        the server-assembled rework/findings evidence
        (``PestControlEngine.evidence_context``); ``digest_context`` is the
        server-assembled shipped-this-week digest (``shipped_work_digest``)
        — all empty when none exist yet; never blocks."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        max_items = PROGRAMS["pest_control"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        evidence_block = (
            f"\n## Evidence gathered for you\n{evidence_context}\n"
            if evidence_context
            else ""
        )
        digest_block = _shipped_digest_block(digest_context)
        return f"""\
You are the Product Owner. It's time for your periodic Pest Control exploration.

TASK: {task_id}

Hunt LATENT defects — bugs the org already recorded but nobody read, not
whatever CI happens to be red on right now (that's self-heal/CI-watch's job,
not yours). Propose evidence-backed bug tasks for the CEO to review; you
author this alone.
{evidence_block}{digest_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Read the evidence gathered for you above (rework hotspots, recurring/
   waived findings). It is server-assembled — you cannot re-run those
   queries yourself, so start from it, don't second-guess it.
3. Also grep the repo (read-only) for `ponytail:` comments and TODO markers
   — deliberate shortcuts and deferred debt are exactly the kind of "green
   but rotten" signal this program exists to surface.
4. For each candidate, confirm it's a REAL, LIVE bug (not already fixed,
   not already tracked) before drafting an item.
5. propose_bug_hunt(items=[...])
     — call this EXACTLY ONCE with 1-{max_items} item drafts. Each item is an
       object with: title, description, acceptance_criteria (list of
       strings), project_slug, team ('backend'|'frontend'|'ux_ui'), priority
       (1-4, default 2), evidence (REQUIRED — the file:line / ledger row /
       metric that justifies this as a real bug; no evidence, no item).

   If the evidence above and your own grep turned up no REAL, LIVE bug this
   cycle, call nothing_to_propose(task_id="{task_id}", reason="<what you
   checked and why nothing qualified>") instead — an invented bug is worse
   than an honest miss.
6. i_am_idle() — once proposed (or declined). The CEO reviews and approves/
   rejects each item individually in the pest-control queue; an approved
   item lands in BACKLOG for normal PM activation — nothing here auto-starts.

Do NOT claim, plan, delegate, fix anything yourself, or attempt to start any
of the items — that is not your job here, and the gateway will reject those
verbs.
"""

    def _build_scales_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        evidence_context: str = "",
    ) -> str:
        """Prompt for the Product Owner's one-shot Scales exploration.

        PO-solo: review the injected stale-backlog snapshot against the
        charter, propose up to 7 re-priority/cancellation drafts, then idle.
        ``prior_context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``); ``evidence_context`` is
        the server-assembled stale-backlog snapshot (``ScalesEngine.
        evidence_context``) — both empty when none exist yet."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        max_items = PROGRAMS["scales"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        evidence_block = (
            f"\n## Evidence gathered for you\n{evidence_context}\n"
            if evidence_context
            else ""
        )
        return f"""\
You are the Product Owner. It's time for your periodic Scales
portfolio-rebalance exploration.

TASK: {task_id}

Review the LIVE backlog against the company charter and propose
re-prioritizations and cancellations — the org has no other mechanism that
ever retires stale backlog, and a board role is exactly who should propose
deletions. You author this alone.
{evidence_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context, including the company charter.
2. Read the stale-backlog snapshot gathered for you above (BACKLOG/PENDING
   tasks older than 30 days). It is server-assembled — you cannot re-run
   that query yourself, so start from it, don't second-guess it.
3. Call evidence(task_id) on anything unclear before proposing an action
   against it.
4. For each candidate, decide: reprioritize (it's still worth doing, just at
   the wrong priority) or cancel (it no longer serves the charter and should
   be retired) — never both.
5. propose_rebalance(items=[...])
     — call this EXACTLY ONCE with 1-{max_items} item drafts. Each item is an
       object with: task_ref (the id8 or exact title of the live task),
       action ('reprioritize' or 'cancel'), new_priority (int 0-3, REQUIRED
       iff action is 'reprioritize' — 0 is P0/highest, 3 is P3/lowest),
       rationale (REQUIRED — why this task should change).

   If the stale-backlog snapshot has nothing genuinely worth rebalancing
   this cycle, call nothing_to_propose(task_id="{task_id}", reason="<what
   you reviewed and why nothing warranted a change>") instead — churning
   the backlog for its own sake is worse than leaving it alone.
6. i_am_idle() — once proposed (or declined). The CEO reviews and approves/
   rejects each item individually in the Scales queue; approval MUTATES the
   live task in place — nothing here changes anything itself.

Do NOT cancel, reprioritize, claim, plan, or delegate anything yourself —
that is not your job here, and the gateway will reject those verbs. You only
ever propose.
"""

    def _build_coroner_prompt(
        self, task: dict[str, Any], incident_context: str = ""
    ) -> str:
        """Prompt for the Auditor's one-shot Coroner postmortem.

        ``incident_context`` is the server-assembled findings-ledger +
        transition-history evidence (``CoronerEngine.incident_context``) for
        the incident named on this task's ``coroner_incident`` marker — empty
        when the marker or the read failed (degrade, never block the spawn).
        """
        task_id = task.get("id", "unknown")
        markers_dict = task.get("orchestration_markers") or {}
        incident_ref = markers_dict.get(_markers.CORONER_INCIDENT) or {}
        incident_task_id = incident_ref.get("incident_task_id", "unknown")
        kind = incident_ref.get("kind", "unknown")
        title = incident_ref.get("title", "unknown")
        context_block = (
            f"\n## Evidence gathered for you\n{incident_context}\n"
            if incident_context
            else ""
        )
        return f"""\
You are the Auditor. An incident just triggered your Coroner postmortem.

TASK: {task_id}

INCIDENT: {title!r} ({incident_task_id}) — {kind}
{context_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. evidence({incident_task_id!r}) — read the incident's full journey: PR,
   commits, dev/QA/PM journal trail, decisions.
3. Read the evidence gathered for you above (findings ledger, transition
   history). It is server-assembled — you cannot re-run those queries
   yourself, so start from it.
4. Determine: what actually failed, at which lifecycle stage, and the
   SYSTEMIC cause (not just this one incident's symptom — what about the
   process let it happen).
5. propose_postmortem(incident_summary=..., root_cause=..., failed_stage=...,
   process_change={{...}}, playbook=...)
     — call this EXACTLY ONCE. process_change.kind is one of 'playbook'
       (also pass playbook={{'title':..., 'body':...}} — drafted immediately
       into the pending-playbook curation queue), 'prompt_fix',
       'conventions_rule', or 'other'. Propose ONE change — the smallest
       thing that would have caught or prevented this.

   If the evidence genuinely supports no systemic process change — a true
   one-off with no lesson the org can act on — call nothing_to_propose(
   task_id="{task_id}", reason="<what you found and why no process change
   is warranted>") instead of forcing one.
6. i_am_idle() — once proposed (or declined). This completes the autopsy
   immediately and notifies the CEO; there is no per-item queue to leave
   open.

Do NOT message the fleet about this — you stay silent to other agents; your
output is this postmortem and your journal, both CEO-facing.
"""

    def _build_war_room_prompt(self, task: dict[str, Any]) -> str:
        """Prompt for the Head of Marketing's one-shot War Room campaign-
        planning cycle.

        EVENT-triggered (spec §4): opened by the release-publish hook
        (carrying a release version + highlights on the ``war_room_brief``
        marker) or a CEO "run now" call (blank brief — ``{}``). No LEARN
        injection — mirrors ``_build_coroner_prompt``: no cron cadence to
        have learned from.
        """
        task_id = task.get("id", "unknown")
        markers_dict = task.get("orchestration_markers") or {}
        brief = markers_dict.get(_markers.WAR_ROOM_BRIEF) or {}
        version = brief.get("version")
        highlights = brief.get("highlights") or []
        if version:
            highlight_lines = "\n".join(f"- {h}" for h in highlights)
            brief_block = (
                f"\nRELEASE: v{version} just shipped. Highlights:\n{highlight_lines}\n"
            )
        else:
            brief_block = (
                "\nNo release triggered this cycle — the CEO called this "
                "on-demand. Ground the campaign in what's actually shipped "
                "and worth talking about (CHANGELOG.md, the feature-flags "
                "ledger, your own recent spotlight/brief history).\n"
            )
        return f"""\
You are the Head of Marketing. It's time to plan a War Room campaign.

TASK: {task_id}
{brief_block}
Design ONE campaign — an ordered arc of 2-6 posts (teaser -> launch ->
follow-up -> spotlight) — for the CEO to review. You author this alone.

== WHAT TO DO ==

1. triage() — see your board-level context.
2. If a release triggered this cycle, ground every post in the highlights
   above — never invent a feature. On an on-demand cycle, investigate
   CHANGELOG.md, the feature-flags ledger, docs/map/, and the knowledge base
   for real, currently-shipped material worth a campaign.
3. Design the arc: a teaser (build anticipation, no full reveal), a launch
   (the announcement itself), a follow-up (a concrete detail or use case),
   and optionally a spotlight (a related capability) — order matters, drop
   any stage that doesn't earn its place; 2 posts is a valid campaign.
4. Pick a recommended publish_after for each post — spaced sensibly (hours to
   days apart depending on the arc), STRICTLY ascending, and all in the
   future. This is GUIDANCE only: V1 is manual-cadence — the CEO approves
   each draft individually in the X post queue at their own moment, nothing
   here schedules or auto-posts.
5. propose_campaign(campaign_name="<short name>", posts=[...])
     — call this EXACTLY ONCE with 2-6 posts IN ORDER. Each post is an
       object with: body (the tweet text, plain, <=280 chars, in your voice —
       see the VOICE GUIDE, and clearing the IMPACT BAR in your identity:
       deliverable noun in the first sentence, one falsifiable specific, no
       hashtags/emoji/exclamations), publish_after (ISO 8601 datetime,
       future, strictly ascending across posts), stage_label (one of
       'teaser', 'launch', 'follow_up', 'spotlight', 'other').

   If there is genuinely nothing this cycle worth a campaign — no real
   highlights on a release trigger, nothing fresh on an on-demand run —
   call nothing_to_propose(task_id="{task_id}", reason="<what you checked
   and why no campaign qualifies>") instead of forcing a weak arc.
6. i_am_idle() — once proposed (or declined). This completes your planning
   cycle immediately; the CEO reviews, edits, approves, or rejects each post
   individually in the X post queue — you never post anything yourself.

Do NOT claim, plan, delegate, or attempt to post anything yourself — that is
not your job here, and the gateway will reject those.
"""

    def _build_spackle_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        digest_context: str = "",
    ) -> str:
        """Prompt for the Product Owner's one-shot Spackle exploration.

        Unlike Pest Control there is no server-assembled evidence context
        (spec: Spackle carries no heavy server-side inventory engine) — the
        inventory diffing (API routes vs panel surfaces, armed flags vs
        docs, docs claims vs code, coverage holes, dead-end panel tabs) is
        the PO's own read-tool work, ordered explicitly below. ``prior_
        context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``); ``digest_context`` is
        the server-assembled shipped-this-week digest
        (``shipped_work_digest``) — both empty when none exist yet; never
        blocks."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        max_items = PROGRAMS["spackle"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        digest_block = _shipped_digest_block(digest_context)
        return f"""\
You are the Product Owner. It's time for your periodic Spackle exploration.

TASK: {task_id}

Audit the target project's half-shipped surface area — the gaps between what
was built and what was finished. Propose evidence-backed gap-fill tasks for
the CEO to review; you author this alone.
{digest_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Compare inventories against each other, citing `file:line` for every
   claimed gap:
   a. API routes (roboco/api/routes/) with no panel surface that exposes
      them, and vice versa — a panel page calling an endpoint that doesn't
      exist or was removed.
   b. Armed feature flags (roboco/config.py, the feature-flags panel card)
      with no docs describing them.
   c. Docs-site / docs/map/ promises the code doesn't actually keep.
   d. Coverage holes by module, if a coverage report is available.
   e. Dead-end panel tabs — a page/tab with no working action or data.
3. For each candidate, confirm it's a REAL, LIVE gap (not already fixed, not
   already tracked as a task) before drafting an item.
4. propose_gap_fill(items=[...])
     — call this EXACTLY ONCE with 1-{max_items} item drafts. Each item is an
       object with: title, description, acceptance_criteria (list of
       strings), project_slug, team ('backend'|'frontend'|'ux_ui'), priority
       (1-4, default 2), evidence (REQUIRED — must name BOTH sides of the
       gap, e.g. the route that exists and the panel surface that doesn't;
       no evidence, no item).

   If your inventory comparison turned up no REAL, LIVE gap this cycle,
   call nothing_to_propose(task_id="{task_id}", reason="<what you compared
   and why nothing qualified>") instead — a manufactured gap is worse than
   an honest miss.
5. i_am_idle() — once proposed (or declined). The CEO reviews and approves/
   rejects each item individually in the spackle queue; an approved item
   lands in BACKLOG for normal PM activation — nothing here auto-starts.

Do NOT claim, plan, delegate, fix anything yourself, or attempt to start any
of the items — that is not your job here, and the gateway will reject those
verbs.
"""

    def _build_mirror_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
    ) -> str:
        """Prompt for the Head of Marketing's one-shot Mirror exploration.

        Unlike Periscope/Sentinel there is no server-assembled evidence
        context (spec: Mirror carries no heavy server-side inventory engine,
        same posture as Spackle) — the messaging audit (README claims vs
        shipped features, docs-site promises vs code, charter alignment) is
        the HoM's own read-tool work, ordered explicitly below.
        ``prior_context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``) — empty when none exist
        yet."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        max_items = PROGRAMS["mirror"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        return f"""\
You are the Head of Marketing. It's time for your periodic Mirror exploration.

TASK: {task_id}

Audit the target project's messaging surfaces against the company charter
and shipped reality — the gaps between what the copy claims and what the
product actually does. Propose evidence-backed docs tasks for the CEO to
review; you author this alone.
{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context (carries the company charter).
2. Compare the messaging surfaces against shipped reality, citing `file:line`
   or a URL for every claimed drift:
   a. README claims vs what the codebase actually ships (CHANGELOG.md,
      docs/map/, feature flags).
   b. The docs site / docs-site repo's promises vs the code — when that repo
      is registered as a project and opted into mirror, treat it as a
      first-class target, not an afterthought.
   c. Charter alignment — does the messaging still match the CEO's stated
      objectives and positioning (`company_goals`, already in your
      briefing)?
   d. Shipped capabilities the copy doesn't mention at all — the inverse
      drift, not just overclaiming.
3. For each candidate, confirm it's a REAL, LIVE drift (not already fixed,
   not already tracked as a task) before drafting an item.
4. propose_messaging_fixes(items=[...])
     — call this EXACTLY ONCE with 1-{max_items} item drafts. Each item is an
       object with: title, description, acceptance_criteria (list of
       strings), project_slug, team ('backend'|'frontend'|'ux_ui'), priority
       (1-4, default 2), evidence (REQUIRED — must name BOTH the drifted
       claim and the reality it contradicts; no evidence, no item).

   If the messaging matches shipped reality with no drift worth a fix this
   cycle, call nothing_to_propose(task_id="{task_id}", reason="<what you
   compared and why nothing qualified>") instead — a manufactured drift is
   worse than an honest miss.
5. i_am_idle() — once proposed (or declined). The CEO reviews and approves/
   rejects each item individually in the mirror queue; an approved item
   lands in BACKLOG as a docs task for normal PM activation — nothing here
   auto-starts.

Do NOT claim, plan, delegate, fix anything yourself, or attempt to start any
of the items — that is not your job here, and the gateway will reject those
verbs.
"""

    def _build_dogfood_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
    ) -> str:
        """Prompt for the Product Owner's one-shot Dogfood walk.

        The ONE board-program spawn that also gets the playwright MCP (see
        ``_is_dogfood_spawn`` — task-scoped on this exact task, not a
        role-blanket grant), so this is the only prompt that references
        browser tools. ``prior_context`` is the LEARN rendering of the last
        closed cycles (``BoardProgramEngine.prior_cycle_context``) — empty
        when none exist yet."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        max_items = PROGRAMS["dogfood"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        panel_line = ""
        self_slug = settings.self_heal_project_slug or "roboco-api"
        if task.get("project_slug") == self_slug and settings.panel_base_url:
            panel_line = (
                f"\nThe panel is reachable at {settings.panel_base_url} — start "
                "your walk there.\n"
            )
        return f"""\
You are the Product Owner. It's time for your periodic Dogfood walk.

TASK: {task_id}

Walk the target project's live surfaces as a real USER would, not as a code
reviewer — the panel (browser tools: browser_navigate, browser_snapshot,
browser_click, browser_type, browser_take_screenshot, etc. are mounted for
THIS task only), the docs site, and the Telegram flow when a live URL is
reachable. File UX friction: what broke, what confused you, what felt slow
or wrong. You author this alone.
{panel_line}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Find a live URL for each surface: the panel (see above, when this cycle's
   target is RoboCo's own project) and the docs site (check the target
   project's README/docs for a published URL). If NO live URL is reachable
   for a surface, do NOT fabricate a walk — fall back to an honest read-tool
   review of that surface's source instead, and say so explicitly in the
   item's evidence (e.g. "docs site URL unreachable; reviewed docs/ source
   instead").
3. Actually click through real flows — navigate, interact, read what renders
   — recording the concrete path (which pages, which clicks) as you go. A
   friction item without a walked path is not dogfooding, it's guessing.
4. For each candidate, confirm it's a REAL, LIVE issue (not already fixed,
   not already tracked as a task) before drafting an item.
5. propose_friction_fixes(items=[...])
     — call this EXACTLY ONCE with 1-{max_items} item drafts. Each item is an
       object with: title, description, acceptance_criteria (list of
       strings), project_slug, team ('backend'|'frontend'|'ux_ui'), priority
       (1-4, default 2), evidence (REQUIRED — the actual walked path: which
       pages, which clicks, what broke or felt wrong, in prose; NEVER a
       screenshot; no evidence, no item).

   If your walk turned up nothing genuinely broken or confusing this cycle,
   call nothing_to_propose(task_id="{task_id}", reason="<what you walked
   and why nothing qualified>") instead — a manufactured friction item is
   worse than an honest "it worked fine".
6. i_am_idle() — once proposed (or declined). The CEO reviews and approves/
   rejects each item individually in the dogfood queue; an approved item
   lands in BACKLOG for normal PM activation — nothing here auto-starts.

Do NOT claim, plan, delegate, fix anything yourself, or attempt to start any
of the items — that is not your job here, and the gateway will reject those
verbs.
"""

    def _build_feature_spotlight_prompt(
        self, task: dict[str, Any], prior_context: str = ""
    ) -> str:
        """Prompt for the Head of Marketing's one-shot feature-spotlight cycle.

        ``prior_context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``) — empty when none exist
        yet."""
        task_id = task.get("id", "unknown")
        markers_dict = task.get("orchestration_markers") or {}
        seen_line = _format_seen_features(markers_dict)
        shipped_line = _format_shipped_since(markers_dict)
        rejected_line = _format_rejected_spotlights(markers_dict)
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        return f"""\
You are the Head of Marketing. It's time for your periodic feature-spotlight cycle.

TASK: {task_id}

RoboCo markets its own capabilities, not just releases. Investigate what the
company has actually shipped and draft ONE marketing post about a genuinely
useful, under-publicized capability — something a user or prospect would not
already know from the last release announcement. Prefer something fresh but
not yet spotlighted over stale already-covered ground.

ALREADY COVERED — do not repeat: {seen_line}

SHIPPED SINCE THE LAST CYCLE (CHANGELOG.md): {shipped_line}

RECENTLY REJECTED BY THE CEO — avoid repeating these angles: {rejected_line}
{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Investigate (read-only, you have full repo read access): CHANGELOG.md (what
   has actually shipped), the feature-flags ledger (panel/src/components/
   settings/feature-flags-card.tsx and roboco/services/settings.py's
   FEATURE_FLAGS — the enumerated subsystems), docs/map/ (the exhaustive
   codebase map — each slice's Purpose section is marketing-readable), the
   company charter (already in your briefing), and the knowledge base
   (roboco_ask_mentor / roboco_kb_search).
3. Pick ONE feature that is real, currently shipped (or shipped behind a flag
   the CEO can enable), not in the already-covered list above, and worth
   telling people about.
4. Draft ONE post in your voice (see your identity's VOICE GUIDE) — plain
   text, no markdown, no thread, max 280 characters, never invent a
   capability that doesn't exist, and it must clear the IMPACT BAR in your
   identity (deliverable noun in the first sentence, one falsifiable
   specific, no hashtags/emoji/exclamations).
5. propose_feature_spotlight(feature_slug="<a short stable slug>",
   feature_title="<human-readable feature name>", body="<the post>")
     — call this EXACTLY ONCE.

   If nothing shipped is genuinely worth spotlighting this cycle, call
   propose_feature_spotlight(skip=True, skip_reason="<why nothing qualifies>")
   instead — a weak, forced spotlight is worse than skipping a cycle, and the
   next cycle will see this skip as recent activity (the cadence won't just
   re-fire into the same quiet period tomorrow). nothing_to_propose(
   task_id="{task_id}", reason="<same explanation>") does the identical job
   and is the standard exit every other board program uses — either call is
   fine for this cycle.
6. i_am_idle() — once proposed (or skipped). The CEO reviews, edits, approves,
   or rejects the draft in the X post queue; nothing posts without that
   explicit approval.

Do NOT claim, plan, delegate, or attempt to post anything yourself — that is
not your job here, and the gateway will reject those.
"""

    def _build_periscope_prompt(
        self, task: dict[str, Any], prior_context: str = ""
    ) -> str:
        """Prompt for the Head of Marketing's one-shot Periscope market-
        research cycle.

        HoM-solo, complete-at-propose (mirrors the feature-spotlight prompt's
        shape): research the market, file ONE brief, then idle. ``prior_
        context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``) — empty when none exist
        yet."""
        task_id = task.get("id", "unknown")
        from roboco.foundation.policy.board_programs import PROGRAMS

        max_findings = PROGRAMS["periscope"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        return f"""\
You are the Head of Marketing. It's time for your periodic Periscope
market-research cycle.

TASK: {task_id}

Research the market — competitors, adjacent-tool releases, positioning
shifts — and file ONE brief for the CEO. This is a REPORT, not a task queue:
nothing you file here materializes work, and there is no per-item CEO
decision to wait on. Your brief also becomes the Product Owner's cross-role
input for the next roadmap-exploration cycle (Printer), so ground every
claim in a real source.
{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Research: use web_search/web_fetch for competitor moves, adjacent-tool
   releases, and positioning shifts; check the knowledge base for prior
   market signal. Cite the source URL for every claim you act on — a claim
   without a source is noise, and the verb rejects an uncited finding.
3. Pick a one-line headline naming this cycle's biggest signal.
4. propose_market_brief(headline="<one-line summary>", findings=[...],
   threats=[...], opportunities=[...], positioning_note="<optional>")
     — call this EXACTLY ONCE with 1-{max_findings} cited findings. Each
       finding is an object with: claim, source_url (REQUIRED — a real
       http(s) URL), relevance (why this matters to us). threats/
       opportunities are optional lists of up to 5 short notes each;
       positioning_note is an optional note on a shift worth acting on.

   If your research turned up no citable finding worth a brief this cycle,
   call nothing_to_propose(task_id="{task_id}", reason="<what you
   researched and why nothing was citable>") instead — an uncited or
   invented finding is worse than filing nothing.
5. i_am_idle() — once filed (or declined). The CEO reads the brief as a
   report in the panel; nothing here needs your further attention.

Do NOT claim, plan, delegate, or attempt to act on anything you find
yourself — that is not your job here, and the gateway will reject those.
"""

    def _build_barfly_prompt(
        self, task: dict[str, Any], prior_context: str = ""
    ) -> str:
        """Prompt for the Head of Marketing's one-shot Barfly conversation-
        reply cycle.

        HoM-solo, complete-at-propose (mirrors the Periscope prompt's shape):
        review the SCREENED candidates already gathered — never invent a
        tweet — pick up to N worth replying to, draft, then idle. ``prior_
        context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``) — empty when none exist
        yet."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        markers_dict = task.get("orchestration_markers") or {}
        candidates_line = _format_barfly_candidates(markers_dict)
        max_items = PROGRAMS["barfly"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        return f"""\
You are the Head of Marketing. It's time for your periodic Barfly
conversation-reply cycle.

TASK: {task_id}

Barfly finds X conversations where RoboCo is relevant but UNMENTIONED —
search results, not the mentions timeline. Review the SCREENED candidates
already gathered below and draft replies for the ones genuinely worth it. You
must reply ONLY to a candidate already on this list — never invent a tweet or
target an id that isn't here.

SCREENED CANDIDATES:
{candidates_line}
{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Pick up to {max_items} candidates genuinely worth a reply — skip anything
   low-value, off-topic despite the keyword match, or already answered by
   someone else in a way that makes a RoboCo reply redundant.
3. For each one, draft a reply in your voice (see your identity's VOICE
   GUIDE): answer or add value to the actual conversation, plain text, max
   280 characters, never invent facts about RoboCo, and it must clear the
   IMPACT BAR in your identity (deliverable noun in the first sentence, one
   falsifiable specific, no hashtags/emoji/exclamations). The platform
   appends the conversation's own URL to your reply automatically, so
   reply_body itself must contain NO links; the IMPACT BAR's one-link rule
   is satisfied by that appended URL.
4. propose_conversation_replies(items=[...])
     — call this EXACTLY ONCE with 1-{max_items} items. Each item is an
       object with: tweet_id (REQUIRED — must be one of the candidate ids
       above, verbatim), reply_body (the reply text, <=280 chars), rationale
       (REQUIRED — why this conversation is worth replying to).

   If none of the screened candidates are genuinely worth a reply this
   cycle, call nothing_to_propose(task_id="{task_id}", reason="<what you
   reviewed and why none qualified>") instead — a forced, low-value reply
   is worse than no reply.
5. i_am_idle() — once proposed (or declined). Each reply materializes its
   own held draft in the X post queue; the CEO reviews, edits, approves, or
   rejects each one individually — nothing here posts anything itself.

Do NOT claim, plan, delegate, or attempt to post anything yourself — that is
not your job here, and the gateway will reject those.
"""

    def _build_sentinel_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        evidence_context: str = "",
    ) -> str:
        """Prompt for the Auditor's one-shot Sentinel drift-watch cycle.

        Auditor-solo, complete-at-propose (mirrors the periscope prompt's
        shape): assess drift, file ONE report, then idle. ``prior_context``
        is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``); ``evidence_context`` is
        the server-assembled waiver/findings/conventions/budget evidence
        (``SentinelEngine.evidence_context``) — both empty when none exist
        yet. The Auditor stays silent to agents throughout — this report goes
        to the CEO only, never a fleet notification."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        max_items = PROGRAMS["sentinel"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        evidence_block = (
            f"\n## Evidence gathered for you\n{evidence_context}\n"
            if evidence_context
            else ""
        )
        return f"""\
You are the Auditor. It's time for your periodic Sentinel drift-watch cycle.

TASK: {task_id}

Assess org-wide QUALITY DRIFT — waiver-accumulation trends, conventions-
violation hotspots, budget anomalies — and file ONE "state of quality"
report for the CEO. This is a REPORT, not a task queue: nothing you file
here materializes work, and there is no per-item CEO decision to wait on.
You stay silent to the fleet throughout — this report goes to the CEO only.
{evidence_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Read the evidence gathered for you above (waived-findings trend, open-
   findings-by-severity, conventions-violation hotspots, top spend by task/
   project). It is server-assembled — you cannot re-run those queries
   yourself, so start from it, don't second-guess it.
3. Also check docs/map/ (the exhaustive codebase map) for staleness against
   what you know has actually shipped, if that would sharpen a finding.
4. For each candidate drift signal, confirm it's REAL and worth naming (not
   noise) before drafting an item.
5. propose_quality_report(headline="<one-line summary>", items=[...],
   overall_assessment="<synthesis across all items>")
     — call this EXACTLY ONCE with 1-{max_items} items. Each item is an
       object with: area (one of 'waivers', 'findings', 'conventions',
       'budget', 'docs', 'other'), observation (what you found), evidence
       (the ledger row / metric / file that backs it), suggested_action
       (what should happen next — a later "convert to task" step, not
       something you do yourself).

   If the evidence above shows no real drift worth naming this cycle, call
   nothing_to_propose(task_id="{task_id}", reason="<what you reviewed and
   why nothing rose to a finding>") instead — a manufactured drift signal
   is worse than an honest "no drift this cycle".
6. i_am_idle() — once filed (or declined). The CEO reads the report in the
   panel; nothing here needs your further attention.

Do NOT claim, plan, delegate, fix anything yourself, message any other
agent, or attempt to act on anything you find — that is not your job here,
and the gateway will reject those verbs.
"""

    def _build_megaphone_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        digest_context: str = "",
    ) -> str:
        """Prompt for the Head of Marketing's one-shot Megaphone editorial
        cycle.

        HoM-solo, complete-at-propose (mirrors the periscope/feature-
        spotlight prompts' shape): pick ONE angle, write ONE post, then idle.
        ``prior_context`` is the LEARN rendering of the last closed cycles
        (``BoardProgramEngine.prior_cycle_context``); ``digest_context`` is
        the server-assembled shipped-this-week digest (``MegaphoneEngine.
        digest_context``) — both empty when none exist yet."""
        task_id = task.get("id", "unknown")
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        digest_block = (
            f"\n## Shipped-this-week digest\n{digest_context}\n"
            if digest_context
            else ""
        )
        return f"""\
You are the Head of Marketing. It's time for your periodic Megaphone
editorial cycle.

TASK: {task_id}

This is the standing editorial calendar — beyond release posts and feature
spotlights: dev-log threads on what the fleet shipped this week,
behind-the-scenes posts, changelog highlights. Pick ONE angle and file ONE
post; the draft lands in the SAME X post queue release/spotlight drafts do —
there is no separate approval surface.
{digest_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Read the shipped-this-week digest above (completed tasks + the
   CHANGELOG.md Unreleased section, when available). It is server-assembled —
   you cannot re-run those queries yourself, so start from it.
3. Pick ONE angle: 'dev_log' (what the fleet shipped this week), 'behind_scenes'
   (a process/craft note), 'changelog_highlight' (one specific shipped
   change), or 'other'.
4. Draft ONE post in your voice (see your identity's VOICE GUIDE) — plain
   text, no markdown, no thread, max 280 characters, never invent a
   capability that doesn't exist, and it must clear the IMPACT BAR in your
   identity (deliverable noun in the first sentence, one falsifiable
   specific, no hashtags/emoji/exclamations).
5. propose_editorial_post(angle="<one of the four above>", body="<the post>",
   rationale="<why this angle, this cycle>")
     — call this EXACTLY ONCE.

   If the digest above has genuinely nothing worth an editorial post this
   cycle, call nothing_to_propose(task_id="{task_id}", reason="<what you
   reviewed and why no angle qualified>") instead of forcing a thin post.
6. i_am_idle() — once proposed (or declined). The CEO reviews, edits,
   approves, or rejects the draft in the X post queue; nothing posts
   without that explicit approval.

Do NOT claim, plan, delegate, or attempt to post anything yourself — that is
not your job here, and the gateway will reject those.
"""

    def _build_librarian_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        mining_context: str = "",
    ) -> str:
        """Prompt for the Auditor's one-shot Librarian playbook-mining cycle.

        Auditor-solo, complete-at-propose (mirrors the sentinel prompt's
        shape): mine journals/learnings for repeated patterns, draft 1-3
        playbooks, then idle. ``prior_context`` is the LEARN rendering of the
        last closed cycles (``BoardProgramEngine.prior_cycle_context``);
        ``mining_context`` is the server-assembled recurring-learning-topic +
        existing-playbook-title evidence (``LibrarianEngine.
        mining_context``) — both empty when none exist yet. The Auditor
        stays silent to agents throughout — the drafts ride the normal
        pending-playbook curation queue, never a fleet notification."""
        from roboco.foundation.policy.board_programs import PROGRAMS

        task_id = task.get("id", "unknown")
        max_drafts = PROGRAMS["librarian"].max_items_per_cycle
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        mining_block = (
            f"\n## Mining context gathered for you\n{mining_context}\n"
            if mining_context
            else ""
        )
        return f"""\
You are the Auditor. It's time for your periodic Librarian playbook-mining
cycle.

TASK: {task_id}

Playbook curation today is reactive — you only judge what delivery roles
happen to draft via draft_playbook. This cycle is the proactive half: mine
what the org already recorded (journals, learnings) for a repeated pattern
nobody has turned into a playbook yet, and draft it yourself. This is a
mining pass, not a task queue: nothing you file here materializes work
for anyone else, and there is no per-item CEO decision to wait on — each
draft you author lands directly in the SAME pending-playbook curation
queue your own approve_playbook/reject_playbook already review (a later
cycle curates them, never this same call).
{mining_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Read the mining context gathered for you above (recurring learning-
   journal topics, existing playbook titles). It is server-assembled — you
   cannot re-run those queries yourself, so start from it.
3. Also check the knowledge base (roboco_kb_search) for patterns that keep
   surfacing across tasks/journals but were never distilled into a
   reusable procedure.
4. For each candidate pattern, confirm it is REAL and REPEATED (at least
   two independent instances) — a one-off is not a pattern — and that it
   does NOT already duplicate an existing playbook title (listed above,
   case-insensitive; the verb rejects a duplicate).
5. propose_playbook_drafts(drafts=[...]) — call this EXACTLY ONCE with
   1-{max_drafts} drafts. Each draft is an object with: title (<=200 chars,
   must not duplicate an existing playbook), body (<=4000 chars — the
   procedure itself: when to use it, the steps), pattern_evidence
   (REQUIRED, <=500 chars — which repeated journal/learning pattern
   justifies this playbook; a draft without it is noise, and the verb
   rejects it).

   If the mining context above shows no genuinely repeated, undrafted
   pattern this cycle, call nothing_to_propose(task_id="{task_id}",
   reason="<what you mined and why nothing qualified>") instead — a one-off
   dressed up as a pattern is worse than an honest miss.
6. i_am_idle() — once filed (or declined). The drafts sit in the normal
   pending-playbook curation queue; nothing here needs your further
   attention this cycle.

Do NOT claim, plan, delegate, fix anything yourself, message any other
agent, or call draft_playbook (you don't have it — use
propose_playbook_drafts instead) — that is not your job here, and the
gateway will reject those verbs.
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

    def _build_audit_prompt(
        self,
        alert: dict[str, Any] | None = None,
        *,
        scheduled: bool = False,
    ) -> str:
        """Build initial prompt for the auditor."""
        if alert:
            subject = alert.get("subject", "Quality issue detected")
            body = alert.get("body", "Review system quality metrics")

            return f"""QUALITY ALERT triggered your attention.

ALERT: {subject}
DETAILS: {body}

Your job:

1. Investigate the quality issue
2. Review relevant tasks and history (you have read access to all)
3. Compile your findings
4. Report to CEO via your journal (note scope='reflect')
5. Call i_am_idle() when complete
"""

        if scheduled:
            return """SCHEDULED AUDIT SWEEP.

You are running a periodic delivery-process review. Look across the org
for the past audit window and log anything the CEO should know.

Your job:

1. Scan recent task state: long-running blocked work, repeated rework
   (needs_revision), and PR-review failures
2. Check quality drift: QA pass/fail patterns, convention violations,
   tracing gaps on recently completed work
3. Spot cross-cell hand-off friction and silent stranded work
4. Call triage() — beyond anomalies it also surfaces the oldest pending
   playbook draft awaiting your approve_playbook/reject_playbook curation
5. Record every observation via note(scope='reflect'); if nothing is
   amiss, note exactly that
6. Call i_am_idle() when complete
"""

        return """Periodic AUDIT requested.

Your job:

1. Review recent activity across all cells
2. Check quality metrics (QA pass/fail rates, blocker frequency, etc.)
3. Identify any concerns or patterns
4. Compile audit report for CEO
5. Call i_am_idle() when complete
"""

    def _build_vault_curation_prompt(self, task_id: str, title: str) -> str:
        """Build initial prompt for a root-completion Obsidian-vault curation."""
        return f"""Root task COMPLETED — Obsidian-vault curation requested.

TASK: {title} ({task_id})

Your job:

1. Review this task's full tree (description, subtasks, PR, journal trail,
   decisions, any rework story).
2. Call curate_vault(task_id="{task_id}", narrative=...) EXACTLY ONCE with a
   concise narrative: what happened, key decisions, rework if any.
3. Call i_am_idle() when complete.
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
