"""Structural type for the composed TaskService, TYPE_CHECKING-only.

``TaskService`` is composed from mixins (``roboco/services/task.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
TaskService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``TaskServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``TaskSurface`` what ``TaskService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_task_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    import asyncio
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import AgentTable, TaskTable, WorkSessionTable
    from roboco.events import EventType
    from roboco.models.base import AgentRole, TaskStatus, Team
    from roboco.models.task import TaskCreateRequest
    from roboco.services.task import (
        GatewayAgentView,
        SoftBlockInfo,
        _ClaimFieldSnapshot,
        _LineageCutContext,
    )
    from roboco.services.uptime import UptimeLedger


class TaskAcceptanceCriteriaSurface(Protocol):
    """``TaskAcceptanceCriteria``'s members that other classes reach."""

    @staticmethod
    def _ac_texts_for(parent: TaskTable, ids: set[str]) -> list[str]: ...
    @staticmethod
    def _normalize_ac_refs(parent: TaskTable, refs: list[str] | None) -> set[str]: ...
    async def _parent_ac_ref_sets(
        self, task_id: UUID
    ) -> tuple[TaskTable, set[str], set[str], bool, set[str]] | None: ...


class TaskAdminOverrideSurface(Protocol):
    """``TaskAdminOverride``'s members that other classes reach."""

    async def _admin_out_of_blocked(
        self,
        task: TaskTable,
        from_status: str,
        new_status: TaskStatus,
        *,
        actor_id: str | UUID | None,
        actor_role: str | None,
    ) -> TaskTable | None: ...
    async def _apply_forced_override_effects(
        self,
        task: TaskTable,
        from_status: str,
        new_status: TaskStatus,
        actor_id: str | UUID | None,
        actor_role: str | None,
    ) -> None: ...
    def _refuse_terminal_resurrection(
        self, task: TaskTable, from_status: str, new_status: TaskStatus, force: bool
    ) -> None: ...


class TaskAgentQueriesSurface(Protocol):
    """``TaskAgentQueries``'s members that other classes reach."""

    async def held_task_blocking_claim(
        self, agent_id: UUID, task_id: UUID
    ) -> TaskTable | None: ...


class TaskBlockingSurface(Protocol):
    """``TaskBlocking``'s members that other classes reach."""

    async def _notify_unblock(
        self, task_id: UUID, restored_owner: Any, task_title: str | None = ...
    ) -> None: ...
    def _snapshot_pre_block(self, task: TaskTable) -> None: ...
    async def soft_block(
        self, task_id: UUID, info: SoftBlockInfo, agent_role: str | None = ...
    ) -> TaskTable | None: ...
    async def unblock(
        self, task_id: UUID, agent_role: str | None = ...
    ) -> TaskTable | None: ...


class TaskBranchesSurface(Protocol):
    """``TaskBranches``'s members that other classes reach."""

    async def _create_work_session_if_needed(
        self, task: TaskTable, agent_id: UUID, agent_role: str | None
    ) -> WorkSessionTable | None: ...
    async def _ensure_branch_for_task(self, task: TaskTable, agent_id: UUID) -> str: ...
    async def _resolve_parent_branch(self, task: TaskTable, project: Any) -> str: ...


class TaskCEOApprovalSurface(Protocol):
    """``TaskCEOApproval``'s members that other classes reach."""

    async def escalate_to_ceo(
        self,
        task_id: UUID,
        agent_role: str = ...,
        notes: str | None = ...,
        actor_agent_id: UUID | None = ...,
        allow_subtask_ceo_merge: bool = ...,
    ) -> TaskTable | None: ...


class TaskClaimSurface(Protocol):
    """``TaskClaim``'s members that other classes reach."""

    def _mark_agent_active_for_claim(
        self, agent: AgentTable | None, task_id: Any
    ) -> None: ...
    async def claim(
        self,
        task_id: UUID,
        agent_id: UUID,
        allow_reassign: bool = ...,
        *,
        provision: bool = ...,
    ) -> TaskTable | None: ...


class TaskClaimRulesSurface(Protocol):
    """``TaskClaimRules``'s members that other classes reach."""

    def _set_original_developer_context(
        self, task: TaskTable, agent: AgentTable | None
    ) -> None: ...
    async def _validate_claim_preconditions(
        self,
        task: TaskTable,
        agent: AgentTable | None,
        agent_id: UUID,
        allow_reassign: bool,
    ) -> bool: ...


