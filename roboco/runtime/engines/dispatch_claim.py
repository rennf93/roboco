"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: dispatch_claim)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from fastapi import status as http_status

from roboco.agents_config import (
    get_agent_role,
)
from roboco.config import settings
from roboco.foundation.identity import (
    is_human_only_role,
    role_for_slug_or_none,
)
from roboco.models.runtime import (
    SpawnGitContext,
)
from roboco.runtime.orchestrator import (
    AgentState,
    _agent_api_headers,
    _branch_is_expected,
    _created_before,
    _is_coordination_task,
    _is_held_ceo_source,
    _SlaBreach,
    logger,
)
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class DispatchClaimEngine(_Base):
    """Mixin holding the "dispatch_claim" methods moved out of AgentOrchestrator."""

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
                return f"Task {task_id} waiting for parent branch provisioning"

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

        return None  # All validations passed

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

    async def _auto_resume_paused_parent(
        self, client: httpx.AsyncClient, task_id: str
    ) -> None:
        """Resume a paused parent right before its PM is respawned for closure.

        A PM auto-pauses its owned parent on i_am_idle (by design,
        so the closure dispatcher knows to respawn it). Pre-gateway the
        parent was resumed at respawn so the PM landed actionable; the
        gateway refactor dropped that, so the respawned PM had to issue
        ``resume()`` itself — which weak models reliably fail,
        wedging the whole chain. Restore the auto-resume:
        paused -> in_progress before spawn so the PM can directly
        submit_up / complete / escalate. Best-effort; a resume failure
        must not block the spawn (the PM can still resume manually).
        """
        try:
            await client.patch(
                f"{self._api_url}/tasks/{task_id}",
                json={"status": "in_progress"},
            )
            logger.info(
                "Auto-resumed paused parent for PM closure respawn",
                task_id=task_id,
            )
        except Exception as e:
            logger.error(
                "Failed to auto-resume paused parent",
                task_id=task_id,
                error=str(e),
            )

    async def _auto_recover_blocked_parent(
        self, client: httpx.AsyncClient, task_id: str
    ) -> None:
        """Recover a blocked parent right before its PM is respawned for closure.

        Symmetric to ``_auto_resume_paused_parent``. The
        closure dispatcher only reaches this point once every descendant
        is terminal, so a parent still ``blocked`` here is an errant /
        stale block (e.g. a child's i_am_blocked propagated, or a PM
        blocked it and never unblocked) — the real dependency is already
        done. That resume path handled only ``paused`` parents, so a ``blocked``
        one wedged the whole chain forever: the respawned PM cannot
        submit_up / complete a blocked parent and must first ``unblock``
        it (needs journal:decision), which weak models never reliably do
        (a dogfood run wedged exactly here). ``blocked -> in_progress``
        is lifecycle-valid — it is precisely what ``unblock(restore=True)``
        performs. Best-effort; a failure must not block the spawn (the PM
        can still ``unblock`` manually).
        """
        try:
            await client.patch(
                f"{self._api_url}/tasks/{task_id}",
                json={"status": "in_progress"},
            )
            logger.info(
                "Auto-recovered blocked parent for PM closure respawn",
                task_id=task_id,
            )
        except Exception as e:
            logger.error(
                "Failed to auto-recover blocked parent",
                task_id=task_id,
                error=str(e),
            )

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

    def _select_agent_for_cell(self, cell: str, role: str) -> str | None:
        """
        Select the best available agent for a cell and role.

        Prefers agents that are not currently active.
        For developers, uses round-robin among candidates.
        """
        prefix_map = {"backend": "be", "frontend": "fe", "ux_ui": "ux"}
        prefix = prefix_map.get(cell)
        if not prefix:
            return None

        # Build candidate list based on role
        if role == "dev":
            candidates = [f"{prefix}-dev-1", f"{prefix}-dev-2"]
        elif role == "qa":
            candidates = [f"{prefix}-qa"]
        elif role == "doc":
            candidates = [f"{prefix}-doc"]
        elif role == "pm":
            candidates = [f"{prefix}-pm"]
        elif role == "pr_reviewer":
            candidates = [f"{prefix}-pr-reviewer", "cell-pr-reviewer-2"]
        else:
            return None

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

    @staticmethod
    def _all_descendants_terminal(descendants: list[dict[str, Any]]) -> bool:
        """Every descendant in a closure-complete state?"""
        return all(st.get("status") in ("completed", "cancelled") for st in descendants)

    @staticmethod
    def _already_promoted_for_closure(task: dict[str, Any]) -> bool:
        """Skip closure respawn when PR+status show task has moved past the PM.

        ``awaiting_pm_review`` is deliberately NOT in the skip set: it is the
        PM's own merge/review turn, and the session that submitted may be gone
        (restart, idle-reap) — the ``_is_agent_active`` check below already
        prevents double-spawning while a PM is genuinely alive."""
        return bool(
            task.get("pr_number")
            and task.get("status") in ("awaiting_ceo_approval", "completed")
        )

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

    def _is_recently_paused(self, task: dict[str, Any]) -> bool:
        """A paused task whose heartbeat is fresher than the closure debounce.

        Closes the ``i_am_idle`` vs closure-respawn race:
        ``i_am_idle`` auto-pauses in-flight tasks and then sets the agent
        IDLE. If the dispatcher ticks between those two writes it sees a
        paused parent and would spawn the closure PM against a session
        that is mid-shutdown. A fresh ``last_heartbeat_at`` (newer than
        ``settings.pm_closure_recently_paused_seconds``) is the signal that
        the agent was alive moments ago and a respawn now would race the
        existing session. Genuinely-stale paused tasks (or tasks with no
        heartbeat recorded) fall through and follow the regular closure path.

        This debounce is deliberately SHORT (a few dispatch ticks). It is
        NOT the reaper window (``_claim_heartbeat_ttl`` /
        ``stale_claim_reap_seconds``, 600s default and 1800s on the NAS):
        binding it there delayed every cell/main closure by up to 10-30
        minutes, because a paused parent's heartbeat reflects when the PM
        last *worked*, so a PM that worked right up to idle leaves a fresh
        heartbeat. The live-session case is already covered separately by
        the ``_is_agent_active`` check in ``_maybe_spawn_pm_closure``.
        """
        if task.get("status") != "paused":
            return False
        last_hb = self._coerce_heartbeat(task.get("last_heartbeat_at"))
        if last_hb is None:
            return False
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self._closure_recently_paused_ttl
        )
        return last_hb > cutoff

    def _closure_pm_for_team(self, team: str | None) -> str:
        """Pick the PM that owns closure for a given team."""
        if team in ("backend", "frontend", "ux_ui"):
            return self._TEAM_PM_MAP.get(team, "be-pm")
        return "main-pm"

    def _auto_submit_target(
        self, task: dict[str, Any], pm_slug: str
    ) -> tuple[str, str, str, str] | None:
        """(role, route, verb, pm_uuid) when this parent is auto-submittable.

        Unconditional — no kill-switch: the PM-turn cut IS the flow. None
        when the parent is branchless coordination (a MegaTask umbrella
        assembles no PR), the role has no submit verb, or no PM identity
        can be resolved; the caller falls back to the classic PM spawn.
        """
        role = get_agent_role(pm_slug) or ""
        pair = self._AUTO_SUBMIT_VERB_BY_ROLE.get(role)
        pm_uuid = str(task.get("assigned_to") or AGENT_UUIDS.get(pm_slug) or "")
        if (
            _is_coordination_task(task)
            or not task.get("branch_name")
            or not task.get("project_id")
            or pair is None
            or not pm_uuid
        ):
            return None
        return (role, pair[0], pair[1], pm_uuid)

    @staticmethod
    def _auto_submit_rejection_reason(body: Any) -> str:
        """Human-readable reason for a gate refusal, for the PM's prompt.

        ``message`` already folds ``remediate`` in for tracing_gap envelopes
        (see Envelope._missing_message); append remediate only when it adds
        information ``message`` doesn't already carry.
        """
        if not isinstance(body, dict):
            return f"unexpected gate response: {body!r}"
        error = body.get("error") or "rejected"
        message = body.get("message") or "no message"
        remediate = body.get("remediate")
        reason = f"{error}: {message}"
        if remediate and remediate not in message:
            reason = f"{reason} ({remediate})"
        return reason

    async def _try_auto_submit(
        self, client: httpx.AsyncClient, task: dict[str, Any], pm_slug: str
    ) -> tuple[bool, str | None]:
        """Submit an assembled, all-children-terminal parent to the PR gate
        WITHOUT spawning its PM — the turn's substance (freshness rebase,
        integrity check, PR open) is deterministic gate code, so the real
        submit verb is run through the internal API as the owning PM.

        Returns ``(True, None)`` when the gate accepted (the reviewer
        dispatch takes it from awaiting_pr_review). Returns ``(False,
        reason)`` on ANY refusal — branchless / unmapped role (``reason``
        is ``None``, nothing to report), a gate rejection
        (freshness/integrity/AC-coverage/race — the PM turn is then
        genuinely needed), or a transport error (``reason`` is a
        human-readable string) — the caller falls back to the classic PM
        closure spawn and threads ``reason`` into its prompt so the
        respawned PM isn't rediscovering the refusal blind.
        """
        target = self._auto_submit_target(task, pm_slug)
        if target is None:
            return False, None
        role, role_path, verb, pm_uuid = target
        task_id = str(task.get("id"))
        notes = (
            "Auto-submitted for gate review: every child task is terminal and "
            "the assembled branch is ready. Freshness and integrity are "
            "enforced by the submit gate itself; the in-path PR reviewer "
            "takes it from here."
        )
        try:
            resp = await client.post(
                f"{self._api_url}/v1/flow/{role_path}/{verb}",
                headers=_agent_api_headers(pm_uuid, role),
                json={"task_id": task_id, "notes": notes},
            )
            body = resp.json()
        except Exception as e:
            reason = f"auto-submit transport error: {e}"
            logger.warning(
                "Auto-submit transport failure; falling back to PM closure spawn",
                task_id=task_id,
                error=str(e),
            )
            return False, reason
        if not isinstance(body, dict) or body.get("error"):
            reason = self._auto_submit_rejection_reason(body)
            logger.info(
                "Auto-submit rejected by the gate; PM closure spawn proceeds",
                task_id=task_id,
                error=(body or {}).get("error") if isinstance(body, dict) else body,
                message=(body or {}).get("message") if isinstance(body, dict) else None,
            )
            return False, reason
        logger.info(
            "Assembled parent auto-submitted to the PR gate (PM turn skipped)",
            task_id=task_id,
            verb=verb,
            pm=pm_slug,
        )
        self._fire_audit(
            event_type="task.auto_submitted",
            agent_slug=pm_slug,
            task_id=task_id,
            details={"verb": verb, "auto": True},
        )
        self._mark_task_handled(task_id)
        return True, None

    async def _closure_handled_without_pm(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        task_id: str,
        pm_id: str,
    ) -> tuple[bool, str | None]:
        """Recover the parent's status, then try the submit turn cut.

        The parent auto-paused when its PM idled (by design) — resume it
        before anything else so whoever acts next (the auto-submit or the
        spawned PM) lands on an actionable in_progress parent; an errant
        `blocked` at closure is recovered symmetrically. Then the turn cut:
        an assembled parent whose children are all terminal is submitted to
        the PR gate system-side (True => the PM spawn is skipped); parents
        past the gate (awaiting_pm_review — the merge turn) always spawn.
        """
        parent_status = task.get("status")
        if parent_status == "paused":
            await self._auto_resume_paused_parent(client, task_id)
        elif parent_status == "blocked":
            await self._auto_recover_blocked_parent(client, task_id)
        if parent_status not in ("claimed", "in_progress", "paused"):
            return False, None
        return await self._try_auto_submit(client, task, pm_id)

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

    @staticmethod
    def _resolve_dev_owner_uuid(task: dict[str, Any]) -> str | None:
        """Pick the right owner UUID for dev dispatch based on status.

        Always falls back to ``claimed_by`` when ``assigned_to`` is missing, so
        a task left half-reaped (assigned_to nulled but still claimed) still
        dispatches to its rightful owner instead of going dormant — the
        orchestrator knows who to call even when one ownership field was cleared.
        """
        status = task.get("status")
        if status in ("claimed", "blocked"):
            return task.get("claimed_by") or task.get("assigned_to")
        return task.get("assigned_to") or task.get("claimed_by")

    @staticmethod
    def _is_hitl_blocked(task: dict[str, Any]) -> bool:
        """HITL-blocked tasks wait for human resolution; skip respawn."""
        return (
            task.get("status") == "blocked"
            and task.get("blocker_resolver_type") == "human"
        )

    async def _handle_dev_existing_owner(
        self, task: dict[str, Any], status: str, agent_slug: str
    ) -> None:
        """Respawn existing dev for needs_revision / in_progress / claimed."""
        # A `blocked` task is waiting for its blocker to clear (PM / dependency);
        # the owner has no legal move from `blocked`, so respawning it does
        # nothing but churn. It is revived only when unblocked back to
        # in_progress, or released to the pool (unclaim) for re-delegation.
        if status == "blocked":
            return
        if status in (
            "in_progress",
            "claimed",
        ) and not self._is_agent_active(agent_slug):
            logger.info(
                "Respawning agent for orphaned task",
                task_id=task["id"],
                agent=agent_slug,
                status=status,
            )
        await self._respawn_dev_if_inactive(task, agent_slug)

    @staticmethod
    def _dev_dispatch_role_matches(task: dict[str, Any], agent_slug: str) -> bool:
        """Return True if the assignee role matches the task's task_type.

        Dev dispatcher only spawns developer-role agents. A doc/qa task
        assigned to a dev (or vice versa) should be flagged, not silently
        spawned. Returns True when the type is unknown or the assignee role
        is unknown — the validation runs as a guard, not a strict gate, so
        an unknown classification doesn't block work that would otherwise
        proceed.
        """
        role = get_agent_role(agent_slug)
        if role == "unknown":
            return True
        task_type = task.get("task_type")
        if task_type == "documentation":
            return role == "documenter"
        # `code` / `research` / `planning` / `administrative` / `design` all
        # route through dev or PM; only the doc-task case is unambiguous.
        return role == "developer"

    async def _fetch_gate_ci_state(self, slug: str, pr_number: int) -> str | None:
        """Best-effort CI ``state`` lookup for the gate CI-pending check.

        Reuses ``GitService.get_pr_ci_status`` - the exact signal ``pr_pass``
        itself blocks on. Returns ``None`` on any lookup failure so the
        caller fails open (never strands a task over a transient error).
        """
        from roboco.db import get_db_context
        from roboco.services.git import GitService

        try:
            async with get_db_context() as db:
                status = await GitService(db).get_pr_ci_status(slug, pr_number)
        except Exception as exc:
            logger.warning(
                "gate CI status lookup failed",
                project_slug=slug,
                pr_number=pr_number,
                error=str(exc),
            )
            return None
        return status.get("state") if isinstance(status, dict) else None

    async def _gate_task_ci_pending(self, task: dict[str, Any]) -> bool:
        """True while the assembled PR's head-commit CI is still running or
        not yet scheduled.

        Without this, the dispatcher spawns a reviewer that immediately hits
        ``pr_pass``'s own CI-pending block and exits, respawning every tick
        until the respawn breaker trips and pages the CEO (live 2026-08: 7
        rejected ``pr_pass`` calls in ~12 min on each of two tasks). The
        lookup is cached per (slug, pr_number) for
        ``_GATE_CI_STATUS_CACHE_TTL_SECONDS``.

        Fails open (``False`` - spawn proceeds) on a missing project_slug/
        pr_number, a lookup error, or any non-pending state (including
        ``no_ci_configured``, ``error``, ``success``, ``failure``) - a
        reviewer must still be spawned for a repo with no CI configured, or
        to act on a genuinely-failing PR via ``pr_fail``. Only ``pending``
        and ``pending_not_scheduled`` hold the spawn back.
        """
        slug = task.get("project_slug")
        pr_number = task.get("pr_number")
        if not slug or not pr_number:
            return False
        cache_key = (str(slug), int(pr_number))
        now = time.monotonic()
        cached = self._gate_ci_status_cache.get(cache_key)
        ttl = self._GATE_CI_STATUS_CACHE_TTL_SECONDS
        if cached is not None and now - cached[0] < ttl:
            state = cached[1]
        else:
            state = await self._fetch_gate_ci_state(str(slug), int(pr_number))
            self._gate_ci_status_cache[cache_key] = (now, state)
        return state in ("pending", "pending_not_scheduled")

    def _gate_task_reviewer(self, task: dict[str, Any]) -> str | None:
        """Pick which reviewer to dispatch for one awaiting_pr_review task.

        A task with a REAL live claim (``active_claimant_id`` set - some
        reviewer ran ``claim_gate_review`` on it in an earlier tick) is
        always re-targeted at that SAME reviewer, whether or not it is
        currently free - never re-routed to a different one (e.g. the
        overflow ``cell-pr-reviewer-2``) just because that other reviewer
        happens to be free this tick. Observed live: fe-pr-reviewer claimed a
        gate task, its container exited between ticks, and the dispatcher
        kept spawning cell-pr-reviewer-2 onto the SAME task every tick since
        it recomputed "which cell reviewer is free" from scratch each time -
        each spawn's ``claim_gate_review`` was rejected outright
        ("already claimed by another reviewer"), burning spawns until the
        respawn breaker paged the CEO over a task that had already
        advanced. Deliberately NOT keyed on ``claimed_by``:
        ``_notify_pr_reviewer`` already sets ``claimed_by`` (alongside
        ``assigned_to``) to the PRIMARY reviewer at gate entry via a plain
        ``reassign``, before any real claim - pinning on that would read the
        entry assignment as "already claimed" and skip the overflow fallback
        even while the primary is busy, reinstating the serialization the
        overflow reviewer exists to prevent. Falls back to the normal
        team-based selection when there is no live claim yet, or the
        claimant can't be resolved to a known slug (stale/foreign id).
        """
        claimant = task.get("active_claimant_id")
        if claimant:
            resolved = self._resolve_agent_slug(str(claimant))
            if resolved != str(claimant):
                return resolved
        team = task.get("team")
        if team in ("backend", "frontend", "ux_ui"):
            return self._select_agent_for_cell(team, "pr_reviewer")
        return "pr-reviewer-1"

    async def _sync_gate_reviewer_assignment(
        self, task: dict[str, Any], reviewer: str
    ) -> None:
        """Keep ``assigned_to`` matching the reviewer actually being spawned.

        ``TaskService.pr_reviewer_for`` (via ``submit_up``/``submit_root``'s
        ``_notify_pr_reviewer``) already assigns the PRIMARY reviewer at
        gate entry, so a mismatch here only means that reviewer is busy and
        ``_select_agent_for_cell`` fell back to the overflow
        ``cell-pr-reviewer-2``, the one case this dispatcher's own
        selection can legitimately diverge from the entry assignment. Skips
        entirely (no DB round trip) when the task is still unassigned or
        already matches, so the common case costs nothing. Best-effort: a
        failure leaves the stale assignee, which ``claim_gate_review``'s
        role-only gate tolerates fine (the reviewer can still claim it).
        """
        current = task.get("assigned_to")
        if not current or self._resolve_agent_slug(str(current)) == reviewer:
            return
        reviewer_uuid = AGENT_UUIDS.get(reviewer)
        if reviewer_uuid is None:
            return
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.task import TaskService
        from roboco.utils.converters import InvalidIdentifierError, require_uuid

        try:
            task_id = require_uuid(str(task["id"]))
        except InvalidIdentifierError:
            return
        try:
            async with get_db_context() as db:
                await TaskService(db).reassign(task_id, UUID(reviewer_uuid))
        except Exception as exc:
            logger.warning(
                "gate reviewer overflow reassign failed",
                task_id=task.get("id"),
                reviewer=reviewer,
                error=str(exc),
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
        if str(task.get("task_type") or "") != "code":
            return False
        parent_id = task.get("parent_task_id")
        seq = task.get("sequence")
        owner = task.get("assigned_to") or task.get("claimed_by")
        if not parent_id or seq is None or not owner:
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
                "lane-order check failed; dispatching anyway",
                task_id=task.get("id"),
                error=str(exc),
            )
            return False
        task_id = str(task.get("id"))
        task_created = task.get("created_at")
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
        )

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

    def _claimed_task_needs_agent(self, task: dict[str, Any]) -> str | None:
        """Return the assignee slug to (re)spawn for an agentless claimed task.

        A task left CLAIMED/IN_PROGRESS with an assignee but no running
        container (e.g. a reassignment that didn't spawn) is invisibly stuck —
        only PENDING tasks get fresh dispatch, and the heartbeat reaper can't
        see it because the claim seeded a fresh heartbeat. Returns the assignee
        slug when the task has sat past the grace window with no active agent;
        ``None`` when it is healthy, too fresh, or HITL-blocked.
        """
        if self._is_hitl_blocked(task):
            return None
        owner_uuid = task.get("assigned_to") or task.get("claimed_by")
        if not owner_uuid:
            return None
        agent_slug = self._resolve_agent_slug(str(owner_uuid))
        # Human-only roles (CEO / prompter / secretary) are never containers —
        # there is no agent to respawn. Leave the task as-is for the human to
        # act on through the panel; do NOT release it to pending (that would
        # re-route a human-owned task to a PM). A stale slug (None role) is NOT
        # skipped here — a stale-slug claim SHOULD be released to pending so a
        # real agent can reclaim it (recovery, not spawning). See spawn_agent's
        # human-role guard for the structural backstop.
        if is_human_only_role(role_for_slug_or_none(agent_slug)):
            return None
        # The assignee is running, and on THIS task — healthy.
        instance = self._instances.get(agent_slug)
        if instance is not None and instance.state == AgentState.ACTIVE:
            return None
        # Grace window: a just-claimed task whose spawn is still in flight must
        # not be churned. _time_in_state under-counts (any update bumps it),
        # which biases toward "agent is working" — exactly the safe direction.
        age = self._time_in_state(task)
        grace = settings.claimed_no_agent_grace_seconds
        if age is None or age.total_seconds() < grace:
            return None
        return agent_slug

    def _audit_spawn_cooled(self) -> bool:
        """True when a scheduled sweep should be blocked.

        Blocks when ROBOCO_AUDIT_INTERVAL_SECONDS is 0 (disabled) or when the
        auditor was spawned within the interval window.
        """
        interval = settings.audit_interval_seconds
        if interval <= 0:
            return True
        last = self._last_audit_spawn_at
        if last is None:
            return False
        return (datetime.now(UTC) - last).total_seconds() < interval

    async def _next_unobserved_audit_alert(
        self, client: httpx.AsyncClient
    ) -> dict[str, Any] | None:
        """Newest ALERT targeting the auditor that the auditor has not acked.

        Fetches the auditor's OWN pending-ack view: ``GET /notifications`` authed
        as the auditor routes through ``list_for_agent``, which filters
        ``acked_by`` for the auditor — not the system-wide "not fully acked"
        view ``list_system_notifications`` returns. The auditor is read-only (no
        ack verb) and ``auditor_triage`` never acks, so under the system view a
        stale rework alert stayed pending forever (the CEO hadn't acked either)
        and the per-alert cooldown only paced a rotation that respawned the
        auditor every ~3 min. The auditor's own view excludes alerts it has
        already observed — even ones the CEO has not acked yet — so each alert
        is a one-shot, not a rotation source. Authed as the auditor (not the
        system identity) so the route selects the per-recipient view.
        """
        auditor_uuid = AGENT_UUIDS.get("auditor")
        if not auditor_uuid:
            return None
        try:
            resp = await client.get(
                f"{self._api_url}/notifications",
                params={"type_filter": "alert", "pending_ack_only": "true"},
                headers=_agent_api_headers(auditor_uuid, "auditor"),
            )
            if resp.status_code == http_status.HTTP_200_OK:
                items = resp.json().get("items", [])
                return items[0] if items else None
        except Exception as e:
            logger.error("Fetch unobserved audit alert failed", error=str(e))
        return None

    async def _ack_alert_as_auditor(
        self, client: httpx.AsyncClient, notification_id: str
    ) -> None:
        """Acknowledge an alert as the auditor on dispatch (HTTP, loop-safe).

        The spawn IS the auditor's observation; the auditor has no ack verb
        (read-only), so the orchestrator acks on its behalf via the API authed
        as the auditor. This is the terminal one-shot response that clears the
        alert from the auditor's pending view so the next tick cannot respawn
        on the same alert. Best-effort: a failed ack degrades to the per-alert
        cooldown guarding the next tick — it never blocks the dispatch.
        """
        auditor_uuid = AGENT_UUIDS.get("auditor")
        if not auditor_uuid:
            return
        try:
            resp = await client.post(
                f"{self._api_url}/notifications/{notification_id}/ack",
                headers=_agent_api_headers(auditor_uuid, "auditor"),
            )
            if resp.status_code != http_status.HTTP_200_OK:
                logger.warning(
                    "Ack audit alert as auditor failed",
                    notification_id=notification_id,
                    status=resp.status_code,
                )
        except Exception as e:
            logger.warning("Ack audit alert as auditor failed", error=str(e))

    async def _has_recent_delivery_activity(
        self,
        client: httpx.AsyncClient,
    ) -> bool:
        """True when delivery work has moved recently enough to warrant a sweep.

        Active delivery states always count as recent activity. Completed tasks
        only count if they were updated inside the audit interval window.
        """
        active_statuses = [
            "in_progress",
            "verifying",
            "awaiting_qa",
            "needs_revision",
            "awaiting_documentation",
            "awaiting_pr_review",
            "awaiting_pm_review",
            "awaiting_ceo_approval",
        ]
        if await self._fetch_tasks(client, active_statuses):
            return True

        interval = settings.audit_interval_seconds
        cutoff = datetime.now(UTC) - timedelta(seconds=interval)
        completed = await self._fetch_tasks(client, "completed")
        for task in completed:
            ts = self._coerce_heartbeat(task.get("updated_at"))
            if ts is not None and ts >= cutoff:
                return True
        return False

    async def _detect_stuck_tasks(self, client: httpx.AsyncClient) -> None:
        """
        Detect and auto-block tasks that are stuck.

        This is a proactive enforcement mechanism that finds tasks which
        have been pending without progress and have prerequisite issues.
        Runs every dispatcher cycle but only takes action on truly stuck tasks.

        CEO-approved timeout: 10 minutes
        """
        STUCK_THRESHOLD_MINUTES = 10  # CEO-approved threshold

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
