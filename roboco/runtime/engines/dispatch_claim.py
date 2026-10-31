"""The dispatcher loop and claiming: each tick's run of every work dispatcher
and the reads they make, whether a task may be claimed now and is ready to
spawn on, the claim and spawn themselves, and the stuck-task and SLA checks
the tick runs.

Engine mixins composed into AgentOrchestrator."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import httpx
from fastapi import status as http_status

from roboco.agents_config import get_agent_role
from roboco.config import settings
from roboco.foundation.policy.lane import transitive_dependents
from roboco.models.runtime import SpawnGitContext
from roboco.runtime.orchestrator import (
    _PARENT_BRANCH_WAIT,
    _branch_is_expected,
    _created_before,
    _is_coordination_task,
    _is_held_ceo_source,
    _SlaBreach,
    _system_api_headers,
    logger,
)
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class DispatcherLoopEngine(_Base):
    """The dispatcher loop, its heartbeat, and each tick's run of every work
    dispatcher (`_dispatch_all_work`), the reads they make (tasks,
    notifications, one task's fields, descendants and children), and the
    handled-this-tick check."""

    # Re-declared (not just inherited from _Base above): mypy's Protocol
    # attribute inference cannot determine the type of an inherited member
    # that a method both reads AND assigns within the same method body
    # (read-then-write in one scope; see _emit_dispatcher_heartbeat below)
    # without a bare re-declaration directly on the concrete class. Without
    # it a fresh run rooted at orchestrator.py alone infers `datetime` from
    # the heartbeat assignment and rejects __init__'s `datetime | None`.
    if TYPE_CHECKING:
        _last_dispatch_heartbeat: datetime | None

    def _is_task_handled_this_tick(self, task_id: str | None) -> bool:
        """True if a prior dispatcher already handled this task this tick."""
        return bool(task_id and task_id in self._tick_handled_tasks)

    @staticmethod
    def _parse_iso_dt(value: Any) -> datetime | None:
        """Parse a notification's ISO timestamp to an aware UTC datetime, or
        None if absent/unparseable. Naive values are assumed UTC."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    async def _fetch_task_fields(
        self, client: httpx.AsyncClient, task_id: str
    ) -> dict[str, Any] | None:
        """Best-effort GET /tasks/{id} → full task dict (None on any failure —
        fail-open so a fetch hiccup never suppresses a real escalation)."""
        try:
            resp = await client.get(f"{self._api_url}/tasks/{task_id}")
            if resp.status_code == http_status.HTTP_200_OK:
                return cast("dict[str, Any]", resp.json())
        except Exception as exc:
            logger.debug("notification task-fields fetch failed", error=str(exc))
        return None

    async def _fetch_tasks(
        self,
        client: httpx.AsyncClient,
        status: str | list[str],
        team: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch tasks by status and optional team filter.

        ``limit`` forwards to ``GET /tasks``'s own ``limit`` query param
        (default 100 server-side); omit to keep that default. A caller whose
        fetch is NOT team-scoped needs this raised (see
        ``_dispatch_pm_work``'s docstring for why an unscoped fetch can
        silently truncate).
        """
        # If multiple statuses, make separate requests and combine results
        statuses = status if isinstance(status, list) else [status]
        all_tasks: list[dict[str, Any]] = []

        for single_status in statuses:
            params: dict[str, Any] = {"status": single_status}
            if team:
                params["team"] = team
            if limit is not None:
                params["limit"] = limit

            try:
                resp = await client.get(f"{self._api_url}/tasks", params=params)
                if resp.status_code == http_status.HTTP_200_OK:
                    tasks: list[dict[str, Any]] = resp.json()
                    all_tasks.extend(tasks)
            except Exception as e:
                logger.error(
                    "Fetch tasks error", status=single_status, team=team, error=str(e)
                )

        return all_tasks

    async def _fetch_notifications(
        self,
        client: httpx.AsyncClient,
        notification_type: str,
        unacknowledged: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch notifications by type."""
        params: dict[str, Any] = {
            "type_filter": notification_type,
            "pending_ack_only": str(unacknowledged).lower(),
        }
        try:
            resp = await client.get(
                f"{self._api_url}/notifications",
                params=params,
            )
            if resp.status_code == http_status.HTTP_200_OK:
                data = resp.json()
                items: list[dict[str, Any]] = data.get("items", [])
                return items
        except Exception as e:
            logger.error(
                "Fetch notifications error",
                notification_type=notification_type,
                error=str(e),
            )
        return []

    async def _dispatcher_loop(self) -> None:
        """
        Main dispatcher loop - periodically checks for work and spawns agents.

        This is the BRAIN of the orchestrator. It:
        1. Queries for tasks needing work (pending, awaiting_qa, etc.)
        2. Queries for events needing attention (blockers, escalations)
        3. Spawns appropriate agents with task assignments

        Hybrid timing: a poll (dispatcher_interval) guarantees progress even
        without external signals, while `_dispatch_wake` lets API routes
        kick the loop for immediate reactions after status transitions.
        """
        while self._running:
            try:
                # Wait either for an explicit wake signal or the poll timeout,
                # whichever comes first. asyncio.wait_for re-raises
                # TimeoutError when the poll window expires, which we treat
                # as "run dispatch anyway".
                import contextlib

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._dispatch_wake.wait(),
                        timeout=self.dispatcher_interval,
                    )
                self._dispatch_wake.clear()
                await self._refresh_grok_auth()
                await self._refresh_codex_auth()
                await self._dispatch_all_work()
                await self._emit_dispatcher_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Dispatcher loop error", error=str(e))

    async def _task_has_children(self, task_id: str | None) -> bool:
        """Dispatch-time probe: does ``task_id`` already have subtasks?

        Feeds the cell code-task classifier — a task with children is a
        coordination node (cell_pm); a childless leaf routes on complexity
        alone. Fails closed (False) on any lookup error: an unclassifiable
        leaf is safer routed to dev than mistakenly parked on a PM.
        """
        if not task_id:
            return False
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                return await TaskService(db).has_children(UUID(task_id))
        except Exception as exc:
            logger.warning(
                "Children probe failed; classifying as childless",
                task_id=task_id,
                error=str(exc),
            )
            return False

    async def _fetch_all_descendants(
        self, client: httpx.AsyncClient, task_id: str
    ) -> list[dict[str, Any]]:
        """Fetch ALL descendants (children, grandchildren, etc.) recursively.

        Uses the /tasks/{id}/descendants endpoint which does BFS traversal.
        """
        try:
            resp = await client.get(f"{self._api_url}/tasks/{task_id}/descendants")
            if resp.status_code == http_status.HTTP_200_OK:
                data = resp.json()
                # Endpoint returns list directly
                return list(data) if data else []
        except Exception as e:
            logger.warning("Failed to fetch descendants", task_id=task_id, error=str(e))
        return []

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

    async def _emit_dispatcher_heartbeat(self) -> None:
        """Periodic dispatcher.alive audit row — a dead loop becomes detectable.

        The dispatch loop can die silently (task cancelled, unhandled exit) and
        its stdout dies with the container; audit_log survives both. Absence of
        a fresh heartbeat row = loop dead, distinguishable from "no work". The
        `dispatch_paused` flag lets the uptime ledger treat a deliberate
        maintenance pause as downtime instead of counting it as an outage.
        """
        now = datetime.now(UTC)
        last = getattr(self, "_last_dispatch_heartbeat", None)
        if last is not None and (now - last).total_seconds() < (
            self._DISPATCH_HEARTBEAT_SECONDS
        ):
            return
        self._last_dispatch_heartbeat = now
        from roboco.services.maintenance_pause import PauseScope

        paused = await self._is_paused(PauseScope.DISPATCH)
        self._fire_audit(
            event_type="dispatcher.alive",
            agent_slug="orchestrator",
            details={
                "interval_seconds": self._DISPATCH_HEARTBEAT_SECONDS,
                "dispatch_paused": paused,
            },
        )