class TaskClaimSequenceSurface(Protocol):
    """``TaskClaimSequence``'s members that other classes reach."""

    async def _claim_blocked_by_sequence(self, task: TaskTable) -> str | None: ...


class TaskCompletionSurface(Protocol):
    """``TaskCompletion``'s members that other classes reach."""

    async def _trigger_completion_hooks(
        self, task: TaskTable, agent_id: UUID | None
    ) -> None: ...
    async def complete(
        self,
        task_id: UUID,
        agent_id: UUID | None = ...,
        force_with_cancelled: bool = ...,
        justification: str | None = ...,
    ) -> TaskTable | None: ...


class TaskCompletionLearningsSurface(Protocol):
    """``TaskCompletionLearnings``'s members that other classes reach."""

    async def _extract_completion_learnings(
        self, task: TaskTable, agent_id: UUID | None
    ) -> None: ...


class TaskCreationSurface(Protocol):
    """``TaskCreation``'s members that other classes reach."""

    async def _task_has_cell_map(self, task: TaskTable) -> bool: ...
    @staticmethod
    def assert_batch_shape_intact(task: TaskTable) -> None: ...
    async def create(self, req: TaskCreateRequest) -> TaskTable: ...


class TaskDependenciesSurface(Protocol):
    """``TaskDependencies``'s members that other classes reach."""

    async def unmet_dependency_ids(self, dependency_ids: list[UUID]) -> list[UUID]: ...


class TaskDependentsRevivalSurface(Protocol):
    """``TaskDependentsRevival``'s members that other classes reach."""

    async def _emit_task_event(
        self, event_type: EventType, task_id: UUID, data: dict[str, Any] | None = ...
    ) -> None: ...
    async def _notify_completion(self, task: TaskTable, task_id: UUID) -> None: ...
    async def _unblock_dependents(self, completed_task_id: UUID) -> None: ...


class TaskDocsReviewSurface(Protocol):
    """``TaskDocsReview``'s members that other classes reach."""

    async def docs_complete(
        self, task_id: UUID, doc_notes: str | None = ...
    ) -> TaskTable | None: ...


class TaskEscalationSurface(Protocol):
    """``TaskEscalation``'s members that other classes reach."""

    async def _divert_owned_task_to_pool(
        self, task: TaskTable, *, note: str
    ) -> None: ...


class TaskLineageSurface(Protocol):
    """``TaskLineage``'s members that other classes reach."""

    async def _apply_dependency_lineage(
        self, task: TaskTable, ctx: _LineageCutContext
    ) -> None: ...
    async def _inherit_upstream_base(self, task: TaskTable, agent_id: UUID) -> None: ...


class TaskListQueriesSurface(Protocol):
    """``TaskListQueries``'s members that other classes reach."""

    def fifo_order(self, query: Any) -> Any: ...
    async def list_by_status(
        self, status: TaskStatus, team: Team | None = ...
    ) -> list[TaskTable]: ...


class TaskPMReviewSurface(Protocol):
    """``TaskPMReview``'s members that other classes reach."""

    async def _maybe_advance_to_pm_review(
        self, task: TaskTable, task_id: UUID
    ) -> None: ...
    @staticmethod
    def _record_completion_notes(task: TaskTable, notes: str | None) -> None: ...
    @staticmethod
    def _record_pr_review(
        task: TaskTable,
        *,
        summary: str | None,
        verdict: str,
        issues: list[str] | None = ...,
    ) -> None: ...
    async def submit_for_pm_review(
        self, task_id: UUID, agent_role: str = ..., notes: str | None = ...
    ) -> TaskTable | None: ...


class TaskProgressSurface(Protocol):
    """``TaskProgress``'s members that other classes reach."""

    async def add_progress(
        self, task_id: UUID, agent_id: UUID, message: str, percentage: int | None = ...
    ) -> TaskTable | None: ...


class TaskQAReviewSurface(Protocol):
    """``TaskQAReview``'s members that other classes reach."""

    async def fail_qa(
        self, task_id: UUID, notes: str, agent_role: str = ...
    ) -> TaskTable | None: ...
    async def pass_qa(
        self, task_id: UUID, notes: str | None = ..., agent_role: str = ...
    ) -> TaskTable | None: ...
    async def submit_for_qa(
        self, task_id: UUID, agent_role: str | None = ...
    ) -> TaskTable | None: ...
    async def submit_for_verification(
        self, task_id: UUID, agent_role: str | None = ...
    ) -> TaskTable | None: ...


