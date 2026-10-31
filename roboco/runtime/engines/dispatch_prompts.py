"""The dev's spawn prompt, the task history it carries, and the reviewers' prompts.

Engine mixins composed into AgentOrchestrator. The other spawn prompts live in
dispatch_prompts_management (PMs, board, notifications) and
dispatch_prompts_programs (the Board Programs and the Auditor)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.agents_config import (
    get_agent_role,
)
from roboco.config import settings
from roboco.runtime.orchestrator import (
    _PROMPT_FINDINGS_CAP,
    _render_open_finding_prompt_line,
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


# B38 submit_now_confidence legend top: score 2 = "remaining work is
# zero", the only verdict that lets the brittle proxy's flip stand.
_SUBMIT_NOW_ZERO_REMAINING = 2


class DispatchPromptsEngine(_Base):
    """The dev's spawn prompt: role routing, workflow state and instructions,
    the open-findings block, the video block and the submit-now veto."""

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
revision. Animation clock: a CSS animation's clock starts at the
data-start of the nearest element that carries one (the element itself
included), and animation-delay counts from there. Clips that hold beats
start at 0 and run the full duration; a layer that appears later gets its
entrance from animation-delay, not data-start; never give one element both
data-start and a composition-time delay (they add: 2.6 + 2.6s = 5.2s).
motion/compositions/animation-clock.test.js (pnpm test) enforces it.
9:16 platform band (motion/README.md "The 9:16 platform band"): TikTok
draws its UI over the vertical, so every word and the product stay
between y 168 and 1330, and no word sits right of x 900 below y 840 (the
action rail). Put the vertical's whole scene in kit's pk-band window, as
compositions/release-0.28.0/vertical.html does, and check the band in
its request_render frames.
Read motion/skills/hyperframes/SKILL.md, the vendored HyperFrames skill,
before authoring; its RoboCo house overrides on top win where they differ.
You have browser tools (playwright MCP) on this task, and they refuse
file:// URLs: serve motion/ over HTTP in
the background (python3 -m http.server 8000 -d motion) and open
http://127.0.0.1:8000/compositions/<id>/vertical.html to watch it while
iterating — do not build blind between renders. That page loads the real
fonts, kit and layout but not the HyperFrames runtime, so judge layout and
type there and timing only from request_render frames. motion/index.html is
the project root: never add an index.html under compositions/
(compositions/assets.test.js fails on one).

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
        workflow_state = await self._dev_workflow_state_with_submit_proxy(
            task, status, has_plan
        )
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
        #
        # B30 context_pruning: while that pilot is ON, low-value description
        # entries may be dropped from the injected block (never gates,
        # findings, acceptance criteria, or blocking info).
        description_text = self._description_body(description)
        if settings.decisions_enabled:
            description_text = await self._decisions_pruned_description(
                description,
                task_id=str(task_id),
                workflow_state=workflow_state,
                fallback=description_text,
            )
        desc_block = f"DESCRIPTION:\n{description_text}"

        # B33 delta_brief: the shared prior-attempt briefing (one classifier,
        # three trigger points — revision respawn, parked/maintenance resume,
        # rate-limit resume — all of which respawn through this builder).
        delta_block = await self._delta_brief_block(task)

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
{delta_block}{video_block}
Start by calling evidence(task_id="{task_id}") for full details, acceptance
criteria, and the upstream parent/ancestor context (the original intake analysis).

When out of work: i_am_idle().
"""

    async def _dev_workflow_state_with_submit_proxy(
        self, task: dict[str, Any], status: str, has_plan: bool
    ) -> str:
        """Workflow state for the dev prompt, with the possibilities-matrix
        prompt proxy applied.

        When the flag is armed and the task already has commits + an open PR,
        steer the dev to submit in one turn instead of re-deriving (re-running
        gates, re-reading the diff). This is a cheap sync proxy; the async DB
        gates (AC coverage, open findings) are NOT re-checked here; the server
        fast path (_i_am_done_fast_path) is the authority and runs them. The
        prompt just collapses the 3-5-turn re-derivation to a single
        i_am_done call.

        B38 submit_now_confidence: while that pilot is ON, the brittle
        3-field proxy's flip is gated by a real "remaining work is zero"
        verdict: a confident zero keeps the flip, a confident not-zero
        vetoes it back to the state-derived workflow, and a below-floor/
        missing verdict leaves the proxy in charge exactly as today.
        """
        workflow_state = self._get_workflow_state(status, has_plan)
        if (
            settings.possibilities_matrix_enabled
            and status in ("claimed", "in_progress")
            and task.get("pr_created")
            and task.get("commits")
        ):
            workflow_state = "WORK_ALREADY_DONE"
            if settings.decisions_enabled and (
                await self._decisions_submit_now_veto(task)
            ):
                workflow_state = self._get_workflow_state(status, has_plan)
        return workflow_state

    async def _decisions_submit_now_veto(self, task: dict[str, Any]) -> bool:
        """B38 submit_now_confidence: True when a CONFIDENT classifier verdict
        is anything short of "remaining work is zero" (vetoing the brittle
        proxy's WORK_ALREADY_DONE flip).

        Legend: 0 = work remains, 1 = signals conflict, 2 = zero
        remaining. Only a confident 2 keeps the flip; any confident 0 or
        1 vetoes it (asymmetric costs: a wrong auto-submit burns a review
        cycle, a missed one follows today's flow), and any
        below-floor/missing/unreachable verdict returns False so the
        proxy decides exactly as today. Fail-open end to end.
        """
        try:
            from roboco.db import get_db_context
            from roboco.services.decisions import pilots_dispatch

            commits = task.get("commits") or []
            task_state = {
                "commit_count": len(commits),
                "commit_subjects": [
                    str(c.get("message") or "")[:80]
                    for c in commits[:5]
                    if isinstance(c, dict)
                ],
                "pr_created": bool(task.get("pr_created")),
                "has_plan": bool(task.get("plan")),
                "status": str(task.get("status") or ""),
            }
            async with get_db_context() as db:
                score, confident = await pilots_dispatch.submit_now_confidence(
                    db,
                    task_id=str(task.get("id") or ""),
                    task_state=task_state,
                )
            # Only a confident ZERO_REMAINING ("remaining work is zero")
            # keeps the proxy's flip; a confident 0 or 1 vetoes (the
            # server fast path stays the authority).
            return bool(
                confident and score is not None and score < _SUBMIT_NOW_ZERO_REMAINING
            )
        except Exception as exc:
            logger.warning(
                "submit-now confidence check failed (best-effort)", error=str(exc)
            )
            return False