class DispatchClaimEngine(_Base):
    """Claim a task for an agent and spawn it: the claim-in-flight registry,
    picking a free cell agent, the claim and review-PM assignment calls, the
    guarded claim-and-spawn, and the prompt a routed spawn gets."""

    def _task_git_context(self, task: dict[str, Any]) -> SpawnGitContext | None:
        """Build SpawnGitContext from a task dict for workspace mounting.

        Without this, spawned agents fall back to project_slug="default"
        and get a Write/Edit permission lock to /data/workspaces/default/...
        which does not exist, so the agent's file tools fail.
        """
        project_slug = task.get("project_slug")
        if not project_slug:
            return None
        branch_name = task.get("branch_name")
        ctx = SpawnGitContext(project_slug=project_slug, branch_name=branch_name)
        # A branch-bearing task edits in a per-task worktree keyed by the short
        # id; a branchless coordination root (umbrella / no-project product
        # root) has no worktree, so task_short_id stays None and the spawn cwd
        # falls back to the clone root.
        if branch_name and task.get("id"):
            ctx.task_short_id = str(task["id"])[:8]
        return ctx

    def _is_claim_in_flight(self, agent_id: str) -> bool:
        """True if `agent_id` has an unexpired claim/spawn in flight.

        Expires lazily on read - a past-deadline entry is dropped here, no
        background sweep needed. ``getattr`` guards unit tests that build an
        orchestrator via ``__new__`` and skip ``__init__``.
        """
        claims: dict[str, tuple[str, float]] | None = getattr(
            self, "_claims_in_flight", None
        )
        if not claims:
            return False
        entry = claims.get(agent_id)
        if entry is None:
            return False
        _, deadline = entry
        if time.monotonic() >= deadline:
            del claims[agent_id]
            return False
        return True

    def _mark_claim_in_flight(self, agent_id: str, task_id: str) -> None:
        """Park `agent_id` as claim-in-flight until the TTL setting elapses."""
        if not hasattr(self, "_claims_in_flight"):
            self._claims_in_flight = {}
        deadline = time.monotonic() + settings.dispatch_claim_inflight_ttl_seconds
        self._claims_in_flight[agent_id] = (task_id, deadline)

    def _clear_claim_in_flight(self, agent_id: str) -> None:
        """Free `agent_id` once its claim has a definite outcome."""
        claims: dict[str, tuple[str, float]] | None = getattr(
            self, "_claims_in_flight", None
        )
        if claims:
            claims.pop(agent_id, None)

    async def _claim_and_spawn_guarded(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        agent_id: str,
        spawn: "Callable[[], Awaitable[None]]",
    ) -> bool | None:
        """Park, claim, and spawn `agent_id` for `task`.

        Clears the park on every outcome but None.

        `spawn` runs only on a successful claim. If it raises - including
        `spawn_agent`'s ordinary `AgentReadinessError` for a not-yet-ready
        task - the park still clears via `finally` before the exception
        propagates, so a callable's leak can't wedge `agent_id` out of
        `_select_agent_for_cell` for the full TTL. Returns the claim
        outcome so callers can log/handle a False (rejected) claim their
        own way; a None outcome (the claim call's own timeout/exception,
        per `_claim_task_for_agent`'s contract) leaves the agent parked
        until TTL, unchanged.
        """
        self._mark_claim_in_flight(agent_id, task["id"])
        claimed = await self._claim_task_for_agent(client, task["id"], agent_id)
        try:
            if claimed:
                await spawn()
            return claimed
        finally:
            if claimed is not None:
                self._clear_claim_in_flight(agent_id)

    @staticmethod
    def _cell_candidates(cell: str | None, role: str) -> list[str]:
        """The agent slugs that fill ``role`` in ``cell``, in preference order."""
        prefix = {"backend": "be", "frontend": "fe", "ux_ui": "ux"}.get(cell or "")
        if not prefix:
            return []
        return {
            "dev": [f"{prefix}-dev-1", f"{prefix}-dev-2"],
            "qa": [f"{prefix}-qa"],
            "doc": [f"{prefix}-doc"],
            "pm": [f"{prefix}-pm"],
            "pr_reviewer": [f"{prefix}-pr-reviewer", "cell-pr-reviewer-2"],
        }.get(role, [])

    async def _free_cell_dev(self, task: dict[str, Any]) -> str | None:
        """The first idle cell dev holding no other work, or None to defer.

        ``_select_agent_for_cell`` only knows which containers are running. A
        dev between spawns can still hold claimed, blocked or paused work, and
        an on-behalf claim for it is refused at the claim chokepoint (before
        that check it was handed a task its own ``i_will_work_on`` refused).
        """
        for slug in self._cell_candidates(task.get("team"), "dev"):
            if self._is_agent_active(slug) or self._is_claim_in_flight(slug):
                continue
            agent_uuid = AGENT_UUIDS.get(slug)
            if agent_uuid and not await self._held_task_blocking_claim(
                agent_uuid, task["id"]
            ):
                return slug
        logger.debug(
            "dev routing: every idle cell dev still holds work, deferring",
            task_id=task.get("id"),
        )
        return None

    def _select_agent_for_cell(self, cell: str, role: str) -> str | None:
        """
        Select the best available agent for a cell and role.

        Prefers agents that are not currently active.
        For developers, uses round-robin among candidates.
        """
        candidates = self._cell_candidates(cell, role)

        # Prefer an agent that is neither active nor mid-claim/spawn.
        for agent_id in candidates:
            if not self._is_agent_active(agent_id) and not self._is_claim_in_flight(
                agent_id
            ):
                return agent_id

        # Every candidate is active or already has a claim/spawn in flight -
        # wait for the next scan instead of stacking a second claim onto one
        # of them (the 2026-09-05 triple-claim lock-convoy amplifier).
        return None

    async def _claim_task_for_agent(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        agent_id: str,
    ) -> bool | None:
        """Claim a task on behalf of an agent before spawning.

        Returns True on a successful claim, False on a definite server
        rejection (the agent is free to try again), and None on a client-side
        exception/timeout - the server may still be finishing the claim, so
        the caller must not treat this the same as a clean failure.
        """
        try:
            resp = await client.post(
                f"{self._api_url}/tasks/{task_id}/claim",
                json={"agent_id": agent_id},
                # A cold-workspace claim clones + installs deps synchronously
                # server-side (clone 300s + install 600s), bounded by the
                # slow-verb wall; the client's 30s default would abandon it.
                timeout=float(settings.flow_verb_slow_timeout_seconds),
            )
            if resp.status_code == http_status.HTTP_200_OK:
                logger.info(
                    "Task claimed for agent",
                    task_id=task_id,
                    agent_id=agent_id,
                )
                return True
            logger.warning(
                "Failed to claim task",
                task_id=task_id,
                agent_id=agent_id,
                status=resp.status_code,
            )
            return False
        except Exception as e:
            logger.error("Claim task error", task_id=task_id, error=str(e))
            return None

    async def _ensure_review_pm_assigned(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> str | None:
        """POST assign-review-pm; return the resulting owner's agent slug.

        CLAIM_RULES has no claim() edge into AWAITING_PM_REVIEW (the
        i_will_plan re-claim-loop fix), so an unassigned task (pr_pass
        resolved no PM) or a stale one (an escalate/unblock(restore=True)
        round trip restores status but not ownership — see the pr_pass
        ownership-clearing fix) needs this instead of ``_claim_task_for_agent``.

        ``None`` on ANY rejection or transport error — this reports the
        route's own outcome only and does NOT fall back to the task's own
        (possibly stale) ``assigned_to``. A blind fallback here would let a
        transient failure silently clobber a caller's independently-known-
        correct default (``_maybe_spawn_pm_closure``'s team-resolved
        ``pm_id`` — see ``_closure_review_pm``); callers that have no better
        default than the task's own ``assigned_to`` (``_dispatch_pm_review_
        work`` — see ``_review_pm_slug``) apply that fallback themselves.
        """
        try:
            resp = await client.post(
                f"{self._api_url}/tasks/{task['id']}/assign-review-pm"
            )
            if resp.status_code == http_status.HTTP_200_OK:
                assigned_to = resp.json().get("assigned_to")
                return self._resolve_agent_slug(assigned_to) if assigned_to else None
            logger.warning(
                "assign-review-pm rejected",
                task_id=task.get("id"),
                status=resp.status_code,
            )
        except Exception as e:
            logger.error(
                "assign-review-pm error",
                task_id=task.get("id"),
                error=str(e),
            )
        return None

    async def _claim_and_spawn_routed_agent(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        routing: str,
        agent_id: str,
    ) -> None:
        """Claim `task` for `agent_id` and spawn it if not already active.

        Extracted from ``_route_unassigned_pm_task`` to keep that classifier
        under the return-statement gate. Parks the agent as claim-in-flight
        for the duration of the claim + spawn so a second pending task this
        tick (or the next) can't pick it again while the branch-creation
        lock is still held server-side.
        """
        if self._is_agent_active(agent_id):
            await self._claim_task_for_agent(client, task["id"], agent_id)
            return

        async def _spawn() -> None:
            prompt = await self._pm_spawn_prompt(routing, agent_id, task)
            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=prompt,
                git_context=self._task_git_context(task),
                spawned_by="_route_unassigned_pm_task",
            )

        await self._claim_and_spawn_guarded(client, task, agent_id, _spawn)

    async def _pm_spawn_prompt(
        self, routing: str, agent_id: str, task: dict[str, Any]
    ) -> str:
        """Pick the correct prompt for a classified spawn."""
        if routing == "dev":
            return await self._build_dev_prompt(task)
        if routing == "main_pm" or agent_id == "main-pm":
            return self._build_main_pm_triage_prompt(task)
        return self._build_pm_triage_prompt(task)


