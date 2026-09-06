"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: dispatch_work)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from roboco.agents_config import (
    get_agent_role,
)
from roboco.config import settings
from roboco.foundation.identity import (
    is_human_only_role,
    is_spawnable_agent_slug,
    role_for_slug_or_none,
)
from roboco.foundation.policy.content import markers as _markers
from roboco.runtime.orchestrator import (
    _PM_DISPATCH_FETCH_LIMIT,
    _dispatch_board_program_exploration,
    _is_branch_pending,
    _is_held_ceo_source,
    _is_non_dev_dispatch_source,
    _system_api_headers,
    logger,
)
from roboco.services.task import (
    PR_REVIEW_SOURCES,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class DispatchWorkEngine(_Base):
    """Mixin holding the "dispatch_work" methods moved out of AgentOrchestrator."""

    async def _dispatch_all_work(self) -> None:
        """Run all dispatchers to check for and assign work.

        Each dispatcher is isolated: if one raises (e.g., a transient API
        error), the rest still run in this tick instead of waiting for the
        next one.

        `_tick_handled_tasks` gives downstream dispatchers a way to
        skip tasks that an earlier dispatcher already acted on this
        tick. Order-dependent bugs (like the Fix-B scenario where
        `_dispatch_qa_work` claimed for QA and the next dispatcher
        re-spawned the dev on the same claimed row) are defanged by
        early dispatchers marking the task handled.

        The stale-claim reaper runs first, before any dispatcher tries to
        spawn an agent for a task whose previous holder is dead. Without
        this ordering, the spawn pass could race against a stale claim and
        skip work the reaper would have freed in the same tick.

        A ``dispatch``-scope maintenance pause drains every spawn-issuing
        dispatcher here EXCEPT ``pm_work``, which always runs: it also
        routes Board Program exploration dispatch (a separate,
        independently-paused ``board_programs`` scope, see
        ``_dispatch_board_program_exploration``), so it cannot be skipped
        wholesale without incorrectly coupling the two scopes. ``pm_work``
        itself skips its own non-board-program branches while paused (see
        ``_dispatch_pm_work``).
        """
        self._tick_handled_tasks = set()

        # Refresh the fleet uptime ledger first: the reaper and stuck-task
        # checks below read _active_age, which needs a fresh ledger to
        # discount a CEO-ordered pause instead of reading it as neglect.
        # _refresh_uptime fails open internally; this only guards session
        # acquisition itself.
        try:
            from roboco.db.base import get_session_factory

            factory = get_session_factory()
            async with factory() as db:
                await self._refresh_uptime(db)
        except Exception as e:
            logger.error("Uptime ledger refresh failed; continuing tick", error=str(e))

        # Free any tasks whose claim went stale before the spawn pass runs.
        # Wrapped because a reaper failure must not block dispatch — the
        # next tick will retry.
        try:
            await self._reap_stale_claims()
        except Exception as e:
            logger.error("Stale-claim reaper failed; continuing tick", error=str(e))

        # Enforce the GROK cost ceiling (budget kill-switch parity). Wrapped so a
        # failure never blocks dispatch; the next tick retries. Unrelated to the
        # maintenance pause: a genuinely runaway-cost container is a real signal,
        # not a "nothing is happening" false positive, so it is never suppressed
        # by a pause; only NEW spawns are.
        try:
            await self._enforce_grok_cost_budget()
        except Exception as e:
            logger.error("Grok cost-budget sweep failed; continuing tick", error=str(e))

        from roboco.services.maintenance_pause import PauseScope

        dispatch_paused = await self._is_paused(PauseScope.DISPATCH)

        dispatchers: list[tuple[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=30.0, headers=_system_api_headers()
        ) as client:
            dispatchers = [("pm_work", self._dispatch_pm_work(client))]
            if not dispatch_paused:
                dispatchers += [
                    ("pm_closure_work", self._dispatch_pm_closure_work(client)),
                    (
                        "revision_coordination",
                        self._dispatch_revision_coordination_roots(client),
                    ),
                    ("dev_work", self._dispatch_dev_work(client)),
                    ("qa_work", self._dispatch_qa_work(client)),
                    # pr_gate_work runs BEFORE pr_review_work: both can spawn
                    # the shared pr-reviewer-1 (root→master gate reviews vs.
                    # inbound external/fork PR reviews), and the internal gate
                    # blocks the delivery pipeline while an external PR can
                    # wait a tick - see _dispatch_pr_gate_work's docstring.
                    ("pr_gate_work", self._dispatch_pr_gate_work(client)),
                    ("pr_review_work", self._dispatch_pr_review_work(client)),
                    ("doc_work", self._dispatch_doc_work(client)),
                    ("pm_review_work", self._dispatch_pm_review_work(client)),
                    ("marketing_work", self._dispatch_marketing_work(client)),
                    ("blocker_work", self._dispatch_blocker_work(client)),
                    (
                        "claimed_without_agent",
                        self._dispatch_claimed_without_agent(client),
                    ),
                    ("escalation_work", self._dispatch_escalation_work(client)),
                    ("approval_work", self._dispatch_approval_work(client)),
                    ("a2a_work", self._dispatch_a2a_work(client)),
                    ("audit_work", self._dispatch_audit_work(client)),
                    (
                        "vault_curation_work",
                        self._dispatch_vault_curation_work(client),
                    ),
                    ("detect_stuck_tasks", self._detect_stuck_tasks(client)),
                ]
            for name, coro in dispatchers:
                try:
                    await coro
                except Exception as e:
                    logger.error(
                        "Dispatcher raised; continuing with next dispatcher",
                        dispatcher=name,
                        error=str(e),
                    )

    async def _handle_pm_assigned_task(
        self, task: dict[str, Any], assigned_to: str
    ) -> None:
        """Spawn an already-assigned PM agent if it isn't running."""
        agent_slug = self._resolve_agent_slug(assigned_to)
        if agent_slug not in self._PM_AGENTS or self._is_agent_active(agent_slug):
            return
        if await self._pm_respawn_should_gate(agent_slug, task):
            return
        logger.info(
            "Spawning assigned PM agent",
            task_id=task.get("id"),
            agent_id=agent_slug,
        )
        pm_prompt = (
            self._build_main_pm_triage_prompt(task)
            if agent_slug == "main-pm"
            else self._build_pm_triage_prompt(task)
        )
        await self.spawn_agent(
            agent_id=agent_slug,
            task_id=task["id"],
            initial_prompt=pm_prompt,
            git_context=self._task_git_context(task),
            spawned_by="_handle_pm_assigned_task",
        )

    async def _handle_board_assigned_task(
        self, task: dict[str, Any], assigned_to: str
    ) -> None:
        """Review an assigned board task with the FULL board (PO + HoM), ONCE each.

        A board/coordination task — especially one with a UI / user-facing
        dimension — must be reviewed by BOTH the Product Owner AND the Head of
        Marketing before it is handed to the CEO. The task is assigned to one
        board agent, but the review is a two-reviewer gate, so this dispatches
        both regardless of which one ``assigned_to`` names.

        Board roles advise: they can triage, record notes, and discuss, but have
        NO verb to claim, plan, delegate, or complete. A respawn cannot advance
        the task — it would just loop — so dispatch is one-shot per (agent, task).
        The board reviews and records requirements; the CEO then approves and
        hands the task to Main PM for delegation to the cells.

        Once BOTH reviewers have finished (each dispatched and no longer active),
        the board-review handoff fires: the task is flagged board-reviewed and a
        single formal CEO notification is emitted so Approve & Start is an
        actionable signal rather than buried chatter.
        """
        # `assigned_to` only gates that this IS a board task; the review itself
        # always involves the whole board, not just the named assignee.
        if self._resolve_agent_slug(assigned_to) not in self._BOARD_AGENTS:
            return
        task_id = str(task.get("id"))
        for board_slug in sorted(self._BOARD_AGENTS):
            await self._dispatch_board_reviewer(board_slug, task_id, task)
        await self._maybe_handoff_board_review_to_ceo(task_id)

    async def _dispatch_board_reviewer(
        self, board_slug: str, task_id: str, task: dict[str, Any]
    ) -> None:
        """One-shot spawn of a single board reviewer for a board task.

        Skips when the reviewer is already running or has already been
        dispatched for this task (board roles have no progression verb, so a
        respawn would loop). Records the (agent, task) pair so the
        review-completion detector can tell which reviewers have run.
        """
        if self._is_agent_active(board_slug):
            return
        key = (board_slug, task_id)
        if key in self._board_dispatched:
            return
        # Respawn circuit breaker — parity with every other task-keyed path.
        if await self._pm_respawn_should_gate(board_slug, task):
            return
        self._board_dispatched.add(key)
        logger.info(
            "Spawning board agent for review",
            task_id=task_id,
            agent_id=board_slug,
        )
        await self.spawn_agent(
            agent_id=board_slug,
            task_id=task["id"],
            initial_prompt=self._build_board_prompt(task),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_board_reviewer",
        )

    async def _dispatch_roadmap_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Product-Owner spawn to author a themed roadmap cycle.

        Unlike ``_handle_board_assigned_task`` (the two-reviewer board-review
        gate), a roadmap cycle is Product-Owner-solo in v1 (see the roadmap
        spec's non-goals — HoM co-authoring is out of scope), so this bypasses
        the review-pair machinery and its board-review-complete/Approve & Start
        handoff entirely: HoM is never spawned for this task, and no CEO
        "Approve & Start" notification fires. ``propose_roadmap`` (not this
        dispatcher) marks the cycle authored (a ``roadmap_cycle`` marker); this
        only ever spawns once per task while that marker is absent, reusing the
        respawn breaker every
        other board dispatch uses.
        """
        task_id = str(task.get("id"))
        markers_dict = task.get("orchestration_markers") or {}
        if markers_dict.get(_markers.ROADMAP_CYCLE) is not None:
            return  # already authored — the CEO roadmap queue owns the rest
        po_slug = "product-owner"
        if self._is_agent_active(po_slug):
            return
        if await self._pm_respawn_should_gate(po_slug, task):
            return
        logger.info("Spawning Product Owner for roadmap exploration", task_id=task_id)
        prior_context = await self._board_program_prior_context("roadmap")
        market_brief_context = await self._periscope_brief_context()
        await self.spawn_agent(
            agent_id=po_slug,
            task_id=task["id"],
            initial_prompt=self._build_roadmap_prompt(
                task, prior_context, market_brief_context
            ),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_roadmap_exploration",
        )

    async def _board_program_prior_context(self, program_key: str) -> str:
        """Best-effort LEARN read for prompt injection — mirrors
        ``_pm_respawn_should_gate``'s tracing-gap audit lookup's best-effort
        DB posture: a read failure here must never block a spawn, only drop
        the '## Prior cycles' section from this cycle's prompt."""
        try:
            from roboco.db import get_db_context
            from roboco.services.board_programs import get_board_program_engine

            async with get_db_context() as db:
                return await get_board_program_engine(db).prior_cycle_context(
                    program_key
                )
        except Exception:
            logger.warning(
                "board-program: prior-cycle-context read failed (best-effort)",
                program=program_key,
            )
            return ""

    async def _dispatch_pest_control_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Product-Owner spawn to author a Pest Control bug hunt.

        Mirrors ``_dispatch_roadmap_exploration`` exactly (PO-solo, the same
        respawn breaker
        "already authored" marker pre-check — ``propose_bug_hunt`` marks it
        via the ``pest_hunt`` marker, not this dispatcher). The extra step is
        the server-assembled evidence context (rework hotspots + findings-
        ledger aggregates) the PO cannot gather itself.
        """
        task_id = str(task.get("id"))
        markers_dict = task.get("orchestration_markers") or {}
        if markers_dict.get(_markers.PEST_HUNT) is not None:
            return  # already authored — the CEO pest-control queue owns the rest
        po_slug = "product-owner"
        if self._is_agent_active(po_slug):
            return
        if await self._pm_respawn_should_gate(po_slug, task):
            return
        logger.info(
            "Spawning Product Owner for pest-control exploration", task_id=task_id
        )
        prior_context = await self._board_program_prior_context("pest_control")
        evidence_context = await self._pest_control_evidence_context()
        await self.spawn_agent(
            agent_id=po_slug,
            task_id=task["id"],
            initial_prompt=self._build_pest_control_prompt(
                task, prior_context, evidence_context
            ),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_pest_control_exploration",
        )

    async def _pest_control_evidence_context(self) -> str:
        """Best-effort evidence-gathering read for prompt injection — mirrors
        ``_board_program_prior_context``'s degrade-to-empty-string posture: a
        read failure here must never block a spawn, only drop the evidence
        section from this cycle's prompt."""
        try:
            from roboco.db import get_db_context
            from roboco.services.pest_control_engine import get_pest_control_engine

            async with get_db_context() as db:
                return await get_pest_control_engine(db).evidence_context()
        except Exception:
            logger.warning("pest-control: evidence-context read failed (best-effort)")
            return ""

    async def _dispatch_scales_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Product-Owner spawn to author a Scales rebalance plan.

        Mirrors ``_dispatch_pest_control_exploration`` exactly (PO-solo, the
        respawn breaker the
        same "already authored" marker pre-check — ``propose_rebalance``
        marks it via the ``rebalance_plan`` marker, not this dispatcher). The
        extra step is the server-assembled stale-backlog snapshot the PO
        cannot gather itself.
        """
        task_id = str(task.get("id"))
        markers_dict = task.get("orchestration_markers") or {}
        if markers_dict.get(_markers.REBALANCE_PLAN) is not None:
            return  # already authored — the CEO Scales queue owns the rest
        po_slug = "product-owner"
        if self._is_agent_active(po_slug):
            return
        if await self._pm_respawn_should_gate(po_slug, task):
            return
        logger.info("Spawning Product Owner for scales exploration", task_id=task_id)
        prior_context = await self._board_program_prior_context("scales")
        evidence_context = await self._scales_evidence_context()
        await self.spawn_agent(
            agent_id=po_slug,
            task_id=task["id"],
            initial_prompt=self._build_scales_prompt(
                task, prior_context, evidence_context
            ),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_scales_exploration",
        )

    async def _scales_evidence_context(self) -> str:
        """Best-effort evidence-gathering read for prompt injection — mirrors
        ``_pest_control_evidence_context``'s degrade-to-empty-string posture: a
        read failure here must never block a spawn, only drop the stale-
        backlog snapshot from this cycle's prompt."""
        try:
            from roboco.db import get_db_context
            from roboco.services.scales_engine import get_scales_engine

            async with get_db_context() as db:
                return await get_scales_engine(db).evidence_context()
        except Exception:
            logger.warning("scales: evidence-context read failed (best-effort)")
            return ""

    async def _dispatch_coroner_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Auditor spawn to autopsy an incident and author ONE
        postmortem via ``propose_postmortem``.

        Like ``_dispatch_feature_spotlight_exploration`` (not the roadmap/
        pest-control "already authored" marker shape): no pre-check is
        needed — ``propose_postmortem`` completes this task atomically, so a
        successful call stops it matching the PENDING fetch on the next
        tick. Reuses respawn breaker every other board dispatch uses. EVENT-triggered
        (spec §4): this task only ever exists because
        ``CoronerEngine.open_for_incident`` opened it — there is no LEARN
        "prior cycles" injection here (no cron cadence to have learned from).
        """
        task_id = str(task.get("id"))
        auditor_slug = "auditor"
        if self._is_agent_active(auditor_slug):
            return
        if await self._pm_respawn_should_gate(auditor_slug, task):
            return
        logger.info("Spawning Auditor for Coroner postmortem", task_id=task_id)
        incident_context = await self._coroner_incident_context(task)
        await self.spawn_agent(
            agent_id=auditor_slug,
            task_id=task["id"],
            initial_prompt=self._build_coroner_prompt(task, incident_context),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_coroner_exploration",
        )

    async def _coroner_incident_context(self, task: dict[str, Any]) -> str:
        """Best-effort evidence-gathering read for prompt injection — mirrors
        ``_pest_control_evidence_context``'s degrade-to-empty-string posture."""
        markers_dict = task.get("orchestration_markers") or {}
        incident_ref = markers_dict.get(_markers.CORONER_INCIDENT) or {}
        incident_task_id = incident_ref.get("incident_task_id")
        if not incident_task_id:
            return ""
        try:
            from uuid import UUID as _UUID

            from roboco.db import get_db_context
            from roboco.services.coroner_engine import get_coroner_engine

            async with get_db_context() as db:
                return await get_coroner_engine(db).incident_context(
                    _UUID(str(incident_task_id))
                )
        except Exception:
            logger.warning("coroner: incident-context read failed (best-effort)")
            return ""

    async def _dispatch_war_room_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Head-of-Marketing spawn to design ONE War Room campaign
        and author it via ``propose_campaign``.

        Like ``_dispatch_coroner_exploration`` (not the roadmap/pest-control
        "already authored" marker shape): no pre-check is needed —
        ``propose_campaign`` completes this task atomically, so a successful
        call stops it matching the PENDING fetch on the next tick. Reuses the
        respawn breaker every
        other board dispatch uses. EVENT-triggered (spec §4): this task only
        ever exists because a release just published or the CEO called "run
        now" — there is no LEARN "prior cycles" injection here (no cron
        cadence to have learned from, mirrors Coroner). The task's own
        ``war_room_brief`` marker (release version + highlights, or {} for a
        blank on-demand cycle) is already server-assembled at origination
        time — no extra DB read is needed to pass it into the prompt.
        """
        task_id = str(task.get("id"))
        hom_slug = "head-marketing"
        if self._is_agent_active(hom_slug):
            return
        if await self._pm_respawn_should_gate(hom_slug, task):
            return
        logger.info(
            "Spawning Head of Marketing for War Room campaign planning",
            task_id=task_id,
        )
        await self.spawn_agent(
            agent_id=hom_slug,
            task_id=task["id"],
            initial_prompt=self._build_war_room_prompt(task),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_war_room_exploration",
        )

    async def _dispatch_spackle_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Product-Owner spawn to author a Spackle gap-fill audit.

        Mirrors ``_dispatch_pest_control_exploration`` exactly (PO-solo, the
        respawn breaker the
        same "already authored" marker pre-check — ``propose_gap_fill`` marks
        it via the ``gap_fill`` marker, not this dispatcher). Unlike Pest
        Control there is no server-assembled evidence context — the spec
        deliberately keeps Spackle free of a heavy server-side inventory
        engine; the inventory diffing is the PO's own read-tool work, ordered
        explicitly by ``_build_spackle_prompt``.
        """
        task_id = str(task.get("id"))
        markers_dict = task.get("orchestration_markers") or {}
        if markers_dict.get(_markers.GAP_FILL) is not None:
            return  # already authored — the CEO spackle queue owns the rest
        po_slug = "product-owner"
        if self._is_agent_active(po_slug):
            return
        if await self._pm_respawn_should_gate(po_slug, task):
            return
        logger.info("Spawning Product Owner for spackle exploration", task_id=task_id)
        prior_context = await self._board_program_prior_context("spackle")
        await self.spawn_agent(
            agent_id=po_slug,
            task_id=task["id"],
            initial_prompt=self._build_spackle_prompt(task, prior_context),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_spackle_exploration",
        )

    async def _dispatch_mirror_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Head-of-Marketing spawn to author a Mirror positioning
        audit.

        Mirrors ``_dispatch_spackle_exploration`` exactly (HoM-solo, the same
        respawn breaker
        "already authored" marker pre-check — ``propose_messaging_fixes``
        marks it via the ``messaging_fixes`` marker, not this dispatcher).
        Like Spackle there is no server-assembled evidence context — the
        messaging audit (README claims vs shipped features, docs-site
        promises vs code, charter alignment) is the HoM's own read-tool
        work, ordered explicitly by ``_build_mirror_prompt``.
        """
        task_id = str(task.get("id"))
        markers_dict = task.get("orchestration_markers") or {}
        if markers_dict.get(_markers.MESSAGING_FIXES) is not None:
            return  # already authored — the CEO mirror queue owns the rest
        hom_slug = "head-marketing"
        if self._is_agent_active(hom_slug):
            return
        if await self._pm_respawn_should_gate(hom_slug, task):
            return
        logger.info(
            "Spawning Head of Marketing for mirror exploration", task_id=task_id
        )
        prior_context = await self._board_program_prior_context("mirror")
        await self.spawn_agent(
            agent_id=hom_slug,
            task_id=task["id"],
            initial_prompt=self._build_mirror_prompt(task, prior_context),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_mirror_exploration",
        )

    async def _dispatch_dogfood_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Product-Owner spawn to walk the product and author a
        Dogfood friction audit.

        Mirrors ``_dispatch_spackle_exploration`` exactly (PO-solo, the same
        respawn breaker
        "already authored" marker pre-check — ``propose_friction_fixes``
        marks it via the ``friction_fixes`` marker, not this dispatcher).
        There is no server-assembled evidence context — walking the product
        live is the PO's own tool work, ordered explicitly by
        ``_build_dogfood_prompt``. This is the ONE spawn (task-scoped, keyed
        on ``task["source"] == DOGFOOD_SOURCE``) that also gets the
        playwright MCP mounted — see ``_is_dogfood_spawn``.
        """
        task_id = str(task.get("id"))
        markers_dict = task.get("orchestration_markers") or {}
        if markers_dict.get(_markers.FRICTION_FIXES) is not None:
            return  # already authored — the CEO dogfood queue owns the rest
        po_slug = "product-owner"
        if self._is_agent_active(po_slug):
            return
        if await self._pm_respawn_should_gate(po_slug, task):
            return
        logger.info("Spawning Product Owner for dogfood exploration", task_id=task_id)
        prior_context = await self._board_program_prior_context("dogfood")
        await self.spawn_agent(
            agent_id=po_slug,
            task_id=task["id"],
            initial_prompt=self._build_dogfood_prompt(task, prior_context),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_dogfood_exploration",
        )

    async def _dispatch_feature_spotlight_exploration(
        self, task: dict[str, Any]
    ) -> None:
        """One-shot Head-of-Marketing spawn to investigate + author a spotlight.

        Simpler than _dispatch_roadmap_exploration: no "already authored" marker
        pre-check is needed here, because a successful propose_feature_spotlight()
        completes this task atomically (it stops matching the PENDING fetch on the
        next tick) — unlike the roadmap cycle, which stays open across the CEO's
        per-item decisions and needs the marker check to avoid re-spawning the PO
        after authoring. Reuses the respawn breaker every other board dispatch uses.
        """
        task_id = str(task.get("id"))
        hom_slug = "head-marketing"
        if self._is_agent_active(hom_slug):
            return
        if await self._pm_respawn_should_gate(hom_slug, task):
            return
        logger.info(
            "Spawning Head of Marketing for feature-spotlight exploration",
            task_id=task_id,
        )
        prior_context = await self._board_program_prior_context("x_feature")
        await self.spawn_agent(
            agent_id=hom_slug,
            task_id=task["id"],
            initial_prompt=self._build_feature_spotlight_prompt(task, prior_context),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_feature_spotlight_exploration",
        )

    async def _dispatch_periscope_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Head-of-Marketing spawn to research the market and file a
        Periscope brief.

        Mirrors ``_dispatch_feature_spotlight_exploration``: no "already
        authored" marker pre-check is needed — ``propose_market_brief``
        completes this task atomically (the x_feature complete-at-propose
        asymmetry), so it stops matching the PENDING fetch on the next tick.
        Reuses respawn breaker every other board dispatch uses.
        """
        task_id = str(task.get("id"))
        hom_slug = "head-marketing"
        if self._is_agent_active(hom_slug):
            return
        if await self._pm_respawn_should_gate(hom_slug, task):
            return
        logger.info(
            "Spawning Head of Marketing for periscope exploration", task_id=task_id
        )
        prior_context = await self._board_program_prior_context("periscope")
        await self.spawn_agent(
            agent_id=hom_slug,
            task_id=task["id"],
            initial_prompt=self._build_periscope_prompt(task, prior_context),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_periscope_exploration",
        )

    async def _dispatch_barfly_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Head-of-Marketing spawn to review Barfly's screened X
        conversations and draft replies.

        Mirrors ``_dispatch_periscope_exploration``: no "already authored"
        marker pre-check is needed — ``propose_conversation_replies``
        completes this task atomically (the x_feature/periscope complete-at-
        propose asymmetry, multiplied across every materialized reply), so
        it stops matching the PENDING fetch on the next tick. Reuses the
        respawn breaker every
        other board dispatch uses.
        """
        task_id = str(task.get("id"))
        hom_slug = "head-marketing"
        if self._is_agent_active(hom_slug):
            return
        if await self._pm_respawn_should_gate(hom_slug, task):
            return
        logger.info(
            "Spawning Head of Marketing for barfly exploration", task_id=task_id
        )
        prior_context = await self._board_program_prior_context("barfly")
        await self.spawn_agent(
            agent_id=hom_slug,
            task_id=task["id"],
            initial_prompt=self._build_barfly_prompt(task, prior_context),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_barfly_exploration",
        )

    async def _periscope_brief_context(self) -> str:
        """Best-effort "latest market brief" read for the roadmap exploration
        prompt's cross-role injection (spec §4) — mirrors
        ``_pest_control_evidence_context``'s degrade-to-empty-string posture:
        a read failure here must never block the roadmap spawn, only drop
        the brief section from this cycle's prompt."""
        try:
            from roboco.db import get_db_context
            from roboco.services.periscope_engine import get_periscope_engine

            async with get_db_context() as db:
                return await get_periscope_engine(db).latest_brief_context()
        except Exception:
            logger.warning("periscope: latest-brief read failed (best-effort)")
            return ""

    async def _dispatch_sentinel_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Auditor spawn to assess org-wide quality drift and file a
        Sentinel report.

        Mirrors ``_dispatch_periscope_exploration``: no "already authored"
        marker pre-check is needed — ``propose_quality_report`` completes
        this task atomically (the x_feature/periscope complete-at-propose
        asymmetry), so it stops matching the PENDING fetch on the next tick.
        Reuses respawn breaker every other board dispatch uses. The extra step is the
        server-assembled drift evidence (waived-findings trend, open-
        findings-by-severity, conventions hotspots, budget snapshot) the
        Auditor cannot gather itself — mirrors
        ``_dispatch_pest_control_exploration``'s evidence-context shape.
        """
        task_id = str(task.get("id"))
        auditor_slug = "auditor"
        if self._is_agent_active(auditor_slug):
            return
        if await self._pm_respawn_should_gate(auditor_slug, task):
            return
        logger.info("Spawning Auditor for sentinel exploration", task_id=task_id)
        prior_context = await self._board_program_prior_context("sentinel")
        evidence_context = await self._sentinel_evidence_context()
        await self.spawn_agent(
            agent_id=auditor_slug,
            task_id=task["id"],
            initial_prompt=self._build_sentinel_prompt(
                task, prior_context, evidence_context
            ),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_sentinel_exploration",
        )

    async def _sentinel_evidence_context(self) -> str:
        """Best-effort evidence-gathering read for prompt injection — mirrors
        ``_pest_control_evidence_context``'s degrade-to-empty-string posture:
        a read failure here must never block a spawn, only drop the evidence
        section from this cycle's prompt."""
        try:
            from roboco.db import get_db_context
            from roboco.services.sentinel_engine import get_sentinel_engine

            async with get_db_context() as db:
                return await get_sentinel_engine(db).evidence_context()
        except Exception:
            logger.warning("sentinel: evidence-context read failed (best-effort)")
            return ""

    async def _dispatch_megaphone_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Head-of-Marketing spawn to pick ONE editorial angle and
        file a Megaphone post.

        Mirrors ``_dispatch_periscope_exploration``: no "already authored"
        marker pre-check is needed — ``propose_editorial_post`` completes
        this task atomically (the x_feature/periscope complete-at-propose
        asymmetry), so it stops matching the PENDING fetch on the next tick.
        Reuses respawn breaker every other board dispatch uses. The extra step is the
        server-assembled shipped-this-week digest (completed tasks +
        CHANGELOG Unreleased bullets) the Head of Marketing cannot gather
        itself — mirrors ``_dispatch_pest_control_exploration``'s
        evidence-context shape.
        """
        task_id = str(task.get("id"))
        hom_slug = "head-marketing"
        if self._is_agent_active(hom_slug):
            return
        if await self._pm_respawn_should_gate(hom_slug, task):
            return
        logger.info(
            "Spawning Head of Marketing for megaphone exploration", task_id=task_id
        )
        prior_context = await self._board_program_prior_context("megaphone")
        digest_context = await self._megaphone_digest_context()
        await self.spawn_agent(
            agent_id=hom_slug,
            task_id=task["id"],
            initial_prompt=self._build_megaphone_prompt(
                task, prior_context, digest_context
            ),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_megaphone_exploration",
        )

    async def _megaphone_digest_context(self) -> str:
        """Best-effort shipped-this-week digest read for prompt injection —
        mirrors ``_sentinel_evidence_context``'s degrade-to-empty-string
        posture: a read failure here must never block the spawn, only drop
        the digest section from this cycle's prompt."""
        try:
            from roboco.db import get_db_context
            from roboco.services.megaphone_engine import get_megaphone_engine

            async with get_db_context() as db:
                return await get_megaphone_engine(db).digest_context()
        except Exception:
            logger.warning("megaphone: digest-context read failed (best-effort)")
            return ""

    async def _dispatch_librarian_exploration(self, task: dict[str, Any]) -> None:
        """One-shot Auditor spawn to mine journals/learnings and draft
        playbooks (Librarian, Board Program).

        Mirrors ``_dispatch_sentinel_exploration``: no "already authored"
        marker pre-check is needed — ``propose_playbook_drafts`` completes
        this task atomically (the x_feature/periscope/sentinel complete-at-
        propose asymmetry), so it stops matching the PENDING fetch on the
        next tick. Reuses the respawn breaker every other board dispatch
        uses. The extra step is
        the server-assembled mining context (recurring learning-journal
        topics + existing playbook titles) the Auditor cannot gather itself
        — mirrors ``_dispatch_sentinel_exploration``'s evidence-context
        shape.
        """
        task_id = str(task.get("id"))
        auditor_slug = "auditor"
        if self._is_agent_active(auditor_slug):
            return
        if await self._pm_respawn_should_gate(auditor_slug, task):
            return
        logger.info("Spawning Auditor for librarian exploration", task_id=task_id)
        prior_context = await self._board_program_prior_context("librarian")
        mining_context = await self._librarian_mining_context()
        await self.spawn_agent(
            agent_id=auditor_slug,
            task_id=task["id"],
            initial_prompt=self._build_librarian_prompt(
                task, prior_context, mining_context
            ),
            git_context=self._task_git_context(task),
            spawned_by="_dispatch_librarian_exploration",
        )

    async def _librarian_mining_context(self) -> str:
        """Best-effort mining-context read for prompt injection — mirrors
        ``_sentinel_evidence_context``'s degrade-to-empty-string posture: a
        read failure here must never block a spawn, only drop the mining
        section from this cycle's prompt."""
        try:
            from roboco.db import get_db_context
            from roboco.services.librarian_engine import get_librarian_engine

            async with get_db_context() as db:
                return await get_librarian_engine(db).mining_context()
        except Exception:
            logger.warning("librarian: mining-context read failed (best-effort)")
            return ""

    def _board_review_complete(self, task_id: str) -> bool:
        """True once EVERY board reviewer has reviewed and gone idle.

        A reviewer has finished when it was dispatched for this task
        (``_board_dispatched``) and is no longer running (``_is_agent_active``).
        Both PO and HoM must satisfy this before the task is handoff-ready.
        """
        return all(
            (board_slug, task_id) in self._board_dispatched
            and not self._is_agent_active(board_slug)
            for board_slug in self._BOARD_AGENTS
        )

    async def _maybe_handoff_board_review_to_ceo(self, task_id: str) -> None:
        """Unlock the CEO's Approve & Start gate when the board review is done.

        Two one-shot effects fire once BOTH board reviewers have finished:
          1. Persist ``board_review_complete`` on the task. The task stays
             pending (its pending state is what hands it to Main PM on approval),
             so this flag is the only thing that makes the CEO's Approve & Start
             button appear — it never shows on a board task the board hasn't
             finished reviewing.
          2. Emit an ack-required APPROVAL notification to the CEO. Board agents
             only record journal notes during review, which
             left the CEO with no actionable signal; this is that signal.

        Fires at most once per task; a failure clears the guard so a later tick
        retries, and never blocks the dispatch loop.
        """
        if task_id in self._board_review_ceo_notified:
            return
        if not self._board_review_complete(task_id):
            return
        self._board_review_ceo_notified.add(task_id)
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.notification import NotificationService
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                task_service = TaskService(db)
                task = await task_service.get(UUID(task_id))
                await task_service.mark_board_review_complete(UUID(task_id))
                await db.commit()
            await NotificationService().send_board_review_complete_notification(
                task_id=task_id,
                task_title=task.title if task else None,
            )
        except Exception as exc:
            # Don't wedge dispatch on a failure; allow a retry by clearing the
            # one-shot guard so a later tick can re-run the handoff.
            self._board_review_ceo_notified.discard(task_id)
            logger.warning(
                "Failed to hand board-review completion to CEO",
                task_id=task_id,
                error=str(exc),
            )
            return
        logger.info(
            "Board review complete — CEO Approve & Start unlocked",
            task_id=task_id,
        )
        # Keep-alive re-draft: if an intake chat is parked awaiting this review,
        # inject the board's feedback so the still-resident prompter re-drafts
        # in-context. Best-effort; the cold "Re-draft" path covers the rest.
        await self._inject_board_brief_into_parked_intake(task_id)

    async def _compose_parked_intake_redraft(
        self, db: "AsyncSession", task_id: str
    ) -> str | None:
        """The redraft seed message for a parked intake session, or None if the
        task is gone. A MegaTask umbrella gets its batch-aware composer (every
        LIVE root-subtask's snapshot); a normal task gets the single-task one.
        """
        from uuid import UUID

        from roboco.foundation.policy.batch import is_batch_umbrella
        from roboco.services.journal import get_journal_service
        from roboco.services.prompter import (
            compose_batch_redraft_message,
            compose_redraft_message,
        )
        from roboco.services.task import get_task_service

        task_service = get_task_service(db)
        task = await task_service.get(UUID(task_id))
        if task is None:
            return None
        entries = await get_journal_service(db).board_review_brief(UUID(task_id))
        if is_batch_umbrella(
            batch_id=task.batch_id, parent_task_id=task.parent_task_id
        ):
            children = await task_service.get_live_subtasks(UUID(task_id))
            return compose_batch_redraft_message(task, children, entries)
        return compose_redraft_message(task, entries)

    async def _inject_board_brief_into_parked_intake(self, task_id: str) -> None:
        """Inject the board's review into a parked intake session, if one exists.

        No-op when no session is parked for this task (it was reaped, the
        container died, or the draft never used the board route) — the CEO then
        re-drafts via the cold ``/re-interview`` path instead. Never raises.
        """
        from roboco.services.prompter_live import get_live_registry

        session = get_live_registry().find_by_task(task_id)
        if session is None:
            return
        from roboco.db.base import get_db_context

        try:
            async with get_db_context() as db:
                message = await self._compose_parked_intake_redraft(db, task_id)
                if message is None:
                    return
                # Best-effort, its own try/except: a persistence hiccup (or, in
                # tests, a non-UUID fake session id) must never block delivering
                # the board's feedback back into the live conversation.
                await self._persist_parked_redraft_message(
                    db, session.session_id, message
                )
            delivered = await get_live_registry().deliver(session.session_id, message)
            logger.info(
                "Injected board feedback into parked intake",
                task_id=task_id,
                delivered=delivered,
            )
        except Exception as exc:
            logger.warning(
                "Failed to inject board feedback into parked intake",
                task_id=task_id,
                error=str(exc),
            )

    async def _persist_parked_redraft_message(
        self, db: "AsyncSession", session_id: str, message: str
    ) -> None:
        """Durably record the board-feedback message injected into a parked
        intake session. Best-effort: never let a persistence failure block
        delivering the feedback into the live conversation."""
        from roboco.services.prompter import get_prompter_service

        try:
            await get_prompter_service(db).record_live_message(
                session_id, "user", message
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist parked intake redraft message",
                session_id=session_id,
                error=str(exc),
            )

    async def _pm_spawn_prompt(
        self, routing: str, agent_id: str, task: dict[str, Any]
    ) -> str:
        """Pick the correct prompt for a classified spawn."""
        if routing == "dev":
            return await self._build_dev_prompt(task)
        if routing == "main_pm" or agent_id == "main-pm":
            return self._build_main_pm_triage_prompt(task)
        return self._build_pm_triage_prompt(task)

    async def _dispatch_pm_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch PM triage work - routes new tasks to appropriate level.

        This is the FIRST dispatcher called - it classifies unassigned tasks
        and routes them to Board, Main PM, Cell PM, or directly to devs.
        Also handles already-assigned pending tasks for PM agents.

        Monitors: pending tasks (both assigned and unassigned)
        Spawns: product-owner, main-pm, be-pm, fe-pm, ux-pm (or devs for simple)

        Mixed dispatch-pause scope: a ``dispatch``-scope pause skips this
        method's own PM/board-review routing (below) but never the Board
        Program exploration branch, which is gated independently by
        ``board_programs`` scope inside ``_dispatch_board_program_
        exploration``, the two scopes must stay decoupled even though they
        share this one dispatcher.

        Fetches with ``_PM_DISPATCH_FETCH_LIMIT`` (not the API's 100
        default): this dispatcher's fetch is NOT team-scoped (see that
        constant's comment for the silent-truncation class of bug a plain
        default fetch would reintroduce here).
        """
        from roboco.services.maintenance_pause import PauseScope

        tasks = await self._fetch_tasks(
            client, "pending", limit=_PM_DISPATCH_FETCH_LIMIT
        )
        dispatch_paused = await self._is_paused(PauseScope.DISPATCH)

        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            # CEO-HELD / externally-owned sources are never PM delivery work
            # (external-PR review, release proposals, X posts/replies, and a
            # not-yet-confirmed self-heal fix task) — see _is_held_ceo_source.
            if _is_held_ceo_source(task):
                continue
            # A supersede umbrella whose branch cut is still in progress
            # (background task or pending reconciliation). Main PM must not
            # be routed until the branch is ready.
            if _is_branch_pending(task):
                continue
            assigned_to = task.get("assigned_to")
            if assigned_to:
                # Every registered Board Program (roadmap / x_feature /
                # pest_control / periscope / coroner / sentinel / spackle /
                # scales / mirror / megaphone / librarian / war_room /
                # barfly / dogfood) is solo-authored and bypasses the
                # two-reviewer board-review gate — routed via the module-level dict
                # dispatch table so this chain stays flat as new programs
                # register (xenon budget). Falls through to the generic
                # board/PM-assigned handlers only for a non-program task.
                if not await _dispatch_board_program_exploration(self, task):
                    if dispatch_paused:
                        continue
                    if self._resolve_agent_slug(assigned_to) in self._BOARD_AGENTS:
                        await self._handle_board_assigned_task(task, assigned_to)
                    else:
                        await self._handle_pm_assigned_task(task, assigned_to)
                continue

            if dispatch_paused:
                continue
            await self._route_unassigned_pm_task(client, task)

    async def _dispatch_revision_coordination_roots(
        self, client: httpx.AsyncClient
    ) -> None:
        """Re-spawn the owning PM for a PM-owned needs_revision task.

        Two cases land a task in ``needs_revision`` owned by a PM rather than a
        developer: a CEO-rejected coordination root (team=main_pm, product-linked,
        no repo), and a gate-failed assembled task (a cell→root or root→master PR
        the in-path reviewer sent back via pr_fail). The dev dispatcher only
        spawns developers and the closure path only handles paused parents, so
        without this such a task would sit in needs_revision forever — the
        deadlock. The PM-ownership filter below scopes this to exactly those: a
        leaf dev revision stays owned by its developer and is left to the dev
        dispatcher.
        """
        tasks = await self._fetch_tasks(client, "needs_revision")
        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            owner = task.get("assigned_to") or task.get("claimed_by")
            agent_slug = self._resolve_agent_slug(owner) if owner else None
            if not agent_slug or self._is_agent_active(agent_slug):
                continue
            if get_agent_role(agent_slug) not in ("cell_pm", "main_pm"):
                continue
            # Respawn circuit breaker — a revision the PM can never land must
            # stop respawning the coordinator (progress resets the strikes).
            if await self._pm_respawn_should_gate(agent_slug, task):
                continue
            await self.spawn_agent(
                agent_id=agent_slug,
                task_id=task["id"],
                initial_prompt=await self._get_prompt_for_agent(agent_slug, task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_revision_coordination_roots",
            )

    async def _maybe_spawn_pm_closure(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> None:
        """If this parent task is ready for closure, spawn its PM."""
        task_id = task.get("id")
        if not task_id:
            return

        if self._is_recently_paused(task):
            logger.debug(
                "Skipping closure spawn for recently-paused parent",
                task_id=task_id,
                last_heartbeat_at=task.get("last_heartbeat_at"),
            )
            return

        descendants = await self._fetch_all_descendants(client, task_id)
        # A childless task in awaiting_pm_review IS the PM's review turn —
        # its dev→qa→doc stages ran on the task itself. Without this, a leaf
        # review stranded by a restart (the submit-time PM session gone) had
        # no periodic pickup at all — proven live on the docs-sync leaf
        # after the 0.25.0 redeploy.
        is_leaf_review = not descendants and task.get("status") == "awaiting_pm_review"
        if not is_leaf_review and (
            not descendants or not self._all_descendants_terminal(descendants)
        ):
            return
        if self._already_promoted_for_closure(task):
            return

        pm_id = self._closure_pm_for_team(task.get("team"))
        if self._is_agent_active(pm_id):
            return

        logger.info(
            "Parent task ready for closure",
            task_id=task_id,
            descendants_count=len(descendants),
            pm_id=pm_id,
        )

        # The parent auto-paused when its PM idled (by design). Resume
        # it before respawn so the PM lands actionable (in_progress) and can
        # directly submit_up / complete / escalate — pre-gateway behaviour the
        # gateway refactor dropped, which wedged a dogfood run (the model
        # never issued resume() itself).
        # A parent that is `blocked` at closure (all descendants
        # terminal) is an errant/stale block — recover it symmetrically so
        # the chain can't wedge forever waiting for a PM to manually unblock.
        handled, auto_submit_reason = await self._closure_handled_without_pm(
            client, task, task_id, pm_id
        )
        if handled:
            return

        pm_id = await self._closure_review_pm(client, task, pm_id)

        prompt = self._build_pm_closure_prompt(
            task, descendants, auto_submit_reason=auto_submit_reason
        )
        await self.spawn_agent(
            agent_id=pm_id,
            task_id=task_id,
            initial_prompt=prompt,
            git_context=self._task_git_context(task),
            spawned_by="_maybe_spawn_pm_closure",
        )

    async def _closure_review_pm(
        self, client: httpx.AsyncClient, task: dict[str, Any], default_pm: str
    ) -> str:
        """The PM to spawn for a closure parent already at awaiting_pm_review.

        CLAIM_RULES has no claim() edge into this status, so pr_pass's/
        mark_pr_created's own PM resolution, or a stale escalate/
        unblock(restore=True) round trip, may have left assigned_to wrong
        or unset — assign_review_pm corrects it. ``default_pm`` (the
        caller's already-correct ``_closure_pm_for_team`` value) is kept on
        ANY failure: ``_ensure_review_pm_assigned`` returns ``None`` on both
        an unresolvable owner and a transient route/transport error, and
        adopting a stale fallback there would spawn the wrong PM in exactly
        the incident shape this fix targets (a task mis-assigned to main-pm
        that should be be-pm). A no-op for any other status — ownership
        there already came from the normal claim/delegate flow.
        """
        if task.get("status") != "awaiting_pm_review":
            return default_pm
        resolved = await self._ensure_review_pm_assigned(client, task)
        return resolved or default_pm

    async def _dispatch_pm_closure_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch PM closure work - check parent tasks ready to close.

        When all subtasks of a parent task are completed, spawn the PM
        to review and close the parent task.

        Monitors: tasks with completed subtasks but parent still open
        Spawns: be-pm, fe-pm, ux-pm, main-pm (based on parent team)
        """
        # Find parent tasks that might have children ready for closure
        # Include "paused" - PM pauses while waiting, respawned when subtasks done
        # Include "awaiting_pm_review" - parent awaiting review when children done
        parent_statuses = ["claimed", "in_progress", "paused", "awaiting_pm_review"]

        for status in parent_statuses:
            tasks = await self._fetch_tasks(client, status)
            for task in tasks:
                await self._maybe_spawn_pm_closure(client, task)

    async def _dispatch_vault_curation_work(self, _client: httpx.AsyncClient) -> None:
        """Obsidian-vault root-completion hook: spawn the Auditor to write a
        just-completed root task-tree's narrative.

        Gated on ``ROBOCO_OBSIDIAN_VAULT_ENABLED``; a no-op scan otherwise.
        Owns ONLY this vault-curation trigger — distinct from
        ``_dispatch_audit_work`` (scheduled sweeps + alert producers, owned by
        a separate, queued fleet task). ``_client`` is accepted for
        dispatcher-tuple shape parity but unused — this reads the DB
        directly (see ``TaskService.list_completed_roots_pending_vault_curation``).
        """
        if not settings.obsidian_vault_enabled:
            return
        from roboco.db.base import get_db_context
        from roboco.services.task import TaskService

        async with get_db_context() as db:
            candidates = await TaskService(
                db
            ).list_completed_roots_pending_vault_curation()
            for task in candidates:
                await self._maybe_spawn_vault_curation(str(task.id), task.title)

    async def _maybe_spawn_vault_curation(self, task_id: str, title: str) -> None:
        """One-shot Auditor spawn for one completed root task.

        Mirrors ``_dispatch_board_reviewer``'s guard shape: an in-memory
        one-shot tracker (reuses ``_board_dispatched``) for the same-process
        race, plus a durable ``vault_curation_dispatched`` marker so a
        restart can't re-spawn a root another process instance already
        handled. Spawned WITHOUT a bound task_id (mirrors
        ``_dispatch_audit_work``'s alert spawn) — the root task is
        `completed`, so binding it would trip the readiness gate's
        role-for-status check; the task id is named in the prompt instead,
        and ``curate_vault`` takes it as an explicit argument.
        """
        auditor_slug = "auditor"
        if self._is_agent_active(auditor_slug):
            return
        key = (auditor_slug, task_id)
        if key in self._board_dispatched:
            return
        self._board_dispatched.add(key)
        await self._mark_vault_curation_dispatched(task_id)
        logger.info("Spawning Auditor for vault curation", task_id=task_id)
        await self.spawn_agent(
            agent_id=auditor_slug,
            initial_prompt=self._build_vault_curation_prompt(task_id, title),
            spawned_by="_maybe_spawn_vault_curation",
        )

    @staticmethod
    async def _mark_vault_curation_dispatched(task_id: str) -> None:
        """Persist the one-shot marker so a restart never re-spawns this
        root. Best-effort: a failure here only risks a harmless duplicate
        Auditor spawn (curate_vault's write is idempotent), never a crash."""
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.foundation.policy.content import markers
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                svc = TaskService(db)
                task = await svc.get(UUID(task_id))
                if task is not None:
                    markers.mark_vault_curation_dispatched(task)
                    await db.commit()
        except Exception as exc:
            logger.warning(
                "Failed to persist vault_curation_dispatched marker",
                task_id=task_id,
                error=str(exc),
            )

    async def _dispatch_dev_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch assigned work to the assigned agent.

        NOTE: This handles PRE-ASSIGNED tasks (assigned by PM),
        needs_revision tasks, and in_progress tasks where agent is not active
        (e.g., after unblock). New unassigned pending tasks are handled by
        _dispatch_pm_work() which routes them through the PM hierarchy.

        Monitors: assigned pending tasks, needs_revision tasks, orphaned in_progress
        Spawns: Any assigned agent (dev, doc, qa) with appropriate prompt
        """
        # Get tasks needing attention. Includes:
        # - `claimed` — PM-delegated claims where the assignee was never spawned
        # - `blocked` — but only when another agent can resolve (see below)
        # `pending`, `needs_revision`, `in_progress` are the classic cases.
        # Not team-scoped (filtered per-task below, after fetch), so it needs
        # the same raised limit as `_dispatch_pm_work` (see that constant's
        # comment): FIFO ordering means a status bucket over 100 org-wide
        # could fill the fetch window with old ineligible tasks.
        tasks = await self._fetch_tasks(
            client,
            ["pending", "claimed", "needs_revision", "in_progress", "blocked"],
            limit=_PM_DISPATCH_FETCH_LIMIT,
        )

        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            # Held CEO artifacts + Board exploration cycles belong to other
            # dispatchers (their own routes / _dispatch_pm_work), never a dev.
            if _is_non_dev_dispatch_source(task):
                continue
            await self._dev_dispatch_one(client, task)

    async def _spawn_pending_dev(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        agent_slug: str,
    ) -> None:
        """Validate and spawn a dev agent for a pending, pre-assigned task."""
        if self._is_agent_active(agent_slug):
            return
        # Sequence is the bar: skip the spawn when the assignee-blind sequence
        # guard would refuse the claim (a non-terminal lower-sequence same-parent
        # sibling). Mirrors _route_unassigned_pm_task's prefilter so a
        # sequence-held dev leaf isn't booted into a claim the chokepoint will
        # refuse — pure spawn churn until the predecessor goes terminal.
        if await self._pending_claim_blocked(task.get("id")):
            return
        # Per-dev queue order: hold a dev's higher-sequence code leaf while it
        # still has an earlier non-terminal code sibling under the same parent,
        # so the dev works its queue one task at a time, in order. Loop-free —
        # just not dispatched this tick.
        if await self._blocked_by_earlier_lane_sibling(task):
            return
        # Respawn circuit breaker — a dev leaf that respawns without the task
        # advancing (wedged workspace, unclaimable state) stops after strikes.
        if await self._pm_respawn_should_gate(agent_slug, task):
            return
        validation_issue = await self._validate_task_for_spawn(client, task, agent_slug)
        if validation_issue:
            logger.warning(
                "Skipping spawn due to validation failure",
                task_id=task["id"],
                agent=agent_slug,
                reason=validation_issue,
            )
            return
        await self.spawn_agent(
            agent_id=agent_slug,
            task_id=task["id"],
            initial_prompt=await self._get_prompt_for_agent(agent_slug, task),
            git_context=self._task_git_context(task),
            spawned_by="_spawn_pending_dev",
        )

    async def _dev_dispatch_one(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> None:
        """Dispatch a single task from `_dispatch_dev_work`'s fetch set."""
        team = task.get("team")
        if team not in ["backend", "frontend", "ux_ui"]:
            return

        if self._is_hitl_blocked(task):
            logger.debug(
                "Skipping HITL-blocked task; waiting for human",
                task_id=task["id"],
            )
            return

        status = task.get("status")
        owner_uuid = self._resolve_dev_owner_uuid(task)
        agent_slug = self._resolve_agent_slug(owner_uuid) if owner_uuid else None

        # Role/task_type mismatch guard. The dispatcher
        # previously trusted whatever ``assigned_to`` named, so a
        # documentation task accidentally assigned to a developer agent
        # would silently spawn the dev. Reject the dispatch if the
        # assignee's role doesn't match the task type — the PM that
        # mis-assigned needs to fix it before any agent runs.
        # Tasks owned by PM/board/QA roles aren't this dispatcher's lane;
        # `_dispatch_pm_work` and the QA-pool path own them. Silently skip
        # so the warning only fires on actual dev/doc misassignments.
        if agent_slug:
            assignee_role = get_agent_role(agent_slug)
            if assignee_role not in ("developer", "documenter", "unknown"):
                return
            if not self._dev_dispatch_role_matches(task, agent_slug):
                logger.warning(
                    "dev dispatch: role/task_type mismatch — skipping spawn",
                    task_id=task.get("id"),
                    task_type=task.get("task_type"),
                    assignee_slug=agent_slug,
                    assignee_role=assignee_role,
                )
                return

        if agent_slug and status in (
            "needs_revision",
            "in_progress",
            "claimed",
            "blocked",
        ):
            await self._handle_dev_existing_owner(task, status, agent_slug)
            return

        # Pending tasks pre-assigned by PM.
        if agent_slug:
            await self._spawn_pending_dev(client, task, agent_slug)

    async def _spawn_assigned_qa(self, task: dict[str, Any], assigned_to: str) -> bool:
        """If task.assigned_to is a QA slug, spawn/skip-if-running; else False.

        Returns True when the dispatch decision for this task was
        handled at the assignee level (spawned or already running).
        Returns False when the assigned_to is NOT a QA agent — caller
        then falls through to the unassigned-select path.
        """
        assigned_slug = self._resolve_agent_slug(assigned_to)
        if not assigned_slug or "qa" not in assigned_slug:
            logger.warning(
                "awaiting_qa task assigned to non-QA slug; reassigning via QA pool",
                task_id=task["id"],
                assigned_slug=assigned_slug,
            )
            return False
        if self._is_agent_active(assigned_slug):
            return True
        # Respawn circuit breaker — same progress-aware gate as every other
        # task-keyed spawn path; notifies the CEO once it trips.
        if await self._pm_respawn_should_gate(assigned_slug, task):
            return True
        await self.spawn_agent(
            agent_id=assigned_slug,
            task_id=task["id"],
            initial_prompt=self._build_qa_prompt(task),
            git_context=self._task_git_context(task),
            spawned_by="_spawn_assigned_qa",
        )
        return True

    async def _dispatch_qa_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch QA work to QA agents.

        Monitors: awaiting_qa tasks
        Spawns: be-qa, fe-qa, ux-qa

        Not team-scoped at fetch time (team is filtered per-task below), so
        it takes the same raised limit as `_dispatch_pm_work`.
        """
        tasks = await self._fetch_tasks(
            client, "awaiting_qa", limit=_PM_DISPATCH_FETCH_LIMIT
        )

        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            team = task.get("team")
            if team not in ["backend", "frontend", "ux_ui"]:
                continue

            assigned_to = task.get("assigned_to")
            if assigned_to and await self._spawn_assigned_qa(task, assigned_to):
                continue

            # Unassigned task - select QA agent for this team
            agent_id = self._select_agent_for_cell(team, "qa")
            if not agent_id:
                continue

            if self._is_agent_active(agent_id):
                # QA already running, they'll pick up on scan
                continue

            # Respawn circuit breaker — same progress-aware gate as every
            # other task-keyed spawn path.
            if await self._pm_respawn_should_gate(agent_id, task):
                continue
            # NO pre-claim (matches _spawn_assigned_qa and the external-PR
            # reviewer dispatch): the transitioning claim moved the task to
            # 'claimed' before the agent existed, stranding the QA whose own
            # claim_review/pass_review demand awaiting_qa (live 2026-07-02,
            # ba7b751c). The agent claims itself via claim_review; the
            # _is_agent_active guard prevents a double-spawn across ticks.
            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=self._build_qa_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_qa_work",
            )
            # Only spawn one QA at a time per cell
            break

    async def _dispatch_pr_review_work(self, client: httpx.AsyncClient) -> None:
        """Dispatch inbound external-PR review tasks to the PR reviewer.

        Monitors: pending tasks with ``source='external_pr'``.
        Spawns: the single global reviewer ``pr-reviewer-1`` (one review at a
        time). No pre-claim — the task stays PENDING until the reviewer claims
        it itself via ``claim_pr_review``; the prompt carries the task id. The
        ``is_agent_active`` guard prevents a double-spawn across ticks.

        Not team-scoped at fetch time (source is filtered per-task below),
        so it takes the same raised limit as ``_dispatch_pm_work``.
        """
        reviewer = "pr-reviewer-1"
        if self._is_agent_active(reviewer):
            return
        tasks = await self._fetch_tasks(
            client, "pending", limit=_PM_DISPATCH_FETCH_LIMIT
        )
        for task in tasks:
            if task.get("source") not in PR_REVIEW_SOURCES:
                continue
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            if task.get("assigned_to"):
                continue
            # Respawn circuit breaker — parity with the in-path gate dispatcher.
            if await self._pm_respawn_should_gate(reviewer, task):
                continue
            await self.spawn_agent(
                agent_id=reviewer,
                task_id=task["id"],
                initial_prompt=self._build_pr_review_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_pr_review_work",
            )
            break

    async def _dispatch_pr_gate_work(self, client: httpx.AsyncClient) -> None:
        """Dispatch in-path PR-review-gate tasks (awaiting_pr_review) to reviewers.

        Routes by level: a cell→root task (team backend/frontend/ux_ui) goes to
        that cell's reviewer (be/fe/ux-pr-reviewer); the root→master task goes to
        the main reviewer (pr-reviewer-1) - the SAME shared reviewer
        ``_dispatch_pr_review_work`` uses for inbound external/fork PRs, so this
        dispatcher runs BEFORE it in ``_dispatch_all_work``'s order: an
        assembled root PR blocks the whole delivery pipeline (every PM merge
        downstream waits on it) while an external PR can wait a tick, so ties
        for the shared reviewer must favor the internal gate. Without this
        order a busy external-PR queue starves the root→master gate
        indefinitely (live 2026-08: two cell tasks self-routed via their
        dedicated cell reviewers while two Main-PM root tasks sat on
        assignee=main-pm until the CEO manually intervened). The reviewer
        claims the task itself via ``claim_gate_review`` (no pre-claim -
        mirrors the external-PR dispatcher); the ``is_agent_active`` guard +
        one-reviewer-per-cell prevent a double-spawn, and ``spawned`` bounds
        each reviewer to one task per tick. A task whose assembled PR's CI is
        still pending is skipped entirely (see ``_gate_task_ci_pending``) so
        the reviewer isn't spawned only to be immediately CI-blocked.

        Not team-scoped at fetch time (routed per-task below), so it takes
        the same raised limit as ``_dispatch_pm_work``.

        When a cell's dedicated reviewer is active on another gate task,
        ``_select_agent_for_cell`` falls back to the shared
        ``cell-pr-reviewer-2`` (board-team, image-identical), so a same-cell
        pile-up no longer serializes 12-14 min on a single reviewer.
        ``cell-pr-reviewer-2`` is never used for the root→master gate or
        inbound external PRs (both hardcode pr-reviewer-1), preserving the
        run-order starvation guard above. This fallback selection only runs
        for a task nobody has claimed yet (see ``_gate_task_reviewer``): a
        task already claimed by a reviewer is always re-targeted at that
        SAME reviewer, never re-routed to the overflow just because it
        happens to be free.
        """
        tasks = await self._fetch_tasks(
            client, "awaiting_pr_review", limit=_PM_DISPATCH_FETCH_LIMIT
        )
        spawned: set[str] = set()
        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            if await self._gate_task_ci_pending(task):
                continue
            reviewer = self._gate_task_reviewer(task)
            if not reviewer or reviewer in spawned or self._is_agent_active(reviewer):
                continue
            # Respawn circuit breaker — a gate task that keeps re-surfacing
            # without advancing must stop respawning the reviewer.
            if await self._pm_respawn_should_gate(reviewer, task):
                continue
            await self._sync_gate_reviewer_assignment(task, reviewer)
            spawned.add(reviewer)
            await self.spawn_agent(
                agent_id=reviewer,
                task_id=task["id"],
                initial_prompt=self._build_pr_gate_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_pr_gate_work",
            )

    async def _dispatch_doc_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch documentation + developer work during the parallel
        awaiting_documentation phase.

        `awaiting_documentation` requires BOTH docs_complete=True AND
        pr_created=True to advance to awaiting_pm_review. Doc writes the
        docs; original developer pushes and creates the PR. Whoever
        finishes last triggers the state transition. Previously this
        dispatcher only spawned the documenter — if the documenter
        finished first, the task would sit indefinitely with pr_created=
        False and nothing would spawn the dev to finish the other half.

        Monitors: awaiting_documentation tasks
        Spawns:
            - documenter (be-doc, fe-doc, ux-doc) if docs_complete=False
            - original_developer if pr_created=False (tracked in
              quick_context as "original_developer:<uuid>")
        """
        # Fetch both `awaiting_documentation` and `claimed` because the
        # doc's claim transitions status from awaiting_documentation →
        # claimed. Without including `claimed` we'd miss tasks where doc
        # already grabbed it but pr_created is still false (dev hasn't
        # pushed/created PR yet). The `original_developer:` marker in
        # quick_context identifies tasks that are actually in the parallel
        # phase vs unrelated claimed tasks.
        # Not team-scoped at fetch time, so it takes the same raised limit
        # as `_dispatch_pm_work`.
        tasks = await self._fetch_tasks(
            client,
            ["awaiting_documentation", "claimed"],
            limit=_PM_DISPATCH_FETCH_LIMIT,
        )
        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            await self._doc_dispatch_one(client, task)

    async def _auto_assign_doc(
        self, client: httpx.AsyncClient, task: dict[str, Any], team: str
    ) -> None:
        """
        Auto-select and spawn a documenter for an unassigned awaiting_documentation task
        """
        agent_id = self._select_agent_for_cell(team, "doc")
        if not agent_id or self._is_agent_active(agent_id):
            return

        # Respawn circuit breaker — before claiming, so a wedged doc task
        # doesn't churn claims while the gate is open.
        if await self._pm_respawn_should_gate(agent_id, task):
            return

        async def _spawn() -> None:
            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=self._build_doc_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_auto_assign_doc",
            )

        claimed = await self._claim_and_spawn_guarded(client, task, agent_id, _spawn)
        if claimed is False:
            logger.warning(
                "Failed to claim awaiting_documentation task for doc",
                task_id=task["id"],
                agent_id=agent_id,
            )

    async def _doc_dispatch_one(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
    ) -> None:
        """Process a single task for `_dispatch_doc_work`."""
        team = task.get("team")
        if team not in ["backend", "frontend", "ux_ui"]:
            return

        dev_uuid = (task.get("orchestration_markers") or {}).get("original_developer")
        status = task.get("status")

        # Only consider `claimed` tasks actually in the doc/PR parallel
        # phase. See `_is_parallel_phase_claim` docstring for the why.
        if status == "claimed" and not self._is_parallel_phase_claim(task, dev_uuid):
            return

        # Developer half: push + create PR
        await self._respawn_dev_for_pr_half(task, dev_uuid)

        # Documenter half: write docs
        if task.get("docs_complete"):
            return

        if await self._respawn_doc_if_assigned(task):
            return

        # Auto-assign a documenter only when still in awaiting_documentation.
        if status != "awaiting_documentation":
            return

        await self._auto_assign_doc(client, task, team)

    async def _respawn_doc_if_assigned(self, task: dict[str, Any]) -> bool:
        """If task is assigned to an inactive documenter, respawn them.

        Returns True when the task is already assigned (whether or not a
        respawn happened) so the caller can stop processing. Returns
        False when the task is unassigned so the caller can auto-select
        a documenter for it.
        """
        assigned_to = task.get("assigned_to")
        if not assigned_to:
            return False
        assigned_slug = self._resolve_agent_slug(assigned_to)
        if self._is_agent_active(assigned_slug):
            return True
        if assigned_slug and "doc" in assigned_slug:
            # Respawn circuit breaker — the fe-doc 26-respawn loop ran on this
            # exact path unguarded; the gate notifies the CEO once it trips.
            if await self._pm_respawn_should_gate(assigned_slug, task):
                return True
            await self.spawn_agent(
                agent_id=assigned_slug,
                task_id=task["id"],
                initial_prompt=self._build_doc_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_respawn_doc_if_assigned",
            )
        return True

    async def _dispatch_pm_review_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch PM review work to cell PMs or Main PM.

        Monitors: awaiting_pm_review tasks
        Spawns: be-pm, fe-pm, ux-pm, main-pm
        """
        tasks = await self._fetch_tasks(client, "awaiting_pm_review")

        for task in tasks:
            # Sequence-ordered merge: don't review/merge a leaf until its
            # earlier same-team siblings have landed, so they merge into the
            # shared cell branch in order instead of racing and wedging.
            if await self._blocked_by_earlier_sibling(task):
                continue

            assigned_slug = await self._review_pm_slug(client, task)
            if not assigned_slug:
                logger.warning(
                    "Could not resolve/assign an owning PM for awaiting_pm_review task",
                    task_id=task["id"],
                )
                continue

            # Human-only roles (CEO / prompter / secretary) are never
            # containers — there is no reviewer agent to respawn. Leave
            # the task for the human (the CEO approves via the panel).
            # A stale/ex-human slug is also skipped: is_spawnable_agent_slug
            # is False for it, so a renamed secretary slug can't slip past
            # the layered guard to a doomed spawn (#49). Mirrors the
            # spawn_agent human-role guard; a skip here keeps a mis-assigned
            # human task from aborting this dispatcher's whole tick.
            if not is_spawnable_agent_slug(assigned_slug):
                continue
            if self._is_agent_active(assigned_slug):
                continue
            # Loop guard: a review task that keeps re-surfacing without
            # advancing (e.g. an unmergeable PR that re-blocks every cycle)
            # must stop respawning the reviewer, else it burns tokens
            # forever. The gate notifies the CEO once it trips.
            if await self._pm_respawn_should_gate(assigned_slug, task):
                continue
            # Agent not running - spawn them to continue
            await self.spawn_agent(
                agent_id=assigned_slug,
                task_id=task["id"],
                initial_prompt=self._build_pm_review_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_pm_review_work",
            )

    async def _dispatch_marketing_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch marketing work to head-marketing.

        Monitors: pending tasks with team=marketing
        Spawns: head-marketing
        """
        tasks = await self._fetch_tasks(client, "pending", team="marketing")

        for task in tasks:
            # Skip already claimed/assigned tasks
            if task.get("assigned_to"):
                continue

            if self._is_agent_active("head-marketing"):
                # Already running, they'll pick up on scan
                continue

            await self.spawn_agent(
                agent_id="head-marketing",
                task_id=task["id"],
                initial_prompt=self._build_marketing_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_marketing_work",
            )
            break

    async def _dispatch_blocker_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch blocker resolution to the task's current unblock authority.

        Monitors: blocked tasks
        Spawns: the task's current PM/board assignee, else the cell PM
        """
        tasks = await self._fetch_tasks(client, "blocked")

        for task in tasks:
            # HITL-blocked tasks wait for a human; never spawn an agent on them.
            if self._is_hitl_blocked(task):
                continue

            # A dependency-held blocked task can't be unblocked by any
            # resolver until the dependency lands — spawn_agent's readiness
            # gate would refuse anyway, and each refused attempt burned a
            # respawn-breaker strike + a CEO escalation once tripped
            # (2026-07-29). Skip quietly; this dispatcher re-checks every
            # tick and proceeds once the dependency goes terminal.
            if await self._check_dependencies_terminal(client, task):
                continue

            agent_id = self._blocker_resolver_slug(task)
            if not agent_id:
                continue

            if self._is_agent_active(agent_id):
                continue

            # Loop guard: a blocked task whose unblock can never succeed (e.g.
            # a cold-respawned PM that can't satisfy the unblock decision gate,
            # or an unresolvable merge conflict) must stop respawning the
            # resolver. The gate notifies the CEO once it trips so the wedged
            # task surfaces instead of silently burning tokens.
            if await self._pm_respawn_should_gate(agent_id, task):
                continue

            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=self._build_pm_blocker_prompt(task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_blocker_work",
            )
            break

    async def _dispatch_claimed_without_agent(self, client: httpx.AsyncClient) -> None:
        """(Re)spawn or release claimed/in_progress tasks that have no agent.

        Net for the invisible-stuck case the other dispatchers miss: a task
        held CLAIMED/IN_PROGRESS by an assignee with no running container. If
        the assignee is a known spawnable agent, respawn it on the task; if not
        (unknown slug — e.g. a stale UUID), release the claim to PENDING so the
        normal routing reclaims it with a role match.

        Throttle: spawns at most ONE container per tick (``break`` after the
        first respawn), matching every sibling dispatcher. A restart leaves
        many agentless claims at once; without the cap this single tick would
        burst-spawn a container for every one of them. The release-to-pending
        path spawns nothing, so it does not consume the per-tick spawn budget
        and keeps draining stale claims.
        """
        tasks = await self._fetch_tasks(client, ["claimed", "in_progress"])
        for task in tasks:
            task_id = task.get("id")
            if self._is_task_handled_this_tick(task_id):
                continue
            agent_slug = self._claimed_task_needs_agent(task)
            if agent_slug is None:
                continue
            if get_agent_role(agent_slug) in (None, "unknown"):
                # Unknown assignee — no agent to spawn; release for re-dispatch.
                await self._release_claim_to_pending(str(task_id))
                continue
            logger.warning(
                "Claimed/in_progress task has no running agent; respawning assignee",
                task_id=task_id,
                agent=agent_slug,
                status=task.get("status"),
            )
            await self.spawn_agent(
                agent_id=agent_slug,
                task_id=str(task_id),
                initial_prompt=await self._get_prompt_for_agent(agent_slug, task),
                git_context=self._task_git_context(task),
                spawned_by="_dispatch_claimed_without_agent",
            )
            break

    async def _dispatch_escalation_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch escalations to appropriate managers.

        Monitors: escalation notifications (unacknowledged)
        Spawns: be-pm, fe-pm, ux-pm, main-pm, product-owner, head-marketing
        """
        notifications = await self._fetch_notifications(client, "blocker_escalation")

        for notif in notifications:
            targets = notif.get("to_agents", [])

            for agent_id in targets:
                # Resolve UUID to slug - to_agents contains UUIDs from database
                agent_slug = self._resolve_agent_slug(str(agent_id))

                valid_targets = [
                    "be-pm",
                    "fe-pm",
                    "ux-pm",
                    "main-pm",
                    "product-owner",
                    "head-marketing",
                ]
                if agent_slug not in valid_targets:
                    continue

                if self._is_agent_active(agent_slug):
                    continue

                if await self._notification_spawn_cooled(agent_slug, notif.get("id")):
                    continue
                if not await self._notification_has_live_work(client, notif):
                    continue
                await self.spawn_agent(
                    agent_id=agent_slug,
                    initial_prompt=self._build_escalation_prompt(notif),
                    spawned_by="_dispatch_escalation_work",
                )
                break

    async def _dispatch_approval_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch approval requests to approvers.

        Monitors: approval notifications (unacknowledged)
        Spawns: product-owner, head-marketing, main-pm
        """
        notifications = await self._fetch_notifications(client, "approval")

        for notif in notifications:
            targets = notif.get("to_agents", [])

            for agent_id in targets:
                # Resolve UUID to slug - to_agents contains UUIDs from database
                agent_slug = self._resolve_agent_slug(str(agent_id))

                if agent_slug not in ["product-owner", "head-marketing", "main-pm"]:
                    continue

                if self._is_agent_active(agent_slug):
                    continue

                if await self._notification_spawn_cooled(agent_slug, notif.get("id")):
                    continue
                if not await self._notification_has_live_work(client, notif):
                    continue
                await self.spawn_agent(
                    agent_id=agent_slug,
                    initial_prompt=self._build_approval_prompt(notif),
                    spawned_by="_dispatch_approval_work",
                )
                break

    async def _dispatch_audit_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch audit work to the auditor.

        Monitors: quality alert notifications + scheduled periodic sweeps
        Spawns: auditor
        """
        # Alert path: dispatch the auditor ONCE per alert targeting it that the
        # auditor has not observed yet. The auditor is read-only (no ack verb)
        # and auditor_triage never acks, so under the old system-wide fetch
        # (every not-fully-acked alert) a stale rework alert stayed pending
        # forever and the per-alert cooldown only paced a rotation that
        # respawned the auditor every ~3 min. Fetching the auditor's own
        # not-yet-acked-by-me view and acking on dispatch makes each alert a
        # one-shot, DB-persistent — the rotation cannot restart on a tick.
        if not self._is_agent_active("auditor"):
            alert = await self._next_unobserved_audit_alert(client)
            if alert is not None:
                alert_id = str(alert["id"])
                if not await self._notification_spawn_cooled("auditor", alert_id):
                    await self.spawn_agent(
                        agent_id="auditor",
                        initial_prompt=self._build_audit_prompt(
                            {
                                "subject": alert.get("subject", ""),
                                "body": alert.get("body", ""),
                            }
                        ),
                        spawned_by="_dispatch_audit_work",
                    )
                    await self._ack_alert_as_auditor(client, alert_id)
                    self._last_audit_spawn_at = datetime.now(UTC)
                    return

        # Scheduled periodic audit sweep. Reuse the notification cooldown
        # pattern with a sentinel key as a one-tick breaker so a single
        # dispatcher tick cannot spawn the auditor twice.
        if self._is_agent_active("auditor"):
            return
        if self._audit_spawn_cooled():
            return
        if not await self._has_recent_delivery_activity(client):
            return
        if await self._notification_spawn_cooled("auditor", "_scheduled_audit"):
            return
        await self.spawn_agent(
            agent_id="auditor",
            initial_prompt=self._build_audit_prompt(scheduled=True),
            spawned_by="_dispatch_audit_work",
        )
        self._last_audit_spawn_at = datetime.now(UTC)

    async def _dispatch_a2a_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch A2A (Agent-to-Agent) requests to target agents.

        Monitors: a2a_request notifications (unacknowledged)
        Spawns: Any agent that is the target of an A2A request

        This is a fallback mechanism - primary A2A routing happens via events.
        If the event-based spawn fails, these notifications will be picked up here.
        """
        notifications = await self._fetch_notifications(client, "a2a_request")

        for notif in notifications:
            targets = notif.get("to_agents", [])

            for agent_id in targets:
                # Resolve UUID to slug - to_agents contains UUIDs from database
                agent_slug = self._resolve_agent_slug(str(agent_id))

                # Human-only roles (CEO / prompter / secretary) are never
                # dispatched — the CEO is the human operator and intake/
                # secretary are human-driven chats with their own launch
                # paths. Spawning a container for one is a trust violation
                # (the system acting as the human CEO). A stale/ex-human slug
                # is skipped too (is_spawnable_agent_slug is False for it) so
                # a renamed secretary slug can't slip past to a spawn (#49).
                # The CEO being a notification target (board-review handoff,
                # escalation, etc.) is expected; it is NOT a spawn signal.
                # Skip — the notification stays for the human to read. #75:
                # surface the skip for a human-only target (vs a silent stale
                # slug) so an a2a expecting a human-side action (a CEO sign-off
                # relay) is visible in the dispatch log, not silently dropped.
                if is_human_only_role(role_for_slug_or_none(agent_slug)):
                    logger.info(
                        "a2a request targets a human-only role; left as a "
                        "notification for the human (not spawned)",
                        target_slug=agent_slug,
                    )
                    continue
                if not is_spawnable_agent_slug(agent_slug):
                    continue

                if self._is_agent_active(agent_slug):
                    # Agent is online - SDK handles A2A delivery directly
                    # No action needed here, SDK server receives messages
                    continue

                # Agent is offline - spawn them with A2A context
                if await self._notification_spawn_cooled(agent_slug, notif.get("id")):
                    continue
                await self.spawn_agent(
                    agent_id=agent_slug,
                    initial_prompt=self._build_a2a_prompt(notif),
                    spawned_by="_dispatch_a2a_work",
                )
                break
