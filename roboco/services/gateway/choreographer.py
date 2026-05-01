"""Choreographer — composes existing services into intent-verb sequences.

This module has interface signatures only in Phase 0. Each verb's full
implementation lands in its respective phase (Phase 1: dev verbs, Phase 2:
QA verbs, Phase 3: doc + PM verbs, Phase 4: board verbs).

The signatures are stable contracts that the MCP servers and the
/api/v2/flow/* endpoints will call into. Phase 0 wires the dependency
injection so later phases just fill in the bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from roboco.services.gateway.envelope import Envelope
from roboco.services.gateway.evidence_builder import (
    BriefingInputs,
    build_context_briefing,
)

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True)
class ChoreographerDeps:
    """All service dependencies bundled for Choreographer.

    Frozen dataclass to avoid PLR0913 (too many arguments) and to make
    dependency injection explicit. Each field is typed as Any in Phase 1 —
    per-service Protocol typing lands alongside verb implementations that
    actually exercise the methods.
    """

    task: Any
    work_session: Any
    git: Any
    a2a: Any
    journal: Any
    audit: Any
    evidence_repo: Any


class Choreographer:
    """Composes existing services into intent-verb sequences.

    Constructor takes a ``ChoreographerDeps`` bundle (DI). Verb methods are
    async. Each returns a standardized Envelope. Implementations land
    progressively: see __init__ docstring.

    Service deps are typed as ``Any`` in Phase 1 — per-service Protocol typing
    lands alongside the verb implementations that exercise the methods.
    """

    def __init__(self, deps: ChoreographerDeps) -> None:
        """Initialize Choreographer with bundled service dependencies.

        Args:
            deps: Frozen dataclass holding all 7 service dependencies.
        """
        self._deps = deps

    # --- Convenience properties so call-sites stay readable ---

    @property
    def task(self) -> Any:
        return self._deps.task

    @property
    def work_session(self) -> Any:
        return self._deps.work_session

    @property
    def git(self) -> Any:
        return self._deps.git

    @property
    def a2a(self) -> Any:
        return self._deps.a2a

    @property
    def journal(self) -> Any:
        return self._deps.journal

    @property
    def audit(self) -> Any:
        return self._deps.audit

    @property
    def evidence_repo(self) -> Any:
        return self._deps.evidence_repo

    # --- Phase 1 (developer) verbs ---

    async def give_me_work(self, agent_id: UUID) -> Envelope:
        """Return the agent's most-actionable task or signal idle."""
        assigned = await self._deps.task.list_assigned_for_agent(agent_id)
        if assigned:
            t = assigned[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call i_will_work_on(task_id='{t.id}', plan='<plan>') to start",
                context_briefing=await self._briefing_for(agent_id, t.id),
            )
        paused = await self._deps.task.list_paused_for_agent(agent_id)
        if paused:
            t = paused[0]
            return Envelope.ok(
                status=str(t.status),
                task_id=str(t.id),
                next=f"call i_will_work_on(task_id='{t.id}') to resume",
                context_briefing=await self._briefing_for(agent_id, t.id),
            )
        return Envelope.ok(
            status="idle",
            task_id=None,
            next="call i_am_idle() — no work available",
            context_briefing=await self._briefing_for(agent_id, None),
        )

    async def _briefing_for(
        self, agent_id: UUID, task_id: UUID | None
    ) -> dict[str, Any]:
        """Assemble context_briefing for agent_id, optionally scoped to task_id."""
        repo = self._deps.evidence_repo
        inputs = BriefingInputs(
            unread_a2a=await repo.list_unread_a2a(agent_id),
            unread_mentions=await repo.list_unread_mentions(agent_id),
            pending_notifications=await repo.list_pending_notifications(agent_id),
            task_metadata_gaps=(
                await repo.task_metadata_gaps(task_id) if task_id else []
            ),
            recent_team_activity=await repo.recent_team_activity(agent_id),
            blockers_in_my_lane=await repo.blockers_in_lane(agent_id),
        )
        return build_context_briefing(inputs)

    async def i_will_work_on(
        self, agent_id: UUID, task_id: UUID, plan: str | None = None
    ) -> Envelope:
        """Phase 1: claim task_id for agent_id with optional plan."""
        del agent_id, task_id, plan
        raise NotImplementedError("Phase 1")

    async def i_have_committed(self, agent_id: UUID, message: str) -> Envelope:
        """Phase 1: record commit message for agent_id."""
        del agent_id, message
        raise NotImplementedError("Phase 1")

    async def i_am_done(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 1: mark task_id complete for agent_id with notes."""
        del agent_id, task_id, notes
        raise NotImplementedError("Phase 1")

    async def i_am_blocked(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Phase 1: block task_id for agent_id with reason."""
        del agent_id, task_id, reason
        raise NotImplementedError("Phase 1")

    async def i_am_idle(self, agent_id: UUID) -> Envelope:
        """Phase 1: report idle state for agent_id."""
        del agent_id
        raise NotImplementedError("Phase 1")

    # --- Phase 2 (QA) verbs ---

    async def claim_review(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Phase 2: QA agent_id claims review of task_id."""
        del agent_id, task_id
        raise NotImplementedError("Phase 2")

    async def pass_review(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 2: QA agent_id passes task_id with notes."""
        del agent_id, task_id, notes
        raise NotImplementedError("Phase 2")

    async def fail_review(
        self, agent_id: UUID, task_id: UUID, issues: list[str]
    ) -> Envelope:
        """Phase 2: QA agent_id fails task_id with issues."""
        del agent_id, task_id, issues
        raise NotImplementedError("Phase 2")

    # --- Phase 3 (documenter + PM) verbs ---

    async def claim_doc_task(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Phase 3: documenter agent_id claims documentation for task_id."""
        del agent_id, task_id
        raise NotImplementedError("Phase 3")

    async def i_documented(
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
    ) -> Envelope:
        """Phase 3: documenter completes docs with notes and files."""
        del agent_id, task_id, notes, files
        raise NotImplementedError("Phase 3")

    async def triage(self, agent_id: UUID) -> Envelope:
        """Phase 3: PM agent_id triages next task in queue."""
        del agent_id
        raise NotImplementedError("Phase 3")

    async def triage_all(self, agent_id: UUID) -> Envelope:
        """Phase 3: PM agent_id triages all waiting tasks."""
        del agent_id
        raise NotImplementedError("Phase 3")

    async def unblock(
        self, agent_id: UUID, task_id: UUID, *, restore: bool = True
    ) -> Envelope:
        """Phase 3: PM agent_id unblocks task_id; restore=True (default) returns
        task to its pre_block_state. restore=False legacy: dumps to in_progress.
        """
        del agent_id, task_id, restore  # Phase 3 implementation will use these
        raise NotImplementedError("Phase 3")

    async def complete(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 3: PM agent_id completes task_id with notes."""
        del agent_id, task_id, notes
        raise NotImplementedError("Phase 3")

    async def escalate_up(self, agent_id: UUID, task_id: UUID, reason: str) -> Envelope:
        """Phase 3: PM agent_id escalates task_id up with reason."""
        del agent_id, task_id, reason
        raise NotImplementedError("Phase 3")

    # --- Phase 4 (board) verbs ---

    async def escalate_to_ceo(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Phase 4: board agent_id escalates task_id to CEO with reason."""
        del agent_id, task_id, reason
        raise NotImplementedError("Phase 4")