class SpawnReadinessEngine(_Base):
    """Is a task ready to spawn on: its shape, its parent's branch (with the
    provisioning grace and retry), and the dev-needs-subtasks block."""

    async def _retry_parent_branch_fetch(
        self, client: httpx.AsyncClient, parent_id: str, parent: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Re-fetch the parent up to 3 times (250ms apart) for its branch.

        Returns (True, parent) the moment branch_name lands, else
        (False, last-seen parent) once retries are exhausted.
        """
        for _ in range(3):
            await asyncio.sleep(0.25)
            parent_resp = await client.get(f"{self._api_url}/tasks/{parent_id}")
            if not parent_resp.is_success:
                continue
            parent = parent_resp.json()
            if parent.get("branch_name"):
                return True, parent
        return False, parent

    def _parent_within_provisioning_grace(self, parent: dict[str, Any]) -> bool:
        """True if a mid-claim, still-branchless parent's `claimed_at` is
        younger than `parent_branch_provisioning_grace_seconds`, measured in
        fleet-active time so a paused fleet never eats into the budget."""
        claimed_at = self._parse_iso_dt(parent.get("claimed_at"))
        if claimed_at is None:
            return False
        grace = timedelta(seconds=settings.parent_branch_provisioning_grace_seconds)
        return self._active_age(claimed_at) < grace

    async def _check_parent_branch_ready(
        self, client: httpx.AsyncClient, task_id: str, parent_id: str
    ) -> str | None:
        """Verify the parent task has a branch; auto-block + return msg if not.

        Race window: the PM's `i_will_plan` claims the parent (transitions
        status -> claimed/in_progress, sets assigned_to, commits via
        `_apply_claim_fields`) and then `_provision_claim` creates the branch
        and commits again right after. A child dev's spawn dispatch can fire
        microseconds after the first commit (the fields land, sub-second gap)
        or minutes into a slow branch creation (git network I/O under load),
        and sees branch_name=None either way. Without tolerating both, the
        common healthy path auto-blocks the child.

        Two tolerances, checked in order, only while the parent is mid-claim
        (status claimed/in_progress with assigned_to set):
        - Fast retry (`_retry_parent_branch_fetch`): re-fetch up to 3 times
          with a 250ms delay. Total worst-case wait is 750ms, absorbing the
          sub-second gap between the two commits above.
        - Provisioning grace (`_parent_within_provisioning_grace`): once the
          fast retry is exhausted, a parent still inside
          `parent_branch_provisioning_grace_seconds` of its `claimed_at` is
          skipped this dispatch tick (no auto-block) and the next tick
          retries; branch creation itself can outlast the fast retry under
          load. Past the grace, or when the parent isn't mid-claim at all
          (pending/unassigned, or terminal - no branch is ever coming),
          auto-block as before.
        """
        parent_resp = await client.get(f"{self._api_url}/tasks/{parent_id}")
        if not parent_resp.is_success:
            return None
        parent = parent_resp.json()
        if parent.get("branch_name"):
            return None

        # A coordination/fan-out parent (product, no repo of its own) never gets
        # a branch: the child resolves its own real project and cuts from that
        # project's default branch, not from the parent. Blocking the child on a
        # branch the parent will never have wedges the cell<->Main-PM loop.
        if _is_coordination_task(parent):
            return None

        mid_claim = parent.get("status") in ("claimed", "in_progress") and parent.get(
            "assigned_to"
        )
        if mid_claim:
            ready, parent = await self._retry_parent_branch_fetch(
                client, parent_id, parent
            )
            if ready:
                return None
            if self._parent_within_provisioning_grace(parent):
                logger.debug(
                    "Parent branch still provisioning, skipping child this tick",
                    task_id=task_id,
                    parent_id=parent_id,
                )
                return f"Task {task_id} {_PARENT_BRANCH_WAIT}"

        await self._auto_block_task(
            client,
            task_id,
            "Parent task must be claimed first to create its branch",
        )
        return f"Task {task_id} waiting for parent branch"

    async def _check_dev_needs_subtasks(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> str | None:
        """Block non-trivial root tasks routed to a dev without subtasks."""
        complexity = task.get("estimated_complexity", "low")
        parent_task_id = task.get("parent_task_id")
        if complexity not in ("medium", "high") or parent_task_id:
            return None
        task_id = task.get("id")
        try:
            resp = await client.get(f"{self._api_url}/tasks/{task_id}/subtasks")
            subtasks = resp.json() if resp.is_success else []
        except Exception:
            subtasks = []
        if subtasks:
            return None
        await self._auto_block_task(
            client,
            str(task_id),
            f"Task complexity is {complexity} but no subtasks. "
            "Cell PM must break down work first.",
        )
        return (
            f"Task {task_id} is {complexity} complexity "
            "without subtasks - Cell PM must break it down"
        )

    async def _validate_task_for_spawn(
        self,
        client: httpx.AsyncClient,
        task: dict,
        agent_slug: str,
    ) -> str | None:
        """
        Validate task is ready for agent spawn.

        Returns None if valid, or error message if task cannot proceed.
        This prevents spawning agents on tasks that are missing prerequisites.
        """
        from roboco.agents_config import get_agent_role

        if shape_err := await self._check_spawn_task_shape(client, task):
            return shape_err

        if dep_err := await self._check_dependencies_terminal(client, task):
            return dep_err

        # _check_spawn_task_shape guarantees a non-empty id past this point.
        task_id = str(task.get("id"))
        parent_id = task.get("parent_task_id")
        if parent_id:
            err = await self._check_parent_branch_ready(client, task_id, parent_id)
            if err:
                return err

        logger.info("Task ready for hierarchical branch creation", task_id=task_id)

        if get_agent_role(agent_slug) == "developer":
            err = await self._check_dev_needs_subtasks(client, task)
            if err:
                return err

        # All validations passed
        return None

    async def _check_spawn_task_shape(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> str | None:
        """Reject a task that is structurally unroutable (id/description/repo)."""
        task_id = task.get("id")
        if not task_id:
            return "Task missing ID"
        min_description_len = 10
        description = (task.get("description") or "").strip()
        if len(description) < min_description_len:
            return (
                f"Task {task_id} has inadequate description ({len(description)} chars)"
            )
        # A coordination task carries a product or an ad-hoc cell map instead of a
        # repo; only a task with neither is genuinely unroutable.
        if not task.get("project_id") and not _is_coordination_task(task):
            await self._auto_block_task(
                client, task_id, "Task needs a project_id, product_id, or cell map"
            )
            return f"Task {task_id} needs a project, product, or cell map"
        return None


class ClaimBarrierEngine(_Base):
    """May a task be claimed now: the dependency and sequence probe, the
    same-team sibling bar, the per-dev lane barrier, and the work author who
    still holds other work."""

    async def _pending_claim_blocked(self, task_id: str | None) -> bool:
        """Dispatch-time probe: is ``task_id`` held by the dependency/sequence
        guard right now?

        `_dispatch_pm_work` fetches every PENDING task each tick with no
        sequencing filter, so a later-wave MegaTask root-subtask (or any
        dependency-blocked task) got a doomed claim attempt every tick —
        harmless (the claim chokepoint already refuses it) but pure churn.
        Reuses `TaskService.is_pending_claim_blocked` (the exact claim-gate
        predicate) so this can't drift from what the claim endpoint enforces.
        Fails open (False) on any lookup error — the claim attempt itself is
        the safety net and will surface a real error if something's wrong.
        """
        if not task_id:
            return False
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                return await TaskService(db).is_pending_claim_blocked(UUID(task_id))
        except Exception as exc:
            logger.warning(
                "Claim-block probe failed; falling through to claim attempt",
                task_id=task_id,
                error=str(exc),
            )
            return False

    async def _held_task_blocking_claim(
        self, agent_uuid: str, task_id: str
    ) -> tuple[str, str] | None:
        """Dispatch-time probe: ``(status, id)`` of the task ``agent_uuid``
        still holds that its own ``i_will_work_on`` on ``task_id`` is refused
        for, or None.

        Reuses ``TaskService.held_task_blocking_claim``, the rule the gateway
        guards and the claim chokepoint apply, so this can't drift from what
        the agent is actually refused. Fails open (None) on any lookup error,
        like ``_pending_claim_blocked``: the claim remains the safety net.
        """
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                held = await TaskService(db).held_task_blocking_claim(
                    UUID(agent_uuid), UUID(task_id)
                )
                return None if held is None else (str(held.status), str(held.id))
        except Exception as exc:
            logger.warning(
                "Held-work probe failed; falling through to dispatch",
                agent_uuid=agent_uuid,
                task_id=task_id,
                error=str(exc),
            )
            return None

    async def _work_author_holds_other_work(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        owner_uuid: str,
        agent_slug: str,
    ) -> bool:
        """True when ``agent_slug`` must not be spawned on ``task`` yet.

        A pending / needs_revision / claimed task still needs the author's own
        ``i_will_work_on``, which the gateway refuses while the author holds
        any other claimed, in_progress, blocked or paused task. Spawning it
        anyway was pure churn (live 2026-10-28: be-dev-1 was spawned 14 times
        in an hour on tasks it was refused, never once on the two paused tasks
        every refusal told it to resume). Instead:

        - a held paused task is resumed and the idle author spawned on it;
        - a second never-started claimed task (two claims neither can start,
          left by on-behalf claims the chokepoint now refuses) is released to
          the pool, which breaks the deadlock;
        - any other held work is waited on: it gets its own dispatch, or its
          PM unblocks it.
        """
        status = task.get("status")
        if status not in ("pending", "needs_revision", "claimed"):
            return False
        held = await self._held_task_blocking_claim(owner_uuid, task["id"])
        if held is None:
            return False
        held_status, held_id = held
        if held_status == "paused" and not self._is_agent_active(agent_slug):
            await self._resume_paused_task_for_author(client, held_id, agent_slug)
        elif held_status == "claimed" and status == "claimed":
            await self._release_claim_to_pending(task["id"])
        return True

    async def _resume_paused_task_for_author(
        self, client: httpx.AsyncClient, task_id: str, agent_slug: str
    ) -> None:
        """Resume an idle work author's paused task and spawn it there.

        ``i_am_idle`` auto-pauses the author's in_progress task and the gateway
        then refuses all new work until it is resumed, yet no dispatch path
        ever spawned a developer on a paused task. The dev twin of
        ``_auto_resume_paused_parent`` (weak models reliably fail to call
        ``resume()`` themselves), behind the same respawn breaker as every
        other task-keyed spawn, so a task that keeps getting paused pages the
        CEO instead of looping.
        """
        task = await self._fetch_task_fields(client, task_id)
        if task is None or task.get("status") != "paused":
            return
        if await self._pm_respawn_should_gate(agent_slug, task):
            return
        try:
            resp = await client.patch(
                f"{self._api_url}/tasks/{task_id}", json={"status": "in_progress"}
            )
        except Exception as exc:
            logger.error(
                "Failed to resume paused task", task_id=task_id, error=str(exc)
            )
            return
        if resp.status_code != http_status.HTTP_200_OK:
            logger.warning(
                "Paused task resume refused",
                task_id=task_id,
                status=resp.status_code,
            )
            return
        task["status"] = "in_progress"
        logger.info(
            "Auto-resumed paused task for its idle author",
            task_id=task_id,
            agent=agent_slug,
        )
        await self.spawn_agent(
            agent_id=agent_slug,
            task_id=task_id,
            initial_prompt=await self._build_dev_prompt(task),
            git_context=self._task_git_context(task),
            spawned_by="_resume_paused_task_for_author",
        )

    async def _blocked_by_earlier_sibling(self, task: dict[str, Any]) -> bool:
        """True if a lower-sequence, same-team sibling is not yet terminal.

        Sequence-ordered merge: leaf siblings share one cell branch, so merging
        a later sibling before an earlier one diverges the branch and wedges the
        loser. Hold a higher-sequence sibling's review/merge dispatch until the
        earlier ones land (or are cancelled). Loop-free: the task simply isn't
        dispatched this tick — no reject, no respawn churn. Equal sequences
        (wave-stamped independent siblings — parallel to CLAIM and build) tie-
        break by ``created_at`` so the shared-branch merge stays serialized.

        Only same-team siblings block (they target the same branch). Terminal
        siblings (completed/cancelled) never block, so a cancelled sibling can't
        deadlock the rest. Best-effort: any lookup failure falls through to
        dispatch — the ordering check must never wedge the dispatcher.
        """
        parent_id = task.get("parent_task_id")
        seq = task.get("sequence")
        team = task.get("team")
        if not parent_id or seq is None:
            return False
        from uuid import UUID

        from roboco.db.base import get_session_factory
        from roboco.models.base import TaskStatus
        from roboco.services.task import get_task_service

        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                task_svc = get_task_service(db)
                siblings = await task_svc.get_subtasks(UUID(str(parent_id)))
        except Exception as exc:
            logger.debug(
                "sibling-order check failed; dispatching anyway",
                task_id=task.get("id"),
                error=str(exc),
            )
            return False
        task_created = task.get("created_at")
        return any(
            self._is_earlier_live_team_sibling(
                sib,
                team=str(team),
                seq=seq,
                terminal=terminal,
                task_created=task_created,
            )
            for sib in siblings
        )

    @staticmethod
    def _is_earlier_live_team_sibling(
        sib: Any, *, team: str, seq: int, terminal: set[Any], task_created: Any
    ) -> bool:
        """True if ``sib`` is an earlier non-terminal same-team sibling.

        Earlier = lower sequence, or an equal sequence created first (the
        wave-tie tiebreak that keeps the shared-branch merge serialized).
        """
        sib_seq = getattr(sib, "sequence", 0) or 0
        sib_team = getattr(sib, "team", None)
        sib_team_val = getattr(sib_team, "value", sib_team)
        earlier = sib_seq < seq or (
            sib_seq == seq
            and _created_before(getattr(sib, "created_at", None), task_created)
        )
        return (
            str(sib_team_val) == team
            and earlier
            and getattr(sib, "status", None) not in terminal
        )

    async def _blocked_by_earlier_lane_sibling(self, task: dict[str, Any]) -> bool:
        """True if the SAME dev has an earlier non-terminal code sibling.

        Per-dev sequenced queues (Spec 3): a PM delegates a full queue of code
        subtasks to each cell dev up front. This BUILD/dispatch barrier holds a
        dev's higher-sequence code leaf until its own lower-sequence code
        siblings under the same parent are terminal, so the dev works its queue
        one live task at a time, in order — while the other dev's lane runs
        concurrently (true two-dev parallelism).

        Distinct from :meth:`_blocked_by_earlier_sibling` (the MERGE barrier,
        keyed on team): this is keyed on the assignee and only gates ``code``.
        Loop-free (skip this tick — no reject, no respawn churn) and best-effort
        (any lookup failure falls through to dispatch so the check never wedges).
        Equal sequences tie-break by ``created_at``, mirroring the merge
        barrier, so a dev's wave-tied queue keeps a deterministic order.
        """
        lane = self._lane_context(task)
        if lane is None:
            return False
        parent_id, seq, owner, task_created = lane
        from uuid import UUID

        from roboco.db.base import get_session_factory
        from roboco.models.base import TaskStatus
        from roboco.services.task import get_task_service

        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        try:
            session_factory = get_session_factory()
            async with session_factory() as db:
                task_svc = get_task_service(db)
                siblings = await task_svc.get_subtasks(UUID(str(parent_id)))
        except Exception as exc:
            logger.debug(
                "lane-order check failed; dispatching anyway",
                task_id=task.get("id"),
                error=str(exc),
            )
            return False
        task_id = str(task.get("id"))
        # Siblings that (transitively) depend on this task are ordered AFTER
        # it by the dependency guard — counting them as "earlier" via the
        # created-at tiebreak wedges the two guards against each other
        # (be-dev-2, 2026-09-27). See roboco.foundation.policy.lane.
        dep_map = {
            str(sib.id): {str(d) for d in (getattr(sib, "dependency_ids", None) or ())}
            for sib in siblings
        }
        dependents = transitive_dependents(dep_map, task_id)
        return any(
            self._is_earlier_live_lane_sibling(
                sib,
                task_id=task_id,
                owner=str(owner),
                seq=seq,
                terminal=terminal,
                task_created=task_created,
            )
            for sib in siblings
            if str(sib.id) not in dependents
        )

    @staticmethod
    def _lane_context(task: dict[str, Any]) -> tuple[Any, Any, Any, Any] | None:
        """(parent_id, sequence, owner, created_at) when the lane barrier
        applies to ``task``, else ``None``: only ``code`` leaves with a
        parent, a sequence, and an assignee (or claimant) are lane-ordered."""
        if str(task.get("task_type") or "") != "code":
            return None
        parent_id = task.get("parent_task_id")
        seq = task.get("sequence")
        owner = task.get("assigned_to") or task.get("claimed_by")
        if not parent_id or seq is None or not owner:
            return None
        return parent_id, seq, owner, task.get("created_at")

    @staticmethod
    def _is_earlier_live_lane_sibling(
        sib: Any,
        *,
        task_id: str,
        owner: str,
        seq: int,
        terminal: set[Any],
        task_created: Any = None,
    ) -> bool:
        """True if ``sib`` is an earlier non-terminal code task for ``owner``.

        Earlier = lower sequence, or an equal sequence created first (the
        wave-tie tiebreak mirroring the merge barrier).
        """
        if str(sib.id) == task_id:
            return False
        sib_type = getattr(sib, "task_type", None)
        sib_type_val = getattr(sib_type, "value", sib_type)
        sib_seq = getattr(sib, "sequence", 0) or 0
        earlier = sib_seq < seq or (
            sib_seq == seq
            and _created_before(getattr(sib, "created_at", None), task_created)
        )
        return (
            str(getattr(sib, "assigned_to", None)) == owner
            and str(sib_type_val) == "code"
            and earlier
            and getattr(sib, "status", None) not in terminal
        )


class StuckTaskEngine(_Base):
    """Tasks that have sat too long: stuck-task detection and its auto-block,
    per-role SLA escalation, and the active-time clocks both read (which
    discount recorded fleet downtime)."""

    def _active_age(
        self, ts: datetime | None, now: datetime | None = None
    ) -> timedelta:
        """Elapsed time since ``ts``, discounting recorded fleet downtime.

        Falls back to plain wall-clock elapsed when no ledger has loaded yet
        (today's behaviour). A missing ``ts`` is "no age" - every caller here
        already returns/skips on an unparseable timestamp before reaching
        this, so it only ever sees a real one in practice.
        """
        if ts is None:
            return timedelta(0)
        if self._uptime is not None:
            return self._uptime.active_elapsed(ts, now)
        return (now or datetime.now(UTC)) - ts

    @staticmethod
    def _coerce_heartbeat(value: Any) -> datetime | None:
        """Normalize ``last_heartbeat_at`` to an aware UTC datetime.

        The dispatcher reads tasks via the HTTP API, which serializes
        datetimes as ISO-8601 strings; direct service callers (and tests)
        may pass ``datetime`` objects. Anything else is treated as
        absent so a malformed value can't accidentally arm the gate.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return None

    async def _detect_stuck_tasks(self, client: httpx.AsyncClient) -> None:
        """
        Detect and auto-block tasks that are stuck.

        This is a proactive enforcement mechanism that finds tasks which
        have been pending without progress and have prerequisite issues.
        Runs every dispatcher cycle but only takes action on truly stuck tasks.

        CEO-approved timeout: 10 minutes
        """
        # CEO-approved threshold
        STUCK_THRESHOLD_MINUTES = 10

        tasks = await self._fetch_tasks(client, "pending")

        for task in tasks:
            # never auto-block a CEO-held artifact (release_manager / x_post /
            # video_post / ...); it sits PENDING by design until the CEO acts
            if _is_held_ceo_source(task):
                continue
            age = self._get_task_age(task)
            if age is None or age < timedelta(minutes=STUCK_THRESHOLD_MINUTES):
                continue

            issues = self._check_stuck_conditions(task)
            issues.extend(await self._check_dev_subtask_issue(client, task))

            if issues:
                task_id = task.get("id")
                if not task_id:
                    continue
                age_mins = int(age.total_seconds() // 60)
                reason = f"Task stuck for {age_mins} minutes: " + ", ".join(issues)
                await self._auto_block_task(client, task_id, reason)
                logger.warning(
                    "Auto-blocked stuck task",
                    task_id=task_id,
                    age_minutes=age_mins,
                    issues=issues,
                )

        # Per-(role, state) SLA check. Independent from the pending-task
        # sweep above — different states, different action (escalate vs
        # auto-block).
        await self._detect_sla_exceeded(client)

    async def _check_sla_for_task(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        status: str,
    ) -> None:
        """Check one task's SLA; escalate if exceeded. No-ops on missing data."""
        from roboco.enforcement.task_lifecycle import sla_seconds_for

        assigned = task.get("assigned_to")
        if not assigned:
            return
        assigned_slug = self._resolve_agent_slug(assigned)
        role = get_agent_role(assigned_slug or "")
        sla = sla_seconds_for(role, status)
        if sla is None:
            return
        age = self._time_in_state(task)
        if age is None or age.total_seconds() < sla:
            return
        task_id = task.get("id")
        if not task_id:
            return
        await self._escalate_sla_breach(
            client,
            _SlaBreach(
                task_id=str(task_id),
                role=role or "",
                status=status,
                age_seconds=int(age.total_seconds()),
                sla_seconds=sla,
            ),
        )

    async def _detect_sla_exceeded(self, client: httpx.AsyncClient) -> None:
        """Auto-escalate tasks that exceeded their per-role SLA.

        Uses ROLE_STATE_SLA_KEYS in enforcement/task_lifecycle.py. Dev tasks
        stuck in `in_progress`/`verifying`, QA tasks in `claimed`, doc tasks
        in `claimed`, and cell-PM tasks in `claimed` all get a soft bump so
        work doesn't silently rot.
        """
        from roboco.enforcement.task_lifecycle import ROLE_STATE_SLA_KEYS

        # Fetch each (role, state) combo we care about. One API call per
        # unique status so we don't fan out pointlessly.
        statuses = sorted({state for _, state in ROLE_STATE_SLA_KEYS})
        for status in statuses:
            try:
                tasks = await self._fetch_tasks(client, status)
            except Exception as e:
                logger.debug(
                    "SLA sweep fetch failed; skipping status",
                    status=status,
                    error=str(e),
                )
                continue
            for task in tasks:
                await self._check_sla_for_task(client, task, status)

    def _time_in_state(self, task: dict[str, Any]) -> timedelta | None:
        """Approximate active time in current state via task.updated_at.

        Not perfect — any field update bumps `updated_at`, not just status
        changes — but it's the coarse signal we have, and it under-counts
        (biased toward "agent is working") rather than over-counts, which
        matches the soft-SLA intent. Fleet downtime (a CEO pause/outage) is
        discounted via `_active_age`, so a SLA measured across a pause does
        not fire the instant the fleet wakes up.
        """
        updated_at = task.get("updated_at") or task.get("created_at")
        if not updated_at:
            return None
        try:
            if updated_at.endswith("Z"):
                updated_at = updated_at[:-1] + "+00:00"
            parsed = datetime.fromisoformat(updated_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return self._active_age(parsed)
        except (ValueError, TypeError):
            return None

    async def _escalate_sla_breach(
        self, client: httpx.AsyncClient, breach: _SlaBreach
    ) -> None:
        """Record SLA breach in dev_notes and nudge state forward.

        We don't force a state transition here — the MCP lifecycle rules are
        still authoritative. We log, annotate the task, and notify the
        assignee's escalation target. The agent's next spawn picks up the
        updated notes and usually self-escalates.
        """
        age_mins = breach.age_seconds // 60
        sla_mins = breach.sla_seconds // 60
        note = (
            f"[SLA] role={breach.role} status={breach.status} "
            f"time_in_state={age_mins}m sla={sla_mins}m. "
            "Escalating — agent should call escalate_up() "
            "or unclaim()."
        )
        try:
            await client.patch(
                f"{self._api_url}/tasks/{breach.task_id}",
                json={"dev_notes": note},
            )
            logger.warning(
                "SLA breach noted on task",
                task_id=breach.task_id,
                role=breach.role,
                status=breach.status,
                age_minutes=age_mins,
                sla_minutes=sla_mins,
            )
        except Exception as e:
            logger.debug(
                "SLA breach annotation failed",
                task_id=breach.task_id,
                error=str(e),
            )

    def _get_task_age(self, task: dict[str, Any]) -> timedelta | None:
        """Parse task created_at and return its active-time age (fleet
        downtime discounted via `_active_age`), or None if unparseable."""
        created_at_str = task.get("created_at")
        if not created_at_str:
            return None
        try:
            if created_at_str.endswith("Z"):
                created_at_str = created_at_str[:-1] + "+00:00"
            created_at = datetime.fromisoformat(created_at_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return self._active_age(created_at)
        except (ValueError, TypeError):
            return None

    def _check_stuck_conditions(self, task: dict[str, Any]) -> list[str]:
        """Check for common stuck conditions (git, description)."""
        issues: list[str] = []
        # A branch only exists once a task is claimed; a coordination task does
        # no git at all. A pending, never-claimed code task therefore has no
        # branch by design — flagging that here auto-blocked tasks before their
        # first dispatch. Only flag a missing branch when the task is in a
        # state where it should already own one.
        if not task.get("branch_name") and _branch_is_expected(task):
            issues.append("Task missing branch_name")
        description = (task.get("description") or "").strip()
        if len(description) < self._MIN_DESCRIPTION_LEN:
            issues.append("Empty or inadequate description")
        return issues

    async def _check_dev_subtask_issue(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> list[str]:
        """Check if complex dev task is missing subtasks."""
        from roboco.agents_config import get_agent_role

        assigned_to = task.get("assigned_to")
        if not assigned_to:
            return []

        agent_slug = self._resolve_agent_slug(assigned_to)
        if not agent_slug or get_agent_role(agent_slug) != "developer":
            return []

        complexity = task.get("estimated_complexity", "low")
        is_low_complexity = complexity not in ("medium", "high")
        if is_low_complexity or task.get("parent_task_id"):
            return []

        try:
            resp = await client.get(f"{self._api_url}/tasks/{task.get('id')}/subtasks")
            subtasks = resp.json() if resp.is_success else []
        except Exception:
            subtasks = []

        if not subtasks:
            return [f"{complexity} complexity task without subtasks"]
        return []
