"""The Board Programs' one-shot prompts, by owner, and the Auditor's other prompts.

Engine mixins composed into AgentOrchestrator; split out of
dispatch_prompts.py with every method body unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.foundation.policy.content import markers as _markers
from roboco.runtime.orchestrator import (
    _format_barfly_candidates,
    _format_rejected_spotlights,
    _format_seen_features,
    _format_shipped_since,
    _shipped_digest_block,
)

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class ProductOwnerProgramPromptsEngine(_Base):
    """The Product Owner's one-shot Board Program prompts: Roadmap, Pest
    Control, Scales, Spackle and Dogfood."""

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


class MarketingProgramPromptsEngine(_Base):
    """The Head of Marketing's one-shot Board Program prompts: War Room,
    Mirror, the feature spotlight, Periscope, Barfly and Megaphone."""

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


class AuditorPromptsEngine(_Base):
    """The Auditor's prompts: the Coroner, Sentinel, decisions-audit and
    Librarian programs, the audit alert and vault curation."""

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
        stranded_block = (
            "\n## Stranded-block context\n"
            f"- Block reason: {incident_ref.get('block_reason', 'unknown')}\n"
            f"- Time blocked: {incident_ref.get('time_blocked', 'unknown')}\n"
            "- Escalation history: "
            f"{incident_ref.get('escalation_history', 'none')}\n"
            if kind == "stranded"
            else ""
        )
        context_block = (
            f"\n## Evidence gathered for you\n{incident_context}\n"
            if incident_context
            else ""
        )
        return f"""\
You are the Auditor. An incident just triggered your Coroner postmortem.

TASK: {task_id}

INCIDENT: {title!r} ({incident_task_id}) — {kind}
{stranded_block}{context_block}
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

    def _build_decisions_audit_prompt(
        self,
        task: dict[str, Any],
        prior_context: str = "",
        evidence_context: str = "",
    ) -> str:
        """Prompt for the Auditor's one-shot daily decisions-audit review
        (Board Program #15). Auditor-solo, complete-at-note: review the
        injected decision_log aggregates (yesterday vs. the trailing-week
        baseline), record per-pilot health via note(), and where the
        evidence shows miscalibration name the pilot, the numbers, and the
        recommended knob change. The Auditor never adjusts anything itself
        - recommendations are requirements the CEO decides; the pilot
        verdicts being audited were themselves advisory or gated by
        confidence floors, so this pass audits the evidence, not live
        gates."""
        task_id = task.get("id", "unknown")
        prior_block = f"\n## Prior cycles\n{prior_context}\n" if prior_context else ""
        evidence_block = (
            f"\n## Decision-log evidence gathered for you\n{evidence_context}\n"
            if evidence_context
            else ""
        )
        return f"""\
You are the Auditor. It's time for your daily Decisions audit.

TASK: {task_id}

The Decisions service renders typed verdicts (choice/score/noul with
confidence) that gate or advise fleet decisions; pilots run ON (acting) or
SHADOW (logged only). Your job is to say, from the evidence, whether each
pilot is behaving like its confidence numbers mean anything: volume
collapses or spikes, mean confidence drifting off the threshold it gates
on, spend anomalies, or a pilot whose actions stopped correlating with
outcomes.
{evidence_block}{prior_block}
== WHAT TO DO ==

1. triage() — see your board-level context.
2. Read the decision-log evidence gathered for you above (per-pilot 24h
   aggregates vs. the trailing-week baseline). It is server-assembled —
   you cannot re-run those queries yourself, so start from it.
3. Judge each active pilot on drift, not on a single day: one noisy window
   is not miscalibration.
4. note() your assessment — one line per notable pilot, and EVERY call
   must carry task_id="{task_id}": the audit completes at note, and a
   note without the task id is only a journal entry that completes
   nothing (the 2026-09-27 wedge — findings were noted, the cycle
   pend-resp-looped anyway). Where the evidence supports a knob
   change, name the pilot, the numbers, and your recommended threshold
   value or shadow/on flip. You never adjust anything yourself; the
   CEO decides.
5. If (and only if) the whole window is genuinely unremarkable, call
   nothing_to_propose(task_id="{task_id}", reason="no pilot anomalous in
   this window") instead of a filler note — an honest miss is better than
   manufactured findings.
6. i_am_idle() — once noted (or declined).
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