class TaskRagIndexingSurface(Protocol):
    """``TaskRagIndexing``'s members that other classes reach."""

    async def _flip_docs_provenance_background(self, task_ids: list[str]) -> None: ...
    async def _index_blocker_background(
        self, task_id: UUID, task_team: Team | None, blocker_info: dict[str, str]
    ) -> None: ...
    async def _index_code_changes_background(
        self, task_id: UUID, commits: list[dict[str, Any]], project: str
    ) -> None: ...
    async def _index_decisions_background(
        self,
        task_id: UUID,
        task_title: str,
        task_team: Team | None,
        dev_notes: str | None,
        agent_id: UUID | None,
    ) -> None: ...
    async def _index_docs_background(
        self,
        task_id: UUID,
        documents: list[dict[str, Any]],
        actor_agent_id: UUID | None = ...,
    ) -> None: ...
    async def _index_lifecycle_event_background(
        self,
        task_id: UUID,
        event_type: str,
        task_title: str,
        task_team: Team | None,
        details: dict[str, Any] | None = ...,
    ) -> None: ...
    async def _index_qa_errors_background(
        self, task_id: UUID, task_title: str, task_team: Team | None, qa_notes: str
    ) -> None: ...
    async def _index_qa_review_background(
        self,
        task_id: UUID,
        original_developer: str | None,
        passed: bool,
        qa_notes: str,
        qa_agent_id: UUID | None,
    ) -> None: ...


class TaskReassignmentSurface(Protocol):
    """``TaskReassignment``'s members that other classes reach."""

    async def _retarget_agent_claim(
        self, old_agent_id: UUID | None, new_agent_id: UUID | None, task_id: UUID
    ) -> None: ...


class TaskRecordsSurface(Protocol):
    """``TaskRecords``'s members that other classes reach."""

    async def admin_set_status(
        self,
        task_id: UUID,
        new_status: TaskStatus,
        *,
        actor_id: str | UUID | None = ...,
        actor_role: str | None = ...,
        force: bool = ...,
    ) -> TaskTable | None: ...
    async def get(self, task_id: UUID) -> TaskTable | None: ...
    async def get_live_subtasks(self, parent_task_id: UUID) -> list[TaskTable]: ...
    async def update(self, task_id: UUID, **updates: Any) -> TaskTable | None: ...


class TaskReviewClaimsSurface(Protocol):
    """``TaskReviewClaims``'s members that other classes reach."""

    async def _qa_or_doc_claim(
        self,
        agent_id: UUID,
        task_id: UUID,
        expected_status: TaskStatus,
        enforce_competing_claimant: bool = ...,
    ) -> TaskTable | None: ...


class TaskRoleLookupSurface(Protocol):
    """``TaskRoleLookup``'s members that other classes reach."""

    async def _is_board_advisory_agent(self, agent_id: UUID) -> bool: ...
    async def _is_main_pm_agent(self, agent_id: UUID) -> bool: ...
    async def agent_for(self, agent_id: UUID) -> GatewayAgentView | None: ...
    async def cell_pm_for_team(self, team: Team) -> AgentTable | None: ...
    async def main_pm_agent(self) -> AgentTable | None: ...
    async def resolve_agent_id(self, agent_id_str: str) -> UUID: ...


class TaskRouteClaimsSurface(Protocol):
    """``TaskRouteClaims``'s members that other classes reach."""

    async def _load_task_or_raise(self, task_id: UUID) -> TaskTable: ...
    @staticmethod
    def _task_status_value(task: TaskTable) -> str: ...


class TaskRouteCompletionSurface(Protocol):
    """``TaskRouteCompletion``'s members that other classes reach."""

    def _format_id_list(self, ids: list[str]) -> str: ...


