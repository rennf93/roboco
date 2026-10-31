"""Closing a parent whose children are done: the system-side submit that
replaces a PM spawn, and the checks before a PM closure spawn.

Engine mixins composed into AgentOrchestrator; split out of dispatch_claim.py
with every method body unchanged."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from roboco.agents_config import get_agent_role
from roboco.config import settings
from roboco.runtime.orchestrator import (
    _agent_api_headers,
    _is_coordination_task,
    logger,
)
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    import httpx

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class AutoSubmitEngine(_Base):
    """The PR-gate turn cut: recover the parent's status, then run its PM's
    submit_up / submit_root system-side, with the B42 closure-safety advisory
    and the refusal reason a fallback PM spawn is given."""

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

    async def _decisions_closure_advisory(self, task: dict[str, Any]) -> str:
        """B42 pm_closure_confidence: the advisory line for the PM closure
        prompt, or "" when off/shadow/below-floor/unreachable. The ask is
        logged inside the pilot on EVERY call while the pilot is ON - that
        log is the point; the injection is advisory. The gate chain stays
        deterministic."""
        if not settings.decisions_enabled:
            return ""
        try:
            return await self._closure_safety_verdict(task)
        except Exception as exc:
            logger.warning(
                "closure-safety advisory failed (best-effort)",
                task_id=str(task.get("id") or ""),
                error=str(exc),
            )
            return ""

    async def _closure_safety_verdict(self, task: dict[str, Any]) -> str:
        """The B42 closure-safety advisory line from the classifier ('' when
        none). The ask is logged inside the pilot on EVERY call while the
        pilot is ON - that log is the point; the injection is advisory."""
        from roboco.db import get_db_context
        from roboco.services.decisions import pilots_dispatch

        async with get_db_context() as db:
            _score, line = await pilots_dispatch.closure_safety(
                db,
                task_id=str(task.get("id") or ""),
                team=str(task.get("team") or "") or None,
                branch=str(task.get("branch_name") or "") or None,
                child_count=int(task.get("children_count") or 0),
            )
        return line or ""

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
        # B42 pm_closure_confidence: score closure safety alongside the gate
        # chain. The score is logged ALWAYS while the pilot is ON and its
        # advisory line is stashed for the PM closure prompt (advisory only:
        # the deterministic gate chain below is untouched and a classifier
        # never widens auto-submit).
        advisory_line = await self._decisions_closure_advisory(task)
        if advisory_line:
            self.__dict__.setdefault("_decisions_closure_advisories", {})[task_id] = (
                advisory_line
            )
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


class PMClosureEngine(_Base):
    """Whether a parent needs its PM for closure: every descendant terminal,
    not already promoted past the PM, not paused a moment ago, and which PM
    owns closure for the team."""

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
