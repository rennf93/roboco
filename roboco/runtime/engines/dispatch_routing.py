"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: dispatch_routing)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.foundation.identity import (
    CELL_TEAMS,
)
from roboco.runtime.orchestrator import (
    _CREATOR_ROUTE_GRACE_SECONDS,
    logger,
)

if TYPE_CHECKING:
    import httpx


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class DispatchRoutingEngine(_Base):
    """Mixin holding the "dispatch_routing" methods moved out of AgentOrchestrator."""

    def _has_board_keywords(self, text: str) -> bool:
        """Check if text contains board-level keywords."""
        return any(kw in text for kw in self._BOARD_KEYWORDS)

    def _has_pm_keywords(self, text: str) -> bool:
        """Check if text contains PM coordination keywords."""
        return any(kw in text for kw in self._PM_KEYWORDS)

    def _has_cross_cell_keywords(self, text: str) -> bool:
        """Check if text indicates work spanning multiple cells."""
        return any(kw in text for kw in self._CROSS_CELL_KEYWORDS)

    @staticmethod
    def _route_by_task_type(task_type: str, team: str | None) -> str | None:
        """Route based on task_type field alone; returns None if no match."""
        cell_teams = tuple(
            sorted(t.value for t in CELL_TEAMS)
        )  # ("backend", "frontend", "ux_ui")
        if task_type in ("planning", "research", "administrative"):
            return "cell_pm" if team in cell_teams else "main_pm"
        if task_type == "design" and team not in ("backend", "frontend"):
            return "cell_pm"
        return None

    def _classify_cell_code_task(self, complexity: str, has_children: bool) -> str:
        """Route a cell-owned code task WITHIN its cell (dev or cell_pm).

        Implementation work that belongs to a CELL never escalates to the
        board or main_pm by keyword — a dev task whose description says
        "Create & Launch" or "auth/security" is still a dev task. Letting the
        board/main_pm keyword heuristics fire on it is how a cell code task
        ended up "reviewed" by the board and a PM ended up owning (and
        deadlocking) a dev code task.

        Keyword matching (``_has_pm_keywords``) was retired from this decision:
        ordinary dev prose ("review", "dependencies", "sync") false-positived
        a leaf fix task to cell_pm, which can't execute code and just
        plan/delegate/escalate-looped. Only an actual coordination node
        (already has children) or a genuine decomposition candidate (high
        complexity) goes to cell_pm now.

        Deliberate trade: a childless medium-complexity task now routes to
        dev (the retired keyword net used to catch some of these); high
        complexity remains the decomposition escape hatch to cell_pm.
        """
        if has_children or complexity == "high":
            return "cell_pm"
        return "dev"

    def _classify_strategic_code_task(
        self, text: str, team: str | None, complexity: str
    ) -> str:
        """Route a team-less / "all" top-level code task by strategic heuristics."""
        if self._has_board_keywords(text):
            return "board"

        if (
            self._has_cross_cell_keywords(text)
            or complexity == "high"
            or not team
            or team == "all"
        ):
            return "main_pm"

        if self._has_pm_keywords(text) or complexity == "medium":
            return "cell_pm"

        return "dev"

    def _classify_code_task(
        self, task: dict[str, Any], has_children: bool = False
    ) -> str:
        """Classify a generic `code` task via keyword/complexity heuristics."""
        team = task.get("team")
        title = (task.get("title") or "").lower()
        description = (task.get("description") or "").lower()
        text = f"{title} {description}"
        complexity = task.get("estimated_complexity", "medium").lower()

        cell_teams = frozenset(t.value for t in CELL_TEAMS)
        if team in cell_teams:
            return self._classify_cell_code_task(complexity, has_children)

        return self._classify_strategic_code_task(text, team, complexity)

    def _classify_task_routing(
        self, task: dict[str, Any], has_children: bool = False
    ) -> str:
        """
        Classify a task for routing based on task_type, team, complexity, and keywords.

        Returns one of: "board", "main_pm", "cell_pm", "dev", "marketing"
        """
        team = task.get("team")
        task_type = task.get("task_type", "code")

        # Task type takes precedence for non-code work
        by_type = self._route_by_task_type(task_type, team)
        if by_type:
            return by_type
        if team in self._TEAM_ROUTING_MAP:
            return self._TEAM_ROUTING_MAP[team]

        return self._classify_code_task(task, has_children)

    def _resolve_routing_target(self, routing: str, task: dict[str, Any]) -> str | None:
        """
        Resolve a routing decision to a specific agent slug (never-strand
        fallbacks default to main-pm — code-task safety lives in the async
        wrapper ``_get_routing_target``: main_pm+code must not coexist by
        org invariant, so the dispatcher's pre-claim route refuses it
        (``_raise_if_main_pm_code_claim``, needs_revision recovery exempt);
        the lifecycle spec's broader ``i_will_plan`` claim allowance covers
        cell PMs planning code parents, not this dispatch path).

        Args:
            routing: One of "board", "main_pm", "cell_pm", "dev", "marketing"
            task: The task being routed

        Returns:
            Agent slug (e.g., "main-pm", "be-pm", "be-dev-1") or None
        """
        team = task.get("team")

        # Static routing targets
        static_targets = {
            "board": "product-owner",
            "main_pm": "main-pm",
            "marketing": "head-marketing",
        }
        if routing in static_targets:
            return static_targets[routing]

        # Cell PM routing - requires team lookup
        if routing == "cell_pm":
            return self._TEAM_PM_MAP.get(team, "main-pm") if team else "main-pm"

        # Dev routing - select a cell agent.
        if routing == "dev":
            if team not in self._TEAM_PM_MAP:
                # No cell agent: team is missing or a non-cell team (fullstack
                # / system). Fall back to main-pm to triage rather than
                # leaving the task ownerless-and-dormant: the dispatcher never
                # re-spawns an unrouted pending task, so a None here strands
                # it. Mirrors the cell_pm / escalation `... or "main-pm"`
                # default.
                logger.warning(
                    "dev routing found no cell agent; falling back to main-pm",
                    task_id=task.get("id"),
                    team=team,
                )
                return "main-pm"
            agent = self._select_agent_for_cell(team, "dev")
            if agent:
                return agent
            # Both cell devs are active or mid-claim/spawn this tick. Return
            # None (never main-pm, MAIN_PM_NO_CODE refuses it forever) so
            # the task stays pending for the next scan instead of stacking a
            # second claim onto a dev that hasn't finished its first one.
            logger.debug(
                "dev routing: both cell devs busy this tick, deferring",
                task_id=task.get("id"),
                team=team,
            )
            return None

        # Unrecognized routing classification — never strand the task; main-pm
        # triages it instead of it going dormant.
        logger.warning(
            "unrecognized routing classification; falling back to main-pm",
            routing=routing,
            task_id=task.get("id"),
        )
        return "main-pm"

    @staticmethod
    def _is_code_task(task: dict[str, Any]) -> bool:
        return str(task.get("task_type") or "").lower() == "code"

    async def _nearest_cell_team(self, task: dict[str, Any]) -> str | None:
        """Walk the parent chain for the nearest ancestor's cell team.

        Mirrors ``TaskService._resolve_pm_for_review`` (task.py:6493) but
        keys on ``team`` rather than ``assigned_to`` — this runs before any
        assignment exists. Best-effort: any lookup failure returns None so
        the caller falls back to the deterministic default cell.
        """
        parent_id = task.get("parent_task_id")
        if not parent_id:
            return None
        from uuid import UUID

        from roboco.db.base import get_session_factory
        from roboco.services.task import get_task_service

        cell_teams = frozenset(t.value for t in CELL_TEAMS)
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                task_svc = get_task_service(db)
                while parent_id:
                    parent = await task_svc.get(UUID(str(parent_id)))
                    if not parent:
                        return None
                    parent_team = getattr(parent.team, "value", parent.team)
                    if parent_team in cell_teams:
                        return str(parent_team)
                    parent_id = parent.parent_task_id
        except Exception as exc:
            logger.warning(
                "cell-team parent walk failed; using default cell",
                task_id=task.get("id"),
                error=str(exc),
            )
        return None

    async def _cell_pm_for_stranded_code_task(self, task: dict[str, Any]) -> str:
        """Redirect a code task that would strand on main-pm to a cell PM.

        The dispatcher's pre-claim (``_raise_if_main_pm_code_claim``, the
        MAIN_PM_NO_CODE org invariant: main_pm+code must not coexist —
        intake coerces such roots to planning) refuses a Main-PM claim of a
        pending code task, so a stranded one loops claim-reject forever at
        ~30s cadence instead of failing loud. A code task belongs to a
        cell: resolve the nearest ancestor cell team first; a parentless/
        unresolvable chain defaults to backend (ponytail: no signal to pick
        a better default among the three cells).
        """
        team = await self._nearest_cell_team(task)
        target = self._TEAM_PM_MAP.get(team or "", "be-pm")
        logger.warning(
            "code task would strand on main-pm (MAIN_PM_NO_CODE); "
            "redirecting to cell pm",
            task_id=task.get("id"),
            resolved_team=team,
            agent_id=target,
        )
        return target

    async def _get_routing_target(
        self, routing: str, task: dict[str, Any]
    ) -> str | None:
        """Resolve a routing decision to a specific agent slug.

        Wraps ``_resolve_routing_target``: when that would strand a code
        task on main-pm (a claim MAIN_PM_NO_CODE refuses forever), redirects
        to a cell PM instead — see ``_cell_pm_for_stranded_code_task``.
        """
        target = self._resolve_routing_target(routing, task)
        if target == "main-pm" and self._is_code_task(task):
            return await self._cell_pm_for_stranded_code_task(task)
        return target

    def _creator_route_should_skip(
        self, task: dict[str, Any], agent_id: str, routing: str
    ) -> bool:
        """The creator-skip guard for ``_route_unassigned_pm_task``.

        A PM that just created this task is about to assign it (e.g. be-pm
        creating a code subtask to hand to be-dev-1 one tool-call later);
        racing in and claiming for the PM would hijack that delegation. True
        only while the task is still within ``_CREATOR_ROUTE_GRACE_SECONDS``
        of creation — past that the creator's session is long gone (it
        exited without assigning), so this falls through (False) to normal
        routing instead of skipping forever. Fails open on an
        unparseable/missing ``created_at`` (treated as OLD) — routing is the
        safe default, the skip is only an optimization.
        """
        created_by = task.get("created_by")
        if not created_by or self._resolve_agent_slug(str(created_by)) != agent_id:
            return False
        age = self._get_task_age(task)
        if age is not None and age.total_seconds() < _CREATOR_ROUTE_GRACE_SECONDS:
            logger.info(
                "Skipping auto-claim: routing target is the creator",
                task_id=task.get("id"),
                creator=agent_id,
                routing=routing,
            )
            return True
        logger.info(
            "Creator-skip grace elapsed; routing task to its creator",
            task_id=task.get("id"),
            creator=agent_id,
            routing=routing,
            age_seconds=None if age is None else int(age.total_seconds()),
        )
        return False

    async def _route_unassigned_pm_task(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> None:
        """Classify and route an unassigned pending task to its target agent."""
        if await self._pending_claim_blocked(task.get("id")):
            return
        # Only the cell code-task classifier consults has_children; skip the
        # DB round-trip for every other task_type/team (planning, research,
        # board, main_pm, marketing, ...) that never looks at it.
        cell_teams = frozenset(t.value for t in CELL_TEAMS)
        needs_children_probe = (
            task.get("task_type", "code") == "code" and task.get("team") in cell_teams
        )
        has_children = (
            await self._task_has_children(task.get("id"))
            if needs_children_probe
            else False
        )
        routing = self._classify_task_routing(task, has_children)
        agent_id = await self._get_routing_target(routing, task)

        if not agent_id:
            logger.warning(
                "No routing target found",
                task_id=task.get("id"),
                routing=routing,
            )
            return

        # Board work is a two-reviewer gate (PO + Head of Marketing), not a
        # single-assignee claim. Routing only ever names one board agent
        # (product-owner), so claiming + spawning that one here would leave the
        # Head of Marketing out (finding #4). Delegate to the board handler,
        # which dispatches BOTH reviewers one-shot and leaves the task pending
        # for the CEO's Approve & Start. ``agent_id`` is the routed board slug.
        if routing == "board":
            await self._handle_board_assigned_task(task, agent_id)
            return

        # Don't auto-claim back to the creator while the task is fresh — see
        # _creator_route_should_skip.
        if self._creator_route_should_skip(task, agent_id, routing):
            return

        logger.info(
            "Routing task",
            task_id=task.get("id"),
            routing=routing,
            agent_id=agent_id,
        )
        await self._claim_and_spawn_routed_agent(client, task, routing, agent_id)

    async def _review_pm_slug(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> str | None:
        """Owning PM slug for an awaiting_pm_review task.

        Skips the assign-review-pm round trip (a row lock + internal HTTP
        call) when the task's own ``assigned_to`` already matches the
        team-resolved owner (the same ``_closure_pm_for_team`` mapping) —
        every review task hitting that route on every tick even when
        already correct is needless DB-lock pressure on a stack with
        documented lock-contention incidents (#721/#726). The route stays
        authoritative for the mismatch/unassigned case (it re-resolves
        under its own row lock). Falls back to the task's own (possibly
        stale) ``assigned_to`` when the route itself fails/rejects — unlike
        ``_closure_review_pm``, there is no independently-known-better
        default here to protect.
        """
        expected = self._closure_pm_for_team(task.get("team"))
        current = task.get("assigned_to")
        current_slug = self._resolve_agent_slug(current) if current else None
        if current_slug == expected:
            return expected
        resolved = await self._ensure_review_pm_assigned(client, task)
        return resolved or current_slug

    def _blocker_resolver_slug(self, task: dict[str, Any]) -> str | None:
        """Pick the agent that should be dispatched to unblock ``task``.

        The unblock content gate (note/unblock) is assignee-only: the
        dispatched agent must be the task's CURRENT ``assigned_to``, or its
        required pre-unblock decision note returns not_authorized and the
        orchestrator respawns it forever (a livelock — a task escalated to
        Main PM kept respawning the ex-assignee cell PM, which could not author
        the note). So whenever the blocked task carries an assignee that is a
        PM role, dispatch THAT assignee. Only a task with no PM assignee
        (e.g. still held by the dev who raised i_am_blocked) falls back to the
        cell PM for its team.

        A BOARD/advisory assignee (product-owner / head-marketing) is the one
        case we must NOT dispatch: a board role has no ``unblock`` verb at all
        — its only moves are notify/note/triage/i_am_idle — so dispatching it
        to "resolve" a blocker is a futile catch-22. It cannot unblock, cannot
        hand the task off (the assignee-only gate also forbids any PM from
        unblocking a task it does not own), and so it spam-notifies the CEO and
        the orchestrator respawns it forever (observed: 6400+ tool calls burned
        on a single delivery root mis-assigned to product-owner). Return None so
        the blocker dispatch SKIPS it — the task is mis-owned and must be
        re-routed / surfaced to the CEO out-of-band, never auto-respawned onto a
        role that physically cannot act. (The upstream cure is to never assign a
        board role as the owner of an executable delivery/coordination root.)
        """
        assignee_uuid = task.get("assigned_to") or task.get("claimed_by")
        if assignee_uuid:
            assignee_slug = self._resolve_agent_slug(str(assignee_uuid))
            if assignee_slug in self._BOARD_AGENTS:
                return None
            if assignee_slug in self._PM_AGENTS:
                return assignee_slug
        team = task.get("team")
        if team not in ("backend", "frontend", "ux_ui"):
            return None
        return self._select_agent_for_cell(team, "pm")