class DevPromptContextEngine(_Base):
    """What the dev's prompt says about the task's history: the pruned
    description (B30) and the prior-attempt delta brief (B33)."""

    _CONTEXT_PRUNE_MIN_ENTRIES = 3

    def _context_prune_candidates(
        self, entries: list[str]
    ) -> tuple[list[int], list[str]]:
        """Indices and texts of description entries eligible for dropping.

        Any entry matching a protected pattern (gates, findings, acceptance
        criteria, blocking info) is never offered for dropping.
        """
        import re as _re

        protected = _re.compile(
            r"(?i)(acceptance criter|finding|blocked|blocker|gate\b"
            r"|security|secret|credential|must |required|critical"
            r"|migration|breaking|do not|never )"
        )
        droppable = [i for i, e in enumerate(entries) if not protected.search(e)]
        offered = [entries[i] for i in droppable]
        return droppable, offered

    def _context_prune_entries(self, description: str | None) -> tuple[str, list[str]]:
        """Stripped description text and its blank-line-separated entries."""
        import re as _re

        text = (description or "").strip()
        entries = [e.strip() for e in _re.split(r"\n\s*\n", text) if e.strip()]
        return text, entries

    async def _pruned_description_text(
        self,
        description: str | None,
        *,
        task_id: str,
        workflow_state: str,
    ) -> str | None:
        """Pruned description rendering, or None when the pass does not fire.

        Entries are blank-line-separated paragraphs of the raw description.
        Only a confident, below-cut relevance verdict drops an entry. Fewer
        than 3 entries, more than the batch cap, or no droppable entries
        leaves the description untouched (None).
        """
        from roboco.services.decisions import pilots_dispatch

        text, entries = self._context_prune_entries(description)
        if not (
            text
            and len(entries) >= self._CONTEXT_PRUNE_MIN_ENTRIES
            and len(entries) <= pilots_dispatch.CONTEXT_PRUNE_MAX_ENTRIES
        ):
            return None
        droppable, offered = self._context_prune_candidates(entries)
        if not droppable or not offered:
            return None
        from roboco.db import get_db_context

        async with get_db_context() as db:
            keeps = await pilots_dispatch.context_relevance_verdicts(
                db,
                task_id=task_id,
                workflow_state=workflow_state,
                entries=offered,
            )
        if keeps and len(keeps) == len(offered):
            pruned, dropped = self._apply_context_keep_set(entries, droppable, keeps)
            if dropped > 0:
                logger.info(
                    "Pruned low-value context entries from spawn prompt",
                    task_id=task_id,
                    dropped=dropped,
                    kept=len(pruned),
                )
                return self._description_body("\n\n".join(pruned))
        return None

    def _apply_context_keep_set(
        self,
        entries: list[str],
        droppable: list[int],
        keeps: list[bool],
    ) -> tuple[list[str], int]:
        """Drop kept-out entries: returns (pruned entries, dropped count)."""
        keep_set = {i for i, keep in zip(droppable, keeps, strict=False) if keep}
        pruned = [
            e for i, e in enumerate(entries) if i in keep_set or i not in droppable
        ]
        return pruned, len(entries) - len(pruned)

    async def _decisions_pruned_description(
        self,
        description: str | None,
        *,
        task_id: str,
        workflow_state: str,
        fallback: str,
    ) -> str:
        """B30 context_pruning: drop low-value description entries from the
        injected block.

        Only a confident, below-cut relevance verdict drops an entry, and
        fewer than 3 entries, more than the batch cap, or ANY failure
        returns the original rendering unchanged.
        """
        result = fallback
        try:
            pruned = await self._pruned_description_text(
                description, task_id=task_id, workflow_state=workflow_state
            )
            if pruned is not None:
                result = pruned
        except Exception as exc:
            logger.warning(
                "context-pruning decisions pass failed (best-effort)",
                task_id=task_id,
                error=str(exc),
            )
            result = fallback
        return result

    async def _delta_brief_block(self, task: dict[str, Any]) -> str:
        """B33 delta_brief: the shared prior-attempt briefing section.

        ONE classifier serves the three trigger points (spec 7.1): a
        revision respawn (needs_revision), a parked/maintenance resume,
        and a rate-limit resume - every one of them respawns the agent
        through this builder, so the hook lives here rather than in each
        resume path. Off/shadow/below-floor/fresh-start renders nothing
        (today's briefing exactly). Findings ride the existing
        open-findings block; this section adds prior commits, PR state,
        and elapsed time the respawned dev otherwise never sees.
        """
        try:
            from roboco.db import get_db_context
            from roboco.services.decisions import pilots_dispatch

            task_id = str(task.get("id") or "")
            if not task_id:
                return ""
            status = str(task.get("status") or "")
            trigger = (
                "revision_respawn" if status == "needs_revision" else "resume_respawn"
            )
            task_state, commits = self._delta_brief_task_state(task, status)
            elapsed_line = self._delta_brief_elapsed_line(task_state, task)
            async with get_db_context() as db:
                verdict = await pilots_dispatch.delta_brief(
                    db,
                    task_id=task_id,
                    trigger=trigger,
                    task_state=task_state,
                )
            return self._delta_brief_render(
                task_id, verdict, task_state, commits, elapsed_line
            )
        except Exception as exc:
            logger.warning(
                "delta-brief decisions pass failed (best-effort)", error=str(exc)
            )
            return ""

    def _delta_brief_task_state(
        self, task: dict[str, Any], status: str
    ) -> tuple[dict[str, Any], list[Any]]:
        """The delta-brief classifier payload plus the dict-valued commits."""
        commits = [c for c in (task.get("commits") or []) if isinstance(c, dict)]
        task_state: dict[str, Any] = {
            "commit_count": len(commits),
            "commit_subjects": [
                f"{str(c.get('hash') or '')[:8]} "
                f"{str(c.get('message') or '')[:80]}".strip()
                for c in commits[:5]
            ],
            "pr_created": bool(task.get("pr_created")),
            "has_plan": bool(task.get("plan")),
            "status": status,
            "updated_at": str(task.get("updated_at") or ""),
        }
        return task_state, commits

    def _delta_brief_elapsed_line(
        self, task_state: dict[str, Any], task: dict[str, Any]
    ) -> str:
        """Elapsed-time line for the delta brief, stamping elapsed_minutes
        into task_state when a start/claim timestamp parses."""
        from datetime import UTC as _UTC
        from datetime import datetime as _datetime

        started_at = self._parse_iso_dt(task.get("started_at"))
        if started_at is None:
            started_at = self._parse_iso_dt(task.get("claimed_at"))
        if started_at is not None:
            elapsed = _datetime.now(_UTC) - started_at
            elapsed_minutes = int(elapsed.total_seconds() // 60)
            task_state["elapsed_minutes"] = elapsed_minutes
            return f"- Time on this attempt so far: {elapsed_minutes} min"
        return ""

    def _delta_brief_render(
        self,
        task_id: str,
        verdict: Any,
        task_state: dict[str, Any],
        commits: list[Any],
        elapsed_line: str,
    ) -> str:
        """Render the delta-brief section from the classifier verdict."""
        from roboco.services.decisions import pilots_dispatch

        if verdict is None:
            return ""
        mode, depth = verdict
        if mode is pilots_dispatch.DeltaBriefMode.FRESH_START_BRIEF or depth == 0:
            return ""
        if mode is pilots_dispatch.DeltaBriefMode.WORK_ALREADY_DONE_BRIEF:
            return (
                "\n## PRIOR ATTEMPT: WORK APPEARS DONE\n"
                "A prior attempt on this task left committed work. Check "
                f'evidence(task_id="{task_id}") and submit via '
                "i_am_done if the gates pass instead of redoing it.\n"
            )
        lines = ["\n## WHERE YOUR PRIOR ATTEMPT LEFT OFF"]
        if commits:
            lines.append("- Commits already on the branch (do NOT redo them):")
            lines.extend(f"  - {s}" for s in task_state["commit_subjects"])
        else:
            lines.append("- No commits yet from the prior attempt.")
        lines.append(
            "- A PR is already open."
            if task_state["pr_created"]
            else "- No PR open yet."
        )
        if elapsed_line:
            lines.append(elapsed_line)
        lines.append(
            "- Open review findings, if any, are listed in the workflow "
            "section above; address them by id."
        )
        lines.append("")
        return "\n".join(lines)


class CellReviewPromptsEngine(_Base):
    """Spawn prompts for the roles that review a cell's work: QA, the PR
    reviewer (external PRs and the in-path gate), DevOps and the documenter."""

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

    def _build_devops_gate_prompt(self, task: dict[str, Any]) -> str:
        """Prompt for the DevOps agent co-reviewing an infra-flagged gate task.

        The assembled PR touches the project's declared infra paths (the
        ``infra:`` section of its .roboco/conventions.yml), so the DevOps
        agent is the required second reviewer: the primary reviewer's
        pr_pass is blocked until a devops verdict exists for the current PR
        head. The co-claim leaves the primary reviewer's ownership intact.
        """
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")
        pr_number = task.get("pr_number", "?")
        pr_url = task.get("pr_url", "")
        return f"""\
An assembled pull request touches this project's declared INFRA paths
(container / CI / deploy / proxy surfaces from its .roboco/conventions.yml).
You are the required second reviewer on its gate.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}
ASSEMBLED PR: #{pr_number}  {pr_url}

== YOUR ROLE ON THIS GATE ==
You co-review; you do NOT take over the gate. A primary PR reviewer owns the
task: your co-claim must not disturb it. Review the assembled diff with an
infra eye: compose/runtime topology, workflow triggers and permissions,
secret handling, image/deploy chain, env handling. Read-only: you never push
or merge.

== REVIEW WORKFLOW ==

1. claim_gate_review(task_id="{task_id}")
   - co-claims the review (the primary reviewer keeps ownership); returns
   the assembled diff + acceptance criteria inline.
2. note(scope="learning", task_id="{task_id}", text="<what the review surfaced>")
   - required before you decide.
3a. record_devops_review(task_id="{task_id}",
        notes="<what you verified on the infra surface>")
    - if the infra surface is sound: records your verdict for the current PR
    head; the primary reviewer's pr_pass then composes. Do NOT call pr_pass
    unless you intend to decide the gate yourself.
3b. pr_fail(task_id="{task_id}", issues=["<concrete, actionable gap>", ...])
    - if the infra surface is wrong: sends the PR back for revision; your
    findings are ledgered under the devops_review origin.
4. i_am_idle() when done.
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