class TaskSurface(Protocol):
    """``TaskService``'s members that other classes reach."""

    MAX_ERR_IDS: ClassVar[int]
    MIN_NOTES_CHARS: ClassVar[int]
    _AGENT_NON_TERMINAL_STATUSES: ClassVar[set[TaskStatus]]
    _CLAIMABLE_STATUSES: ClassVar[set[TaskStatus]]
    _DEV_ACTIVE_STATUSES: ClassVar[set[TaskStatus]]
    _JOURNAL_CONTEXT_STATUSES: ClassVar[set[TaskStatus]]
    _MANAGEMENT_ROLES: frozenset[str]
    _REVIEW_HANDOFF_STATUSES: frozenset[TaskStatus]
    _SINGLE_ACTIVE_TASK_ROLES: ClassVar[frozenset[AgentRole]]
    _SUBSTITUTE_REASON_TO_STATUS: ClassVar[dict[str, TaskStatus]]

    async def _alert_auditor_of_rework(
        self,
        task: TaskTable,
        *,
        reason: str,
        actor_agent_id: UUID | None = ...,
        actor_role: str | None = ...,
    ) -> None: ...
    async def _alert_coroner_of_cancel(self, task: TaskTable) -> None: ...

    _background_tasks: ClassVar[set[asyncio.Task[None]]]

    @staticmethod
    def _clear_stale_stalled_marker(task: TaskTable) -> None: ...
    def _emit_escalation_audit(
        self, task: TaskTable, *, escalator_slug: str, target_slug: str
    ) -> None: ...
    def _emit_status_transition_audit(
        self,
        task: TaskTable,
        *,
        from_status: str,
        to_status: str,
        agent_role: str | None,
        audit_agent_id: str | UUID | None,
    ) -> None: ...
    async def _materialize_vault_note(self, task: TaskTable) -> None: ...
    def _validate_and_set_status(
        self,
        task: TaskTable,
        new_status: TaskStatus,
        agent_role: str | None = ...,
        audit_agent_id: str | UUID | None = ...,
        extra_allowed_roles: tuple[str, ...] = ...,
    ) -> None: ...


class TaskStalledSurface(Protocol):
    """``TaskStalled``'s members that other classes reach."""

    async def _load_uptime_ledger(self, now: datetime) -> UptimeLedger | None: ...


class TaskTerminalCleanupSurface(Protocol):
    """``TaskTerminalCleanup``'s members that other classes reach."""

    async def _abandon_work_session_for_task(
        self, task: TaskTable, reason: str
    ) -> None: ...
    async def _close_task_pr_best_effort(self, task: TaskTable) -> None: ...
    async def _close_work_session_for_task(
        self, task: TaskTable, reason: str
    ) -> None: ...
    async def _delete_task_branch_best_effort(self, task: TaskTable) -> None: ...
    async def _remove_task_worktree_on_terminal(self, task: TaskTable) -> None: ...


class TaskTreeSurface(Protocol):
    """``TaskTree``'s members that other classes reach."""

    async def get_all_descendants(self, task_id: UUID) -> list[TaskTable]: ...
    async def get_subtasks(self, parent_task_id: UUID) -> list[TaskTable]: ...


class TaskUnclaimSurface(Protocol):
    """``TaskUnclaim``'s members that other classes reach."""

    async def _clear_agent_current_task(
        self, agent_id: UUID | None, task_id: UUID
    ) -> None: ...


class TaskWorkStateSurface(Protocol):
    """``TaskWorkState``'s members that other classes reach."""

    async def pause(
        self, task_id: UUID, agent_role: str | None = ...
    ) -> TaskTable | None: ...


class TaskServiceSelf(
    TaskAcceptanceCriteriaSurface,
    TaskAdminOverrideSurface,
    TaskAgentQueriesSurface,
    TaskBlockingSurface,
    TaskBranchesSurface,
    TaskCEOApprovalSurface,
    TaskClaimSurface,
    TaskClaimRulesSurface,
    TaskClaimSequenceSurface,
    TaskCompletionSurface,
    TaskCompletionLearningsSurface,
    TaskCreationSurface,
    TaskDependenciesSurface,
    TaskDependentsRevivalSurface,
    TaskDocsReviewSurface,
    TaskEscalationSurface,
    TaskLineageSurface,
    TaskListQueriesSurface,
    TaskPMReviewSurface,
    TaskProgressSurface,
    TaskQAReviewSurface,
    TaskRagIndexingSurface,
    TaskReassignmentSurface,
    TaskRecordsSurface,
    TaskReviewClaimsSurface,
    TaskRoleLookupSurface,
    TaskRouteClaimsSurface,
    TaskRouteCompletionSurface,
    TaskSurface,
    TaskStalledSurface,
    TaskTerminalCleanupSurface,
    TaskTreeSurface,
    TaskUnclaimSurface,
    TaskWorkStateSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseService's and TaskService's instance attributes here, each class's
    members on its surface."""

    session: AsyncSession
    log: Any
    service_name: ClassVar[str]
    _pending_claim_snapshots: dict[UUID, _ClaimFieldSnapshot]
