"""Choreographer — composes existing services into intent-verb sequences.

This module has interface signatures only in Phase 0. Each verb's full
implementation lands in its respective phase (Phase 1: dev verbs, Phase 2:
QA verbs, Phase 3: doc + PM verbs, Phase 4: board verbs).

The signatures are stable contracts that the MCP servers and the
/api/v2/flow/* endpoints will call into. Phase 0 wires the dependency
injection so later phases just fill in the bodies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.services.gateway.envelope import Envelope


class TaskServiceProto(Protocol):
    """Protocol for task service dependency."""

    async def get(self, task_id: UUID) -> object: ...


class WorkSessionServiceProto(Protocol):
    """Protocol for work session service dependency."""

    async def has_unpushed_commits(self, work_session_id: UUID | None) -> bool: ...


class GitServiceProto(Protocol):
    """Protocol for git service dependency."""

    async def push(self, branch_name: str) -> None: ...

    async def create_pr(
        self, branch_name: str, *, parent: str, is_root_pr: bool
    ) -> dict: ...

    async def pr_merge(self, pr_number: int, *, target: str) -> dict: ...


class A2AServiceProto(Protocol):
    """Protocol for agent-to-agent service dependency."""

    async def send(
        self,
        *,
        from_agent: UUID,
        to_agent: UUID,
        skill: str,
        task_id: UUID,
        body: str,
    ) -> dict: ...


class JournalServiceProto(Protocol):
    """Protocol for journal service dependency."""

    async def has_reflect_for_task(self, agent_id: UUID, task_id: UUID) -> bool: ...

    async def has_decision_for_task(self, agent_id: UUID, task_id: UUID) -> bool: ...

    async def has_learning_for_task(self, agent_id: UUID, task_id: UUID) -> bool: ...


class Choreographer:
    """Composes existing services into intent-verb sequences.

    Constructor takes already-instantiated services (DI). Verb methods are
    async. Each returns a standardized Envelope. Implementations land
    progressively: see __init__ docstring.
    """

    def __init__(
        self,
        *,
        task: TaskServiceProto,
        work_session: WorkSessionServiceProto,
        git: GitServiceProto,
        a2a: A2AServiceProto,
        journal: JournalServiceProto,
    ) -> None:
        """Initialize Choreographer with service dependencies.

        Args:
            task: Service for task operations.
            work_session: Service for work session operations.
            git: Service for git operations.
            a2a: Service for agent-to-agent communication.
            journal: Service for agent journal operations.
        """
        self.task = task
        self.work_session = work_session
        self.git = git
        self.a2a = a2a
        self.journal = journal

    # --- Phase 1 (developer) verbs ---

    async def give_me_work(self, agent_id: UUID) -> Envelope:
        """Phase 1: get next work item for agent_id."""
        raise NotImplementedError("Phase 1")

    async def i_will_work_on(
        self, agent_id: UUID, task_id: UUID, plan: str | None = None
    ) -> Envelope:
        """Phase 1: claim task_id for agent_id with optional plan."""
        raise NotImplementedError("Phase 1")

    async def i_have_committed(self, agent_id: UUID, message: str) -> Envelope:
        """Phase 1: record commit message for agent_id."""
        raise NotImplementedError("Phase 1")

    async def i_am_done(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 1: mark task_id complete for agent_id with notes."""
        raise NotImplementedError("Phase 1")

    async def i_am_blocked(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Phase 1: block task_id for agent_id with reason."""
        raise NotImplementedError("Phase 1")

    async def i_am_idle(self, agent_id: UUID) -> Envelope:
        """Phase 1: report idle state for agent_id."""
        raise NotImplementedError("Phase 1")

    # --- Phase 2 (QA) verbs ---

    async def claim_review(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Phase 2: QA agent_id claims review of task_id."""
        raise NotImplementedError("Phase 2")

    async def pass_review(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 2: QA agent_id passes task_id with notes."""
        raise NotImplementedError("Phase 2")

    async def fail_review(
        self, agent_id: UUID, task_id: UUID, issues: list[str]
    ) -> Envelope:
        """Phase 2: QA agent_id fails task_id with issues."""
        raise NotImplementedError("Phase 2")

    # --- Phase 3 (documenter + PM) verbs ---

    async def claim_doc_task(self, agent_id: UUID, task_id: UUID) -> Envelope:
        """Phase 3: documenter agent_id claims documentation for task_id."""
        raise NotImplementedError("Phase 3")

    async def i_documented(
        self,
        agent_id: UUID,
        task_id: UUID,
        notes: str,
        files: list[str],
    ) -> Envelope:
        """Phase 3: documenter completes docs with notes and files."""
        raise NotImplementedError("Phase 3")

    async def triage(self, agent_id: UUID) -> Envelope:
        """Phase 3: PM agent_id triages next task in queue."""
        raise NotImplementedError("Phase 3")

    async def triage_all(self, agent_id: UUID) -> Envelope:
        """Phase 3: PM agent_id triages all waiting tasks."""
        raise NotImplementedError("Phase 3")

    async def unblock(
        self, agent_id: UUID, task_id: UUID, *, restore: bool = True
    ) -> Envelope:
        """Phase 3: PM agent_id unblocks task_id, optionally restoring to pending."""
        raise NotImplementedError("Phase 3")

    async def complete(self, agent_id: UUID, task_id: UUID, notes: str) -> Envelope:
        """Phase 3: PM agent_id completes task_id with notes."""
        raise NotImplementedError("Phase 3")

    async def escalate_up(self, agent_id: UUID, task_id: UUID, reason: str) -> Envelope:
        """Phase 3: PM agent_id escalates task_id up with reason."""
        raise NotImplementedError("Phase 3")

    # --- Phase 4 (board) verbs ---

    async def escalate_to_ceo(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> Envelope:
        """Phase 4: board agent_id escalates task_id to CEO with reason."""
        raise NotImplementedError("Phase 4")
