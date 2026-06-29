"""
Task Service

Provides CRUD operations and lifecycle management for tasks.
Handles status transitions, assignments, and queries.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstanceState

from roboco.db.tables import (
    AgentTable,
    JournalEntryTable,
    JournalTable,
    ProjectTable,
    SessionTaskTable,
    TaskCellProjectTable,
    TaskTable,
    WorkSessionTable,
)
from roboco.enforcement import (
    GitContext,
    TaskLifecycleError,
    TaskOwnershipError,
    validate_git_requirements,
    validate_task_ownership,
    validate_task_transition,
)
from roboco.events import Event, EventType, get_event_bus
from roboco.foundation.policy.batch import (
    is_batch_umbrella,
    is_branchless_coordination,
    is_valid_batch_shape,
    main_pm_cannot_own_code,
)
from roboco.foundation.policy.content import markers
from roboco.foundation.policy.content.validators import ContentValidationError
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    BlockerResolverType,
    Complexity,
    JournalEntryType,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.models.permissions import AgentContext, TaskAction
from roboco.models.task import TaskCreateRequest
from roboco.models.work_session import WorkSessionCreate
from roboco.seeds.initial_data import AGENT_UUIDS
from roboco.services.base import (
    BaseService,
    ConflictError,
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.content_notes import apply_structured_note
from roboco.services.work_session import WorkSessionService
from roboco.utils.converters import require_uuid, to_python_uuid

if TYPE_CHECKING:
    from roboco.services.permissions import PermissionService

# UUID format constants for validation
_UUID_LENGTH = 36  # Standard UUID string length
_UUID_HYPHEN_COUNT = 4  # Number of hyphens in a UUID


_ROLE_CLAIM_STATUSES: dict[str, set[TaskStatus]] = {
    "qa": {TaskStatus.PENDING, TaskStatus.AWAITING_QA},
    "documenter": {TaskStatus.PENDING, TaskStatus.AWAITING_DOCUMENTATION},
    # PMs re-claim NEEDS_REVISION to recover a rejected coordination/assembled
    # task (pr_fail / qa_fail / ceo_reject lands it there): the spec
    # (lifecycle.CLAIM_RULES) grants it, and the runtime must match or the spec
    # gate passes i_will_plan on a needs_revision root while the composed
    # claim() returns None -> INVALID_STATE -> the PM respawn-loops on its own
    # rejected root, unable to plan or idle it. Parity is locked by
    # tests/unit/services/test_pm_claim_needs_revision.py.
    "cell_pm": {
        TaskStatus.PENDING,
        TaskStatus.NEEDS_REVISION,
        TaskStatus.AWAITING_PM_REVIEW,
    },
    "main_pm": {
        TaskStatus.PENDING,
        TaskStatus.NEEDS_REVISION,
        TaskStatus.AWAITING_PM_REVIEW,
    },
}


# Board / advisory roles review and advise; they never own or execute a
# descendant code task. Handing one to them (e.g. via the main_pm→product_owner
# escalation rung) strands the work: the board has no verb to claim, build, or
# complete it, and the dev's finished work deadlocks. A descendant code task
# that would otherwise land on one of these roles is instead released to the
# pool for a role-matched cell agent to reclaim.
_BOARD_ADVISORY_ROLES: frozenset[AgentRole] = frozenset(
    {AgentRole.PRODUCT_OWNER, AgentRole.HEAD_MARKETING, AgentRole.AUDITOR}
)


# Task types a CELL agent (developer / documenter / designer) must own and a
# board/advisory role has no verb to build or complete. CODE → developer,
# DOCUMENTATION → documenter, DESIGN → UX/design cell. The remaining types
# (PLANNING / RESEARCH / ADMINISTRATIVE) route to a PM, not a cell agent, and
# are not diverted here — the guard only fires for board/advisory targets.
_DESCENDANT_EXECUTABLE_TASK_TYPES: frozenset[str] = frozenset(
    {TaskType.CODE.value, TaskType.DOCUMENTATION.value, TaskType.DESIGN.value}
)

# Implementation-cell teams. A board/advisory role must never own a cell task —
# including the cell's own coordination/planning task (which carries a cell team
# but not a CODE/DOC/DESIGN type), so escalating one toward a board role is
# diverted to the cell pool instead of handing ownership up.
_CELL_TEAMS: frozenset[str] = frozenset({"backend", "frontend", "ux_ui"})


def _is_descendant_executable_task(task: TaskTable) -> bool:
    """True for a child task that does cell-executed work.

    A board/advisory role must never become the assignee of such a task: it has
    no verb to build, document, or complete it. ``task_type`` is a ``TaskType``
    enum; CODE / DOCUMENTATION / DESIGN are the members a cell agent (developer,
    documenter, designer) — not a board role — must own. ``parent_task_id``
    being set makes it a descendant (a root task can legitimately escalate up
    the chain — the CEO is its reviewer).
    """
    if task.parent_task_id is None:
        return False
    # task_type is a Mapped[TaskType] column; normalize to its string value so
    # the comparison is robust whether SQLAlchemy hands back the enum or its raw
    # string (the latter happens for detached/partially-hydrated rows in tests).
    task_type: Any = task.task_type
    type_value = task_type.value if isinstance(task_type, TaskType) else task_type
    return str(type_value) in _DESCENDANT_EXECUTABLE_TASK_TYPES


def _is_cell_team_task(task: TaskTable) -> bool:
    """True for a descendant task owned by an implementation cell.

    Complements ``_is_descendant_executable_task``: a cell's coordination /
    planning task carries a cell ``team`` but not a CODE/DOC/DESIGN ``task_type``,
    so the executable-type check alone would let it escalate onto a board role.
    """
    if task.parent_task_id is None:
        return False
    team_value = getattr(task.team, "value", task.team)
    return str(team_value) in _CELL_TEAMS


def _is_coordination_task(task: TaskTable) -> bool:
    """True for a Main-PM-owned coordination task (delivery root or batch
    root-subtask).

    A board/advisory role can no more OWN a coordination task than a cell task:
    it has no verb to delegate, submit, unblock, or complete it. The escalation
    chain points main-pm at product-owner, so a Main PM's ``i_am_blocked`` /
    escalate on its own coordination root used to reassign the WHOLE root to the
    board — which can only notify / triage / i_am_idle. The blocker dispatcher
    then respawned that board role forever to "resolve" a blocker it physically
    cannot unblock (a catch-22 that burned thousands of tool calls on a single
    root). Keyed on the ``main_pm`` team, which is set on every coordination root
    and MegaTask root-subtask, so it holds whether the task is a top-level root
    or parented under an umbrella. The two predicates above both require a
    descendant (``parent_task_id`` set), so a top-level coordination root slipped
    through them — this closes that gap.
    """
    team_value = getattr(task.team, "value", task.team)
    return str(team_value) == Team.MAIN_PM.value


def _board_cannot_own(task: TaskTable) -> bool:
    """True when a board/advisory role must NOT become the owner of ``task``.

    The single invariant behind the escalation / reassign / revival board guards:
    a board role (product-owner / head-marketing / auditor) has no verb to build,
    document, delegate, submit, unblock, or complete delivery work. It covers
    cell-executed descendants, a cell's own coordination/planning descendants,
    AND Main-PM coordination roots — every task shape a board role cannot drive.
    """
    return (
        _is_descendant_executable_task(task)
        or _is_cell_team_task(task)
        or _is_coordination_task(task)
    )


def _task_type_is_code(task_type: Any) -> bool:
    """True when ``task_type`` is ``TaskType.CODE`` (enum member or its value).

    Robust to the two shapes SQLAlchemy hands back: the ``TaskType`` enum or
    its raw ``"code"`` string (the latter on detached/partially-hydrated rows).
    Used by the Main-PM claim guard, which keys on the task's *type* (a Main PM
    cannot execute code) rather than the team+type combo the create / reassign
    guards use — claiming is owning, and a Main PM claiming a code task is a
    mismatch regardless of the task's team.
    """
    value = task_type.value if isinstance(task_type, TaskType) else task_type
    return str(value) == TaskType.CODE.value


def _is_terminal_task(task: TaskTable) -> bool:
    """True when ``task`` is in a terminal state (completed / cancelled).

    Terminal tasks must never be resurrected by a side-channel write
    (escalation → BLOCKED, reassign, dependency-revival). The lifecycle spec
    guards the gateway verbs, but the HTTP routes and direct service callers
    bypass it, so the single write primitives consult this too (F043). Robust
    to the enum-or-raw-string shapes SQLAlchemy hands back.
    """
    status = getattr(task, "status", None)
    value = status.value if isinstance(status, TaskStatus) else str(status)
    return value in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)


_PM_OWNED_CELL_TASK_TYPES: frozenset[str] = frozenset(
    {
        TaskType.PLANNING.value,
        TaskType.RESEARCH.value,
        TaskType.ADMINISTRATIVE.value,
        TaskType.DOCUMENTATION.value,
        TaskType.DESIGN.value,
    }
)


def _is_cell_pm_owned_task(task: TaskTable) -> bool:
    """True for a descendant cell-team task that must be owned by its cell PM.

    A cell team's planning / research / administrative / documentation / design
    work is not Main-PM work — the Main PM coordinates across cells, but each
    cell's own non-code work belongs to that cell's PM. Assigning such a child
    to main-pm deadlocks because the Main PM's escalation chain points up, not
    across to the cell PM who can actually decompose it. ``code`` is excluded
    because leaf code work is delegated by the cell PM to a developer, not
    owned by the cell PM itself.
    """
    if task.parent_task_id is None:
        return False
    team_value = getattr(task.team, "value", task.team)
    if str(team_value) not in _CELL_TEAMS:
        return False
    task_type_value = getattr(task.task_type, "value", task.task_type)
    return str(task_type_value) in _PM_OWNED_CELL_TASK_TYPES


# Notes fields (dev_notes, qa_notes, quick_context) are append-only —
# every revision cycle adds more. Cap total size so a task that cycles
# dozens of times doesn't grow into megabytes. When we exceed the cap,
# keep the latest entries and prepend a "[...truncated]" marker so the
# reader can tell something was dropped.
_MAX_NOTES_CHARS = 8000
_TRUNCATION_MARKER = "[...earlier notes truncated for size...]\n"


def _mark_subtask_complete(sub_tasks: list[dict[str, Any]], plan_step: str) -> bool:
    """Mark the matching sub_task ``completed`` in place.

    ``plan_step`` matches a sub_task by its id, its ``order``, or its
    1-based position. Returns True iff a sub_task matched (mutated in
    place); False when nothing matched.
    """
    ref = str(plan_step).strip()
    for i, st in enumerate(sub_tasks):
        if ref in {str(st.get("id")), str(st.get("order")), str(i + 1)}:
            st["completed"] = True
            return True
    return False


def _plan_subtasks(task: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(plan dict, sub_tasks list) for a task — safe on str/None plans."""
    plan = task.plan if isinstance(task.plan, dict) else {}
    sub_tasks = [st for st in (plan.get("sub_tasks") or []) if isinstance(st, dict)]
    return plan, sub_tasks


def _valid_step_refs(sub_tasks: list[dict[str, Any]]) -> list[str]:
    """Human-listable step refs (id, else order, else 1-based index)."""
    return [
        str(st.get("id") or st.get("order") or i + 1) for i, st in enumerate(sub_tasks)
    ]


def _derive_plan_pct(
    sub_tasks: list[dict[str, Any]], fallback: int | None
) -> int | None:
    """% = completed/total of the checklist (equal weight); ``fallback``
    only when there is no checklist."""
    if not sub_tasks:
        return fallback
    done = sum(1 for st in sub_tasks if st.get("completed"))
    return round(done / len(sub_tasks) * 100)


def _append_capped(existing: str | None, addition: str) -> str:
    """Append `addition` to `existing`, capped at _MAX_NOTES_CHARS.

    When the combined size exceeds the cap, drops OLDEST content (not
    newest — the newest entry is what the current agent/reviewer needs).
    """
    base = existing.strip() if existing else ""
    joined = f"{base}\n\n{addition}" if base else addition
    if len(joined) <= _MAX_NOTES_CHARS:
        return joined
    # Keep the newest, drop enough of the head to fit the marker + content.
    keep = _MAX_NOTES_CHARS - len(_TRUNCATION_MARKER)
    return _TRUNCATION_MARKER + joined[-keep:]


def _compose_review_body(summary: str | None, issues: list[str] | None) -> str:
    """Combine a PR-review summary with issue bullets into one body string."""
    body = (summary or "").strip()
    if not issues:
        return body
    bullets = "\n".join(f"- {i}" for i in issues if i and i.strip())
    if not bullets:
        return body
    return f"{body}\n\n{bullets}".strip() if body else bullets


@dataclass
class _CompletionSnapshot:
    """Fields copied off a TaskTable before its session detaches.

    Bundles the inputs to `_collect_completion_learnings` so we stay
    under PLR0913 without losing the explicit contract.
    """

    task_title: str | None
    started_at: Any
    completed_at: Any
    estimated_complexity: Any
    commits: list[Any]
    dev_notes: str | None
    qa_notes: str | None


@dataclass(frozen=True)
class SoftBlockInput:
    """Primitive blocker fields from the API layer.

    Route-layer DTO that bundles the raw string inputs so the service
    method signature stays under PLR0913 without leaking the API's
    Pydantic schema into the service. `resolver_type_raw` is coerced to
    BlockerResolverType inside the service.
    """

    blocker_type: str
    reason: str
    what_needed: str
    resolver_type_raw: str


@dataclass
class SoftBlockInfo:
    """Blocker metadata for `TaskService.soft_block`.

    Bundles the four blocker fields — reason, type, what's needed, and
    resolver type — so soft_block stays under PLR0913.
    """

    reason: str
    blocker_type: str
    what_needed: str
    resolver_type: BlockerResolverType | None = None


@dataclass(frozen=True)
class GatewayAgentView:
    """Read-only union of DB-backed and config-derived agent attributes.

    The Choreographer reads `agent.id`, `agent.role`, `agent.team`,
    `agent.skills`, and `agent.escalation_target` uniformly. The first
    three live on AgentTable; the last two come from `agents_config`. This
    view assembles them so the Choreographer can stay agnostic about
    storage location.
    """

    id: UUID
    role: str
    team: str | None
    escalation_target: str | None
    skills: list[dict[str, Any]]


def _default_claim_statuses(role: str | None) -> set[TaskStatus]:
    """Return the base (non-reassign) claim statuses for a role."""
    if role is None:
        return {TaskStatus.PENDING}
    if role in _ROLE_CLAIM_STATUSES:
        return set(_ROLE_CLAIM_STATUSES[role])
    # Developer and other roles
    # NEEDS_REVISION for when task is reassigned after QA rejection
    return {TaskStatus.PENDING, TaskStatus.NEEDS_REVISION}


def _get_valid_claim_statuses(
    agent: AgentTable | None,
    allow_reassign: bool,
) -> set[TaskStatus]:
    """
    Get valid task statuses an agent can claim based on their role.

    Role-based claiming:
    - QA: can only claim AWAITING_QA tasks
    - Documenter: can only claim AWAITING_DOCUMENTATION tasks
    - Developers/PMs: can claim PENDING (and CLAIMED if allow_reassign)

    Args:
        agent: The agent attempting to claim
        allow_reassign: Whether to allow claiming already-claimed tasks

    Returns:
        Set of valid TaskStatus values the agent can claim
    """
    role: str | None = None
    if agent and agent.role:
        role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)

    statuses = _default_claim_statuses(role)
    if allow_reassign:
        statuses.add(TaskStatus.CLAIMED)
    return statuses


def extract_original_developer(task: Any) -> str | None:
    """The original-developer UUID from a task's orchestration markers.

    Stored as the ``original_developer`` marker — migration 041 moved it out of
    the human ``quick_context`` blob into ``orchestration_markers``. Returns the
    value only when it is a well-formed UUID; anything else reads as absent.
    """
    dev_id = markers.get_original_developer(task)
    if not dev_id:
        return None
    if len(dev_id) == _UUID_LENGTH and dev_id.count("-") == _UUID_HYPHEN_COUNT:
        return dev_id
    return None


def _normalize_cell(value: object) -> str:
    """Normalize a team/cell token for comparison (e.g. backend, frontend, ux_ui)."""
    raw = str(getattr(value, "value", value)).strip().lower()
    return raw.replace("/", "_").replace("-", "_").replace(" ", "")


def extract_required_cells(task: Any) -> list[str]:
    """Cells the brief explicitly named, from the ``required_cells`` marker.

    The Main PM must create a subtask for each named cell (it may not silently
    collapse one into a neighbour — see commit 60de3499). Stored in
    ``orchestration_markers`` (migration 041). Absent → no constraint (the gate
    is inert). Returns normalized, de-duplicated cells in marker order.
    """
    seen: list[str] = []
    for tok in markers.get_required_cells(task):
        cell = _normalize_cell(tok)
        if cell and cell not in seen:
            seen.append(cell)
    return seen


# Review-task sources. The inbound-PR reviewer handles both external/fork PRs
# and (when enabled) internal org-repo PRs that bypassed the agent task-flow.
# Dispatch, dedup, the decision surface, the git-gate exemption, and supersede
# are identical for both, so they share this set.
PR_REVIEW_SOURCES = ("external_pr", "internal_pr")


# Source tag for a self-healing fix task: a PENDING task the self-heal loop opens
# when RoboCo's own CI regresses. It rides the normal lifecycle once the CEO
# Approve-&-Starts it; the loop itself never starts/approves/merges it.
SELF_HEAL_SOURCE = "self_heal"

# Source tag for a multi-repo CI-watch fix task: opened when an OPTED-IN
# project's CI regresses on its default branch. Like self_heal it rides the
# normal delivery lifecycle (+ PR-review gate) and is never auto-merged; unlike
# self_heal it can target any watched project, not just RoboCo's own repo.
CI_WATCH_SOURCE = "ci_watch"

# Source tag for a dependency-update task: opened by the dep-update bot when an
# opted-in project has dependency updates available. Rides the normal delivery
# lifecycle (+ PR-review gate) and is never auto-merged.
DEP_UPDATE_SOURCE = "dep_update"

# Source tag for a gated release proposal: opened by the release-manager engine
# when accumulated unreleased changes pass the threshold + the gate is green.
# Unlike the sources above it is NEVER dispatched — it is HELD for the CEO
# (confirmed_by_human=False) and acted on by the release routes + executor.
RELEASE_MANAGER_SOURCE = "release_manager"


def extract_self_heal_fingerprint(task: Any) -> str | None:
    """The self-heal dedupe fingerprint from a task's markers, or None.

    The per-signal dedupe key carried on a self-heal task (in
    ``orchestration_markers`` after migration 041), so the loop can tell a
    regression already has an open fix task.
    """
    return markers.get_self_heal_fingerprint(task)


def supersede_marker_line(task: Any) -> str:
    """The supersede marker value, or "" if none.

    ``pr={n} review={uuid}`` plus a ``closed=1`` token once the contributor PR is
    retired. Stored in ``orchestration_markers`` (migration 041); dedup and
    close-state checks parse this value (``needle in ...`` / ``"closed=1" in
    ....split()``) exactly as before.
    """
    return markers.get_external_pr_supersede(task) or ""


class TaskService(BaseService):
    """
    Service for managing tasks.

    Provides:
    - CRUD operations
    - Status transitions with validation
    - Assignment and claiming
    - Queries by team, status, assignee
    - Dependency management
    """

    service_name: ClassVar[str] = "task"
    _background_tasks: ClassVar[set[asyncio.Task[None]]] = set()

    # =========================================================================
    # STATUS TRANSITION HELPER
    # =========================================================================

    def _validate_and_set_status(
        self,
        task: TaskTable,
        new_status: TaskStatus,
        agent_role: str | None = None,
        audit_agent_id: str | UUID | None = None,
    ) -> None:
        """
        Validate and set task status with lifecycle enforcement.

        This is the single point of truth for status changes. All transitions
        are validated against VALID_TRANSITIONS, ROLE_RESTRICTED_TRANSITIONS,
        and git requirements.

        Args:
            task: The task to update
            new_status: Target status
            agent_role: Optional role for role-restricted transitions
            audit_agent_id: Optional explicit agent_id for the audit row.
                Required for transitions where the caller has already
                cleared ``task.claimed_by`` BEFORE invoking this method
                (e.g. ``submit_for_qa`` clears claimed_by so QA can claim).
                Without this, the audit writer reads the now-cleared value
                and stores ``agent_id=NULL`` on rows that should attribute
                the transition to the developer, QA, or PM who triggered it.

        Raises:
            TaskLifecycleError: If transition is invalid or role not permitted
            GitRequirementError: If git requirements not met
        """
        current = (
            task.status.value if isinstance(task.status, TaskStatus) else task.status
        )
        target = new_status.value if isinstance(new_status, TaskStatus) else new_status

        # Validate the transition (raises TaskLifecycleError if invalid)
        validate_task_transition(current, target, agent_role)

        # Validate git requirements (raises GitRequirementError if not met)
        git_ctx = GitContext(
            docs_complete=bool(task.docs_complete),
            pr_created=bool(task.pr_created),
            pr_number=task.pr_number,
            branch_name=str(task.branch_name) if task.branch_name else None,
            # A branchless coordination task does no git and never gets a branch
            # — exempt it from the branch gate so it can reach in_progress and
            # delegate. Two shapes qualify: a product fan-out root (product, no
            # repo) and a MegaTask umbrella (batch_id, top-level). All four are
            # plain columns (no lazy load).
            is_coordination=is_branchless_coordination(
                project_id=task.project_id,
                product_id=task.product_id,
                batch_id=task.batch_id,
                parent_task_id=task.parent_task_id,
                has_cell_projects=bool(task.cell_projects),
            ),
            # An external-PR review task reviews someone else's PR read-only —
            # no branch of its own — so it is branch-gate exempt.
            is_external_review=(getattr(task, "source", "manual") in PR_REVIEW_SOURCES),
            # A MegaTask umbrella assembles no PR of its own, so it is exempt
            # from the awaiting_pm_review->awaiting_ceo_approval pr_number gate.
            is_umbrella=is_batch_umbrella(
                batch_id=task.batch_id, parent_task_id=task.parent_task_id
            ),
        )
        validate_git_requirements(current, target, git_ctx)

        # Apply the status change
        task.status = new_status
        self.log.info(
            "Task status transition",
            task_id=str(task.id),
            from_status=current,
            to_status=target,
            agent_role=agent_role,
        )

        # Poke the orchestrator's dispatcher so it reacts immediately to
        # this transition. Without this, agents wait up to 30 seconds for
        # the next poll (10 minutes cumulative in the pathological case we
        # saw on be-dev-1 spawn). Uses lazy import + silent fallback so
        # code paths that don't have an orchestrator (tests, sync tools)
        # don't break.
        try:
            from roboco.api.deps import get_orchestrator

            get_orchestrator().trigger_dispatch()
        except Exception as e:
            # Swallow so task creation succeeds even when the
            # orchestrator singleton isn't wired (e.g. tests, CLI
            # scripts) — but log so a real regression is visible.
            self.log.debug(
                "Dispatch trigger skipped after task create",
                error=str(e),
            )

        self._emit_status_transition_audit(
            task,
            from_status=current,
            to_status=target,
            agent_role=agent_role,
            audit_agent_id=audit_agent_id,
        )

    def _emit_status_transition_audit(
        self,
        task: TaskTable,
        *,
        from_status: str,
        to_status: str,
        agent_role: str | None,
        audit_agent_id: str | UUID | None,
    ) -> None:
        """Emit the ``task.<status>`` audit row for a status transition.

        Extracted from ``_validate_and_set_status`` so transition paths that
        set ``task.status`` directly — e.g. ``apply_escalation``, which blocks a
        task without routing through the strict transition validator — record
        the same audit event. No status change may bypass the audit log.

        The row is written into the CALLER's session (``self.session.add``),
        so it commits / rolls back ATOMICALLY with the status transition — the
        audit journey can never diverge from real task state. This closes three
        facets of the fire-and-forget decoupling gap:

        * F061/F073 — the audit commit is no longer decoupled on a separate
          connection whose failures were swallowed; a committed transition now
          ALWAYS has its audit row (same transaction), and a persist failure
          fails the transition (fail-closed for the metric source of truth)
          instead of silently dropping the row.
        * F075 — a transition rolled back inside a verb savepoint
          (``_verb_runner`` wraps composed actions in ``begin_nested``) now
          rolls its audit row back too — no phantom ``task.<status>`` row for a
          transition that did not stick. (The claim-branch-failure rollback in
          ``_finalize_claim`` is one such site; the savepoint rollback handles
          the general case.)

        The ``revision_count`` rework counter is incremented synchronously
        here (the single chokepoint every transition funnels through).

        The explicit ``audit_agent_id`` (capture-before-mutate) wins: callers
        like ``submit_for_qa`` clear ``task.claimed_by`` before transitioning
        but still want the row attributed to the outgoing agent. Otherwise fall
        back to ``task.claimed_by``. A structurally-invalid id coerces to
        ``None`` (mirroring ``AuditService._coerce_uuid``) so the row still
        lands unattributed rather than FK-violating; a valid-but-deleted agent
        id is a real bug worth surfacing as a transition failure.
        """
        from roboco.db.tables import AuditLogTable

        # Rework counter: a bounce INTO needs_revision (not a re-entry) is one
        # rework cycle. Incremented at this single chokepoint — every transition
        # path funnels its audit through here exactly once — so the rework rate
        # is an O(1) column read. Synchronous (part of this unit of work).
        if (
            to_status == TaskStatus.NEEDS_REVISION.value
            and from_status != TaskStatus.NEEDS_REVISION.value
        ):
            task.revision_count = (task.revision_count or 0) + 1

        if audit_agent_id is not None:
            resolved_audit_agent_id: str | None = str(audit_agent_id)
        elif task.claimed_by is not None:
            resolved_audit_agent_id = str(task.claimed_by)
        else:
            resolved_audit_agent_id = None

        agent_uuid: UUID | None = None
        if resolved_audit_agent_id:
            try:
                agent_uuid = UUID(resolved_audit_agent_id)
            except (ValueError, AttributeError):
                agent_uuid = None

        details = {
            "from_status": from_status,
            "to_status": to_status,
            "agent_role": agent_role,
            "team": (
                task.team.value if hasattr(task.team, "value") else str(task.team)
            ),
        }
        for event_type in self._audit_events_for(to_status, agent_role):
            self.session.add(
                AuditLogTable(
                    event_type=event_type,
                    agent_id=agent_uuid,
                    target_type="task",
                    target_id=task.id,
                    severity="info",
                    details=dict(details),
                )
            )

    @staticmethod
    def _audit_events_for(to_status: str, agent_role: str | None) -> list[str]:
        """Audit event types to emit for a transition.

        Always the generic ``task.<status>``; plus a rejector-attributed
        ``task.qa_fail`` / ``task.pr_fail`` when a reviewer bounces a task to
        needs_revision — so the per-agent rework scorecard can charge the
        rejection to the QA / PR-reviewer who made it (the audit row carries
        their agent_id), not the developer who owns the task.
        """
        events = [f"task.{to_status}"]
        if to_status == TaskStatus.NEEDS_REVISION.value:
            if agent_role == "pr_reviewer":
                events.append("task.pr_fail")
            elif agent_role == "qa":
                events.append("task.qa_fail")
        return events

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def _validate_parent_depth(self, parent_task_id: UUID) -> None:
        """Enforce MAX_TASK_DEPTH at creation time.

        Walks up the parent chain counting ancestors. Raises ValidationError
        (a ServiceError the API/gateway translate to a clean 400 / remediation
        envelope) if adding a child under this parent would exceed
        MAX_TASK_DEPTH. Previously this raised a bare ValueError that escaped
        uncaught as a 500 (the message told the agent to create a sibling, but
        it never reached the agent as a handled error), and before that it was
        only enforced at branch-name generation time, so invalid hierarchies
        could be created and only fail later at claim.
        """
        from roboco.templates.git.constants import MAX_TASK_DEPTH

        current_id: UUID | None = parent_task_id
        depth = 0
        visited: set[str] = set()
        while current_id is not None:
            key = str(current_id)
            if key in visited:
                raise ValidationError(
                    f"Circular reference detected at {key} while validating depth",
                    field="parent_task_id",
                )
            visited.add(key)
            parent = await self.get(current_id)
            if parent is None:
                raise ValidationError(
                    f"Parent task {current_id} not found",
                    field="parent_task_id",
                )
            depth += 1
            if depth >= MAX_TASK_DEPTH:
                raise ValidationError(
                    f"Task hierarchy would exceed MAX_TASK_DEPTH={MAX_TASK_DEPTH}. "
                    "Create this work as a sibling of the deepest task instead "
                    "of a further nested subtask.",
                    field="parent_task_id",
                )
            parent_parent = parent.parent_task_id
            current_id = UUID(str(parent_parent)) if parent_parent else None

    @staticmethod
    def _require_target_or_umbrella(req: TaskCreateRequest) -> None:
        """Service-layer invariant (covers every create path — API, a2a, gateway).

        A task targets a single repo (``project_id``), fans out across cells via a
        product (``product_id``), or carries an ad-hoc per-cell map
        (``cell_projects``) — it must have exactly one, EXCEPT a MegaTask umbrella,
        which targets neither (it groups N root-subtasks that each carry their own
        project) and is branchless.
        """
        if req.project_id is not None or req.product_id is not None:
            return
        if req.cell_projects:
            return
        if is_batch_umbrella(batch_id=req.batch_id, parent_task_id=req.parent_task_id):
            return
        raise ValueError(
            "task needs a project_id (the repo it targets), a product_id "
            "(a cell->project map for a fan-out task), or cell_projects "
            "(an ad-hoc per-cell map for a multi-cell coordination root)"
        )

    async def _validate_batch_membership(self, req: TaskCreateRequest) -> None:
        """Guardrail: a ``batch_id`` is only valid on a well-formed MegaTask member.

        ``is_valid_batch_shape`` checks the structural shape; for a root-subtask we
        ALSO verify its parent is the batch umbrella (same ``batch_id``, top-level).
        Together they deny a stray ``batch_id`` on a normal task — which would
        otherwise spoof the umbrella's branch-gate / no-PR exemption or mislabel
        the task as part of a batch. No-op when ``batch_id`` is unset.
        """
        if req.batch_id is None:
            return
        if not is_valid_batch_shape(
            batch_id=req.batch_id,
            parent_task_id=req.parent_task_id,
            project_id=req.project_id,
            product_id=req.product_id,
            has_cell_projects=bool(req.cell_projects),
        ):
            raise ValueError(
                "batch_id is only valid on a MegaTask umbrella (targets neither "
                "project, product, nor a cell map) or a root-subtask (targets "
                "exactly one); refusing a stray batch_id on any other task."
            )
        if req.parent_task_id is None:
            return  # a well-formed umbrella
        parent = await self.get(req.parent_task_id)
        if (
            parent is None
            or parent.batch_id != req.batch_id
            or parent.parent_task_id is not None
        ):
            raise ValueError(
                "a MegaTask root-subtask's parent must be the batch umbrella "
                "(same batch_id, top-level)."
            )

    async def create(self, req: TaskCreateRequest) -> TaskTable:
        """
        Create a new task.

        Default status is PENDING. PM can pass status=BACKLOG when creating
        subtasks that need session setup before activation.
        """
        self._require_target_or_umbrella(req)
        await self._validate_batch_membership(req)

        if req.parent_task_id:
            await self._validate_parent_depth(req.parent_task_id)

        # Impossibility backstop: a Main PM coordinates — it never owns a code
        # task. ``main_pm`` + ``code`` on the same task is the structural
        # mismatch behind the 2026-06-27 MegaTask meltdown (a root-subtask the
        # git/PR/review layer treated as code while ownership treated it as
        # coordination — never reconciled, pr_fail looped). Intake
        # (create_task_from_draft) coerces code→planning, so this fires only on
        # a non-intake create (the HTTP route / a direct internal create) that
        # tries to persist the forbidden combo.
        if main_pm_cannot_own_code(team=req.team, task_type=req.task_type):
            raise ValidationError(
                "MAIN_PM_NO_CODE: A Main PM task coordinates — it does not"
                " execute code. Re-draft as `planning` with coordination-level"
                " acceptance criteria, or target a cell so a developer owns the"
                " code.",
                field="task_type",
            )

        # Stable per-criterion ids (1:1 with acceptance_criteria) so children can
        # reference specific parent criteria; generated here when not supplied.
        ac_ids = req.acceptance_criteria_ids or [
            uuid4().hex for _ in (req.acceptance_criteria or [])
        ]
        task = TaskTable(
            title=req.title,
            description=req.description,
            acceptance_criteria=req.acceptance_criteria,
            acceptance_criteria_ids=ac_ids,
            parent_ac_refs=req.parent_ac_refs,
            team=req.team,
            created_by=req.created_by,
            assigned_to=req.assigned_to,
            priority=req.priority,
            parent_task_id=req.parent_task_id,
            target_date=req.target_date,
            estimated_complexity=req.estimated_complexity,
            nature=req.nature,
            status=req.status if req.status else TaskStatus.PENDING,
            sequence=req.sequence,  # Task ordering within siblings
            dependency_ids=req.dependency_ids,  # Task IDs that must complete first
            # Sequenced batch intake collision surface
            batch_id=req.batch_id,
            intends_to_touch=req.intends_to_touch,
            adds_migration=req.adds_migration,
            touches_shared=req.touches_shared,
            # Git configuration (all tasks follow git workflow)
            task_type=req.task_type,
            project_id=req.project_id,
            product_id=req.product_id,
            # Prompter origin tracking
            source=req.source,
            confirmed_by_human=req.confirmed_by_human,
        )
        self.session.add(task)
        await self.session.flush()

        # Persist the ad-hoc per-cell project map (a MegaTask root-subtask
        # spanning multiple cells). Added as explicit rows with the flushed
        # task id rather than via the `cell_projects` collection (which is
        # unloaded on a freshly-built task — mutating it would trigger a lazy
        # load). Unique (task_id, team) is enforced by the table; a caller
        # passing duplicate teams raises IntegrityError here, which is the
        # right failure for a malformed request.
        for mapping in req.cell_projects:
            self.session.add(
                TaskCellProjectTable(
                    task_id=cast("UUID", task.id),
                    team=mapping.team,
                    project_id=mapping.project_id,
                )
            )

        # Inherit parent task's primary session for subtasks
        if req.parent_task_id:
            await self._inherit_parent_session(
                task_id=cast("UUID", task.id),
                parent_task_id=req.parent_task_id,
                created_by=req.created_by,
            )

        self.log.info(
            "Task created",
            task_id=str(task.id),
            title=req.title,
            team=req.team if isinstance(req.team, str) else req.team.value,
        )
        # NOTE: cell-team PM-owned invariants are enforced on the reassign path
        # (`reassign` / `reassign_active_claim` → `_resolve_cell_pm_redirect`)
        # — not at create. Direct task.assigned_to writes in the escalation
        # chain (e.g. the orchestrator's `_dispatch_revision_coordination_roots`)
        # bypass this; they are TODO-listed at the call sites.

        await self._attach_baseline_constraints(task)
        return task

    async def _attach_baseline_constraints(self, task: TaskTable) -> None:
        """Append the project's block-rule constraints to the task description.

        Server-derived backstop (flag-gated): every project task carries the
        hard conventions even if nothing upstream added them — the layer the
        design calls "can't be skipped". Idempotent and non-suppressible: it
        appends only baseline constraints not already present (dedup by exact
        string), so a second pass adds nothing AND an agent-authored
        ``## Constraints`` section can never suppress the mandatory baseline.
        Best-effort: a failure never blocks task creation.
        """
        baseline = await self._project_baseline_constraints(task)
        if not baseline:
            return
        existing = task.description or ""
        missing = [item for item in baseline if item not in existing]
        if not missing:
            return
        section = "## Constraints\n" + "\n".join(f"- {item}" for item in missing)
        task.description = f"{existing}\n\n{section}" if existing else section
        await self.session.flush()

    async def _project_baseline_constraints(self, task: TaskTable) -> list[str]:
        """The project's baseline constraints, or ``[]`` (flag-off / none / error).

        Imported lazily to avoid the task -> conventions -> git import chain.
        """
        from roboco.config import settings

        if not settings.conventions_enabled or task.project_id is None:
            return []

        from roboco.services.conventions import get_conventions_service
        from roboco.services.project import get_project_service

        project = await get_project_service(self.session).get(
            UUID(str(task.project_id))
        )
        if project is None:
            return []
        try:
            conv = get_conventions_service(self.session)
            workspace = await conv.resolve_workspace(project)
            return await conv.baseline_constraints(project, workspace=workspace)
        except Exception as exc:
            self.log.warning(
                "Baseline-constraints attach failed (non-fatal)",
                task_id=str(task.id),
                error=str(exc),
            )
            return []

    async def external_review_task_exists(
        self, project_id: UUID, pr_number: int, head_sha: str | None = None
    ) -> bool:
        """True if this REPO's external PR at this head commit is already reviewed.

        De-dupe key for inbound external-PR ingestion. The scope is the **repo**
        (``git_url``), not a single project: a monorepo registers several
        cell-projects on one repo (the poll already collapses to one canonical
        project per repo), and a review task may be re-pointed to a sibling
        project — so a project-scoped check would open a second review per
        cell-project / after a re-point. Re-review is driven by the PR's head
        commit, recorded as an ``external_pr_head=<sha>`` marker. So:

        - no review task for this PR on any project in this repo -> False;
        - a task already covers THIS ``head_sha`` -> True (nothing changed);
        - a legacy/markerless task exists, or ``head_sha`` is unknown -> True;
        - tasks exist but all cover OTHER SHAs -> False (new commits — re-review).
        """
        # Resolve every project sharing this project's repo (git_url) so the
        # dedupe spans the whole monorepo, not just the one project. An unknown
        # project_id falls back to itself (can't widen).
        git_url = (
            await self.session.execute(
                select(ProjectTable.git_url).where(ProjectTable.id == project_id)
            )
        ).scalar_one_or_none()
        if git_url:
            sibling_ids = (
                (
                    await self.session.execute(
                        select(ProjectTable.id).where(ProjectTable.git_url == git_url)
                    )
                )
                .scalars()
                .all()
            )
            scope_ids = list(sibling_ids) or [project_id]
        else:
            scope_ids = [project_id]
        result = await self.session.execute(
            select(TaskTable.orchestration_markers).where(
                TaskTable.project_id.in_(scope_ids),
                TaskTable.source.in_(PR_REVIEW_SOURCES),
                TaskTable.pr_number == pr_number,
            )
        )
        marker_rows = result.scalars().all()
        if not marker_rows:
            return False
        if not head_sha:
            return True
        for om in marker_rows:
            stored = (om or {}).get("external_pr_head")
            if not stored or stored == head_sha:
                return True
        return False

    async def ingest_external_pr(
        self,
        *,
        project_id: UUID,
        pr: dict[str, Any],
        created_by: UUID,
        team: Team,
        source: str = "external_pr",
    ) -> TaskTable | None:
        """Create one review task for a newly-seen inbound PR; ``None`` if it exists.

        ``pr`` is a normalized record from ``GitService.list_open_prs`` (number,
        url, title, head_sha). De-duped per ``(project_id, pr_number, head_sha)``
        across both review sources — re-polling an unchanged PR is skipped, but
        new commits (a new head SHA) open a fresh review (see
        ``external_review_task_exists``). ``source`` is ``external_pr`` (fork /
        untrusted) or ``internal_pr`` (an org-repo PR opened outside the agent
        task-flow). Both are CODE-typed with ``confirmed_by_human=False``. Caller
        commits.
        """
        pr_number = int(pr["number"])
        pr_url = str(pr.get("url") or "")
        pr_title = str(pr.get("title") or "")
        head_sha = str(pr.get("head_sha") or "")
        if await self.external_review_task_exists(project_id, pr_number, head_sha):
            return None
        kind = "internal" if source == "internal_pr" else "external"
        title = f"Review {kind} PR #{pr_number}: {pr_title}".strip()
        if source == "internal_pr":
            description = (
                f"An internal PR #{pr_number} ({pr_url}) was opened on an org repo "
                "outside the agent task-flow — no active task owns its branch. "
                "Review it adversarially and post a single, complete change-request "
                "with per-criterion findings."
            )
        else:
            description = (
                f"An external contributor opened PR #{pr_number} ({pr_url}).\n\n"
                "Review it adversarially and post a single, complete change-request "
                "with per-criterion findings. Do not fetch, check out, or run the "
                "contributor's code until a human has confirmed this PR."
            )
        req = TaskCreateRequest(
            title=title[:200],
            description=description,
            acceptance_criteria=[
                "Exactly one complete GitHub review is posted with per-criterion "
                "findings",
            ],
            team=team,
            created_by=created_by,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
            project_id=project_id,
            source=source,
            confirmed_by_human=False,
        )
        task = await self.create(req)
        task.pr_number = pr_number
        task.pr_url = pr_url
        # Record the reviewed head commit so a later push (new SHA) re-reviews,
        # while an unchanged PR is skipped (see external_review_task_exists).
        if head_sha:
            markers.set_external_pr_head(task, head_sha)
        await self.session.flush()
        return task

    async def active_task_owns_branch(self, branch_name: str, project_id: UUID) -> bool:
        """True if a non-terminal task on ``project_id`` already owns this branch.

        Lets the internal-PR reviewer skip the org's own in-flight integration
        PRs — those whose head branch a live task created via the agent
        task-flow (and which therefore already pass QA + PM review) — and review
        only org-repo PRs opened outside that flow.

        Scoped to the polled project: a branch in project A's repo can only be
        owned by a task whose ``project_id == A`` (each task branches in its
        own project's repo, including each root-subtask of a multi-repo
        MegaTask). An unscoped lookup would match the wrong project's task on a
        cross-project branch_name collision and false-skip project A's PR.
        """
        if not branch_name:
            return False
        result = await self.session.execute(
            select(TaskTable.id).where(
                TaskTable.branch_name == branch_name,
                TaskTable.project_id == project_id,
                TaskTable.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
        )
        return result.first() is not None

    async def list_open_self_heal_tasks(self) -> list[TaskTable]:
        """Non-terminal self-heal fix tasks — the dedupe + open-cap basis.

        A self-heal task is "open" until it reaches a terminal state. While one
        exists for a regression's fingerprint the loop must not originate a
        second, and the rolling open-task cap counts these.
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.source == SELF_HEAL_SOURCE,
                TaskTable.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
        )
        return list(result.scalars().all())

    async def list_open_ci_watch_tasks(
        self, git_url: str | None = None
    ) -> list[TaskTable]:
        """Non-terminal ci_watch fix tasks — the dedupe + open-cap basis.

        Optionally scoped to one repo by ``git_url``: a monorepo registers
        several cell-projects on ONE git_url, so CI-watch dedupe must key on the
        repo, not the project slug — otherwise a red monorepo would open one fix
        task per cell-project. While an open task exists for a repo the loop must
        not originate a second; the rolling open-task cap counts these.
        """
        stmt = select(TaskTable).where(
            TaskTable.source == CI_WATCH_SOURCE,
            TaskTable.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
        if git_url is not None:
            stmt = stmt.join(
                ProjectTable, TaskTable.project_id == ProjectTable.id
            ).where(ProjectTable.git_url == git_url)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_open_dep_update_tasks(
        self, git_url: str | None = None
    ) -> list[TaskTable]:
        """Non-terminal dep_update tasks — the dedupe + open-cap basis.

        Optionally scoped to one repo by ``git_url`` so a monorepo (several
        cell-projects, one git_url) gets at most one open dependency-update task,
        not one per cell-project. While an open task exists for a repo the bot
        must not originate a second; the rolling open-task cap counts these.
        """
        stmt = select(TaskTable).where(
            TaskTable.source == DEP_UPDATE_SOURCE,
            TaskTable.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
        )
        if git_url is not None:
            stmt = stmt.join(
                ProjectTable, TaskTable.project_id == ProjectTable.id
            ).where(ProjectTable.git_url == git_url)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_open_release_proposals(self) -> list[TaskTable]:
        """Non-terminal release-manager proposals — the one-open-at-a-time basis.

        The release manager holds at most one proposal open at a time; while one
        is awaiting the CEO the loop originates no second. A proposal leaves this
        set when the CEO approves (it completes) or rejects-and-cancels it.
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.source == RELEASE_MANAGER_SOURCE,
                TaskTable.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
        )
        return list(result.scalars().all())

    async def list_external_pr_reviews_awaiting_decision(self) -> list[TaskTable]:
        """Completed external-PR reviews still awaiting the CEO's decision.

        A review is awaiting decision once the reviewer has posted (status
        COMPLETED) and the CEO has neither superseded it (supersede sets
        ``confirmed_by_human=True``) nor dismissed it (a ``dismissed=1`` marker
        in quick_context). This backs the panel's PR-review decision queue.
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.source.in_(PR_REVIEW_SOURCES),
                TaskTable.status == TaskStatus.COMPLETED,
                TaskTable.confirmed_by_human.is_(False),
            )
        )
        return [t for t in result.scalars().all() if not markers.is_dismissed(t)]

    async def list_external_pr_reviews(self) -> list[TaskTable]:
        """Live external-PR reviews for the panel: in-flight PLUS awaiting-decision.

        Every ``source='external_pr'`` task that is not finished-and-decided:
        active reviews (the reviewer is still working — pending / claimed /
        in_progress / verifying / blocked / paused) AND completed reviews the CEO
        has neither superseded (``confirmed_by_human=True``) nor dismissed
        (``dismissed=1`` in quick_context). Cancelled tasks are excluded.

        Active reviews are surfaced on purpose: the reviewer posts its
        change-request to the PR itself, so the panel must show that a review is
        underway and link to that PR rather than going dark until the review
        finishes. Each task carries its ``status`` so the panel can tell
        "reviewing" apart from "awaiting your decision".
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.source.in_(PR_REVIEW_SOURCES),
                TaskTable.status != TaskStatus.CANCELLED,
                or_(
                    TaskTable.status != TaskStatus.COMPLETED,
                    TaskTable.confirmed_by_human.is_(False),
                ),
            )
        )
        return [t for t in result.scalars().all() if not markers.is_dismissed(t)]

    async def dismiss_external_pr_review(self, task_id: UUID) -> TaskTable | None:
        """CEO declines to act on a reviewed external PR — drop it from the queue.

        Appends a ``dismissed=1`` marker to quick_context so the review leaves
        ``list_external_pr_reviews_awaiting_decision``. Returns None if the task
        is missing or is not an external-PR review.
        """
        task = await self.get(task_id)
        if task is None or getattr(task, "source", "") not in PR_REVIEW_SOURCES:
            return None
        if not markers.is_dismissed(task):
            markers.mark_dismissed(task)
        await self.session.flush()
        return task

    async def pr_review_claim(
        self, reviewer_agent_id: UUID, task_id: UUID
    ) -> TaskTable | None:
        """Claim an external-PR review task: pending -> in_progress, no plan, no branch.

        The review is read-only and does no git of its own, so it must NOT route
        through claim()/start() — those would require a plan (start() returns
        None for a planless task) and auto-create + push a branch. Mirrors
        qa_claim's specialized-claim pattern (the verb body owns dispatch instead
        of the generic verb runner). The ``is_external_review`` branch-gate
        exemption (from source='external_pr') keeps claimed->in_progress valid
        with no branch. Returns None if the task is not PENDING (already taken).
        """
        task = await self.get(task_id)
        if task is None or task.status != TaskStatus.PENDING:
            return None
        task.assigned_to = cast("Any", reviewer_agent_id)
        task.claimed_by = cast("Any", reviewer_agent_id)
        self._validate_and_set_status(
            task, TaskStatus.CLAIMED, "pr_reviewer", audit_agent_id=reviewer_agent_id
        )
        self._validate_and_set_status(
            task,
            TaskStatus.IN_PROGRESS,
            "pr_reviewer",
            audit_agent_id=reviewer_agent_id,
        )
        # Seed the heartbeat at claim time — same invariant as _finalize_claim
        # and the QA/Doc claims. The reaper treats last_heartbeat_at IS NULL as
        # a stale claim, and for a GROK reviewer the idle-kill watchdog bypasses
        # the live-container skip on a NULL heartbeat: without this seed the
        # container is killed before the reviewer can post_pr_review, churning
        # the task back to pending on a respawn loop.
        task.last_heartbeat_at = datetime.now(UTC)
        await self.session.flush()
        self.log.info("External PR review claimed", task_id=str(task_id))
        return task

    async def complete_review(
        self, reviewer_agent_id: UUID, task_id: UUID, notes: str | None = None
    ) -> TaskTable | None:
        """Mark an external-PR review task complete (in_progress -> completed).

        The terminal for the pr_reviewer's ``post_pr_review`` verb: the review
        has been posted, so the review task is done. Attributed to the reviewer.
        Mirrors ``qa_pass``'s validated-transition shape; the ``pr_review_done``
        transition (in_progress -> completed, role pr_reviewer) is defined in the
        lifecycle spec and is git-gate exempt (the review task has no branch).
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if task.status != TaskStatus.IN_PROGRESS:
            return None
        self._record_pr_review(task, summary=notes, verdict="changes_requested")
        reviewer_id = to_python_uuid(task.claimed_by) or reviewer_agent_id
        task.assigned_to = None
        task.claimed_by = None
        task.active_claimant_id = cast("Any", None)
        self._validate_and_set_status(
            task,
            TaskStatus.COMPLETED,
            "pr_reviewer",
            audit_agent_id=reviewer_id,
        )
        await self.session.flush()
        self.log.info("External PR review complete", task_id=str(task_id))
        return task

    async def create_supersede_umbrella(
        self, *, review_task_id: UUID, branch_name: str, created_by: UUID
    ) -> TaskTable | None:
        """Create the supersede coordination task for a reviewed external PR.

        A ROOT planning task on the same repo (NOT parented to the review task —
        that would burn a MAX_TASK_DEPTH level and block the cell->dev
        decomposition), handed to Main PM to delegate the code work to a cell.
        The contributor PR number + review-task id are carried in
        ``quick_context`` (so close-on-land and dedup don't need a parent walk).
        ``branch_name`` is the roboco-owned branch already cut from the
        contributor's fork head, so the delegated code subtask builds on the
        contributor's commits (we never push to the fork). ``confirmed_by_human``
        is True — the CEO authorized this supersede. Returns None if the review
        task is missing or is not an external-PR review.
        """
        review = await self.get(review_task_id)
        if review is None or getattr(review, "source", "") not in PR_REVIEW_SOURCES:
            return None
        pr_number = review.pr_number
        req = TaskCreateRequest(
            title=f"Supersede external PR #{pr_number}: finish + harden it ourselves",
            description=(
                f"The org reviewed external PR #{pr_number} and is taking it over. "
                f"A roboco-owned branch ('{branch_name}') has been cut from the "
                "contributor's commits. Delegate the work to the appropriate cell "
                "to finish + harden it to our standards on that branch, open our "
                "own PR, and merge it; the contributor PR is closed and linked on "
                "land. Never push to the contributor's fork."
            ),
            acceptance_criteria=[
                "The contributor's PR is superseded by our own merged PR",
                "The work meets the project's quality standards",
            ],
            team=Team.MAIN_PM,
            created_by=created_by,
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
            project_id=cast("UUID", review.project_id),
            source="external_pr_supersede",
            confirmed_by_human=True,
        )
        umbrella = await self.create(req)
        # Carry the pre-cut fork branch so the delegated code subtask cuts off
        # the contributor's commits (via _resolve_base_branch's parent-branch
        # rule), not the default branch. The marker links back to the review +
        # contributor PR for dedup and close-on-land (no parent link needed).
        umbrella.branch_name = branch_name
        markers.set_external_pr_supersede(
            umbrella, f"pr={pr_number} review={review_task_id}"
        )
        await self.session.flush()
        self.log.info(
            "Supersede umbrella created",
            task_id=str(umbrella.id),
            review_task_id=str(review_task_id),
            pr_number=pr_number,
        )
        return umbrella

    async def find_supersede_umbrella(
        self, project_id: UUID, pr_number: int
    ) -> TaskTable | None:
        """The existing (non-cancelled) supersede umbrella for this PR, or None.

        Idempotency for the supersede trigger — a repeat CEO call must not cut a
        second branch or spawn a second umbrella. Matches the ``quick_context``
        marker exactly (``pr={n} review=`` won't false-match pr=50 for pr=5).
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.project_id == project_id,
                TaskTable.source == "external_pr_supersede",
                TaskTable.status != TaskStatus.CANCELLED,
            )
        )
        needle = f"pr={pr_number} review="
        for task in result.scalars().all():
            if needle in supersede_marker_line(task):
                return task
        return None

    async def supersede_umbrellas_pending_close(self) -> list[TaskTable]:
        """Landed supersede umbrellas whose contributor PR hasn't been closed yet.

        A supersede umbrella reaching COMPLETED is necessary but not sufficient
        proof that our replacement PR merged — the CEO can force-complete the
        root over a *cancelled* code subtask, in which case the team abandoned
        the work and the contributor's still-valid PR must NOT be retired. So we
        additionally require a non-cancelled descendant that landed a PR (see
        :meth:`_supersede_replacement_landed`). The ``closed=1`` token on the
        marker line makes close-on-land idempotent (closed only once).
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.source == "external_pr_supersede",
                TaskTable.status == TaskStatus.COMPLETED,
            )
        )
        pending: list[TaskTable] = []
        for task in result.scalars().all():
            if "closed=1" in supersede_marker_line(task).split():
                continue
            if not await self._supersede_replacement_landed(cast("UUID", task.id)):
                continue
            pending.append(task)
        return pending

    async def _supersede_replacement_landed(self, umbrella_id: UUID) -> bool:
        """True if a non-cancelled descendant of the umbrella landed a PR.

        Walks the umbrella's subtree (bounded by MAX_TASK_DEPTH) and returns
        True as soon as it finds a COMPLETED task carrying a ``pr_number`` — the
        team's merged replacement PR. Returns False when every code descendant
        was cancelled (force-completed umbrella), so close-on-land then leaves
        the contributor PR open.
        """
        frontier: list[UUID] = [umbrella_id]
        seen: set[UUID] = set()
        while frontier:
            result = await self.session.execute(
                select(TaskTable).where(TaskTable.parent_task_id.in_(frontier))
            )
            frontier = []
            for child in result.scalars().all():
                child_id = cast("UUID", child.id)
                if child_id in seen:
                    continue
                seen.add(child_id)
                if child.status == TaskStatus.COMPLETED and child.pr_number is not None:
                    return True
                frontier.append(child_id)
        return False

    async def mark_supersede_pr_closed(self, task_id: UUID) -> None:
        """Record that a landed supersede's contributor PR has been closed.

        Appends ``closed=1`` to the marker LINE (not the end of the whole
        multi-writer field) so the idempotency token stays anchored to the
        marker and survives appended CEO notes.
        """
        task = await self.get(task_id)
        if task is None:
            return
        current = markers.get_external_pr_supersede(task) or ""
        if "closed=1" not in current.split():
            markers.set_external_pr_supersede(task, f"{current} closed=1".strip())
        await self.session.flush()

    async def _inherit_parent_session(
        self,
        task_id: UUID,
        parent_task_id: UUID,
        created_by: UUID,
    ) -> SessionTaskTable | None:
        """
        Inherit the parent task's primary session for a subtask.

        When a subtask is created, it automatically joins the parent task's
        primary discussion session (if one exists). This enables context
        continuity across the task hierarchy.

        Args:
            task_id: The new subtask ID
            parent_task_id: The parent task ID
            created_by: PM who created the subtask

        Returns:
            Created link if parent had a primary session, None otherwise
        """
        # Find parent's primary session
        result = await self.session.execute(
            select(SessionTaskTable).where(
                SessionTaskTable.task_id == parent_task_id,
                SessionTaskTable.is_primary.is_(True),
            )
        )
        parent_link = result.scalar_one_or_none()

        if not parent_link:
            return None

        # Create link for subtask (not primary - parent owns the primary)
        link = SessionTaskTable(
            session_id=parent_link.session_id,
            task_id=task_id,
            is_primary=False,  # Subtasks don't become primary
            relationship_type=parent_link.relationship_type,
            added_by=created_by,
        )

        self.session.add(link)
        await self.session.flush()

        self.log.info(
            "Subtask inherited parent session",
            task_id=str(task_id),
            parent_task_id=str(parent_task_id),
            session_id=str(parent_link.session_id),
        )
        return link

    async def activate(self, task_id: UUID, agent_role: str = "cell_pm") -> TaskTable:
        """
        Activate a task from BACKLOG to PENDING status.

        This is a PM-only operation that transitions a task from setup
        phase to ready-for-work phase. The orchestrator will then spawn
        agents to work on it.

        REQUIRES: Task must have at least one linked session.

        Args:
            task_id: The task to activate
            agent_role: Role of the agent performing activation (must be PM)

        Returns:
            The activated task

        Raises:
            ValueError: If task not found, not in BACKLOG, or has no session
            TaskLifecycleError: If role is not allowed to activate
        """
        task = await self.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status != TaskStatus.BACKLOG:
            raise ValueError(
                f"Task {task_id} is not in BACKLOG status (current: {task.status})"
            )

        # Check if task has at least one linked session
        result = await self.session.execute(
            select(SessionTaskTable).where(SessionTaskTable.task_id == task_id).limit(1)
        )
        session_link = result.scalar_one_or_none()

        if not session_link:
            raise ValueError(
                f"Task {task_id} has no linked session. "
                "Create a session with open_session() "
                "before activating."
            )

        # ENFORCEMENT: a task needs a project (a repo) or a product (a
        # cell->project map for a fan-out task). A coordination task carries a
        # product and does no git itself.
        if not task.project_id and not task.product_id:
            raise ValueError(
                f"Cannot activate task '{task.title}' - no project or product "
                "set. A task needs a project (a repo) or a product (a "
                "cell->project map for a fan-out task)."
            )

        # NOTE: Git branch is auto-created on claim, not required at activation

        # Transition to PENDING with role enforcement
        # (PM-only per ROLE_RESTRICTED_TRANSITIONS)
        self._validate_and_set_status(task, TaskStatus.PENDING, agent_role)
        await self.session.flush()

        self.log.info(
            "Task activated",
            task_id=str(task_id),
            session_id=str(session_link.session_id),
        )
        return task

    async def _task_has_cell_map(self, task: TaskTable) -> bool:
        """True iff ``task`` carries an ad-hoc per-cell project map.

        The ``cell_projects`` relationship is ``lazy="selectin"`` — it loads on
        a task *query*, not on a freshly created/flushed instance. Reading the
        attribute on an unloaded instance fires a greenlet-less lazy SELECT that
        rolls the async transaction back (``MissingGreenlet`` then
        ``PendingRollbackError``), so we must NOT touch it blindly. We peek the
        instance state (no IO): if the map is already loaded (the claim path
        queries the task, so selectin fired) we read it directly; if it is
        genuinely unloaded we ask the DB with an awaited count instead. A
        non-ORM stub (unit-test MagicMock) isn't a real ``InstanceState``, so we
        fall back to its plain ``cell_projects`` attribute.
        """
        # ``sa_inspect`` is typed to return ``InstanceState`` for a mapped
        # ``TaskTable``, but unit-test stubs pass a ``MagicMock`` whose fake
        # inspector is NOT a real ``InstanceState`` — annotate ``object`` so the
        # non-InstanceState fallback stays reachable (and routes the stub to its
        # plain ``cell_projects`` attribute).
        state: object = sa_inspect(task)
        if isinstance(state, InstanceState):
            if "cell_projects" not in state.unloaded:
                return bool(task.cell_projects)
            stmt = (
                select(func.count())
                .select_from(TaskCellProjectTable)
                .where(TaskCellProjectTable.task_id == task.id)
            )
            return (await self.session.scalar(stmt) or 0) > 0
        return bool(task.cell_projects)

    async def _ensure_branch_for_task(
        self,
        task: TaskTable,
        agent_id: UUID,
    ) -> str:
        """Auto-create hierarchical branch for task. Raises on failure.

        Strategy:
        - If branch exists: return it
        - Coordination/fan-out task (carries a product, no repo of its own): no
          branch — it does no git work
        - MegaTask umbrella (batch_id, top-level): branchless by design (spans
          many projects, assembles no PR) — return "" (its root-subtasks branch)
        - If neither project nor product nor umbrella: raise (genuinely
          misconfigured)
        - Create NEW branch (hierarchical name built by build_branch_name)
        - Branch created from parent's branch (or default if root)

        Raises:
            ValueError: If branch cannot be created
        """
        if task.branch_name:
            return str(task.branch_name)

        if not task.project_id:
            # A coordination/fan-out task carries a product (a cell->project
            # map) or an ad-hoc cell_projects map but no repo of its own. Per
            # the CEO-locked branch model it is the Main-PM integration point:
            # it cuts feature/main_pm/{root} off master in EACH repo the map
            # spans, so cells branch off it (not off master) and only the CEO
            # merges the root into master. Only a task with neither project,
            # product, nor a cell map is misconfigured.
            if task.product_id or await self._task_has_cell_map(task):
                return await self._ensure_coordination_root_branches(task, agent_id)
            # A MegaTask umbrella is branchless by design: it spans many projects
            # (no single master to branch off) and assembles no PR of its own —
            # each root-subtask carries its own project/branch/PR. Return "" so
            # the claim path treats it as branchless rather than misconfigured.
            if is_batch_umbrella(
                batch_id=task.batch_id, parent_task_id=task.parent_task_id
            ):
                return ""
            raise ValueError(
                "Task requires a project_id (a repo), a product_id, or a "
                "cell_projects map (a cell->project map) to create a branch. "
                "Assign one before claiming."
            )

        return await self._auto_create_branch(task, agent_id)

    async def _find_ancestor_branch(self, task: TaskTable) -> str | None:
        """Walk up task hierarchy to find nearest ancestor with a branch.

        This handles cases where immediate parent doesn't have a branch
        (e.g., planning tasks created by PMs that don't need branches).

        Returns:
            Branch name of nearest ancestor, or None if no ancestor has one.
        """
        current_parent_id = task.parent_task_id
        visited: set[str] = set()  # Prevent infinite loops

        while current_parent_id:
            parent_id_str = str(current_parent_id)
            if parent_id_str in visited:
                self.log.warning(
                    "Circular reference detected in task hierarchy",
                    task_id=str(task.id),
                    cycle_at=parent_id_str,
                )
                break
            visited.add(parent_id_str)

            parent = await self.get(UUID(parent_id_str))
            if not parent:
                break

            if parent.branch_name:
                self.log.info(
                    "Found ancestor branch",
                    task_id=str(task.id),
                    ancestor_id=parent_id_str,
                    branch=str(parent.branch_name),
                )
                return str(parent.branch_name)

            current_parent_id = parent.parent_task_id

        return None

    async def project_default_branch_for_task(self, task: Any) -> str | None:
        """Default branch of the task's project, or None if it has no project.

        Used by the gateway merge-target resolver
        (:func:`roboco.services.gateway.merge_chain.resolve_parent_branch`) when
        a child task's parent is branchless (a coordination/fan-out parent that
        owns no repo): the real merge target is the child's own project default
        branch — the branch the child was actually cut from — not a derived ref
        the parent never created. Returns None for a task with no
        project_id (e.g. a coordination task itself).
        """
        project_id = getattr(task, "project_id", None)
        if project_id is None:
            return None
        result = await self.session.execute(
            select(ProjectTable.default_branch).where(
                ProjectTable.id == UUID(str(project_id))
            )
        )
        default_branch = result.scalar_one_or_none()
        return str(default_branch) if default_branch else None

    async def _resolve_parent_branch(self, task: TaskTable, project: Any) -> str:
        """Pick parent branch for a new task branch, fall back to project default."""
        parent_branch: str | None = None
        if task.parent_task_id:
            parent_branch = await self._find_ancestor_branch(task)

        if not parent_branch:
            default_branch = (
                str(project.default_branch) if project.default_branch else "master"
            )
            self.log.info(
                "No ancestor branch found, using project default",
                task_id=str(task.id),
                default_branch=default_branch,
            )
            parent_branch = default_branch
        return parent_branch

    @staticmethod
    def _resolve_team_dir(project: Any, task: TaskTable) -> str:
        """Pick the workspace team directory for a task's branch.

        Uses the TASK's team first (who is doing the work), then falls
        back to the project's assigned cell. This ensures a main-pm
        planning task gets a main_pm branch even if the project cell
        is backend.
        """
        project_cell = (
            project.assigned_cell.value
            if project.assigned_cell and hasattr(project.assigned_cell, "value")
            else str(project.assigned_cell)
            if project.assigned_cell
            else None
        )
        task_team = (
            task.team.value if task.team and hasattr(task.team, "value") else None
        )

        if project_cell == "fullstack":
            return f"{project.slug}/{task_team or 'cross'}"
        if task_team:
            return task_team
        if project_cell:
            return project_cell
        return "cross"

    async def _auto_create_branch(
        self,
        task: TaskTable,
        agent_id: UUID,
    ) -> str:
        """Create hierarchical branch for git task. Raises on failure.

        Branch naming (via build_branch_name):
        - Root: feature/team/ROOT_ID
        - Subtask: feature/team/ROOT_ID--SUB_ID
        - Sub-subtask: feature/team/ROOT_ID--SUB_ID--SUBSUB_ID

        Uses '--' separator for task hierarchy to avoid git ref conflicts.

        Parent branch resolution:
        - Subtask: uses parent task's branch_name
        - Root: uses project's default branch (main/master)

        Raises:
            ValueError: If branch cannot be created
        """
        from roboco.services.project import get_project_service

        project_service = get_project_service(self.session)
        project = await project_service.get(UUID(str(task.project_id)))
        if not project:
            raise ValueError(f"Project {task.project_id} not found")
        return await self._create_branch_in_project(task, agent_id, project)

    async def _create_branch_in_project(
        self,
        task: TaskTable,
        agent_id: UUID,
        project: Any,
    ) -> str:
        """Create the task's hierarchical branch inside one resolved repo.

        Split out of :meth:`_auto_create_branch` so a coordination root can cut
        the same ``feature/main_pm/{root}`` integration branch in EACH repo its
        product spans (monorepo: one call; multi-repo: one per repo). The branch
        name is hierarchy-derived so it is identical across repos; the physical
        branch is created in each.
        """
        from roboco.api.schemas.git import GitCreateBranchRequest
        from roboco.services.git import get_git_service

        git_service = get_git_service(self.session)

        parent_branch = await self._resolve_parent_branch(task, project)
        workspace = await git_service.get_workspace(project.slug, agent_id)
        team = self._resolve_team_dir(project, task)

        request = GitCreateBranchRequest(
            task_id=require_uuid(task.id),
            project_slug=project.slug,
            branch_type="feature",
            parent_branch=parent_branch,
        )

        try:
            branch_name, _ = await git_service.create_branch(workspace, team, request)
            task.branch_name = branch_name
            await self.session.flush()
        except Exception:
            # create_branch cuts a per-task worktree at
            # {workspace}/.worktrees/{task-short}/; tear it down on failure so a
            # claim retry doesn't collide with a stale worktree at that path
            # (F123). Best-effort, no-op if the worktree was never created.
            await self._remove_task_worktree(workspace, require_uuid(task.id))
            raise

        self.log.info(
            "Auto-created hierarchical branch",
            task_id=str(task.id),
            project_slug=project.slug,
            branch_name=branch_name,
            parent_branch=parent_branch or "default",
        )
        return branch_name

    async def _remove_task_worktree(self, clone_root: Path, task_id: UUID) -> None:
        """Best-effort removal of a task's per-task worktree (F123 rollback)."""
        from roboco.services.workspace import get_workspace_service

        worktree = clone_root / ".worktrees" / str(task_id)[:8]
        try:
            await get_workspace_service(self.session).remove_worktree(
                clone_root, worktree
            )
        except Exception:
            self.log.warning(
                "worktree cleanup on claim rollback failed",
                task_id=str(task_id),
            )

    async def _distinct_projects_for_task(self, task: TaskTable) -> list[UUID]:
        """The distinct projects a coordination root's map spans — one
        ``feature/main_pm/{root}`` integration branch each.

        A coordination root carries EITHER a product (``product_id`` → its
        ``product_projects`` map) OR an ad-hoc per-cell map (``cell_projects``).
        Both yield the same thing: the distinct project_ids the root spans,
        de-duped (a monorepo mapped across cells yields one project per distinct
        project, in team order). Empty when the root has neither (the umbrella,
        which never reaches here, or a not-yet-mapped product — caller returns
        ``""`` so delegation falls back to the parent's project).
        """
        from roboco.services.product import get_product_service

        if task.product_id is not None:
            return await get_product_service(self.session).distinct_project_ids(
                UUID(str(task.product_id))
            )
        # Ad-hoc cell_projects map: de-dupe by project_id, in team order (mirrors
        # ProductService.distinct_project_ids' ordering + dedup semantics).
        seen: dict[UUID, None] = {}
        for mapping in sorted(task.cell_projects, key=lambda m: m.team.value):
            seen.setdefault(UUID(str(mapping.project_id)), None)
        return list(seen)

    async def _ensure_coordination_root_branches(
        self,
        task: TaskTable,
        agent_id: UUID,
    ) -> str:
        """Cut the Main-PM integration branch in every repo the map spans.

        The coordination root carries a product (a cell->repo map) or an ad-hoc
        ``cell_projects`` map, but no project of its own. Per the CEO-locked model,
        the Main-PM root branches ``feature/main_pm/{root}`` OFF master in each
        distinct repo the map spans; cells then branch off it (via the
        parent-branch resolution) instead of off master, so cell work never
        targets master — only the CEO merges the root branch into master, per
        repo. Monorepo => one branch; multi-repo => N.

        Returns the shared branch name (identical across repos), or ``""`` when
        the map has no projects yet (delegation then falls back to the parent's
        project per the routing spec, and the root stays branchless).
        """
        from roboco.services.project import get_project_service

        project_service = get_project_service(self.session)

        project_ids = await self._distinct_projects_for_task(task)
        branch_name = ""
        for project_id in project_ids:
            project = await project_service.get(project_id)
            if project is None:
                continue
            branch_name = await self._create_branch_in_project(task, agent_id, project)
        return branch_name

    async def get(self, task_id: UUID) -> TaskTable | None:
        """Get a task by ID."""
        result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == task_id)
        )
        return result.scalar_one_or_none()

    async def record_section_note(
        self, task_id: UUID, content_type: str, payload: Any
    ) -> None:
        """Validate + persist a role's structured section note (dev_notes /
        quick_context / auditor_notes / …) through the ``apply_structured_note``
        chokepoint — the only sanctioned writer of the TEXT note columns.

        Raises ``ContentValidationError`` on a malformed payload (the gateway
        maps it to a remediation envelope) and ``LookupError`` if the task is
        gone. The request's route transaction commits the mutation.
        """
        task = await self.get(task_id)
        if task is None:
            raise LookupError(f"task not found: {task_id}")
        apply_structured_note(task, content_type, payload)
        await self.session.flush()

    @staticmethod
    def assert_batch_shape_intact(task: TaskTable) -> None:
        """A mutation must not break a task's MegaTask shape.

        The branchless predicates (is_batch_umbrella / is_branchless_coordination)
        trust the create-time ``is_valid_batch_shape`` invariant — so any update
        path that can change ``parent_task_id`` / ``project_id`` / ``product_id``
        must re-check it, else a PATCH could turn a root-subtask into an
        umbrella-shaped-but-targeted task and spoof the branch-gate / no-PR
        exemption. No-op for a non-batch task (``batch_id`` None/absent) — uses
        ``getattr`` so a partial-caller stub without the column is tolerated.
        """
        if getattr(task, "batch_id", None) is None:
            return
        if not is_valid_batch_shape(
            batch_id=task.batch_id,
            parent_task_id=getattr(task, "parent_task_id", None),
            project_id=getattr(task, "project_id", None),
            product_id=getattr(task, "product_id", None),
            has_cell_projects=bool(getattr(task, "cell_projects", None)),
        ):
            raise ValueError(
                "this update would break the task's MegaTask shape: a batch "
                "member must stay an umbrella (targets neither project, product, "
                "nor a cell map) or a root-subtask (exactly one target, parented)."
            )

    async def update(
        self,
        task_id: UUID,
        **updates: Any,
    ) -> TaskTable | None:
        """Update a task."""
        task = await self.get(task_id)
        if not task:
            return None

        for key, value in updates.items():
            if hasattr(task, key) and value is not None:
                setattr(task, key, value)

        self.assert_batch_shape_intact(task)
        await self.session.flush()

        self.log.info(
            "Task updated",
            task_id=str(task_id),
            updates=list(updates.keys()),
        )
        return task

    async def admin_set_status(
        self,
        task_id: UUID,
        new_status: TaskStatus,
        *,
        actor_id: str | UUID | None = None,
        actor_role: str | None = None,
    ) -> TaskTable | None:
        """Privileged override: set a task's status directly, always audited.

        Bypasses the strict transition validator so an operator can recover a
        task wedged in a state with no valid in-band move (e.g. a ``blocked``
        task whose work already merged out-of-band). The change is recorded in
        the audit log like any other transition — no status change may skip it.

        Taking a task OUT of ``blocked`` here (operator PATCH, or the
        orchestrator's auto-recover/auto-resume) restores the pre-block owner
        exactly as ``unblock(restore=True)`` does. Without this, a code task
        that a developer escalated to its cell PM re-enters ``pending``/
        ``in_progress`` still owned by that PM, and the dispatcher execute-spawns
        the PM on a dev task it cannot do (a respawn loop). The in-band escalate
        and block-down transitions are untouched — this fires only on
        re-activation, and only when a pre-block snapshot exists.
        """
        task = await self.get(task_id)
        if not task:
            return None
        from_status = (
            task.status.value
            if isinstance(task.status, TaskStatus)
            else str(task.status)
        )
        if (
            from_status == TaskStatus.BLOCKED.value
            and new_status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)
            and task.pre_block_assignee is not None
        ):
            return await self._apply_pre_block_restore(task, new_status)
        task.status = new_status
        await self.session.flush()
        self._emit_status_transition_audit(
            task,
            from_status=from_status,
            to_status=new_status.value,
            agent_role=actor_role,
            audit_agent_id=actor_id,
        )
        self.log.info(
            "Task status set via admin override",
            task_id=str(task_id),
            from_status=from_status,
            to_status=new_status.value,
            actor=str(actor_id) if actor_id else None,
        )
        return task

    async def delete(self, task_id: UUID) -> bool:
        """Delete a task and all its descendants."""
        task = await self.get(task_id)
        if not task:
            return False

        # Delete all descendants first (children, grandchildren, etc.)
        # Process in reverse order to delete leaves before parents
        descendants = await self.get_all_descendants(task_id)
        descendants.reverse()  # Delete deepest children first

        for descendant in descendants:
            await self.session.delete(descendant)

        if descendants:
            self.log.info(
                "Cascaded delete to descendants",
                task_id=str(task_id),
                deleted_count=len(descendants),
            )

        await self.session.delete(task)
        await self.session.flush()

        self.log.info("Task deleted", task_id=str(task_id))
        return True

    # =========================================================================
    # STATUS TRANSITIONS
    # =========================================================================

    def _validate_claim_status(
        self,
        task: TaskTable,
        agent: AgentTable | None,
        valid_statuses: set[TaskStatus],
    ) -> str | None:
        """
        Validate task status for claiming.

        Returns:
            Error message if invalid, None if valid
        """
        role_specific = {TaskStatus.AWAITING_QA, TaskStatus.AWAITING_DOCUMENTATION}
        if not agent and task.status in role_specific:
            return "role required for this status"
        if task.status not in valid_statuses:
            return "invalid status for role"
        return None

    # Management roles that can claim tasks from any team
    _MANAGEMENT_ROLES = frozenset(
        {"main_pm", "product_owner", "head_marketing", "auditor"}
    )

    def _validate_claim_team(
        self, task: TaskTable, agent: AgentTable | None
    ) -> str | None:
        """
        Validate agent belongs to task's team.

        Management roles (main_pm, product_owner, head_marketing, auditor)
        can claim tasks from any team.

        Returns:
            Error message if invalid, None if valid
        """
        if not agent or not task.team:
            return None

        # Get agent role as string
        agent_role = (
            agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        )

        # Management roles can claim any task
        if agent_role in self._MANAGEMENT_ROLES:
            return None

        # Regular agents must match team
        if agent.team != task.team:
            return "agent not in task's team"
        return None

    def _validate_not_self_review(
        self, task: TaskTable, agent: AgentTable | None, agent_id: UUID
    ) -> str | None:
        """Prevent QA/Documenter from claiming tasks they developed."""
        if not agent or not agent.role:
            return None
        role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        if role not in ("qa", "documenter"):
            return None
        original_dev = extract_original_developer(task)
        if original_dev and original_dev == str(agent_id):
            return "cannot review your own work (self-review)"
        return None

    def _set_original_developer_context(
        self, task: TaskTable, agent: AgentTable | None
    ) -> None:
        """Set original developer context for QA/Documenter claims.

        Important: We only store original_developer if it's a DIFFERENT agent.
        If PM assigned directly to QA/Documenter (no prior developer), we don't
        set original_developer to avoid blocking them with self-review check.
        """
        if not agent or not agent.role:
            return
        role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        if role not in ("qa", "documenter"):
            return
        if markers.get_original_developer(task):
            return

        # Only set original_developer if it's a DIFFERENT agent than the one claiming
        # This prevents blocking QA/Documenter when PM assigns directly to them
        if task.assigned_to and str(task.assigned_to) != str(agent.id):
            markers.set_original_developer(task, task.assigned_to)

    _CLAIMABLE_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.PENDING,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.AWAITING_PM_REVIEW,
    }

    async def _validate_claim_preconditions(
        self,
        task: TaskTable,
        agent: AgentTable | None,
        agent_id: UUID,
        allow_reassign: bool,
    ) -> bool:
        """Run all per-claim validators; log + return False on failure."""
        valid_statuses = _get_valid_claim_statuses(agent, allow_reassign)

        if error := self._validate_claim_status(task, agent, valid_statuses):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task.id))
            return False

        if error := self._validate_claim_team(task, agent):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task.id))
            return False

        # Prevent pre-assigned theft: if the task is already assigned to a
        # DIFFERENT agent and the claimant is not allowed to reassign, reject.
        # PMs with allow_reassign=True can still take over (used for handoffs).
        if (
            task.assigned_to is not None
            and str(task.assigned_to) != str(agent_id)
            and not allow_reassign
        ):
            self.log.warning(
                "Cannot claim task - assigned to another agent",
                task_id=str(task.id),
                assigned_to=str(task.assigned_to),
                requesting_agent=str(agent_id),
            )
            return False

        if error := self._validate_not_self_review(task, agent, agent_id):
            self.log.warning(f"Cannot claim task - {error}", task_id=str(task.id))
            return False
        return True

    async def _finalize_claim(
        self,
        task: TaskTable,
        agent: AgentTable | None,
        agent_id: UUID,
    ) -> None:
        """
        Apply claim-side-effects: status transition, branch + work session + context.

        On branch-creation failure, the claim fields are rolled back to
        their pre-claim values so a retry starts from a clean state. Without
        this, a partial failure leaves the task CLAIMED with branch_name=NULL,
        and `git checkout -b` on retry fails non-idempotent.
        """
        # Set context for QA/Documenter claims (only if not already set)
        self._set_original_developer_context(task, agent)

        # Snapshot for rollback on branch-creation failure.
        original_status = task.status
        original_assigned_to = task.assigned_to
        original_claimed_by = task.claimed_by
        original_claimed_at = task.claimed_at
        original_heartbeat = task.last_heartbeat_at
        original_claimant_id = task.active_claimant_id

        now = datetime.now(UTC)
        task.assigned_to = cast("Any", agent_id)
        task.claimed_by = cast("Any", agent_id)
        task.claimed_at = now
        # Seed the heartbeat at claim time. The reaper treats
        # last_heartbeat_at IS NULL as stale; without this seed, a
        # freshly-claimed task is reaped on the next dispatch tick
        # (~250ms) before the agent has a chance to call any verb that
        # would touch the heartbeat — leading to an unclaim/reclaim
        # tight loop hammering the orchestrator.
        task.last_heartbeat_at = now
        # Single-claimant invariant (alembic 006): claimant_lock.try_acquire
        # and trigger_filter.decide_spawn both branch on this column. Was
        # declared but never written; now wired so the
        # invariant is functional.
        task.active_claimant_id = cast("Any", agent_id)

        agent_role = agent.role.value if agent and agent.role else None
        if task.status in self._CLAIMABLE_STATUSES:
            self._validate_and_set_status(task, TaskStatus.CLAIMED, agent_role)

        await self.session.flush()

        if not task.branch_name:
            try:
                await self._ensure_branch_for_task(task, agent_id)
            except Exception:
                # Roll back claim fields so the task is reclaimable.
                task.status = original_status
                task.assigned_to = original_assigned_to
                task.claimed_by = original_claimed_by
                task.claimed_at = original_claimed_at
                task.last_heartbeat_at = original_heartbeat
                task.active_claimant_id = original_claimant_id
                await self.session.flush()
                # emit the reversal audit row so the journey doesn't diverge
                # from real state. The forward ``task.claimed`` audit row was
                # already committed on the audit service's own connection; this
                # rollback's flush reverts the task row but NOT that audit row.
                # Without a matching reversal row, downstream metrics
                # (cycle time, bottlenecks) reconstructed from ``task.<status>``
                # events would be corrupted.
                if original_status in self._CLAIMABLE_STATUSES:
                    self._emit_status_transition_audit(
                        task,
                        from_status=TaskStatus.CLAIMED.value,
                        to_status=(
                            original_status.value
                            if isinstance(original_status, TaskStatus)
                            else str(original_status)
                        ),
                        agent_role=agent_role,
                        audit_agent_id=agent_id,
                    )
                raise
            await self.session.refresh(task)

        await self._create_work_session_if_needed(task, agent_id, agent_role)

        bg_task = asyncio.create_task(self._inject_proactive_context(task, agent_id))
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

    async def acquire_claim_lock(self, agent_id: UUID) -> None:
        """Take a per-agent transaction-scoped advisory lock.

        ``claim``'s ``SELECT ... FOR UPDATE`` serializes concurrent claims of
        the SAME task, but the one-task-per-agent invariant is an agent-WIDE
        guard: two concurrent claims by the SAME agent on TWO DIFFERENT pending
        tasks lock different rows and both pass the already_active guard (each
        reads an empty in_progress set before either commits). A
        ``pg_advisory_xact_lock`` keyed by the agent serializes the WHOLE
        claim+guard+start critical section per agent — held until the outer
        request transaction commits, the second concurrent claim blocks until
        the first commits, then its guard read sees the first's committed
        in_progress task and is rejected.

        Transaction-scoped (not session-scoped) so it auto-releases on commit
        or rollback and cannot outlive the request. ``hashtextextended`` maps a
        UUID to a ``bigint`` key; a hash collision only causes benign false
        serialization (two different agents momentarily serializing), never a
        false negative — so it is not a correctness concern.
        """
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:aid AS text), 0))"
            ),
            {"aid": str(agent_id)},
        )

    async def acquire_delegate_parent_lock(self, parent_task_id: UUID) -> None:
        """Take a per-parent transaction-scoped advisory lock for delegate.

        The sibling-dedup guard (``_delegate_sibling_dedup_guard``) reads the
        parent's existing subtasks via an unlocked ``get_subtasks`` SELECT,
        then the verb body calls ``create_subtask`` (the write) — with no DB
        serialization between the two. Two concurrent ``delegate`` calls for
        the SAME parent (a PM re-delegating while a reaper re-dispatches, or
        two orchestrator ticks racing) each read a duplicate-free sibling set,
        each pass the guard, and each create a subtask → the parent gets the
        duplicate the guard exists to prevent. A ``pg_advisory_xact_lock``
        keyed by the parent serializes the WHOLE dedup-read -> create critical
        section per parent — held until the outer request transaction commits,
        the second concurrent same-parent delegate blocks until the first
        commits, then its dedup read sees the first's committed sibling and is
        rejected.

        Per-PARENT (not per-agent): a coordinator PM legitimately delegates many
        subtasks under one parent in quick succession and plans many roots in
        parallel — a per-agent lock would serialize all of a PM's delegates and
        regress coordinator concurrency. The per-parent lock serializes only
        same-parent delegates (the dedup invariant is per-parent) and leaves
        different parents untouched. Seed ``1`` keeps this in a disjoint key
        space from the per-agent claim lock (seed ``0``). Transaction-scoped so
        it auto-releases on commit or rollback and cannot outlive the request.
        ``hashtextextended`` maps a UUID to a ``bigint`` key; a hash collision
        only causes benign false serialization (two different parents
        momentarily serializing), never a false negative.
        """
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:pid AS text), 1))"
            ),
            {"pid": str(parent_task_id)},
        )

    async def acquire_task_lock(self, task_id: UUID) -> None:
        """Take a per-task transaction-scoped advisory lock.

        ``open_pr``'s idempotent re-entry guard reads ``t.pr_number`` from an
        unlocked fetch, then runs the PR-opening runner + emits the 70%
        "opened PR #N" milestone — a read-then-act with no DB serialization.
        Two concurrent ``open_pr`` calls on the SAME task (the
        alive-but-unresponsive respawn race) both fetch ``pr_number=None``,
        both pass the guard, both run the runner (``create_pr``'s GitHub 422
        'already exists' path ensures only one PR), and both emit the
        milestone → a double-emitted progress entry (the audit/milestone
        view double-counts one PR-open). A ``pg_advisory_xact_lock`` keyed by
        the task serializes the WHOLE fetch -> guard -> runner -> milestone
        critical section per task — held until the outer request transaction
        commits, the second concurrent same-task ``open_pr`` blocks until the
        first commits, its fetch then sees the first's committed ``pr_number``,
        the idempotent guard fires, and it short-circuits without re-emitting
        the milestone.

        Per-TASK (not per-agent): the single-active-task guard means a dev
        holds one task at a time, so concurrent ``open_pr`` on the SAME task is
        purely the respawn-race bug case — no legitimate concurrency is
        regressed. Seed ``2`` keeps this in a disjoint key space from the
        per-agent claim lock (seed ``0``) and the per-parent delegate lock
        (seed ``1``). Transaction-scoped so it auto-releases on commit or
        rollback and cannot outlive the request. ``hashtextextended`` maps a
        UUID to a ``bigint`` key; a hash collision only causes benign false
        serialization, never a false negative.
        """
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:tid AS text), 2))"
            ),
            {"tid": str(task_id)},
        )

    async def claim(
        self, task_id: UUID, agent_id: UUID, allow_reassign: bool = False
    ) -> TaskTable | None:
        """
        Claim a task for an agent.

        Role-based claiming:
        - Developers/PMs: can claim PENDING tasks
        - QA: can claim AWAITING_QA tasks
        - Documenters: can claim PENDING (direct assignment) or AWAITING_DOCUMENTATION

        Uses SELECT ... FOR UPDATE to serialize concurrent claim attempts on
        the same task, preventing last-write-wins races between two agents
        racing for the same pending task.
        """
        # Lock the task row for the duration of this transaction so concurrent
        # claim attempts serialize at the DB level. `with_for_update(of=...)`
        # scopes the lock to tasks only — otherwise, because TaskTable.project
        # is lazy="joined", SA emits an outer join and Postgres rejects
        # `FOR UPDATE` on the nullable side with FeatureNotSupportedError.
        lock_result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.id == task_id)
            .with_for_update(of=TaskTable)
        )
        task = lock_result.scalar_one_or_none()
        if not task:
            return None

        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if not await self._validate_claim_preconditions(
            task, agent, agent_id, allow_reassign
        ):
            return None

        await self._finalize_claim(task, agent, agent_id)
        return task

    async def _inject_proactive_context(self, task: TaskTable, agent_id: UUID) -> None:
        """Inject proactive knowledge context when task is claimed.

        Runs as a background task with its own DB session. Pre-fix the
        outer claim() transaction could roll back (branch-creation
        failure, FOR UPDATE conflict, etc.) but this fire-and-forget
        survived and wrote stale context onto a task whose claim was
        reverted.

        Now performs a confirm-after-commit check at the top: re-reads
        the task in a fresh session and skips if (a) task is gone, or
        (b) the claim is no longer held by ``agent_id``. Outer rollback
        clears ``assigned_to``, so this guard is enough to avoid stale
        writes; it also fires correctly under successful commits because
        a fresh read sees the post-commit state.
        """
        from uuid import UUID as PyUUID

        from roboco.db.base import get_session_factory
        from roboco.services.proactive import get_proactive_service

        task_id = PyUUID(str(task.id))
        task_title = task.title
        task_description = task.description or ""

        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                fresh = await session.get(TaskTable, task_id)
                if fresh is None or fresh.assigned_to != agent_id:
                    self.log.debug(
                        "skipping proactive context — claim was rolled back"
                        " or task is gone",
                        task_id=str(task_id),
                    )
                    return

            proactive = await get_proactive_service()
            agent_uuid = PyUUID(str(agent_id))

            context = await proactive.on_task_claimed(
                task_id=task_id,
                agent_id=agent_uuid,
                task_title=task_title,
                task_description=task_description,
                task_type=None,
            )

            if context and not context.is_empty():
                async with session_factory() as session:
                    from sqlalchemy import update

                    await session.execute(
                        update(TaskTable)
                        .where(TaskTable.id == task_id)
                        .values(proactive_context=context.to_dict())
                    )
                    await session.commit()

                self.log.info(
                    "Stored proactive context",
                    task_id=str(task_id),
                    items=len(context.similar_tasks)
                    + len(context.relevant_learnings)
                    + len(context.code_patterns),
                )
        except Exception as e:
            # Don't fail - this is fire-and-forget
            self.log.warning(
                "Failed to inject proactive context",
                task_id=str(task_id),
                error=str(e),
            )

    # =========================================================================
    # GIT WORK SESSION INTEGRATION
    # =========================================================================

    async def _create_work_session_if_needed(
        self,
        task: TaskTable,
        agent_id: UUID,
        agent_role: str | None,
    ) -> WorkSessionTable | None:
        """
        Create a WorkSession when a developer claims a task.

        Only creates a session if:
        - Task has a project_id set
        - Task has a branch_name set (auto-created on claim)
        - Agent is a developer (not QA/Documenter claiming for review)

        Args:
            task: The task being claimed
            agent_id: Agent claiming the task
            agent_role: Agent's role

        Returns:
            Created WorkSession or None if not applicable
        """
        # Only developers need work sessions (not QA/Documenter claiming)
        if agent_role not in ("developer", None):
            return None

        # Need project and branch to create a session
        project_id = getattr(task, "project_id", None)
        branch_name = getattr(task, "branch_name", None)

        if not project_id or not branch_name:
            self.log.debug(
                "Skipping work session - no project or branch",
                task_id=str(task.id),
                has_project=bool(project_id),
                has_branch=bool(branch_name),
            )
            return None

        # Get project to determine base/target branches
        result = await self.session.execute(
            select(ProjectTable).where(ProjectTable.id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            self.log.warning(
                "Project not found for work session",
                task_id=str(task.id),
                project_id=str(project_id),
            )
            return None

        # Determine target branch:
        # - For subtasks: merge into parent task's branch
        # - For parent tasks: merge into default branch (master)
        default_branch = project.default_branch
        target_branch: str = str(default_branch) if default_branch else "master"
        if task.parent_task_id:
            # Get parent task's branch
            parent_id = cast("UUID", task.parent_task_id)
            parent = await self.get(parent_id)
            parent_branch = getattr(parent, "branch_name", None) if parent else None
            if parent_branch:
                target_branch = str(parent_branch)

        # Create the work session through the validated service path (F113): the
        # service-layer ``WorkSessionService.create`` is the single source of
        # truth — it enforces the existing-active check (ConflictError on a
        # duplicate), the single-active-per-task supersede invariant, and
        # project/task existence. Constructing ``WorkSessionTable`` directly
        # here duplicated that validation and the two sites had drifted. The
        # ``ConflictError`` maps to the "if needed" idempotent semantics — an
        # agent re-claiming its own already-active session is a no-op (None),
        # not an error. The supersede still runs inside ``create`` before the
        # insert (closing any OTHER agent's stale ACTIVE row first).
        try:
            work_session = await WorkSessionService(self.session).create(
                WorkSessionCreate(
                    project_id=cast("UUID", project_id),
                    task_id=cast("UUID", task.id),
                    agent_id=agent_id,
                    branch_name=branch_name,
                    base_branch=target_branch,  # Created from target
                    target_branch=target_branch,  # Will merge back to target
                )
            )
        except ConflictError:
            self.log.debug(
                "Work session already exists",
                task_id=str(task.id),
                agent_id=str(agent_id),
            )
            return None

        # Link session to task
        task.work_session_id = cast("Any", work_session.id)
        await self.session.flush()

        self.log.info(
            "Work session created for task",
            task_id=str(task.id),
            session_id=str(work_session.id),
            branch=branch_name,
            target=target_branch,
        )

        return work_session

    # =========================================================================
    # RAG AUTO-INDEXING HOOKS (Fire-and-forget background tasks)
    # =========================================================================

    # Learning extraction thresholds
    _DURATION_OVER_RATIO = 1.5  # Flag if task took 1.5x expected time
    _DURATION_UNDER_RATIO = 0.3  # Flag if task took less than 30% expected
    _MIN_COMMITS_GOOD = 5  # Minimum commits for "good granularity" pattern
    _MIN_NOTES_LENGTH = 50  # Minimum notes length to extract learnings

    def _duration_learning(
        self,
        task_title: str | None,
        started_at: Any,
        completed_at: Any,
        estimated_complexity: Any,
    ) -> tuple[str, Any] | None:
        """Emit an INSIGHT learning when duration diverges from complexity."""
        from roboco.services.learning import LearningType

        if not (started_at and completed_at):
            return None
        duration_hours = (completed_at - started_at).total_seconds() / 3600
        complexity_hours = {"low": 2.0, "medium": 8.0, "high": 24.0}
        complexity_val = (
            estimated_complexity.value
            if hasattr(estimated_complexity, "value")
            else str(estimated_complexity)
        )
        expected = complexity_hours.get(complexity_val, 8.0)
        ratio = duration_hours / expected if expected > 0 else 1.0

        if ratio > self._DURATION_OVER_RATIO:
            msg = (
                f"Task '{task_title}' ({complexity_val}) took "
                f"{duration_hours:.1f}h vs expected {expected:.0f}h."
            )
            return msg, LearningType.INSIGHT
        if ratio < self._DURATION_UNDER_RATIO:
            msg = (
                f"Task '{task_title}' ({complexity_val}) completed "
                f"quickly in {duration_hours:.1f}h."
            )
            return msg, LearningType.INSIGHT
        return None

    def _commit_pattern_learning(
        self, task_title: str | None, commits: list[Any]
    ) -> tuple[str, Any] | None:
        """Emit a PATTERN/GOTCHA learning based on commit count."""
        from roboco.services.learning import LearningType

        if len(commits) >= self._MIN_COMMITS_GOOD:
            msg = f"Good commit granularity on '{task_title}': {len(commits)}."
            return msg, LearningType.PATTERN
        if len(commits) == 1:
            return (
                f"Single commit on '{task_title}'. Try smaller increments.",
                LearningType.GOTCHA,
            )
        return None

    def _collect_completion_learnings(
        self,
        snapshot: _CompletionSnapshot,
    ) -> list[tuple[str, Any]]:
        """Gather all auto-extractable learnings from a completed task."""
        from roboco.services.learning import LearningType

        learnings: list[tuple[str, Any]] = []
        duration = self._duration_learning(
            snapshot.task_title,
            snapshot.started_at,
            snapshot.completed_at,
            snapshot.estimated_complexity,
        )
        if duration:
            learnings.append(duration)

        commit_pattern = self._commit_pattern_learning(
            snapshot.task_title, snapshot.commits
        )
        if commit_pattern:
            learnings.append(commit_pattern)

        if snapshot.dev_notes and len(snapshot.dev_notes) > self._MIN_NOTES_LENGTH:
            learnings.append(
                (
                    f"[DEV NOTES] {snapshot.task_title}: {snapshot.dev_notes[:500]}",
                    LearningType.SOLUTION,
                )
            )
        if snapshot.qa_notes and len(snapshot.qa_notes) > self._MIN_NOTES_LENGTH:
            learnings.append(
                (
                    f"[QA FEEDBACK] {snapshot.task_title}: {snapshot.qa_notes[:500]}",
                    LearningType.REVIEW_FEEDBACK,
                )
            )
        return learnings

    async def _completion_learnings_for(
        self, snapshot: _CompletionSnapshot, acceptance_criteria: list[str]
    ) -> list[tuple[str, Any]]:
        """Org-memory ON -> one distilled lesson; OFF -> the legacy raw capture.

        When ``org_memory_enabled`` the noisy raw-notes / duration / commit-count
        entries are replaced by a single local-LLM-distilled lesson (skipped when
        the distiller returns None, so junk is never stored). Flag off keeps the
        legacy behavior byte-for-byte.
        """
        from roboco.config import settings

        if not settings.org_memory_enabled:
            return self._collect_completion_learnings(snapshot)

        from roboco.services.learning import LearningType
        from roboco.services.memory_distiller import LessonInput, MemoryDistiller

        commit_messages: list[str] = []
        for commit in snapshot.commits:
            msg = (
                commit.get("message")
                if isinstance(commit, dict)
                else getattr(commit, "message", None)
            )
            if msg:
                commit_messages.append(str(msg))

        lesson = await MemoryDistiller().distill(
            LessonInput(
                title=snapshot.task_title or "",
                acceptance_criteria=acceptance_criteria,
                dev_notes=snapshot.dev_notes,
                qa_notes=snapshot.qa_notes,
                commit_messages=commit_messages,
            )
        )
        return [(lesson, LearningType.SOLUTION)] if lesson else []

    async def _extract_completion_learnings(
        self, task: TaskTable, agent_id: UUID | None
    ) -> None:
        """Extract and record learnings from a completed task (fire-and-forget)."""
        from roboco.services.learning import (
            RecordLearningParams,
            get_learning_service,
        )

        # Extract data before session detaches
        task_id = task.id
        task_title = task.title
        task_team = task.team.value if task.team else None
        started_at = task.started_at
        completed_at = task.completed_at
        estimated_complexity = task.estimated_complexity
        commits = list(task.commits) if task.commits else []
        dev_notes = task.dev_notes
        qa_notes = task.qa_notes
        assigned_to = task.assigned_to
        acceptance_criteria = list(task.acceptance_criteria or [])

        try:
            learning_svc = await get_learning_service()
            scope = self._determine_learning_scope(task_team)
            snapshot = _CompletionSnapshot(
                task_title=task_title,
                started_at=started_at,
                completed_at=completed_at,
                estimated_complexity=estimated_complexity,
                commits=commits,
                dev_notes=dev_notes,
                qa_notes=qa_notes,
            )
            learnings = await self._completion_learnings_for(
                snapshot, acceptance_criteria
            )
            for content, ltype in learnings:
                await learning_svc.record_learning(
                    RecordLearningParams(
                        agent_id=to_python_uuid(assigned_to) or agent_id or UUID(int=0),
                        agent_role="developer",
                        content=content,
                        learning_type=ltype,
                        scope=scope,
                        task_id=to_python_uuid(task_id),
                        tags=["auto-extracted", task_team or "general"],
                    )
                )
            if learnings:
                self.log.info(
                    "Extracted completion learnings",
                    task_id=str(task_id),
                    count=len(learnings),
                )
        except Exception as e:
            self.log.warning(
                "Failed to extract learnings",
                task_id=str(task_id),
                error=str(e),
            )

    def _determine_learning_scope(self, team: str | None) -> Any:
        """Map team to learning scope."""
        from roboco.services.learning import LearningScope

        if team in ("backend", "frontend", "ux_ui"):
            return LearningScope.CELL
        if team in ("board", "main_pm"):
            return LearningScope.ORG
        return LearningScope.TEAM

    async def _index_code_changes_background(
        self, task_id: UUID, commits: list[dict[str, Any]], project: str
    ) -> None:
        """Index code files from task commits (fire-and-forget)."""
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()

            # Extract unique file paths from commits
            files: set[str] = set()
            for commit in commits:
                commit_files = commit.get("files", [])
                if isinstance(commit_files, list):
                    files.update(str(f) for f in commit_files)

            if files:
                count = await optimal.index_code(list(files), project=project)
                self.log.debug(
                    "Indexed code files",
                    task_id=str(task_id),
                    files_count=count,
                )
        except Exception as e:
            self.log.warning(
                "Failed to index code",
                task_id=str(task_id),
                error=str(e),
            )

    def _extract_decisions_from_notes(
        self, notes: str, task_title: str
    ) -> list[dict[str, str]]:
        """Parse notes for decision patterns."""
        decisions = []
        decision_patterns = [
            "decided to",
            "chose",
            "decision:",
            "went with",
            "selected",
            "opted for",
            "rationale:",
            "instead of",
        ]

        notes_lower = notes.lower()
        for pattern in decision_patterns:
            if pattern in notes_lower:
                lines = notes.split(".")
                for line in lines:
                    if pattern in line.lower():
                        decisions.append(
                            {
                                "topic": task_title,
                                "decision": line.strip()[:300],
                                "rationale": "Auto-extracted from task notes",
                            }
                        )
                        break
        return decisions

    async def _index_decisions_background(
        self,
        task_id: UUID,
        task_title: str,
        task_team: Team | None,
        dev_notes: str | None,
        agent_id: UUID | None,
    ) -> None:
        """Index decisions detected in notes (fire-and-forget)."""
        from roboco.models.optimal import IndexDecisionParams
        from roboco.services.optimal import get_optimal_service

        if not dev_notes:
            return

        try:
            optimal = await get_optimal_service()
            decisions = self._extract_decisions_from_notes(dev_notes, task_title)

            for decision in decisions:
                await optimal.index_decision(
                    IndexDecisionParams(
                        topic=decision["topic"],
                        decision=decision["decision"],
                        rationale=decision["rationale"],
                        agent_id=agent_id,
                        task_id=task_id,
                        scope="team",
                        tags=[task_team.value if task_team else "general", "auto"],
                    )
                )

            if decisions:
                self.log.debug(
                    "Indexed decisions",
                    task_id=str(task_id),
                    count=len(decisions),
                )
        except Exception as e:
            self.log.warning(
                "Failed to index decisions",
                task_id=str(task_id),
                error=str(e),
            )

    @staticmethod
    def _resolve_doc_abspath(rel_path: str) -> str:
        """Resolve a documenter-supplied doc path to its on-disk absolute path.

        Docs live under ``DOCS_BASE_PATH`` (``/app/docs``). Agents sometimes
        hand a path already rooted at ``docs/`` (or an absolute path); joining
        ``DOCS_BASE_PATH`` with a ``docs/``-prefixed relative path doubles the
        segment (``/app/docs/docs/...``), so the file is never found and the
        docs never index into RAG. Normalize: trust an absolute path; otherwise
        strip a single redundant leading ``docs/`` before joining.
        """
        from pathlib import Path

        from roboco.services.docs import DOCS_BASE_PATH

        path = Path(rel_path)
        # An absolute path already rooted at the docs base may double the
        # segment (``/app/docs/docs/...``) or simply be re-anchored here; reduce
        # it to a path relative to the base so the normalization below applies
        # uniformly. An absolute path OUTSIDE the docs root (e.g. a workspace
        # source file) is returned as-is for the indexer to skip.
        if path.is_absolute():
            try:
                path = path.relative_to(DOCS_BASE_PATH)
            except ValueError:
                return str(path)
        parts = path.parts
        if parts and parts[0] == DOCS_BASE_PATH.name:
            path = Path(*parts[1:]) if len(parts) > 1 else Path()
        return str(DOCS_BASE_PATH / path)

    async def _index_docs_background(
        self,
        task_id: UUID,
        documents: list[dict[str, Any]],
        actor_agent_id: UUID | None = None,
    ) -> None:
        """Index documentation from completed doc task (fire-and-forget)."""
        from roboco.services.optimal import get_optimal_service

        try:
            # Land any workspace-authored docs server-side first, so the
            # indexer (which reads /app/docs) can see docs the agent wrote with
            # Edit/Write in its own clone rather than through roboco_docs_write.
            await self._capture_workspace_docs(task_id, documents, actor_agent_id)

            optimal = await get_optimal_service()

            # Extract doc paths from documents array and resolve to absolute paths
            doc_paths: list[str] = []
            for d in documents:
                rel_path = d.get("path")
                if rel_path:
                    doc_paths.append(self._resolve_doc_abspath(rel_path))

            if doc_paths:
                count = await optimal.index_documentation(doc_paths, project="roboco")
                self.log.debug(
                    "Indexed docs",
                    task_id=str(task_id),
                    docs_count=count,
                )
        except Exception as e:
            self.log.warning(
                "Failed to index docs",
                task_id=str(task_id),
                error=str(e),
            )

    async def _capture_workspace_docs(
        self,
        task_id: UUID,
        documents: list[dict[str, Any]],
        actor_agent_id: UUID | None,
    ) -> None:
        """Copy workspace-authored docs into ``/app/docs`` so they index.

        Docs written through ``roboco_docs_write`` already live under
        ``DOCS_BASE_PATH`` on the orchestrator. Docs an agent wrote with
        Edit/Write live only in that agent's own clone, so resolving their path
        under ``/app/docs`` finds nothing and they never reach RAG (the
        cross-container miss). Read each missing doc's committed content out of
        the branch and write it server-side. Best-effort: one unreadable file
        must not abort the rest of the batch.
        """
        from pathlib import Path

        from roboco.services.git import get_git_service

        task = await self.get(task_id)
        if task is None or not task.branch_name:
            return
        git = get_git_service(self.session)
        for d in documents:
            rel_path = d.get("path")
            # git show needs a repo-relative path; an absolute path can't be
            # mapped back to the repo, and the indexer already skips it.
            if not rel_path or Path(rel_path).is_absolute():
                continue
            abspath = Path(self._resolve_doc_abspath(rel_path))
            if abspath.exists():
                continue
            try:
                content = await git.read_file_at_branch(
                    branch_name=task.branch_name,
                    path=rel_path,
                    actor_agent_id=actor_agent_id,
                )
            except Exception as e:
                self.log.debug(
                    "Workspace doc capture read failed",
                    path=rel_path,
                    error=str(e),
                )
                continue
            if not content:
                continue
            abspath.parent.mkdir(parents=True, exist_ok=True)
            abspath.write_text(content, encoding="utf-8")

    # =========================================================================
    # QA AND ERROR INDEXING HOOKS
    # =========================================================================

    def _parse_qa_notes(self, qa_notes: str) -> list[dict[str, str]]:
        """Parse QA notes into structured issues."""
        issues = []
        for raw_line in qa_notes.split("\n"):
            stripped = raw_line.strip()
            if stripped.startswith(("-", "*", "•")):
                issues.append(
                    {
                        "severity": "error",
                        "description": stripped.lstrip("-*• "),
                    }
                )
            elif stripped and stripped[0].isdigit() and "." in stripped[:3]:
                parts = stripped.split(".", 1)
                desc = parts[1].strip() if len(parts) > 1 else stripped
                issues.append({"severity": "error", "description": desc})
        if not issues and qa_notes.strip():
            issues.append({"severity": "error", "description": qa_notes[:500]})
        return issues

    async def _index_qa_review_background(
        self,
        task_id: UUID,
        original_developer: str | None,
        passed: bool,
        qa_notes: str,
        qa_agent_id: UUID | None,
    ) -> None:
        """Index QA review (fire-and-forget)."""
        from roboco.models.optimal import IndexReviewParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            original_dev = original_developer

            await optimal.record_review(
                IndexReviewParams(
                    file_path=f"task/{task_id}",
                    comments=[
                        {
                            "body": qa_notes,
                            "type": "qa",
                            "severity": "info" if passed else "error",
                        }
                    ],
                    approved=passed,
                    summary=qa_notes[:500] if qa_notes else "QA Review",
                    reviewer_id=qa_agent_id,
                    author_id=UUID(original_dev) if original_dev else None,
                    task_id=task_id,
                )
            )
            self.log.debug("Indexed QA review", task_id=str(task_id), passed=passed)
        except Exception as e:
            self.log.warning(
                "Failed to index QA review",
                task_id=str(task_id),
                error=str(e),
            )

    async def _index_qa_errors_background(
        self,
        task_id: UUID,
        task_title: str,
        task_team: Team | None,
        qa_notes: str,
    ) -> None:
        """Index QA failure issues as error patterns (fire-and-forget)."""
        from roboco.models.optimal import IndexErrorParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            issues = self._parse_qa_notes(qa_notes)

            for issue in issues:
                await optimal.index_error(
                    IndexErrorParams(
                        error_message=f"QA Failure: {issue['description'][:200]}",
                        context=f"Task: {task_title}",
                        solution="",
                        worked=False,
                        task_id=task_id,
                        team=task_team.value if task_team else None,
                        tags=["qa_failure", issue["severity"]],
                    )
                )

            self.log.debug(
                "Indexed QA errors",
                task_id=str(task_id),
                count=len(issues),
            )
        except Exception as e:
            self.log.warning(
                "Failed to index QA errors",
                task_id=str(task_id),
                error=str(e),
            )

    async def _index_blocker_background(
        self,
        task_id: UUID,
        task_team: Team | None,
        blocker_info: dict[str, str],
    ) -> None:
        """Index blocker as error pattern (fire-and-forget).

        Args:
            task_id: Task UUID
            task_team: Team for categorization
            blocker_info: Dict with keys: type, title, reason, what_needed
        """
        from roboco.models.optimal import IndexErrorParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            blocker_type = blocker_info.get("type", "unknown")
            reason = blocker_info.get("reason", "")
            title = blocker_info.get("title", "")
            what_needed = blocker_info.get("what_needed", "")

            await optimal.index_error(
                IndexErrorParams(
                    error_message=f"Blocker ({blocker_type}): {reason[:200]}",
                    context=f"Task: {title}\nNeeded: {what_needed}",
                    solution="",
                    worked=False,
                    task_id=task_id,
                    team=task_team.value if task_team else None,
                    tags=["blocker", blocker_type.lower()],
                )
            )
            self.log.debug("Indexed blocker", task_id=str(task_id))
        except Exception as e:
            self.log.warning(
                "Failed to index blocker",
                task_id=str(task_id),
                error=str(e),
            )

    async def _index_lifecycle_event_background(
        self,
        task_id: UUID,
        event_type: str,
        task_title: str,
        task_team: Team | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Index lifecycle event for pattern analysis (fire-and-forget).

        Tracks task state transitions for organizational learning:
        - Cancellation patterns (what gets cancelled and why)
        - Pause/resume patterns (context switching costs)
        - Block/unblock patterns (dependency bottlenecks)

        Args:
            task_id: Task UUID
            event_type: One of: cancel, pause, resume, block, unblock
            task_title: Task title for context
            task_team: Team for categorization
            details: Additional event details
        """
        from uuid import NAMESPACE_URL, uuid5

        from roboco.models.optimal import IndexJournalEntryParams
        from roboco.services.optimal import get_optimal_service

        try:
            optimal = await get_optimal_service()
            details = details or {}

            # Build content for indexing
            content = f"[{event_type.upper()}] {task_title}"
            if details:
                content += f"\nDetails: {details}"

            # Lifecycle events have no journal-entry row of their own. Derive
            # a deterministic synthetic UUID from (task, event, timestamp)
            # so the index_journal_entry source is meaningful and unique
            # per event — never the literal "None" that the old fallback
            # produced.
            now_iso = datetime.now(UTC).isoformat()
            synthetic_entry_id = uuid5(
                NAMESPACE_URL,
                f"roboco-lifecycle/{task_id}/{event_type}/{now_iso}",
            )

            # Index to journals for lifecycle tracking
            await optimal.index_journal_entry(
                IndexJournalEntryParams(
                    content=content,
                    entry_id=synthetic_entry_id,
                    agent_id=None,  # System event, no specific agent
                    entry_type=f"lifecycle_{event_type}",
                    task_id=task_id,
                    tags=[event_type, task_team.value if task_team else "default"],
                )
            )
            self.log.debug(
                "Indexed lifecycle event",
                task_id=str(task_id),
                event_type=event_type,
            )
        except Exception as e:
            self.log.warning(
                "Failed to index lifecycle event",
                task_id=str(task_id),
                event_type=event_type,
                error=str(e),
            )

    async def start(
        self,
        task_id: UUID,
        agent_id: UUID | None = None,
        agent_role: str | None = None,
    ) -> TaskTable | None:
        """
        Start working on a task.

        Args:
            task_id: The task to start
            agent_id: Optional agent ID to validate ownership
            agent_role: Optional agent role for transition validation

        Returns:
            The started task, or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Validate ownership if agent_id provided
        if agent_id is not None:
            try:
                assigned = task.assigned_to
                validate_task_ownership(
                    agent_id=str(agent_id),
                    task_id=str(task_id),
                    task_assigned_to=str(assigned) if assigned else None,
                    task_team=task.team.value if task.team else "backend",
                    action="start",
                )
            except TaskOwnershipError as e:
                self.log.warning(
                    "Cannot start task - ownership validation failed",
                    task_id=str(task_id),
                    agent_id=str(agent_id),
                    error=str(e),
                )
                return None

        # Valid statuses to start/resume work:
        # - CLAIMED: Developer just claimed a pending task
        # - PAUSED: Developer resuming paused work
        # - NEEDS_REVISION: Developer resuming after QA rejection
        valid_start_statuses = (
            TaskStatus.CLAIMED,
            TaskStatus.PAUSED,
            TaskStatus.NEEDS_REVISION,
        )
        if task.status not in valid_start_statuses:
            self.log.warning(
                "Cannot start task - invalid status",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # PLAN required before starting from CLAIMED (everyone must plan)
        if task.status == TaskStatus.CLAIMED and not task.plan:
            self.log.warning(
                "Cannot start task - no plan",
                task_id=str(task_id),
            )
            return None

        # Only update started_at if this is the first time starting
        if task.started_at is None:
            task.started_at = datetime.now(UTC)
        self._validate_and_set_status(task, TaskStatus.IN_PROGRESS, agent_role)
        await self.session.flush()
        return task

    async def heartbeat(self, task_id: UUID) -> None:
        """Touch ``last_heartbeat_at`` to mark the claimant as alive.

        Called from gateway verb entry points so a dead container's
        claim becomes recoverable after the heartbeat TTL expires.
        No-op if the task does not exist — the UPDATE simply matches zero
        rows. The column is ``DateTime(timezone=True)`` so we write a
        timezone-aware UTC value to match the schema and avoid SA's naive-
        vs-aware mismatch warning.
        """
        await self.session.execute(
            update(TaskTable)
            .where(TaskTable.id == task_id)
            .values(last_heartbeat_at=datetime.now(UTC))
        )

    async def list_in_progress_or_claimed(self) -> list[TaskTable]:
        """All tasks currently in claimed or in_progress state.

        Used by the orchestrator's stale-claim reaper to find rows whose
        holder may have gone silent. Returns the bare row set; the reaper
        applies the heartbeat-TTL filter in Python because the cutoff is a
        runtime decision tied to settings, not a column.
        """
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.status.in_([TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS])
            )
        )
        return list(result.scalars().all())

    async def unclaim_for_reaper(self, task_id: UUID) -> None:
        """Reaper-only unclaim: skip role checks, force the row back to pending.

        Routes through ``_validate_and_set_status`` so the
        canonical state machine in ``enforcement/task_lifecycle.py`` records
        the transition. Pre-fix this used raw UPDATE which bypassed
        VALID_TRANSITIONS — making the lifecycle module's invariants diverge
        from production reality.

        Also abandons the active WorkSession so a re-claim by the same
        agent doesn't trip the uniqueness constraint at
        ``WorkSessionService.create``. Best-effort: if the
        WorkSession lookup fails for any reason, the task is still
        rolled back to pending.

        The operation is named with ``_for_reaper`` so callers cannot
        accidentally use it as a regular unclaim path; uses ``agent_role=None``
        because the system itself is performing the transition. Bypasses
        ownership/role checks because the holder is provably dead (no
        heartbeat past TTL).
        """
        await self._force_unclaim_to_pending(task_id, reason="reaper-unclaim")

    async def release_dependency_blocked_claim(self, task_id: UUID) -> None:
        """Release a claimed/in_progress task whose dependency is still unmet.

        A task assigned with an unfinished dependency cannot proceed, but while
        it sits claimed/in_progress the orchestrator keeps respawning its
        assignee (the respawn loop targets only claimed/in_progress). Releasing
        it to pending stops that churn: the dispatch dependency filter holds it
        un-spawned, and ``_unblock_dependents`` clears the dependency once the
        upstream completes so it re-dispatches on its own. ``claimed -> blocked``
        is not a legal transition, so pending (held by the dependency filter) is
        the lifecycle-correct resting state. No-op when not in a releasable state.

        Also forgets ``branch_name`` so the eventual re-claim re-runs branch
        creation and cuts the branch fresh off the current integration tip —
        which by then includes the upstream's merged work — instead of reusing a
        snapshot taken before the dependency landed. A dependency-blocked task
        has done no work of its own, so nothing is lost; ``create_branch``
        leaves any branch carrying real commits intact.
        """
        if not await self._force_unclaim_to_pending(task_id, reason="dependency-unmet"):
            return
        task = await self.get(task_id)
        if task is not None and task.branch_name:
            task.branch_name = None
            await self.session.flush()

    async def _force_unclaim_to_pending(self, task_id: UUID, *, reason: str) -> bool:
        """Force a claimed/in_progress task back to pending (system action).

        Shared core of ``unclaim_for_reaper`` and
        ``release_dependency_blocked_claim``. Routes through
        ``_validate_and_set_status`` so the state machine records the
        transition, releases the live claim (heartbeat + active claimant) and
        abandons the active WorkSession (best-effort, tagged with ``reason``) so
        a re-claim doesn't trip the uniqueness constraint. Bypasses
        ownership/role checks — the system itself is performing the transition.
        Returns True iff the task was actually released (False when missing or
        not in a releasable state).

        Ownership is **preserved**, not cleared: both callers release a task its
        owner should resume — the reaper's holder is dead but will respawn, and
        a dependency-blocked task continues with the same agent once the
        upstream lands. Leaving ``assigned_to``/``claimed_by`` pointed at the
        owner (mirroring the unblock restore) keeps the task from landing in an
        ownerless ``pending`` limbo that no dispatcher re-spawns. The earlier
        behaviour nulled ``assigned_to``, which is exactly that orphaning bug.
        """
        task = await self.get(task_id)
        if task is None:
            return False
        if task.status not in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
            return False
        # Capture the owner before releasing the claim. A claimed/in_progress
        # task is always owned via claimed_by/active_claimant_id (and usually
        # assigned_to); fall back across them so the row never goes ownerless.
        owner = cast(
            "Any", task.assigned_to or task.claimed_by or task.active_claimant_id
        )
        try:
            self._validate_and_set_status(task, TaskStatus.PENDING, None)
        except TaskLifecycleError:
            return False
        if task.work_session_id:
            await self._abandon_work_session_best_effort(
                task.work_session_id, reason=reason
            )
            task.work_session_id = cast("Any", None)
        task.assigned_to = owner
        task.claimed_by = owner
        task.last_heartbeat_at = None
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()
        return True

    async def _abandon_work_session_best_effort(
        self, session_id: Any, *, reason: str
    ) -> None:
        """Mark a WorkSession ABANDONED. Logs and continues on any failure.

        Unclaim must not leave ACTIVE WorkSessions
        behind, but a service-layer error here mustn't block the task-
        side unclaim from completing.
        """
        try:
            from roboco.services.work_session import (
                WorkSessionService,
            )

            ws_service = WorkSessionService(self.session)
            await ws_service.abandon(UUID(str(session_id)), reason=reason)
        except Exception as exc:
            self.log.warning(
                "abandon WorkSession failed; continuing",
                session_id=str(session_id),
                reason=reason,
                error=str(exc),
            )

    async def unclaim_for_agent(
        self, task_id: UUID, agent_id: UUID
    ) -> TaskTable | None:
        """Voluntary unclaim by the current claimant.

        Distinct from ``unclaim_for_reaper`` (which the orchestrator's
        stale-claim sweeper calls when the holder is provably dead): this
        path is the agent itself releasing the lock. Returns ``None`` and
        makes no write when:

        - the task does not exist
        - the requesting agent is not the current claimant
        - the task status is not claimed/in_progress
        - the lifecycle layer rejects the transition (defense-in-depth
          against future ``VALID_TRANSITIONS``/``ROLE_RESTRICTED_TRANSITIONS``
          changes; the choreographer pre-checks status today)

        On success, clears ``assigned_to`` and transitions the row back to
        ``pending`` so another agent (or the same one, fresh) can pick it
        up. The work-in-progress branch is preserved — only the claim is
        released.
        """
        task = await self.get(task_id)
        if task is None or task.assigned_to != agent_id:
            return None
        if task.status == TaskStatus.PENDING:
            return await self._unclaim_pending_assignment(task)
        if task.status == TaskStatus.BLOCKED:
            return await self._unclaim_from_blocked(task)
        if task.status not in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
            return None

        # Look up the requesting agent's role so role-restricted-transition
        # rules apply if/when claimed→pending or in_progress→pending ever
        # gain restrictions. Mirrors the pattern in `_finalize_claim`.
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        agent_role = agent.role.value if agent and agent.role else None

        # Route through the single point of truth for status transitions.
        # _validate_and_set_status raises TaskLifecycleError on invalid
        # transition or role; treat that as a clean rejection (return None)
        # so the choreographer's existing "invalid_state" envelope still
        # fires instead of a 500 leaking out.
        try:
            self._validate_and_set_status(task, TaskStatus.PENDING, agent_role)
        except TaskLifecycleError:
            return None

        # _validate_and_set_status only updates `status`; clearing the
        # claim is the unclaim's specific side effect. Also abandon the
        # active WorkSession so a re-claim doesn't trip the uniqueness
        # constraint.
        if task.work_session_id:
            await self._abandon_work_session_best_effort(
                task.work_session_id, reason="agent-unclaim"
            )
            task.work_session_id = cast("Any", None)
        task.assigned_to = cast("Any", None)
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()
        return task

    async def _unclaim_pending_assignment(self, task: TaskTable) -> TaskTable:
        """Release a never-claimed ``pending`` assignment (no status change).

        An agent assigned a ``pending`` task it never claimed (a persistent
        claim-time rejection it cannot satisfy) is otherwise trapped: unclaim /
        i_am_idle / i_am_blocked all reject from pending-assigned, so it loops
        until budget-reap and the task is orphaned. The row is already pending,
        so clearing the assignment is a no-status-change escape — no transition,
        no lifecycle validation, no WorkSession to abandon (it was never
        claimed). The task returns to the pool for the dispatcher to reassign.
        """
        task.assigned_to = cast("Any", None)
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()
        return task

    async def _unclaim_from_blocked(self, task: TaskTable) -> TaskTable:
        """Release a ``blocked`` claim back to the pool (audited).

        A task the agent owns but cannot advance (a blocker it cannot
        self-resolve, or a dependency block) is otherwise a trap: from
        ``blocked`` the agent has no legal forward verb and the dispatcher keeps
        respawning it. Releasing the claim returns the task to the pool for the
        cell PM to re-delegate. The active WorkSession is abandoned so a re-claim
        does not trip the uniqueness constraint.
        """
        pre_status = (
            task.status.value
            if isinstance(task.status, TaskStatus)
            else str(task.status)
        )
        prior_owner = cast("Any", task.claimed_by or task.assigned_to)
        if task.work_session_id:
            await self._abandon_work_session_best_effort(
                task.work_session_id, reason="agent-unclaim-from-blocked"
            )
            task.work_session_id = cast("Any", None)
        task.status = TaskStatus.PENDING
        task.assigned_to = cast("Any", None)
        task.claimed_by = cast("Any", None)
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()
        self._emit_status_transition_audit(
            task,
            from_status=pre_status,
            to_status=TaskStatus.PENDING.value,
            agent_role=None,
            audit_agent_id=prior_owner,
        )
        return task

    async def resume_for_agent(self, task_id: UUID, agent_id: UUID) -> TaskTable | None:
        """Voluntary resume: transition paused task → in_progress for the assignee.

        Distinct from ``resume`` (which takes only ``agent_role`` and is
        called by closure-dispatcher / management code paths): this path
        enforces that ``agent_id`` is the current claimant. Returns ``None``
        and makes no write when:

        - the task does not exist
        - the requesting agent is not the current assignee
        - the task status is not paused
        - the lifecycle layer rejects the transition (defense-in-depth
          against future ``VALID_TRANSITIONS``/``ROLE_RESTRICTED_TRANSITIONS``
          changes; the choreographer pre-checks status today)

        Delegates to ``resume`` after the gateway-specific ownership/state
        pre-checks so we inherit its structlog "Task resumed" event and the
        fire-and-forget RAG lifecycle-event indexing — gateway-driven resumes
        must remain visible to logs and the RAG corpus. Mirrors
        ``pause_for_agent``'s delegation pattern.
        """
        task = await self.get(task_id)
        if task is None or task.assigned_to != agent_id:
            return None
        if task.status != TaskStatus.PAUSED:
            return None

        # Look up the requesting agent's role so role-restricted-transition
        # rules apply if/when paused→in_progress ever gains restrictions.
        # Mirrors the pattern in `unclaim_for_agent` and `_finalize_claim`.
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        agent_role = agent.role.value if agent and agent.role else None

        # `resume` calls `_validate_and_set_status` internally, which raises
        # TaskLifecycleError on invalid transition or role. Treat that as a
        # clean rejection (return None) so the choreographer's
        # "invalid_state" envelope still fires instead of a 500 leaking out.
        try:
            return await self.resume(task_id, agent_role)
        except TaskLifecycleError:
            return None

    def _snapshot_pre_block(self, task: TaskTable) -> None:
        """Record the pre-block status + owner so unblock(restore=True) works.

        Captures the resting state a task is leaving so a PM ``unblock`` with
        ``restore=True`` can return it exactly there. Call this *before*
        mutating status/ownership at every block entry (dependency block,
        soft block, escalation). Only the first block in a chain snapshots —
        a re-block (e.g. escalating an already-blocked task) must not overwrite
        the original resting state with ``blocked``. Mirrors the ``not already
        set`` guard used for ``blocker_raised_by``.
        """
        if task.pre_block_state:
            return
        task.pre_block_state = (
            task.status.value
            if isinstance(task.status, TaskStatus)
            else str(task.status)
        )
        task.pre_block_assignee = cast("Any", task.assigned_to or task.claimed_by)

    async def block(
        self,
        task_id: UUID,
        blocker_task_id: UUID,
        agent_role: str | None = None,
    ) -> TaskTable | None:
        """Block a task due to a dependency.

        Args:
            task_id: The task to block
            blocker_task_id: The task causing the block
            agent_role: Role of agent performing the block (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if blocker_task_id not in task.dependency_ids:
            new_deps = [*task.dependency_ids, blocker_task_id]
            task.dependency_ids = new_deps
        # Task-dependency blocks always resolve when the blocker task
        # completes — that's inherently an agent-resolvable condition.
        task.blocker_resolver_type = BlockerResolverType.AGENT
        # Remember who was working this task so `unblock` can hand it
        # back. Dependency blocks don't reassign, but stash anyway so
        # the unblock path has a consistent source of truth.
        if task.assigned_to and not task.blocker_raised_by:
            task.blocker_raised_by = task.assigned_to
        self._snapshot_pre_block(task)
        self._validate_and_set_status(task, TaskStatus.BLOCKED, agent_role)
        await self.session.flush()

        # Update the blocker task to reference this as blocked
        blocker = await self.get(blocker_task_id)
        if blocker and task_id not in blocker.blocker_ids:
            blocker.blocker_ids = [*blocker.blocker_ids, task_id]
            await self.session.flush()

        self.log.info(
            "Task blocked",
            task_id=str(task_id),
            blocker_id=str(blocker_task_id),
        )

        # Index lifecycle event (fire-and-forget)
        blocker_title = blocker.title if blocker else "unknown"
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="block",
                task_title=task.title,
                task_team=task.team,
                details={
                    "blocker_task_id": str(blocker_task_id),
                    "blocker_title": blocker_title,
                },
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def soft_block(
        self,
        task_id: UUID,
        info: SoftBlockInfo,
        agent_role: str | None = None,
    ) -> TaskTable | None:
        """
        Block a task due to an external factor (not a task dependency).

        Unlike `block()` which requires another task as the blocker,
        this method handles soft blocks like:
        - External dependencies (waiting for API access, credentials)
        - Questions that need PM/stakeholder input
        - Technical blockers (infrastructure issues)

        Args:
            task_id: The task to block
            reason: Why the task is blocked
            blocker_type: Type of blocker (external/internal/question/dependency)
            what_needed: What is needed to unblock
            agent_role: Role of agent performing the block (for validation)

        Returns:
            The blocked task, or None if blocking not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        # Build blocker note for dev_notes
        blocker_note = (
            f"[BLOCKED - {info.blocker_type.upper()}]\n"
            f"Reason: {info.reason}\n"
            f"What's needed: {info.what_needed}"
        )
        task.dev_notes = _append_capped(task.dev_notes, blocker_note)

        # Default resolver is AGENT — preserves pre-existing behavior where
        # the dispatcher would respawn. Caller passes HUMAN to tell the
        # dispatcher to stop churning and wait for HITL.
        task.blocker_resolver_type = info.resolver_type or BlockerResolverType.AGENT
        # Remember the raiser so `unblock` can restore the task to them.
        if task.assigned_to and not task.blocker_raised_by:
            task.blocker_raised_by = task.assigned_to
        self._snapshot_pre_block(task)
        self._validate_and_set_status(task, TaskStatus.BLOCKED, agent_role)
        await self.session.flush()

        # Index blocker as error pattern (fire-and-forget)
        blocker_info = {
            "type": info.blocker_type,
            "title": task.title,
            "reason": info.reason,
            "what_needed": info.what_needed,
        }
        bg_task = asyncio.create_task(
            self._index_blocker_background(
                require_uuid(task.id), task.team, blocker_info
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        self.log.info(
            "Task soft-blocked",
            task_id=str(task_id),
            blocker_type=info.blocker_type,
            reason=info.reason,
        )
        return task

    async def unblock(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Unblock a task and hand it back to the agent who raised the block.

        If the block was an escalation, `apply_escalation` stashed the
        original dev's UUID in `blocker_raised_by`. We restore the
        assignment here (and clear the stash) so the orchestrator's
        dispatcher spawns the original agent on the next tick, rather
        than leaving the task in_progress on the resolver PM who is
        already parked waiting_long.

        Args:
            task_id: The task to unblock
            agent_role: Role of agent performing the unblock (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.BLOCKED:
            return None

        # Restore ownership so the dispatcher respawns the original worker, not
        # the PM who merely resolved the block. blocker_raised_by holds the
        # pre-escalation dev; fall back to the surviving claim owner so a task
        # claimed via give_me_work (which has no assigned_to to stash) is never
        # left with a split owner — assigned_to null but claimed_by set, which
        # the dev dispatcher and the PM pool-router would both try to grab.
        # Keep both fields on the owner, mirroring _force_unclaim_to_pending and
        # reassign; this also clears a stale claimed_by left pointing at the
        # resolver PM after an escalation.
        owner = cast(
            "Any", task.blocker_raised_by or task.assigned_to or task.claimed_by
        )
        task.blocker_raised_by = None
        if owner is not None:
            task.assigned_to = owner
            task.claimed_by = owner
        # Clear resolver metadata — only meaningful while BLOCKED.
        task.blocker_resolver_type = None
        # A task with a branch was claimed before it blocked, so resume it
        # in_progress. A task with NO branch was blocked before it was ever
        # claimed (e.g. a dependency-gated claim that got escalated); it cannot
        # resume in_progress (the dispatcher refuses a branchless in_progress
        # task and loops), so return it to pending to be freshly claimed — the
        # claim gate then holds it cleanly if its dependency is still unmet.
        target = TaskStatus.IN_PROGRESS if task.branch_name else TaskStatus.PENDING
        self._validate_and_set_status(task, target, agent_role)
        await self.session.flush()

        self.log.info(
            "Task unblocked",
            task_id=str(task_id),
            restored_assignee=(str(task.assigned_to) if task.assigned_to else None),
        )

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="unblock",
                task_title=task.title,
                task_team=task.team,
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def pause(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Pause a task.

        Args:
            task_id: The task to pause
            agent_role: Role of agent pausing the task (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        self._validate_and_set_status(task, TaskStatus.PAUSED, agent_role)
        await self.session.flush()

        self.log.info("Task paused", task_id=str(task_id))

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="pause",
                task_title=task.title,
                task_team=task.team,
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def resume(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Resume a paused task.

        Args:
            task_id: The task to resume
            agent_role: Role of agent resuming the task (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.PAUSED:
            return None

        self._validate_and_set_status(task, TaskStatus.IN_PROGRESS, agent_role)
        await self.session.flush()

        self.log.info("Task resumed", task_id=str(task_id))

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="resume",
                task_title=task.title,
                task_team=task.team,
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def submit_for_verification(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Submit task for self-verification.

        Args:
            task_id: The task to verify
            agent_role: Role of agent submitting for verification (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.IN_PROGRESS:
            return None

        # self_verified is the dev's attestation that they've reviewed
        # their own work before handing to QA. Setting it here (rather
        # than later in submit_for_qa) means the submit-qa route's
        # NOT_SELF_VERIFIED gate has something to check against.
        task.self_verified = True
        self._validate_and_set_status(task, TaskStatus.VERIFYING, agent_role)
        await self.session.flush()

        self.log.info("Task submitted for verification", task_id=str(task_id))
        return task

    async def submit_for_qa(
        self, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Submit task for QA review.

        Args:
            task_id: The task to submit for QA
            agent_role: Role of agent submitting (for validation)
        """
        task = await self.get(task_id)
        if not task:
            return None

        if task.status != TaskStatus.VERIFYING:
            return None

        # Store original developer BEFORE clearing assignment - authoritative
        # record for self-review prevention (QA can't review own work).
        original_dev = str(task.assigned_to) if task.assigned_to else None
        if original_dev:
            markers.set_original_developer(task, original_dev)

        # Capture the developer's UUID BEFORE clearing claimed_by so the
        # `task.awaiting_qa` audit row is attributed to the dev who
        # submitted, not NULL. Capture-before-mutate per Audit I30.
        captured_dev_id = to_python_uuid(task.claimed_by)

        # Clear assignment so QA can claim the task
        # The original developer is preserved in quick_context
        task.assigned_to = None
        task.claimed_by = None
        task.self_verified = True
        self._validate_and_set_status(
            task,
            TaskStatus.AWAITING_QA,
            agent_role,
            audit_agent_id=captured_dev_id,
        )
        await self.session.flush()

        self.log.info(
            "Task submitted for QA",
            task_id=str(task_id),
            original_developer=original_dev,
        )
        return task

    async def pass_qa(
        self, task_id: UUID, notes: str | None = None, agent_role: str = "qa"
    ) -> TaskTable | None:
        """Mark task as passed QA.

        QA workflow: awaiting_qa → claimed → in_progress → pass_qa
        → awaiting_documentation. Accept claimed/in_progress status.

        Args:
            task_id: The task to pass
            notes: Optional QA notes
            agent_role: Role of agent passing QA (must be 'qa')
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Accept tasks QA is actively working on (claimed or in_progress)
        # as well as awaiting_qa (for direct pass without starting)
        valid_statuses = {
            TaskStatus.AWAITING_QA,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
        }
        if task.status not in valid_statuses:
            return None

        if notes and not (task.notes_structured or {}).get("qa"):
            task.qa_notes = notes

        # Store QA agent before clearing assignment
        qa_agent_id = task.assigned_to

        # Capture the QA agent's UUID BEFORE clearing claimed_by so the
        # `task.awaiting_documentation` audit row is attributed to QA,
        # not NULL. Capture-before-mutate per Audit I30.
        captured_qa_id = to_python_uuid(task.claimed_by)

        # Clear assignment so documenter can claim the task
        task.assigned_to = None
        task.claimed_by = None
        task.qa_verified = True
        # Reset docs so the documenter writes fresh docs for this cycle.
        # DO NOT reset pr_created — the PR exists pre-QA under the current
        # workflow (see CLAUDE.md: "awaiting_documentation | PR already
        # open from pre-QA"). Clearing it here falsely signaled "no PR"
        # to `_maybe_advance_to_pm_review` and left tasks stuck in CLAIMED
        # after docs_complete, since the parallel-completion gate never
        # saw both flags together.
        task.docs_complete = False
        # Use validated transition - QA role required per ROLE_RESTRICTED_TRANSITIONS
        self._validate_and_set_status(
            task,
            TaskStatus.AWAITING_DOCUMENTATION,
            agent_role,
            audit_agent_id=captured_qa_id,
        )
        await self.session.flush()

        # Index positive QA review (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_qa_review_background(
                require_uuid(task.id),
                extract_original_developer(task),
                True,
                notes or "Passed QA review",
                to_python_uuid(qa_agent_id),
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        self.log.info("Task passed QA", task_id=str(task_id))
        return task

    async def fail_qa(
        self, task_id: UUID, notes: str, agent_role: str = "qa"
    ) -> TaskTable | None:
        """
        Mark task as failed QA and reassign to original developer.

        When QA fails a task, it goes back to the original developer for revision.
        The original developer is extracted from quick_context which stores
        "original_developer:{uuid}" when the task was submitted to QA.

        QA workflow: awaiting_qa → claimed → in_progress → fail_qa → needs_revision
        So we need to accept tasks in claimed or in_progress status.

        Args:
            task_id: The task to fail
            notes: QA notes explaining why it failed
            agent_role: Role of agent failing the task (must be 'qa')
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Accept tasks QA is actively working on
        valid_statuses = {
            TaskStatus.AWAITING_QA,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
        }
        if task.status not in valid_statuses:
            return None

        if not (task.notes_structured or {}).get("qa"):
            task.qa_notes = notes
        task.qa_verified = False
        # Use validated transition - QA role required per ROLE_RESTRICTED_TRANSITIONS
        self._validate_and_set_status(task, TaskStatus.NEEDS_REVISION, agent_role)

        # Store QA agent before reassigning
        qa_agent_id = task.assigned_to

        # Reassign to original developer so they can work on revisions
        original_dev = extract_original_developer(task)
        if original_dev:
            task.assigned_to = cast("Any", UUID(original_dev))
            task.claimed_by = cast("Any", UUID(original_dev))
            self.log.info(
                "Task reassigned to original developer for revision",
                task_id=str(task_id),
                original_developer=original_dev,
            )
        else:
            # A dev task in needs_revision must go back to THE DEV, never to
            # the pool. ``submit_for_qa`` sets the ``original_developer`` marker
            # on the normal path, so a missing marker means the task did not
            # pass through it. Unassigning here used to drop the task into the
            # pool, where a cell PM (PMs can re-claim needs_revision) would
            # grab it — exactly "needs revision on a dev task sent to the cell
            # PM" (live 2026-06-27). Fall back to the developer who actually
            # worked it: the most recent work session whose agent is a
            # developer (the QA's own session excluded). Only unassign when no
            # developer ever touched the task.
            fallback_dev = await self._resolve_revision_dev(
                task, exclude=to_python_uuid(qa_agent_id)
            )
            if fallback_dev is not None:
                task.assigned_to = cast("Any", fallback_dev)
                task.claimed_by = cast("Any", fallback_dev)
                # Self-heal the marker so a subsequent re-fail takes the fast
                # path and the QA-review index (below) attributes the work to
                # the right developer. The marker is unreliable in practice
                # (live 2026-06-27: never persisted), so the work session is
                # the load-bearing resolver; stamping it here makes the two
                # paths converge instead of the marker staying absent forever.
                markers.set_original_developer(task, str(fallback_dev))
                self.log.info(
                    "Task reassigned to revision developer (marker missing)",
                    task_id=str(task_id),
                    revision_developer=str(fallback_dev),
                )
            else:
                task.assigned_to = None
                task.claimed_by = None
                self.log.warning(
                    "No developer found for revision; task unassigned",
                    task_id=str(task_id),
                )

        await self.session.flush()

        # Index negative QA review (fire-and-forget)
        review_task = asyncio.create_task(
            self._index_qa_review_background(
                require_uuid(task.id),
                extract_original_developer(task),
                False,
                notes,
                to_python_uuid(qa_agent_id),
            )
        )
        self._background_tasks.add(review_task)
        review_task.add_done_callback(self._background_tasks.discard)

        # Index issues as error patterns (fire-and-forget)
        error_task = asyncio.create_task(
            self._index_qa_errors_background(
                require_uuid(task.id), task.title, task.team, notes
            )
        )
        self._background_tasks.add(error_task)
        error_task.add_done_callback(self._background_tasks.discard)

        self.log.info("Task failed QA", task_id=str(task_id))
        return task

    async def _resolve_revision_dev(
        self, task: TaskTable, *, exclude: UUID | None
    ) -> UUID | None:
        """The developer who should receive a needs_revision dev task when the
        ``original_developer`` marker is missing.

        A dev task in needs_revision must return to the dev, never the pool: an
        unassigned needs_revision task is claimable by a cell PM, which is how a
        dev task's revision landed on the cell PM live (2026-06-27). Resolves the
        developer who actually worked it — the most recent work session on the
        task whose agent is a developer (the QA's own session, ``exclude``, is
        skipped so the fallback can't hand the task back to QA). Returns None
        only when no developer ever touched the task (then unassign is the last
        resort).
        """
        conditions = [WorkSessionTable.task_id == task.id]
        if exclude is not None:
            conditions.append(WorkSessionTable.agent_id != exclude)
        result = await self.session.execute(
            select(WorkSessionTable)
            .where(and_(*conditions))
            .order_by(WorkSessionTable.started_at.desc())
        )
        for ws in result.scalars().all():
            agent_id = to_python_uuid(ws.agent_id)
            if agent_id is None:
                continue
            agent = await self.agent_for(agent_id)
            if agent is None:
                continue
            role = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
            if role == "developer":
                return agent_id
        return None

    async def docs_complete(
        self,
        task_id: UUID,
        doc_notes: str | None = None,
    ) -> TaskTable | None:
        """
        Mark documentation as complete (documenter only).

        Documenter workflow: awaiting_documentation → claim → plan → start
        → docs_complete. Accept claimed/in_progress (documenter working).

        Args:
            task_id: The task to mark docs complete
            doc_notes: Optional notes about the documentation

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        if not self._validate_docs_complete_status(task, task_id):
            return None
        if not await self._validate_docs_complete_descendants(task_id):
            return None

        self._record_doc_notes(task, doc_notes)
        task.docs_complete = True
        self._record_documenter_context(task)
        await self._maybe_advance_to_pm_review(task, task_id)

        await self.session.flush()

        # Index documentation artifacts (fire-and-forget). Capture the current
        # owner (the documenter) now — i_documented reassigns to the PM right
        # after this returns, so reading assigned_to inside the background task
        # would resolve the wrong workspace.
        if task.documents:
            documenter_id = to_python_uuid(task.assigned_to)
            bg_task = asyncio.create_task(
                self._index_docs_background(
                    require_uuid(task.id), task.documents, documenter_id
                )
            )
            self._background_tasks.add(bg_task)
            bg_task.add_done_callback(self._background_tasks.discard)

        return task

    _DOCS_COMPLETE_VALID_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
    }

    def _validate_docs_complete_status(self, task: TaskTable, task_id: UUID) -> bool:
        """Reject docs_complete attempts from wrong task statuses."""
        if task.status not in self._DOCS_COMPLETE_VALID_STATUSES:
            self.log.warning(
                "Cannot mark docs complete - invalid status for documenter workflow",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return False
        return True

    async def _validate_docs_complete_descendants(self, task_id: UUID) -> bool:
        """Refuse docs_complete while any descendant is still live."""
        all_descendants = await self.get_all_descendants(task_id)
        incomplete = [
            d
            for d in all_descendants
            if d.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        ]
        if incomplete:
            self.log.warning(
                "Cannot mark docs complete - incomplete descendants",
                task_id=str(task_id),
                incomplete_count=len(incomplete),
                incomplete_ids=[str(d.id) for d in incomplete[:5]],
            )
            return False
        return True

    @staticmethod
    def _record_doc_notes(task: TaskTable, doc_notes: str | None) -> None:
        """Capture the documenter's note as a structured DocNote (best-effort).

        Routes through the content chokepoint so it lands in the ``doc_notes``
        mirror + ``notes_structured``. Skips if a richer DocNote already exists
        (the gateway ``note`` tool) or the text is too trivial to validate.
        """
        if not doc_notes:
            return
        if (task.notes_structured or {}).get("doc"):
            return
        try:
            apply_structured_note(task, "doc", {"summary": doc_notes})
        except ContentValidationError:
            return

    @staticmethod
    def _record_documenter_context(task: TaskTable) -> None:
        """Stamp the documenter id into orchestration markers if missing."""
        if not task.assigned_to:
            return
        if markers.get_documenter(task):
            return
        markers.set_documenter(task, task.assigned_to)

    @staticmethod
    def _record_pr_review(
        task: TaskTable,
        *,
        summary: str | None,
        verdict: str,
        issues: list[str] | None = None,
    ) -> None:
        """Record a PR-reviewer verdict in the reviewer's OWN slot.

        Routes through the content chokepoint (``pr_reviewer_notes`` mirror +
        ``notes_structured["pr_review"]``) so a review never overwrites
        ``qa_notes`` / ``dev_notes``. Structured per-line findings arrive via the
        reviewer verb; until then the free-text summary + issues are captured.
        Best-effort: a too-trivial review body must never block the transition.
        Skips when a structured PrReviewContent is already stored (the
        post_pr_review verb with findings does that itself).
        """
        if (task.notes_structured or {}).get("pr_review"):
            return
        body = _compose_review_body(summary, issues)
        if not body:
            return
        try:
            apply_structured_note(
                task, "pr_review", {"summary": body, "verdict": verdict}
            )
        except ContentValidationError:
            task.pr_reviewer_notes = _append_capped(task.pr_reviewer_notes, body)

    async def _resolve_pm_for_review(self, task: TaskTable) -> UUID | None:
        """Walk up the parent chain to find the PM who owns this work.

        A dev subtask's parent is the Cell PM's planning task; that PM is
        who should own the review. Main-PM-level tasks (no parent or
        parent's assignee is Main-PM) route to Main-PM. Returns None if
        nothing useful is found — caller falls back to leaving the task
        unassigned for scan-claim.
        """
        parent_id = to_python_uuid(task.parent_task_id)
        while parent_id:
            parent = await self.get(parent_id)
            if not parent:
                return None
            candidate = to_python_uuid(parent.assigned_to)
            if candidate:
                return candidate
            parent_id = to_python_uuid(parent.parent_task_id)
        return None

    async def _maybe_advance_to_pm_review(self, task: TaskTable, task_id: UUID) -> None:
        """If doc+PR gates both pass, promote to awaiting_pm_review.

        On advance, assign the task to the PM up the parent chain
        (Cell PM for a dev's subtask; Main PM for a Cell-PM task).
        Leaving `assigned_to` null forced PMs to scan-and-claim, which
        added ~1 dispatcher round-trip and occasionally stalled when
        the target PM was already parked waiting_long.
        """
        from roboco.enforcement.task_lifecycle import check_parallel_completion

        ready_for_pm = check_parallel_completion(
            docs_complete=True,
            pr_created=task.pr_created,
        )
        if ready_for_pm:
            self._validate_and_set_status(
                task, TaskStatus.AWAITING_PM_REVIEW, "documenter"
            )
            owning_pm = await self._resolve_pm_for_review(task)
            task.assigned_to = cast("Any", owning_pm) if owning_pm else None
            self.log.info(
                "Documentation complete, awaiting PM review",
                task_id=str(task_id),
                pr_created=task.pr_created,
                routed_to_pm=str(owning_pm) if owning_pm else None,
            )
        else:
            self.log.info(
                "Documentation complete, waiting for developer to create PR",
                task_id=str(task_id),
                docs_complete=True,
                pr_created=task.pr_created,
            )

    async def mark_pr_created(
        self,
        task_id: UUID,
        pr_number: int,
        pr_url: str,
    ) -> TaskTable | None:
        """
        Mark that developer has created a PR for the task.

        Called by the choreographer when the developer's submit_for_qa()
        flow opens the PR. This method:
        1. Sets pr_created=True, pr_number, pr_url on the task
        2. Checks if docs_complete is also True
        3. If both complete, transitions to awaiting_pm_review

        This works in parallel with documenter's docs_complete().

        Args:
            task_id: The task ID
            pr_number: GitHub/GitLab PR number
            pr_url: Full URL to the PR

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Accept statuses valid for the parallel phase. awaiting_documentation
        # is the "pool"; claimed/in_progress happen when doc or dev claims+
        # starts their own task during the parallel phase (status shifts
        # out of awaiting_documentation but the PR-creation side is still
        # the dev's responsibility). Without accepting these, the flag
        # setter refuses and the task stays stuck with pr_created=false
        # forever — the dispatcher then respawns the dev in a loop.
        # Also accept verifying/awaiting_qa/needs_revision because the PR
        # may be created (or re-created) during those states.
        allowed_statuses = {
            TaskStatus.AWAITING_DOCUMENTATION,
            TaskStatus.AWAITING_QA,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.NEEDS_REVISION,
            TaskStatus.VERIFYING,
        }
        if task.status not in allowed_statuses:
            self.log.warning(
                "Cannot mark PR created - task status outside parallel phase",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Set PR info
        task.pr_created = True
        task.pr_number = pr_number
        task.pr_url = pr_url

        # Check if BOTH docs_complete AND pr_created are now true
        from roboco.enforcement.task_lifecycle import check_parallel_completion

        ready_for_pm = check_parallel_completion(
            docs_complete=task.docs_complete,
            pr_created=True,  # We just set this
        )

        if ready_for_pm:
            # Both conditions met - transition to PM review using proper validation
            self._validate_and_set_status(
                task, TaskStatus.AWAITING_PM_REVIEW, "developer"
            )
            # Clear assignment so PM can claim the task for review
            task.assigned_to = None
            task.claimed_by = None
            self.log.info(
                "PR created, awaiting PM review",
                task_id=str(task_id),
                pr_number=pr_number,
                docs_complete=task.docs_complete,
            )
        else:
            # PR created but docs not yet complete
            # Stay in awaiting_documentation, waiting for documenter
            self.log.info(
                "PR created, waiting for documenter to complete",
                task_id=str(task_id),
                pr_number=pr_number,
                docs_complete=task.docs_complete,
            )

        await self.session.flush()
        return task

    def _validate_submit_review_status(self, task: TaskTable, task_id: UUID) -> bool:
        """Task must be in_progress with branch + PR; otherwise log + False.

        A MegaTask umbrella is branchless by design (it assembles no PR of
        its own; each root-subtask carries its own project/branch/PR), yet it
        still walks in_progress -> awaiting_pm_review so ``main_pm_complete``
        can escalate it to the CEO. Waive the branch+PR requirement for a
        batch umbrella — without this, umbrella completion deadlocks in
        in_progress (``submit_pm_review`` returns None, the Main PM loops on
        ``complete`` -> invalid_state forever).
        """
        if task.status != TaskStatus.IN_PROGRESS:
            self.log.warning(
                "Cannot submit for PM review - task not in progress",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return False
        is_umbrella = is_batch_umbrella(
            batch_id=task.batch_id, parent_task_id=task.parent_task_id
        )
        if not task.branch_name and not is_umbrella:
            self.log.warning(
                "Cannot submit for PM review - no branch (claim task first)",
                task_id=str(task_id),
            )
            return False
        if (not task.pr_created or not task.pr_number) and not is_umbrella:
            self.log.warning(
                "Cannot submit for PM review - PR must be created first",
                task_id=str(task_id),
                pr_created=task.pr_created,
                pr_number=task.pr_number,
            )
            return False
        return True

    async def _validate_submit_review_descendants(self, task_id: UUID) -> bool:
        """Parent tasks can't submit for review while children are live."""
        all_descendants = await self.get_all_descendants(task_id)
        incomplete = [
            d
            for d in all_descendants
            if d.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        ]
        if incomplete:
            self.log.warning(
                "Cannot submit for PM review - incomplete descendants",
                task_id=str(task_id),
                incomplete_count=len(incomplete),
                incomplete_ids=[str(d.id) for d in incomplete[:5]],
            )
            return False
        return True

    @staticmethod
    def _record_completion_notes(task: TaskTable, notes: str | None) -> None:
        """Append completion_notes entry to quick_context when supplied."""
        if not notes:
            return
        # A coordination annotation, not a human ResumptionNote — store as a
        # marker so quick_context never carries `completion_notes:<text>` soup.
        markers.set_transition_note(task, "completion", notes)

    async def submit_for_pm_review(
        self,
        task_id: UUID,
        agent_role: str = "cell_pm",
        notes: str | None = None,
    ) -> TaskTable | None:
        """
        Submit a task directly for PM review (PM, QA, or Documenter only).

        Use this for tasks that don't follow the standard dev→QA→docs workflow,
        such as PM validation tasks, QA audit tasks, or other directly-assigned work.
        Even these tasks must have a branch and PR created to maintain git workflow.

        Transitions task from IN_PROGRESS to AWAITING_PM_REVIEW.

        Note: Only PM roles, QA, and Documenter can use this method (not developers).

        Args:
            task_id: The task to submit
            agent_role: Role of the agent submitting (must be PM, QA, or documenter)
            notes: Optional completion notes

        Returns:
            The updated task or None if not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        if not self._validate_submit_review_status(task, task_id):
            return None
        if not await self._validate_submit_review_descendants(task_id):
            return None

        self._record_completion_notes(task, notes)
        self._validate_and_set_status(task, TaskStatus.AWAITING_PM_REVIEW, agent_role)
        await self.session.flush()

        self.log.info(
            "Task submitted for PM review",
            task_id=str(task_id),
            agent_role=agent_role,
        )
        return task

    async def _get_completing_agent_role(self, agent_id: UUID | None) -> str | None:
        """Get the role of the completing agent."""
        if not agent_id:
            return None
        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent and agent.role:
            return agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        return None

    def _is_valid_completion_status(
        self, task: TaskTable, agent_id: UUID | None
    ) -> bool:
        """Check if task is in a valid status for completion."""
        if task.status == TaskStatus.AWAITING_PM_REVIEW:
            return True
        is_own_task = agent_id and task.assigned_to == agent_id
        return task.status == TaskStatus.IN_PROGRESS and bool(is_own_task)

    async def _validate_completion_prerequisites(
        self, task: TaskTable, task_id: UUID, agent_id: UUID | None
    ) -> list[TaskTable] | None:
        """Validate task can be completed. Returns descendants or None."""
        if not self._is_valid_completion_status(task, agent_id):
            self.log.warning("Cannot complete - invalid status", task_id=str(task_id))
            return None

        all_descendants = await self.get_all_descendants(task_id)
        incomplete = [
            st
            for st in all_descendants
            if st.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
        ]
        if incomplete:
            self.log.warning(
                "Cannot complete - incomplete descendants", task_id=str(task_id)
            )
            return None
        return all_descendants

    async def _trigger_completion_hooks(
        self, task: TaskTable, agent_id: UUID | None
    ) -> None:
        """Trigger background RAG indexing hooks after completion."""
        bg_task = asyncio.create_task(
            self._extract_completion_learnings(task, agent_id)
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        if task.commits:
            code_task = asyncio.create_task(
                self._index_code_changes_background(
                    require_uuid(task.id),
                    task.commits,
                    task.team.value if task.team else "default",
                )
            )
            self._background_tasks.add(code_task)
            code_task.add_done_callback(self._background_tasks.discard)

        if task.dev_notes:
            decision_task = asyncio.create_task(
                self._index_decisions_background(
                    require_uuid(task.id),
                    task.title,
                    task.team,
                    task.dev_notes,
                    to_python_uuid(task.assigned_to),
                )
            )
            self._background_tasks.add(decision_task)
            decision_task.add_done_callback(self._background_tasks.discard)

    async def _apply_complete_approval_chain(
        self,
        task: TaskTable,
        task_id: UUID,
        completing_agent_role: str | None,
        all_descendants: list[TaskTable],
    ) -> TaskTable | None:
        """Run the PM-completion approval chain; return escalated task or None.

        The cell_pm branch was removed (it reassigned every
        ``awaiting_pm_review`` task from the cell PM to the main PM and
        kept it in ``awaiting_pm_review`` — a legacy two-step "cell PM
        approves, main PM approves" review chain). The gateway model
        actually in use forbids ``main_pm_complete`` on any non-root
        task (``parent_task_id IS NOT NULL`` → invalid_state), so a
        leaf or cell-level task reassigned that way was permanently
        wedged — main PM had no verb to advance it. Cell PM completing
        a non-root task now just transitions it to COMPLETED (the
        gateway model); the cell→main escalation, when intended,
        happens via the dedicated ``submit_up`` verb, not via
        ``complete``. The main_pm → CEO escalation for root parents
        stays — it's the still-correct second tier.
        """
        if task.status != TaskStatus.AWAITING_PM_REVIEW:
            return None
        is_root_parent = all_descendants and not task.parent_task_id
        if completing_agent_role == "main_pm" and is_root_parent:
            self.log.info(
                "Main PM approved root parent - escalating to CEO",
                task_id=str(task_id),
            )
            return await self.escalate_to_ceo(task_id, "main_pm")
        return None

    async def _assert_pr_merged_for_complete(self, task: TaskTable) -> bool:
        """True if the task's PR is merged (or no PR gate applies).

        Non-root tasks must have their PR merged before a PM can mark
        them completed — mirrors the CEO-approve guard but applies to
        the PM's own awaiting_pm_review → completed transition.
        Root parent tasks and tasks without a work_session skip this
        check (escalation to CEO handles them).
        """
        if not task.work_session_id:
            return True
        result = await self.session.execute(
            select(WorkSessionTable).where(WorkSessionTable.id == task.work_session_id)
        )
        ws = result.scalar_one_or_none()
        if ws is None or ws.pr_status == "merged":
            return True
        self.log.warning(
            "Cannot complete - PR must be merged first",
            task_id=str(task.id),
            pr_status=ws.pr_status,
            pr_number=ws.pr_number,
        )
        return False

    @staticmethod
    def _cancelled_force_allowed(
        all_descendants: list[TaskTable],
        force_with_cancelled: bool,
        justification: str | None,
    ) -> bool:
        """Return True if any cancelled descendants are acceptable to the PM."""
        has_cancelled = any(st.status == TaskStatus.CANCELLED for st in all_descendants)
        if not has_cancelled:
            return True
        return force_with_cancelled and bool(justification)

    async def complete(
        self,
        task_id: UUID,
        agent_id: UUID | None = None,
        force_with_cancelled: bool = False,
        justification: str | None = None,
    ) -> TaskTable | None:
        """
        Mark task as completed (PM only).

        Approval model (matches the gateway invariant
        ``main_pm_complete`` rejects any non-root task):

        - Cell PM completes a non-root task → COMPLETED. The cell→main
          escalation, when intended, is the cell PM's ``submit_up``
          verb on the cell-level parent, not ``complete``.
        - Main PM completes a leaf/non-root task → COMPLETED.
        - Main PM completes a root parent (descendants all terminal)
          → escalates to CEO (``awaiting_ceo_approval``).
        """
        task = await self.get(task_id)
        if not task:
            return None

        completing_agent_role = await self._get_completing_agent_role(agent_id)
        all_descendants = await self._validate_completion_prerequisites(
            task, task_id, agent_id
        )
        if all_descendants is None:
            return None

        escalated = await self._apply_complete_approval_chain(
            task, task_id, completing_agent_role, all_descendants
        )
        if escalated:
            return escalated

        if not self._cancelled_force_allowed(
            all_descendants, force_with_cancelled, justification
        ):
            self.log.warning(
                "Cannot complete - cancelled descendants", task_id=str(task_id)
            )
            return None

        if not await self._assert_pr_merged_for_complete(task):
            return None

        task.completed_at = datetime.now(UTC)
        self._validate_and_set_status(
            task, TaskStatus.COMPLETED, completing_agent_role or "cell_pm"
        )
        await self._close_work_session_for_task(task, reason="task completed")
        await self._remove_task_worktree_on_terminal(task)
        await self.session.flush()

        await self._trigger_completion_hooks(task, agent_id)
        await self._unblock_dependents(task_id)
        return task

    async def apply_escalation(
        self,
        *,
        task: TaskTable,
        target_agent_id: UUID,
        escalator_slug: str,
        target_slug: str,
        reason: str,
    ) -> bool:
        """Apply the state mutations for a generic chain escalation.

        Sets the task to BLOCKED, reassigns to the escalation target, and
        appends an [ESCALATED] line to dev_notes. Notification delivery is
        handled upstream by `NotificationDeliveryService.escalate_and_notify`.

        Records the pre-escalation assignee as `blocker_raised_by` so the
        subsequent `unblock` call hands the task back to the original dev
        and the orchestrator re-spawns them. Without this, escalation
        loses the dev's identity permanently.

        Returns True when the escalation was applied (or diverted to the pool
        by the board/main-pm guard); False when refused because the task is in
        a terminal state (COMPLETED / CANCELLED) — a terminal task must not be
        resurrected to BLOCKED (F043). The gateway ``escalate_up`` path also
        guards this in the lifecycle spec, but the HTTP escalate route bypasses
        the spec gate, so the single write primitive refuses it here too.

        Invariant: a board/advisory role is NEVER assigned a task it cannot own
        — a descendant executable task (code / documentation / design), a cell's
        own coordination/planning descendant, OR a Main-PM coordination root
        (see ``_board_cannot_own``). They have no verb to build, delegate,
        unblock, or complete such work. The escalation chain points main-pm at
        product-owner, so without the coordination-root arm a Main PM's
        ``i_am_blocked`` on its own root reassigned the whole root to the board,
        which then respawn-looped on a blocker it could not resolve. Such an
        escalation is diverted to a pool release so a role-matched agent reclaims
        it. Enforced here — the single write primitive — so both the gateway
        ``escalate`` verb and the HTTP escalate route are covered.
        """
        if _is_terminal_task(task):
            self.log.warning(
                "Refusing to escalate a terminal task (no resurrection to blocked)",
                task_id=str(task.id),
                status=str(task.status),
                escalator=escalator_slug,
                target=target_slug,
            )
            return False
        if _board_cannot_own(task) and await self._is_board_advisory_agent(
            target_agent_id
        ):
            await self._release_code_task_to_pool(
                task=task,
                escalator_slug=escalator_slug,
                blocked_target_slug=target_slug,
                reason=reason,
            )
            return True
        # Impossibility backstop: a Main-PM target must never receive (back) a
        # main_pm + code task — a coordinator with no code verb cannot fix the
        # code, so escalating it to Main PM perpetuates the mismatch (the
        # 2026-06-27 meltdown shape). Scoped to the team+type combo (NOT a broad
        # code+main-pm-target rule) so a legacy main_pm+code task can still be
        # escalated to a cell dev — the correct remediation. The combo is
        # uncreatable going forward (create backstop + intake coercion), so this
        # is a backstop for legacy / direct-ORM-write tasks.
        if main_pm_cannot_own_code(
            team=task.team, task_type=task.task_type
        ) and await self._is_main_pm_agent(target_agent_id):
            await self._release_code_task_to_pool(
                task=task,
                escalator_slug=escalator_slug,
                blocked_target_slug=target_slug,
                reason=reason,
            )
            return True
        if task.assigned_to and not task.blocker_raised_by:
            task.blocker_raised_by = cast("Any", task.assigned_to)
        # Capture before mutating: the audit row must record the real prior
        # status and attribute the block to the outgoing owner, not the
        # escalation target we are about to assign.
        pre_block_status = (
            task.status.value
            if isinstance(task.status, TaskStatus)
            else str(task.status)
        )
        pre_block_owner = cast("Any", task.claimed_by)
        self._snapshot_pre_block(task)
        task.assigned_to = cast("Any", target_agent_id)
        task.claimed_by = cast("Any", target_agent_id)
        task.status = TaskStatus.BLOCKED
        # Record the escalation as a structured marker — NOT appended to
        # dev_notes (the developer's space). The old append polluted dev_notes
        # and, on a re-escalation loop, grew it unboundedly (a cell PM stuck on
        # needs_revision escalated 5x → 8KB of [ESCALATED] blocks). The target
        # learns the reason from the escalate notification (escalate_and_notify).
        markers.set_escalation(
            task, from_slug=escalator_slug, to_slug=target_slug, reason=reason
        )
        await self.session.flush()
        # This path sets BLOCKED directly (bypassing the strict transition
        # validator), so emit the task.blocked audit explicitly — no status
        # change may skip the audit log.
        self._emit_status_transition_audit(
            task,
            from_status=pre_block_status,
            to_status=TaskStatus.BLOCKED.value,
            agent_role=None,
            audit_agent_id=pre_block_owner,
        )
        self.log.info(
            "Task escalated and blocked",
            task_id=str(task.id),
            escalator=escalator_slug,
            target=target_slug,
        )
        return True

    # =========================================================================
    # CEO APPROVAL WORKFLOW
    # =========================================================================

    async def escalate_to_ceo(
        self,
        task_id: UUID,
        agent_role: str = "cell_pm",
        notes: str | None = None,
    ) -> TaskTable | None:
        """
        Escalate a task to CEO for final approval (PM only).

        Used for major tasks that require CEO sign-off before merge:
        - Parent tasks with subtasks
        - High-priority features
        - Breaking changes

        Args:
            task_id: The task to escalate
            agent_role: Role of the agent escalating (must be PM)
            notes: Optional notes for the CEO

        Returns:
            The escalated task or None if escalation not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow escalation from awaiting_pm_review
        if task.status != TaskStatus.AWAITING_PM_REVIEW:
            self.log.warning(
                "Cannot escalate to CEO - task not in PM review",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Only parent tasks can be escalated to CEO (not subtasks)
        if task.parent_task_id:
            self.log.warning(
                "Cannot escalate subtask to CEO - only parent tasks allowed",
                task_id=str(task_id),
                parent_task_id=str(task.parent_task_id),
            )
            return None

        # ENFORCEMENT: Tasks must have a PR before CEO approval — EXCEPT a
        # MegaTask umbrella, which is branchless by design (assembles no PR of
        # its own; each root-subtask carries its own project/branch/PR). It
        # escalates to the CEO with no pr_number once every root-subtask is done.
        if not task.pr_number and not is_batch_umbrella(
            batch_id=task.batch_id, parent_task_id=task.parent_task_id
        ):
            self.log.warning(
                "Cannot escalate to CEO - task has no PR",
                task_id=str(task_id),
                pr_created=task.pr_created,
            )
            return None

        # Store the escalation note as a marker, not quick_context soup.
        if notes:
            markers.set_transition_note(task, "escalate_to_ceo", notes)

        # Validate transition with PM role requirement
        self._validate_and_set_status(
            task, TaskStatus.AWAITING_CEO_APPROVAL, agent_role
        )
        await self.session.flush()

        # Emit event for CEO approval queue
        await self._emit_task_event(
            EventType.TASK_AWAITING_CEO_APPROVAL,
            task_id,
            {"escalated_by_role": agent_role, "notes": notes},
        )

        self.log.info(
            "Task escalated to CEO for approval",
            task_id=str(task_id),
            escalated_by_role=agent_role,
        )
        return task

    async def ceo_approve(
        self,
        task_id: UUID,
        notes: str | None = None,
    ) -> TaskTable | None:
        """
        CEO approves and completes a task.

        Final approval step for major tasks. Only CEO can perform this action.
        PR must be merged before approval (CEO merges as final action).

        Args:
            task_id: The task to approve
            notes: Optional CEO notes

        Returns:
            The completed task or None if approval not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow approval from awaiting_ceo_approval
        if task.status != TaskStatus.AWAITING_CEO_APPROVAL:
            self.log.warning(
                "Cannot CEO approve - task not awaiting CEO approval",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Verify PR is merged (CEO merges as final action before approving)
        if task.work_session_id:
            work_session_result = await self.session.execute(
                select(WorkSessionTable).where(
                    WorkSessionTable.id == task.work_session_id
                )
            )
            work_session = work_session_result.scalar_one_or_none()
            if work_session and work_session.pr_status != "merged":
                self.log.warning(
                    "Cannot CEO approve - PR must be merged first",
                    task_id=str(task_id),
                    pr_status=work_session.pr_status,
                    pr_number=work_session.pr_number,
                )
                return None

        # Store the CEO's approval note as a marker, not quick_context soup.
        if notes:
            markers.set_transition_note(task, "ceo_approval", notes)

        task.completed_at = datetime.now(UTC)
        # Validate transition with CEO role requirement
        self._validate_and_set_status(task, TaskStatus.COMPLETED, "ceo")
        await self.session.flush()
        await self._remove_task_worktree_on_terminal(task)

        # Extract learnings (fire-and-forget)
        bg_task = asyncio.create_task(self._extract_completion_learnings(task, None))
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        # Unblock any tasks waiting on this one
        await self._unblock_dependents(task_id)

        # Emit event for CEO approval
        await self._emit_task_event(
            EventType.TASK_CEO_APPROVED,
            task_id,
            {"notes": notes},
        )

        self.log.info(
            "Task approved by CEO",
            task_id=str(task_id),
        )
        return task

    async def approve_and_start(
        self,
        task_id: UUID,
        notes: str | None = None,
    ) -> TaskTable | None:
        """CEO gate #1: hand a board-reviewed (pending) task to Main PM.

        This is a reassignment, NOT a status transition. Board tasks live in
        the pending pool; setting assigned_to -> main-pm (status unchanged at
        pending) makes the orchestrator's _handle_pm_assigned_task spawn Main
        PM on the next dispatch tick. Idempotent when already on main-pm;
        returns None when the task is not in a startable (pending) state.
        """
        from roboco.services.agent import get_agent_service

        task = await self.get(task_id)
        if not task:
            return None
        if task.status != TaskStatus.PENDING:
            self.log.warning(
                "Cannot approve_and_start - task not pending",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None
        # A board task may not be handed to Main PM until the Product Owner and
        # Head of Marketing have finished reviewing. Today only the UI hides the
        # button; enforce the invariant server-side so an early/rogue call can't
        # start the work before the board's input exists. Non-board tasks
        # (team already MAIN_PM) are unaffected — only a task still on the board
        # carries this precondition.
        if task.team == Team.BOARD and not task.board_review_complete:
            self.log.warning(
                "Cannot approve_and_start - board review not complete",
                task_id=str(task_id),
            )
            return None

        main_pm = await get_agent_service(self.session).get_by_slug("main-pm")
        if main_pm is None:
            self.log.error("approve_and_start - main-pm agent not found")
            return None

        already = task.assigned_to == main_pm.id
        task.assigned_to = cast("Any", main_pm.id)
        # this is the CEO's start gate — approving the task confirms it for
        # dispatch. A self-heal fix task is opened held
        # (confirmed_by_human=False) so dispatch skips it until now; flipping
        # it True lifts that hold. Idempotent for board/intake tasks (already
        # confirmed at creation). The release-manager proposal is not routed
        # here — it has its own CEO routes.
        task.confirmed_by_human = cast("Any", True)
        # The board-reviewed coordination task now belongs to Main PM, who will
        # delegate it to the cells. Leaving team="board" is misleading once it's
        # off the board — reflect the new owner. Team.MAIN_PM is a valid non-cell
        # team and does not affect dispatch (which routes by assignee, not team).
        task.team = cast("Any", Team.MAIN_PM)
        # A board-approved task handed to Main PM must not stay `code` — a Main
        # PM coordinates, it never owns a code task (the 2026-06-27 meltdown was
        # a main_pm + code root). A board-routed PROJECT code task reaches here
        # with team=cell and task_type=code (intake only coerces main_pm-team
        # drafts); once team is flipped to MAIN_PM the combo would persist.
        # Retype code→planning so it's a planning-typed coordination root the
        # Main PM delegates to the cells. (Intake coerces this too; this is the
        # board-review backstop.)
        if main_pm_cannot_own_code(team=task.team, task_type=task.task_type):
            self.log.info(
                "approve_and_start retyped main-pm code task to planning",
                task_id=str(task_id),
            )
            task.task_type = cast("Any", TaskType.PLANNING)

        if notes:
            # Coordination metadata, not a human handoff — store as a marker so
            # quick_context carries only the structured ResumptionNote (no raw
            # `approve_and_start_notes:<text>` soup leaking into the panel).
            markers.set_approve_and_start_notes(task, notes)

        await self.session.flush()
        # A MegaTask umbrella holds its root-subtasks in BACKLOG until this gate
        # (the board reviews the batch first). Now that the CEO approved it,
        # release them so the dependency-gate dispatches wave 0.
        await self._activate_batch_root_subtasks(task)
        await self._emit_task_event(
            EventType.TASK_STARTED,
            task_id,
            {"action": "approve_and_start", "notes": notes, "idempotent": already},
        )
        self.log.info(
            "Task handed to Main PM (approve_and_start)",
            task_id=str(task_id),
            idempotent=already,
        )
        return task

    async def _activate_batch_root_subtasks(self, umbrella: TaskTable) -> None:
        """Release a MegaTask umbrella's held root-subtasks on CEO approval.

        No-op unless ``umbrella`` is a batch umbrella. The board route creates the
        root-subtasks in BACKLOG (team=board) so the work waits for the batch
        review; once the CEO approves the umbrella to the Main PM, flip each held
        child to PENDING + team=main_pm. The dependency-gate then dispatches the
        wave-0 items and holds later waves until their predecessors are terminal.
        Idempotent: a child already past BACKLOG is left untouched.
        """
        if not is_batch_umbrella(
            batch_id=umbrella.batch_id, parent_task_id=umbrella.parent_task_id
        ):
            return
        released = False
        for child in await self.get_subtasks(cast("UUID", umbrella.id)):
            if child.batch_id is not None and child.status == TaskStatus.BACKLOG:
                child.status = TaskStatus.PENDING
                child.team = cast("Any", Team.MAIN_PM)
                # a board-routed root-subtask is created in BACKLOG with
                # team=board and task_type=code. Now that team is flipped to
                # MAIN_PM, leaving task_type=code would re-introduce the
                # main_pm+code meltdown — retype code->planning so the activated
                # child is a planning-typed coordination root the Main PM
                # delegates to the cells.
                if main_pm_cannot_own_code(team=child.team, task_type=child.task_type):
                    self.log.info(
                        "activate_batch_root_subtasks retyped main-pm code "
                        "root-subtask to planning",
                        task_id=str(child.id),
                    )
                    child.task_type = cast("Any", TaskType.PLANNING)
                released = True
        if released:
            await self.session.flush()

    async def mark_board_review_complete(self, task_id: UUID) -> bool:
        """Flag a board task as board-reviewed without moving it off pending.

        The task stays pending (that pending state is what makes
        ``approve_and_start`` hand it to Main PM). This flag only unlocks the
        CEO's Approve & Start button, so the button never shows on a board task
        the PO + Head of Marketing haven't finished reviewing. Idempotent;
        returns True when it flips the flag, False when already set or missing.
        """
        task = await self.get(task_id)
        if task is None or task.board_review_complete:
            return False
        task.board_review_complete = True
        await self.session.flush()
        return True

    async def _write_handoff_journal(
        self, *, author_id: UUID, task_id: UUID, title: str, content: str
    ) -> None:
        """Record a handoff journal entry from ``author_id`` for ``task_id``.

        Handoff-typed (``DECISION_LOG``) entries are the channel that actually
        reaches a downstream worker: ``EvidenceRepo.journal_highlights_for_task``
        serves them into the assignee's evidence/briefing. ``quick_context`` is
        write-only by comparison. Adds rows to the session WITHOUT committing — the
        caller owns the transaction boundary.
        """
        # Best-effort: never let a journaling hiccup block the reject. In
        # production the author (CEO) is a seeded agent; guard so a missing author
        # (e.g. a minimal test DB) skips cleanly instead of FK-failing the flush.
        author_exists = await self.session.scalar(
            select(AgentTable.id).where(AgentTable.id == author_id)
        )
        if author_exists is None:
            self.log.warning(
                "Handoff journal skipped - author agent not found",
                author_id=str(author_id),
            )
            return

        result = await self.session.execute(
            select(JournalTable).where(JournalTable.agent_id == author_id)
        )
        journal = result.scalar_one_or_none()
        if journal is None:
            journal = JournalTable(agent_id=author_id)
            self.session.add(journal)
            await self.session.flush()
        self.session.add(
            JournalEntryTable(
                journal_id=journal.id,
                type=JournalEntryType.DECISION_LOG,
                title=title,
                content=content,
                task_id=task_id,
            )
        )

    async def ceo_reject(
        self,
        task_id: UUID,
        reason: str,
    ) -> TaskTable | None:
        """
        CEO rejects a task and sends back for revision.

        Task goes back to NEEDS_REVISION and is reassigned to the
        original developer (if tracked in quick_context).

        Args:
            task_id: The task to reject
            reason: Required reason for rejection

        Returns:
            The rejected task or None if rejection not allowed
        """
        task = await self.get(task_id)
        if not task:
            return None

        # Only allow rejection from awaiting_ceo_approval
        if task.status != TaskStatus.AWAITING_CEO_APPROVAL:
            self.log.warning(
                "Cannot CEO reject - task not awaiting CEO approval",
                task_id=str(task_id),
                current_status=task.status.value,
            )
            return None

        # Store the CEO's rejection reason as a marker, not quick_context soup.
        markers.set_transition_note(task, "ceo_rejection", reason)

        # A branchless coordination root (a product integration root or a
        # MegaTask umbrella) has no developer to revise it — NEEDS_REVISION is
        # developer-claim-only, so it would deadlock the Main PM that owns the
        # root. Such a root is routed to PENDING below instead; every other task
        # takes the normal NEEDS_REVISION path back toward its developer.
        is_coordination_root = is_branchless_coordination(
            project_id=task.project_id,
            product_id=task.product_id,
            batch_id=task.batch_id,
            parent_task_id=task.parent_task_id,
            has_cell_projects=bool(task.cell_projects),
        )
        if not is_coordination_root:
            self._validate_and_set_status(task, TaskStatus.NEEDS_REVISION, "ceo")

        # Surface the CEO's required changes through the task journal — the one
        # channel a downstream worker actually reads (evidence.journal_highlights
        # serves handoff entries into the briefing). quick_context above is
        # write-only, so the reason would otherwise never reach the reworker.
        await self._write_handoff_journal(
            author_id=UUID(AGENT_UUIDS["ceo"]),
            task_id=task_id,
            title="CEO change request",
            content=reason,
        )

        # Route the rejected task to whoever should drive the rework.
        reassigned_to: str | None
        if is_coordination_root:
            # Coordination/integration root: the Main PM re-plans and
            # re-delegates the rework — a board/dev role cannot drive it. Land it
            # in PENDING (the Main PM's claim source), claim cleared, so it
            # re-enters plan→delegate. awaiting_ceo_approval→pending has no
            # in-band transition, so use the audited privileged override.
            main_pm_id = UUID(AGENT_UUIDS["main-pm"])
            task.team = Team.MAIN_PM
            task.assigned_to = cast("Any", main_pm_id)
            task.claimed_by = None
            reassigned_to = str(main_pm_id)
            await self.session.flush()
            await self.admin_set_status(
                task_id,
                TaskStatus.PENDING,
                actor_role="ceo",
                actor_id=AGENT_UUIDS["ceo"],
            )
            self.log.info(
                "Coordination task rejected by CEO - routed to Main PM (pending)",
                task_id=str(task_id),
            )
        else:
            original_dev = extract_original_developer(task)
            if original_dev:
                task.assigned_to = cast("Any", UUID(original_dev))
                task.claimed_by = cast("Any", UUID(original_dev))
                self.log.info(
                    "Task reassigned to original developer after CEO rejection",
                    task_id=str(task_id),
                    original_developer=original_dev,
                )
                reassigned_to = original_dev
            else:
                # No tracked developer: leave for the pool to claim.
                task.assigned_to = None
                task.claimed_by = None
                reassigned_to = None

        await self.session.flush()

        # Emit event for CEO rejection
        await self._emit_task_event(
            EventType.TASK_CEO_REJECTED,
            task_id,
            {"reason": reason, "reassigned_to": reassigned_to},
        )

        self.log.info(
            "Task rejected by CEO",
            task_id=str(task_id),
            reason=reason,
        )
        return task

    async def _abandon_work_session_for_task(
        self, task: TaskTable, reason: str
    ) -> None:
        """Mark the task's active work session as abandoned, if any.

        Without this, cancelled tasks leave their WorkSessionTable row in
        ACTIVE status forever, polluting list_active_sessions queries.
        """
        if not task.work_session_id:
            return
        from roboco.services.work_session import get_work_session_service

        ws_service = get_work_session_service(self.session)
        await ws_service.abandon(require_uuid(task.work_session_id), reason=reason)

    async def _delete_task_branch_best_effort(self, task: TaskTable) -> None:
        """Delete the task's remote branch + per-task worktree on cancel.

        Best-effort, never raises. Skipped for tasks that didn't make it to a
        branch yet, or whose PR already merged (merge path deletes the source
        branch). The worktree at ``{clone_root}/.worktrees/{task-short}/`` is
        removed from the assignee's clone so cancelled tasks don't leak full
        working trees on disk (F123). The stale-claim reaper must NOT call this
        — it routes to ``pending`` for a re-claim that reuses the worktree.
        """
        branch = task.branch_name
        if not branch:
            return
        try:
            project_result = await self.session.execute(
                select(ProjectTable.slug).where(ProjectTable.id == task.project_id)
            )
            project_slug = project_result.scalar_one_or_none()
            if not project_slug:
                return
            from roboco.services.git import get_git_service

            git_service = get_git_service(self.session)
            await git_service.delete_task_branch(project_slug, str(branch))
            await self._remove_task_worktree_best_effort(task, project_slug)
        except Exception as e:
            # Cleanup is best-effort — don't fail the cancel if the
            # remote is unreachable or the branch is already gone.
            self.log.warning(
                "Branch cleanup skipped",
                task_id=str(task.id),
                branch=str(branch),
                error=str(e),
            )

    async def _remove_task_worktree_best_effort(
        self, task: TaskTable, project_slug: str
    ) -> None:
        """Remove the per-task worktree from the assignee's clone. Never raises.

        No-op when the task has no resolvable assignee (pooled/unassigned at
        cancel) or the assignee carries no team (can't form a clone path).
        """
        assignee = task.assignee
        if assignee is None or assignee.team is None or assignee.slug is None:
            return
        from roboco.services.workspace import get_workspace_service

        ws_service = get_workspace_service(self.session)
        clone_root = ws_service.get_clone_root_path(
            project_slug, assignee.team, assignee.slug
        )
        worktree = clone_root / ".worktrees" / str(task.id)[:8]
        await ws_service.remove_worktree(clone_root, worktree)

    async def _remove_task_worktree_on_terminal(self, task: TaskTable) -> None:
        """Best-effort per-task worktree removal on terminal completion.

        Mirrors the cancel-path cleanup but WITHOUT deleting the remote branch
        (the merge path already deleted it). A completed/merged task would
        otherwise leak its worktree on disk until the whole agent is deleted
        (F123). Best-effort: never raises, so a cleanup failure can't block
        completion. No-op for branchless tasks (no worktree was ever cut).
        Terminal-only by call site — earlier review states may bounce
        ``needs_revision`` and need the worktree back.
        """
        if not task.branch_name:
            return
        try:
            result = await self.session.execute(
                select(ProjectTable.slug).where(ProjectTable.id == task.project_id)
            )
            project_slug = result.scalar_one_or_none()
            if not project_slug:
                return
            await self._remove_task_worktree_best_effort(task, project_slug)
        except Exception as e:
            self.log.warning(
                "Terminal worktree cleanup skipped",
                task_id=str(task.id),
                error=str(e),
            )

    async def _close_work_session_for_task(self, task: TaskTable, reason: str) -> None:
        """Close the task's work session on successful completion.

        `abandon()` is for cancellation (work was discarded). `close()` is
        for successful completion — the PR is merged and we want the
        session marked completed rather than abandoned so reporting can
        distinguish the two outcomes.
        """
        if not task.work_session_id:
            return
        from roboco.services.work_session import get_work_session_service

        ws_service = get_work_session_service(self.session)
        await ws_service.close(require_uuid(task.work_session_id), reason=reason)

    async def cancel(
        self,
        task_id: UUID,
        agent_role: str = "cell_pm",
        cancellation_note: str | None = None,
    ) -> TaskTable | None:
        """Cancel a task and all its descendants (PM only).

        If `cancellation_note` is supplied it's appended to `dev_notes` so
        the audit trail captures who cancelled and why — keeps this out of
        route handlers.
        """
        task = await self.get(task_id)
        if not task:
            return None

        if cancellation_note:
            task.dev_notes = (
                f"{task.dev_notes}\n{cancellation_note}"
                if task.dev_notes
                else cancellation_note
            )
            await self.session.flush()

        # Cancel all descendants first (children, grandchildren, etc.)
        # Skip tasks already in terminal states (completed or cancelled).
        # Route every descendant through _validate_and_set_status so role
        # restrictions (e.g., only CEO can cancel awaiting_ceo_approval) still
        # apply to cascaded cancels — skip descendants that fail validation
        # rather than bypassing the rules.
        descendants = await self.get_all_descendants(task_id)
        cancelled_count = 0
        for descendant in descendants:
            if descendant.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                continue
            try:
                self._validate_and_set_status(
                    descendant, TaskStatus.CANCELLED, agent_role
                )
            except Exception as e:
                self.log.warning(
                    "Skipping cascade-cancel of descendant; role not permitted",
                    descendant_id=str(descendant.id),
                    descendant_status=descendant.status.value,
                    agent_role=agent_role,
                    error=str(e),
                )
                continue
            cancelled_count += 1
            await self._abandon_work_session_for_task(
                descendant, reason="parent task cancelled"
            )
            await self._delete_task_branch_best_effort(descendant)

        if cancelled_count > 0:
            self.log.info(
                "Cascaded cancel to descendants",
                task_id=str(task_id),
                cancelled_count=cancelled_count,
            )

        # Validate transition with PM role requirement
        self._validate_and_set_status(task, TaskStatus.CANCELLED, agent_role)
        await self._abandon_work_session_for_task(task, reason="task cancelled")
        await self._delete_task_branch_best_effort(task)
        await self.session.flush()

        # Index lifecycle event (fire-and-forget)
        bg_task = asyncio.create_task(
            self._index_lifecycle_event_background(
                task_id=task_id,
                event_type="cancel",
                task_title=task.title,
                task_team=task.team,
                details={
                    "cancelled_by_role": agent_role,
                    "descendants_cancelled": cancelled_count,
                },
            )
        )
        self._background_tasks.add(bg_task)
        bg_task.add_done_callback(self._background_tasks.discard)

        return task

    async def _emit_task_event(
        self,
        event_type: EventType,
        task_id: UUID,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Emit a task lifecycle event to the event bus.

        Events are published asynchronously. Failures are logged but
        do not interrupt the calling operation.

        Args:
            event_type: The type of event to emit
            task_id: The task this event relates to
            data: Optional additional event data
        """
        try:
            bus = get_event_bus()
            if bus.is_connected():
                event_data = {"task_id": str(task_id)}
                if data:
                    event_data.update(data)
                await bus.publish(Event(type=event_type, data=event_data))
        except Exception as e:
            self.log.warning(
                "Failed to emit task event",
                event_type=event_type.value,
                task_id=str(task_id),
                error=str(e),
            )

    async def _unblock_dependents(self, completed_task_id: UUID) -> None:
        """Unblock tasks that were waiting on the completed task."""
        result = await self.session.execute(
            select(TaskTable).where(
                TaskTable.dependency_ids.contains([completed_task_id])
            )
        )
        blocked_tasks = result.scalars().all()

        for task in blocked_tasks:
            # Remove the completed task from dependencies, but remember it so the
            # unblock briefing can tell the revived dependent which upstream task
            # just landed (otherwise the only record of it is destroyed here).
            task.dependency_ids = [
                dep_id for dep_id in task.dependency_ids if dep_id != completed_task_id
            ]
            if completed_task_id not in task.completed_dependency_ids:
                task.completed_dependency_ids = [
                    *task.completed_dependency_ids,
                    completed_task_id,
                ]
            # If no more dependencies, unblock (system action - no role validation)
            if not task.dependency_ids and task.status == TaskStatus.BLOCKED:
                await self._revive_unblocked_dependent(task)
                self.log.info(
                    "Task auto-unblocked",
                    task_id=str(task.id),
                    completed_dependency=str(completed_task_id),
                )

        await self.session.flush()

    async def _revive_unblocked_dependent(self, task: TaskTable) -> None:
        """Resume — or re-home — a task whose last dependency just cleared.

        Resume in place when a workable owner still holds it. Re-home to the
        pool when the owner is board/advisory or absent on a cell task: such an
        owner has no verb to work it, so resuming would re-deadlock the task the
        instant its dependency lands.
        """
        owner = cast("Any", task.claimed_by or task.assigned_to)
        needs_rehome = owner is None or await self._is_board_advisory_agent(owner)
        if needs_rehome and _board_cannot_own(task):
            await self._divert_owned_task_to_pool(
                task,
                note=(
                    "\n\n[REVIVAL REDIRECTED] dependency cleared but the owner"
                    " could not work this cell task (board/advisory or"
                    " unassigned). Released to the pool for a role-matched claim."
                ),
            )
        else:
            self._validate_and_set_status(task, TaskStatus.IN_PROGRESS, None)

    # =========================================================================
    # PROGRESS AND CHECKPOINTS
    # =========================================================================

    async def add_progress(
        self,
        task_id: UUID,
        agent_id: UUID,
        message: str,
        percentage: int | None = None,
    ) -> TaskTable | None:
        """Add a progress update to a task."""
        task = await self.get(task_id)
        if not task:
            return None

        update = {
            "timestamp": datetime.now(UTC).isoformat(),
            "agent_id": str(agent_id),
            "message": message,
            "percentage": percentage,
        }
        task.progress_updates = [*task.progress_updates, update]
        await self.session.flush()

        return task

    async def record_plan_progress(
        self,
        task_id: UUID,
        agent_id: UUID,
        message: str,
        plan_step: str | None = None,
        fallback_percentage: int | None = None,
    ) -> dict[str, Any] | None:
        """Append a progress update whose % is DERIVED from the plan checklist.

        The plan's sub_tasks ARE the progress skeleton. When
        ``plan_step`` (a sub_task id, or 1-based order/index) is given,
        that step is marked ``completed`` and the percentage is computed
        as completed/total (equal weight) — the agent cannot game it.
        Narrative entries (no plan_step) are allowed for documentation
        and carry the CURRENT derived % so the bar never regresses. When
        the task has no sub_task checklist the agent's
        ``fallback_percentage`` is used (back-compat).

        Returns ``None`` if the task is missing, else a dict:
        ``{"task", "percentage", "step_resolved": bool | None,
        "valid_steps": [str]}``. ``step_resolved`` is None when no
        plan_step was requested; False when requested but unmatched (the
        caller surfaces a remediation listing ``valid_steps``).
        """
        task = await self.get(task_id)
        if not task:
            return None

        plan, sub_tasks = _plan_subtasks(task)
        step_resolved: bool | None = None
        if plan_step is not None:
            step_resolved = _mark_subtask_complete(sub_tasks, plan_step)
            if step_resolved:
                # Reassign so the JSON column registers the mutation.
                task.plan = {**plan, "sub_tasks": sub_tasks}

        percentage = _derive_plan_pct(sub_tasks, fallback_percentage)
        task.progress_updates = [
            *task.progress_updates,
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "agent_id": str(agent_id),
                "message": message,
                "percentage": percentage,
            },
        ]
        await self.session.flush()
        return {
            "task": task,
            "percentage": percentage,
            "step_resolved": step_resolved,
            "valid_steps": _valid_step_refs(sub_tasks),
        }

    async def add_checkpoint(
        self,
        task_id: UUID,
        agent_id: UUID,
        state_summary: str,
        remaining_work: list[str],
        notes: str | None = None,
    ) -> TaskTable | None:
        """Add a checkpoint for state recovery."""
        task = await self.get(task_id)
        if not task:
            return None

        checkpoint = {
            "id": str(UUID(int=len(task.checkpoints))),
            "timestamp": datetime.now(UTC).isoformat(),
            "agent_id": str(agent_id),
            "state_summary": state_summary,
            "remaining_work": remaining_work,
            "notes": notes,
        }
        task.checkpoints = [*task.checkpoints, checkpoint]
        await self.session.flush()

        return task

    async def add_commit(
        self,
        task_id: UUID,
        hash: str,
        message: str,
        agent_id: UUID | None = None,
    ) -> TaskTable | None:
        """Link a commit to a task."""
        task = await self.get(task_id)
        if not task:
            return None

        commit = {
            "hash": hash,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
            "author_agent_id": str(agent_id) if agent_id else None,
        }
        task.commits = [*task.commits, commit]
        await self.session.flush()

        return task

    # =========================================================================
    # QUERIES
    # =========================================================================

    async def list_all(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskTable]:
        """List all tasks with pagination."""
        result = await self.session.execute(
            select(TaskTable)
            .order_by(TaskTable.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_by_team(
        self,
        team: Team,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> list[TaskTable]:
        """List tasks for a team, ordered by priority, sequence, created_at."""
        query = select(TaskTable).where(TaskTable.team == team)

        if status:
            query = query.where(TaskTable.status == status)

        query = query.order_by(
            TaskTable.priority,
            TaskTable.sequence,
            TaskTable.created_at,
        )
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_assignee(
        self,
        agent_id: UUID,
        status: TaskStatus | None = None,
    ) -> list[TaskTable]:
        """List tasks assigned to an agent."""
        query = select(TaskTable).where(TaskTable.assigned_to == agent_id)

        if status:
            query = query.where(TaskTable.status == status)

        query = query.order_by(TaskTable.priority, TaskTable.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_team_or_assignee(
        self,
        team: Team | None = None,
        agent_id: UUID | None = None,
        status: TaskStatus | None = None,
    ) -> list[TaskTable]:
        """
        List tasks by team OR assignee.

        Useful for finding tasks an agent could work on (their assigned tasks
        or unassigned tasks in their team).
        """
        conditions = []

        if team:
            conditions.append(
                and_(TaskTable.team == team, TaskTable.assigned_to.is_(None))
            )
        if agent_id:
            conditions.append(TaskTable.assigned_to == agent_id)

        if not conditions:
            return []

        query = select(TaskTable).where(or_(*conditions))

        if status:
            query = query.where(TaskTable.status == status)

        query = query.order_by(TaskTable.priority, TaskTable.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_by_status(
        self,
        status: TaskStatus,
        team: Team | None = None,
    ) -> list[TaskTable]:
        """List tasks by status, ordered by priority, sequence, created_at."""
        query = select(TaskTable).where(TaskTable.status == status)

        if team:
            query = query.where(TaskTable.team == team)

        # Order by priority first, then sequence (for sibling order), then created_at
        query = query.order_by(
            TaskTable.priority,
            TaskTable.sequence,
            TaskTable.created_at,
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_pending(
        self,
        team: Team | None = None,
        filter_by_dependencies: bool = True,
    ) -> list[TaskTable]:
        """
        List pending tasks (available to claim).

        Args:
            team: Filter by team
            filter_by_dependencies: If True, exclude tasks with incomplete dependencies

        Returns:
            List of pending tasks, ordered by priority, sequence, then created_at
        """
        tasks = await self.list_by_status(TaskStatus.PENDING, team)

        if not filter_by_dependencies:
            return tasks

        # Filter out tasks whose dependencies aren't complete
        available_tasks = []
        for task in tasks:
            if not task.dependency_ids:
                available_tasks.append(task)
                continue

            # Check if all dependencies are complete
            deps_result = await self.session.execute(
                select(TaskTable.status).where(TaskTable.id.in_(task.dependency_ids))
            )
            dep_statuses = deps_result.scalars().all()

            # All dependencies must be COMPLETED or CANCELLED
            terminal_statuses = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            if all(s in terminal_statuses for s in dep_statuses):
                available_tasks.append(task)

        return available_tasks

    async def list_blocked(self, team: Team | None = None) -> list[TaskTable]:
        """List blocked tasks."""
        return await self.list_by_status(TaskStatus.BLOCKED, team)

    async def list_awaiting_qa(self, team: Team | None = None) -> list[TaskTable]:
        """List tasks awaiting QA review."""
        return await self.list_by_status(TaskStatus.AWAITING_QA, team)

    async def list_awaiting_docs(self, team: Team | None = None) -> list[TaskTable]:
        """List tasks awaiting documentation."""
        return await self.list_by_status(TaskStatus.AWAITING_DOCUMENTATION, team)

    async def list_awaiting_pm_review(
        self, team: Team | None = None
    ) -> list[TaskTable]:
        """List tasks awaiting PM review."""
        return await self.list_by_status(TaskStatus.AWAITING_PM_REVIEW, team)

    async def list_awaiting_ceo_approval(self) -> list[TaskTable]:
        """List tasks awaiting CEO approval (org-wide, no team filter)."""
        return await self.list_by_status(TaskStatus.AWAITING_CEO_APPROVAL)

    async def list_strategic_for_board(self) -> list[TaskTable]:
        """Root tasks (no parent) in awaiting_pm_review with strategic nature.

        Strategic = non-technical roots: product strategy, marketing, vision —
        the work the Board (Product Owner, Head Marketing) curates before
        escalating to CEO. The codebase models nature as a binary
        TECHNICAL/NON_TECHNICAL split, so non_technical is the strategic-board
        bucket.
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.parent_task_id.is_(None),
                TaskTable.status == TaskStatus.AWAITING_PM_REVIEW,
                TaskTable.nature == TaskNature.NON_TECHNICAL,
            )
            .order_by(
                TaskTable.priority,
                TaskTable.sequence,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_long_running_blocked(
        self, *, threshold_minutes: int = 30
    ) -> list[TaskTable]:
        """Tasks in 'blocked' state whose updated_at is older than threshold_minutes.

        Surfaces anomalies for the Auditor to observe. Most-stale first, ordered by
        updated_at ascending so the oldest blocker is at the head of the list.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=threshold_minutes)
        query = (
            select(TaskTable)
            .where(
                TaskTable.status == TaskStatus.BLOCKED,
                TaskTable.updated_at.is_not(None),
                TaskTable.updated_at < cutoff,
            )
            .order_by(
                TaskTable.updated_at,
                TaskTable.priority,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_dependency(self, task_id: UUID, depends_on_id: UUID) -> None:
        """Append a dependency to a task WITHOUT changing its status.

        A PENDING task with unmet dependencies is held back by
        `list_pending(filter_by_dependencies=True)` and released by
        `_unblock_dependents` once the dependency reaches a terminal state.
        Used for cross-cell sequencing — the frontend cell waits on the UX/UI
        design before it dispatches.
        """
        if depends_on_id == task_id:
            return
        task = await self.get(task_id)
        if task is None:
            return
        if depends_on_id not in task.dependency_ids:
            task.dependency_ids = [*task.dependency_ids, depends_on_id]
            await self.session.flush()

    async def wire_sibling_collision_dag(self, parent_task_id: UUID) -> None:
        """Wire the dev-task collision DAG (multi-level sequencing edge kind 3).

        Runs the deterministic collision-sequencing analyzer over a parent's
        surfaced siblings and wires each returned edge as a real
        ``dependency_ids`` entry via :meth:`add_dependency`, so a dev task whose
        surface collides with an earlier sibling's stays PENDING (held by the
        ``list_pending(filter_by_dependencies=True)`` gate) until that sibling
        completes and :meth:`_unblock_dependents` releases it — cross-dev and
        cross-reroute, the ordering the assignee-keyed spawn barrier could not
        guarantee (live 2026-06-27 out-of-order break).

        Incremental + idempotent: re-run after each delegate. A surfaced
        sibling (``intends_to_touch`` / ``adds_migration`` / ``touches_shared``)
        joins the DAG scoped to its ``project_id`` (two repos never collide);
        a no-surface / no-project sibling is parallel to everything and
        contributes no edge. ``dev_task_collision_edges`` orders siblings by
        ``(priority, sequence)`` so re-runs only ADD edges (never flip an
        existing pair's order), and ``add_dependency`` dedupes.
        """
        from roboco.services.sequencing import dev_task_collision_edges

        siblings = await self.get_subtasks(parent_task_id)
        edges = dev_task_collision_edges(siblings)
        for depends_on_id, task_id in edges:
            await self.add_dependency(UUID(str(task_id)), UUID(str(depends_on_id)))

    async def wire_cell_task_wave_chain(self, cell_task_id: UUID) -> None:
        """Wire the cell-task wave chain (multi-level sequencing edge kind 2).

        A new cell-task (under root-subtask ``UT_n``) depends on every cell-task
        under every root-subtask ``UT_n`` itself depends on — ``UT_n`` 's
        ``dependency_ids`` are the kind-1 wave-chain edges, so this chains the
        cell-tasks of wave *k* onto the cell-tasks of wave *k-1*. A root-subtask
        may fan to several cell-tasks (different cells), so the previous wave's
        "cell-task" is a SET, not a single task, and the new cell-task waits for
        the whole set so its branch carries the merged tail.

        ``UT_n`` is held PENDING by ``list_pending(filter_by_dependencies=True)``
        until its kind-1 predecessors are terminal, so by the time the Main PM
        delegates ``UT_n`` 's cell-tasks the predecessor cell-tasks already exist
        and are terminal — the edge is therefore a no-op gate in the common case
        but documents the lineage and protects against any re-ordering.
        Idempotent (``add_dependency`` dedupes) + best-effort (missing
        predecessor / cell-task contributes no edge).
        """
        from roboco.services.sequencing import cell_task_wave_chain_depends_on

        cell_task = await self.get(cell_task_id)
        if cell_task is None or cell_task.parent_task_id is None:
            return
        root = await self.get(UUID(str(cell_task.parent_task_id)))
        if root is None:
            return
        predecessor_root_ids = list(root.dependency_ids)
        cell_tasks_by_root: dict = {}
        for pred_root_id in predecessor_root_ids:
            cell_tasks_by_root[pred_root_id] = await self.get_subtasks(
                UUID(str(pred_root_id))
            )
        for dep_id in cell_task_wave_chain_depends_on(
            predecessor_root_ids, cell_tasks_by_root
        ):
            await self.add_dependency(cell_task_id, UUID(str(dep_id)))

    async def wire_by_osmosis_edge(self, dev_task_id: UUID) -> None:
        """Wire the by-osmosis edge (multi-level sequencing edge kind 4).

        The FIRST dev task (``sequence == 0``) under a cell-task depends on each
        predecessor cell-task's tail (highest-``sequence``) dev task, so the new
        wave's first branch carries the previous wave's fully-merged tail. The
        predecessor cell-tasks are re-derived from the cell-task's root-subtask's
        kind-1 ``dependency_ids`` (the same source :meth:`wire_cell_task_wave_chain`
        uses), not from the cell-task's own ``dependency_ids`` — which also carry
        UX/product-fanout deps the by-osmosis edge must not pick up.

        Subsequent dev tasks (``sequence > 0``) inherit the tail via the kind-3
        collision DAG or share the cell branch's already-merged base, so they
        take no explicit edge. Idempotent + best-effort: a predecessor cell-task
        with no dev tasks, or a missing predecessor, contributes no edge; a tail
        already terminal is a no-op gate.
        """
        from roboco.services.sequencing import by_osmosis_tail_dev_tasks

        dev_task = await self.get(dev_task_id)
        if dev_task is None or dev_task.parent_task_id is None:
            return
        is_first = int(getattr(dev_task, "sequence", 0)) == 0
        cell_task = await self.get(UUID(str(dev_task.parent_task_id)))
        if cell_task is None or cell_task.parent_task_id is None:
            return
        root = await self.get(UUID(str(cell_task.parent_task_id)))
        if root is None:
            return
        groups: list = []
        for pred_root_id in list(root.dependency_ids):
            for pred_ct in await self.get_subtasks(UUID(str(pred_root_id))):
                groups.append(await self.get_subtasks(UUID(str(pred_ct.id))))
        for dep_id in by_osmosis_tail_dev_tasks(is_first, groups):
            await self.add_dependency(dev_task_id, UUID(str(dep_id)))

    async def set_sequence(self, task_id: UUID, sequence: int) -> None:
        """Set a task's sibling-ordering sequence (lower = first).

        `sequence` is a display / dispatch-priority field only — it orders
        siblings in `list_pending`, `list_for_team`, and the panel and carries
        no claim-gating semantics (dependencies gate claims). Cross-cell
        fan-out uses it so an upstream design task sorts ahead of the
        implementation tasks that depend on it. No-op if the task is gone or
        already at `sequence`.
        """
        task = await self.get(task_id)
        if task is None:
            return
        if task.sequence != sequence:
            task.sequence = sequence
            await self.session.flush()

    async def unmet_dependency_ids(self, dependency_ids: list[UUID]) -> list[UUID]:
        """Return the subset of dependency IDs whose status is non-terminal.

        A dependency is "met" only once it reaches a terminal state
        (completed/cancelled). This is the single source of truth for the
        "can a task that depends on these proceed?" question — reused by
        `list_pending`, `list_pending_for_agent`, `inherit_unmet_dependencies`,
        and the claim-time dependency guard. An empty input returns an empty
        list (no dependencies = nothing unmet).
        """
        if not dependency_ids:
            return []
        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        dep_result = await self.session.execute(
            select(TaskTable.id, TaskTable.status).where(
                TaskTable.id.in_(dependency_ids)
            )
        )
        return [
            dep_id
            for dep_id, dep_status in dep_result.all()
            if dep_status not in terminal
        ]

    async def inherit_unmet_dependencies(
        self, subtask_id: UUID, parent_id: UUID
    ) -> None:
        """Copy a parent's still-unresolved dependencies onto a subtask.

        A subtask of a task that is itself waiting on a cross-cell dependency
        (e.g. a frontend dev subtask under a frontend cell task that waits on
        the UX/UI design) must be held until that dependency resolves — the
        developer cannot code ahead of the design. Only non-terminal parent
        dependencies are inherited; already-completed ones would never release
        the subtask via `_unblock_dependents`. Reuses `add_dependency`, so the
        subtask is held by `list_pending(filter_by_dependencies=True)`.
        """
        parent = await self.get(parent_id)
        if parent is None or not parent.dependency_ids:
            return
        for dep_id in await self.unmet_dependency_ids(list(parent.dependency_ids)):
            await self.add_dependency(subtask_id, dep_id)

    async def get_subtasks(self, parent_task_id: UUID) -> list[TaskTable]:
        """Get all subtasks of a parent task."""
        result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.parent_task_id == parent_task_id)
            .order_by(TaskTable.created_at)
        )
        return list(result.scalars().all())

    async def uncovered_required_cells(self, parent_task_id: UUID) -> list[str]:
        """Named cells (parent's ``required_cells:`` marker) with no subtask.

        Inert ([]) when the parent carries no marker — legacy / un-named
        decompositions are never blocked. Otherwise returns each named cell that
        has no child subtask on that team, in marker order.
        """
        parent = await self.get(parent_task_id)
        if parent is None:
            return []
        required = extract_required_cells(parent)
        if not required:
            return []
        children = await self.get_subtasks(parent_task_id)
        covered = {_normalize_cell(c.team) for c in children if c.team is not None}
        return [cell for cell in required if cell not in covered]

    async def has_earlier_incomplete_code_sibling(self, task: TaskTable) -> bool:
        """True if a lower-sequence, non-terminal, same-assignee code sibling exists.

        Service-layer mirror of the orchestrator's per-dev lane dispatch barrier
        (``_blocked_by_earlier_lane_sibling``). Used by the i_am_idle pending-work
        guard so a developer can exit cleanly while the rest of its code queue is
        still waiting its turn: a pre-delegated queue leaf (``pending``, assigned
        to this dev) that sits behind an earlier non-terminal sibling in the same
        lane should NOT pin the dev — the orchestrator spawns it once the lane
        clears. Without this the dev can neither idle nor proceed without jumping
        its own queue order. Only ``code`` queues sequence this way.
        """
        if str(getattr(task, "task_type", "")) != TaskType.CODE.value:
            return False
        parent_id = task.parent_task_id
        owner = task.assigned_to
        seq = task.sequence
        if parent_id is None or owner is None or seq is None:
            return False
        result = await self.session.execute(
            select(TaskTable.status, TaskTable.sequence).where(
                TaskTable.parent_task_id == parent_id,
                TaskTable.assigned_to == owner,
                TaskTable.task_type == TaskType.CODE,
                TaskTable.id != task.id,
            )
        )
        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        return any(
            (sib_seq or 0) < seq and status not in terminal
            for status, sib_seq in result.all()
        )

    async def get_all_descendants(self, task_id: UUID) -> list[TaskTable]:
        """Recursively get ALL descendant tasks (children, grandchildren, etc.).

        Uses iterative BFS to avoid recursion limits and handle arbitrary depth.
        """
        descendants: list[TaskTable] = []
        to_process: list[UUID] = [task_id]

        while to_process:
            current_id = to_process.pop(0)
            children = await self.get_subtasks(current_id)
            for child in children:
                descendants.append(child)
                # child.id is SQLAlchemy Mapped[UUID]
                # but resolves to uuid.UUID at runtime
                to_process.append(child.id)  # type: ignore[arg-type]

        return descendants

    # =========================================================================
    # STATISTICS
    # =========================================================================

    async def count_by_status(self, team: Team | None = None) -> dict[str, int]:
        """Count tasks by status."""
        query = select(
            TaskTable.status,
            func.count(TaskTable.id),
        ).group_by(TaskTable.status)

        if team:
            query = query.where(TaskTable.team == team)

        result = await self.session.execute(query)
        return {row[0].value: row[1] for row in result.all()}

    async def get_delivery_stats_30d(self) -> dict[str, Any]:
        """Return completed-task count and median lead time for the last 30 days.

        Queries tasks WHERE status=completed AND completed_at IS NOT NULL AND
        completed_at >= now()-30d.  Lead time is ``completed_at - created_at``
        expressed in hours; the median is computed with :func:`statistics.median`.

        Returns::

            {
                "completed_30d": int,
                "median_lead_time_hours": float | None,  # None when no tasks
            }
        """
        import statistics

        cutoff = datetime.now(UTC) - timedelta(days=30)
        result = await self.session.execute(
            select(TaskTable.created_at, TaskTable.completed_at).where(
                TaskTable.status == TaskStatus.COMPLETED,
                TaskTable.completed_at.is_not(None),
                TaskTable.completed_at >= cutoff,
            )
        )
        rows = result.all()
        completed_30d = len(rows)
        median_lead_time_hours: float | None = None
        if rows:
            lead_times = [
                (row.completed_at - row.created_at).total_seconds() / 3600
                for row in rows
            ]
            median_lead_time_hours = float(statistics.median(lead_times))
        return {
            "completed_30d": completed_30d,
            "median_lead_time_hours": median_lead_time_hours,
        }

    async def count_by_team(self) -> dict[str, int]:
        """Count tasks by team."""
        result = await self.session.execute(
            select(
                TaskTable.team,
                func.count(TaskTable.id),
            ).group_by(TaskTable.team)
        )
        return {row[0].value: row[1] for row in result.all()}

    async def get_active_count(self, agent_id: UUID) -> int:
        """Get count of active tasks for an agent."""
        result = await self.session.execute(
            select(func.count(TaskTable.id)).where(
                and_(
                    TaskTable.assigned_to == agent_id,
                    TaskTable.status.in_(
                        [
                            TaskStatus.CLAIMED,
                            TaskStatus.IN_PROGRESS,
                            TaskStatus.VERIFYING,
                        ]
                    ),
                )
            )
        )
        return result.scalar() or 0

    async def resolve_agent_id(self, agent_id_str: str) -> UUID:
        """Resolve a UUID-string-or-slug into an agent UUID.

        Used by claim-style endpoints where callers may pass either form.
        Raises NotFoundError if the slug doesn't exist. Exists on the service
        so route modules never issue raw AgentTable queries.
        """
        try:
            return UUID(agent_id_str)
        except ValueError:
            pass

        result = await self.session.execute(
            select(AgentTable.id).where(AgentTable.slug == agent_id_str)
        )
        agent_uuid = result.scalar_one_or_none()
        if not agent_uuid:
            raise NotFoundError(resource_type="Agent", resource_id=agent_id_str)
        return UUID(str(agent_uuid))

    # =========================================================================
    # ROUTE-LEVEL ORCHESTRATION
    #
    # These methods encapsulate the full flow for each task-lifecycle HTTP
    # endpoint. Routes stay thin adapters: parse input → call one of these
    # → translate ServiceError → format response. Business logic and
    # notifications live here.
    # =========================================================================

    # Minimum character count for notes fields that must be substantive
    # (QA pass notes, doc-complete notes, escalation notes).
    MIN_NOTES_CHARS: ClassVar[int] = 20
    # Max descendant IDs shown inline in an error before truncating.
    MAX_ERR_IDS: ClassVar[int] = 5
    # Substitute reason → target status table.
    _SUBSTITUTE_REASON_TO_STATUS: ClassVar[dict[str, TaskStatus]] = {
        "task_complete": TaskStatus.AWAITING_QA,
        "low_context": TaskStatus.PENDING,
        "out_of_scope_team": TaskStatus.PENDING,
        "out_of_scope_role": TaskStatus.PENDING,
        "max_retries": TaskStatus.PENDING,
        "blocked_external": TaskStatus.BLOCKED,
    }

    async def _load_task_or_raise(self, task_id: UUID) -> TaskTable:
        task = await self.get(task_id)
        if not task:
            raise NotFoundError(resource_type="Task", resource_id=str(task_id))
        return task

    @staticmethod
    def _task_status_value(task: TaskTable) -> str:
        return task.status.value if hasattr(task.status, "value") else str(task.status)

    @staticmethod
    def _raise_if_self_review(agent: AgentContext, task: TaskTable) -> None:
        """Refuse a QA/Documenter claim of a task it itself developed."""
        if agent.role in (AgentRole.QA, AgentRole.DOCUMENTER):
            original_dev = extract_original_developer(task)
            if original_dev and str(agent.agent_id) == original_dev:
                raise UnauthorizedError(
                    action="claim",
                    reason=(
                        "SELF_REVIEW: Cannot claim a task that you developed. "
                        f"Leave it for another {agent.role.value}."
                    ),
                )

    @staticmethod
    def _raise_if_main_pm_code_claim(
        claimant_is_main_pm: bool, task: TaskTable
    ) -> None:
        """Refuse a Main-PM claim of a code task it would have to execute."""
        if (
            claimant_is_main_pm
            and _task_type_is_code(task.task_type)
            and task.status != TaskStatus.NEEDS_REVISION
        ):
            raise UnauthorizedError(
                action="claim",
                reason=(
                    "MAIN_PM_NO_CODE: A Main PM coordinates — it does not own a"
                    " code task. Leave it for a developer; delegate instead."
                ),
            )

    async def claim_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        claim_target_slug: str | None,
    ) -> TaskTable:
        """Claim a task (self or, for privileged agents, on behalf of another)."""
        task = await self._load_task_or_raise(task_id)

        if not permissions.can_perform_task_action(agent, TaskAction.CLAIM, task.team):
            raise UnauthorizedError(
                action="claim", reason="Not authorized to claim tasks"
            )

        # QA / Documenter cannot claim what they themselves developed.
        self._raise_if_self_review(agent, task)

        can_assign = permissions.can_perform_task_action(
            agent, TaskAction.ASSIGN, task.team
        )
        claim_agent_id = agent.agent_id
        allow_reassign = False
        if claim_target_slug and can_assign:
            claim_agent_id = await self.resolve_agent_id(claim_target_slug)
            allow_reassign = True

        # awaiting_pm_review is a REVIEW state, not a dev state. Claiming it must
        # NOT transition it to `claimed` — the assigned PM's complete() requires
        # awaiting_pm_review, so a transitioning claim wedges it (the dispatcher
        # claims an ownerless review task before spawning the PM, who then can't
        # complete). Mirror the QA/Doc review-claim: assign the owner, keep the
        # review status. Reached only for an ownerless review task; normal flow
        # keeps the owner and never re-claims here.
        if task.status == TaskStatus.AWAITING_PM_REVIEW:
            return await self._claim_review_state(task_id, claim_agent_id)

        # Impossibility backstop (C8): a Main PM coordinates — it never claims a
        # CODE task to EXECUTE (claiming here is owning through the lifecycle,
        # NOT delegating; delegation uses the `delegate` verb, not claim).
        # Scoped to run only AFTER the awaiting_pm_review review-claim above
        # returned, so the legitimate Main-PM review/merge path is untouched, AND
        # to skip NEEDS_REVISION — the coordination-recovery path. ``CLAIM_RULES``
        # lets a PM re-claim NEEDS_REVISION to re-delegate the fixes after a
        # pr_fail / qa_fail / ceo_reject; blocking that wedges the PM in
        # needs_revision with no actor and no exit (the 2026-06-27 c80e19ff loop:
        # a legacy coordination root still typed `code` until the Phase 3b deploy
        # retype recovers through exactly this claim). The recovery claim
        # re-delegates — it does not execute code — so a code-typed coordination
        # root MUST pass through. The dispatcher never offers code leaf tasks to
        # main-pm (code→dev), so the guard is still an effective backstop against
        # a rogue / on-behalf-of-main-pm claim of a code leaf from PENDING; the
        # needs_revision exemption only re-opens the documented recovery path.
        # The effective claimant is the reassign target when ``allow_reassign``
        # (claim on behalf of), else the caller.
        if allow_reassign:
            claimant_is_main_pm = await self._is_main_pm_agent(claim_agent_id)
        else:
            claimant_is_main_pm = agent.role == AgentRole.MAIN_PM
        self._raise_if_main_pm_code_claim(claimant_is_main_pm, task)

        claimed = await self.claim(
            task_id, claim_agent_id, allow_reassign=allow_reassign
        )
        if not claimed:
            status_msg = "not pending or claimed" if allow_reassign else "not pending"
            raise ValidationError(f"Cannot claim task - {status_msg}")
        await self.session.commit()
        return claimed

    async def _claim_review_state(
        self, task_id: UUID, claim_agent_id: UUID
    ) -> TaskTable:
        """No-transition review-claim for awaiting_pm_review (see caller)."""
        claimed = await self._qa_or_doc_claim(
            claim_agent_id, task_id, TaskStatus.AWAITING_PM_REVIEW
        )
        if not claimed:
            raise ValidationError("Cannot claim task - not in awaiting_pm_review")
        await self.session.commit()
        return claimed

    async def soft_block_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        request: SoftBlockInput,
    ) -> TaskTable:
        """Soft-block a task + notify the owning PM.

        Takes a domain DTO so the API's Pydantic schema doesn't leak into
        the service layer; `SoftBlockInfo` and `BlockerDetails` are built
        internally.
        """
        task = await self._load_task_or_raise(task_id)
        if task.assigned_to != agent.agent_id and agent.role not in (
            AgentRole.CELL_PM,
            AgentRole.MAIN_PM,
        ):
            raise UnauthorizedError(
                action="soft_block", reason="Not authorized to block this task"
            )

        # Coerce the raw string to the domain enum — fall back on AGENT
        # (agent-self-resolvable is the safest default).
        try:
            resolver = BlockerResolverType(request.resolver_type_raw)
        except ValueError:
            resolver = BlockerResolverType.AGENT
        info = SoftBlockInfo(
            reason=request.reason,
            blocker_type=request.blocker_type,
            what_needed=request.what_needed,
            resolver_type=resolver,
        )

        blocked = await self.soft_block(task_id, info, agent.role)
        if not blocked:
            raise ValidationError("Cannot block task - must be in_progress")

        from roboco.services.notification_delivery import (
            BlockerDetails,
            get_notification_delivery_service,
        )

        delivery = get_notification_delivery_service(self.session)
        await delivery.notify_pm_of_block(
            task=blocked,
            task_id=task_id,
            blocker_agent_id=agent.agent_id,
            details=BlockerDetails(
                blocker_type=request.blocker_type,
                reason=request.reason,
                what_needed=request.what_needed,
            ),
        )
        await self.session.commit()
        return blocked

    async def docs_complete_for_task(
        self,
        task_id: UUID,
        agent: AgentContext,
        notes: str | None,
    ) -> TaskTable:
        """Mark documentation complete (documenter role only)."""
        task = await self._load_task_or_raise(task_id)

        if agent.role != AgentRole.DOCUMENTER:
            raise UnauthorizedError(
                action="docs_complete",
                reason="Only documenters can mark documentation as complete",
            )

        original_dev = extract_original_developer(task)
        if original_dev and str(agent.agent_id) == original_dev:
            from roboco.services.audit import get_audit_service

            audit = get_audit_service()
            await audit.log_task_action_denial(
                agent_id=agent.agent_id,
                agent_role=agent.role.value,
                task_id=task_id,
                action="docs_complete",
                reason="Self-documentation not permitted",
            )
            raise UnauthorizedError(
                action="docs_complete", reason="Cannot document your own task"
            )

        if not notes or len(notes.strip()) < self.MIN_NOTES_CHARS:
            raise ValidationError(
                f"DOC_NOTES_REQUIRED: docs_complete must include notes "
                f"(>={self.MIN_NOTES_CHARS} chars) listing what was "
                "documented and where. "
                "Use i_documented(notes='...')."
            )

        completed = await self.docs_complete(task_id, notes)
        if not completed:
            raise ValidationError(
                "Cannot mark docs complete - invalid status for documenter workflow"
            )

        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        delivery = get_notification_delivery_service(self.session)
        await delivery.notify_pm_of_docs_complete(
            task=completed, task_id=task_id, submitter_agent_id=agent.agent_id
        )
        await self.session.commit()
        return completed

    def _format_id_list(self, ids: list[str]) -> str:
        shown = ", ".join(ids[: self.MAX_ERR_IDS])
        extra = (
            f" (+{len(ids) - self.MAX_ERR_IDS} more)"
            if len(ids) > self.MAX_ERR_IDS
            else ""
        )
        return f"{shown}{extra}"

    async def _explain_complete_failure(
        self, task_id: UUID, original_status: str
    ) -> str:
        """Diagnose why `complete` returned None, to surface a clear error."""
        refetch = await self.get(task_id)
        if refetch:
            descendants = await self.get_all_descendants(task_id)
            incomplete = [
                str(d.id)[:8]
                for d in descendants
                if self._task_status_value(d) not in ("completed", "cancelled")
            ]
            if incomplete:
                return (
                    f"Cannot complete task - {len(incomplete)} subtask(s) "
                    f"still in progress: {self._format_id_list(incomplete)}. "
                    "Monitor and help unblock stuck tasks."
                )
            if original_status not in ("awaiting_pm_review", "in_progress"):
                return (
                    f"Cannot complete - status is '{original_status}'. "
                    "Must be 'awaiting_pm_review' or 'in_progress'."
                )
        return "Cannot complete task - check task status and subtasks."

    async def complete_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        force_with_cancelled: bool = False,
        justification: str | None = None,
    ) -> TaskTable:
        """Complete a task (PM/CEO). Includes self-approval + force gates."""
        task = await self._load_task_or_raise(task_id)

        if not permissions.can_perform_task_action(agent, TaskAction.CLOSE, task.team):
            raise UnauthorizedError(
                action="complete", reason="Only PMs can complete tasks"
            )

        # PM self-approval block: the PM who kicked off a task can't also
        # sign off at awaiting_pm_review. CEO is exempt.
        if (
            self._task_status_value(task) == "awaiting_pm_review"
            and task.created_by == agent.agent_id
            and agent.role != AgentRole.CEO
        ):
            raise UnauthorizedError(
                action="complete",
                reason=(
                    "SELF_APPROVAL: You created this task; a different PM "
                    "must complete it from awaiting_pm_review. Escalate or "
                    "hand off to another PM."
                ),
            )

        if force_with_cancelled:
            if agent.role != AgentRole.CEO:
                raise UnauthorizedError(
                    action="force_complete",
                    reason=(
                        "force_with_cancelled requires CEO approval. Only "
                        "CEO can complete tasks when subtasks are not all "
                        "completed."
                    ),
                )
            if not justification:
                raise ValidationError("force_with_cancelled requires justification")

        original_status = self._task_status_value(task)
        completed = await self.complete(
            task_id,
            agent_id=agent.agent_id,
            force_with_cancelled=force_with_cancelled,
            justification=justification,
        )
        if not completed:
            raise ValidationError(
                await self._explain_complete_failure(task_id, original_status)
            )
        await self.session.commit()
        return completed

    async def _validate_escalation_preconditions(
        self,
        task: TaskTable,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        notes: str | None,
    ) -> None:
        """Run all PR/permission/descendants/notes gates for escalate-to-CEO.

        Raises UnauthorizedError or ValidationError on failure; returns
        cleanly when every gate passes. Extracted from
        ``escalate_to_ceo_for_agent`` to keep that orchestrating method
        below B-rank cyclomatic complexity (xenon).
        """
        if not permissions.can_perform_task_action(agent, TaskAction.CLOSE, task.team):
            raise UnauthorizedError(
                action="escalate_to_ceo",
                reason="Only PMs can escalate tasks to CEO",
            )
        if task.pr_number is None:
            raise ValidationError(
                "NO_PR: Cannot escalate to CEO without an open PR. Ensure "
                "the PR exists and pr_number is set on the task."
            )
        if not task.pr_created:
            raise ValidationError(
                "PR_NOT_CONFIRMED: pr_created flag is false. The PR handler "
                "must confirm the PR exists before escalation."
            )
        descendants = await self.get_all_descendants(task_id)
        active = [
            d
            for d in descendants
            if self._task_status_value(d) not in ("completed", "cancelled")
        ]
        if active:
            ids_shown = self._format_id_list([str(d.id)[:8] for d in active])
            raise ValidationError(
                f"ACTIVE_SUBTASKS: Cannot escalate while {len(active)} "
                f"subtask(s) remain active: {ids_shown}."
            )
        if not notes or len(notes.strip()) < self.MIN_NOTES_CHARS:
            raise ValidationError(
                f"ESCALATION_NOTES_REQUIRED: Escalation to CEO must "
                f"include notes (>={self.MIN_NOTES_CHARS} chars) explaining "
                "why CEO review is needed (scope, risk, breaking-change, "
                "etc)."
            )

    async def escalate_to_ceo_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        permissions: "PermissionService",
        notes: str | None,
    ) -> TaskTable:
        """Escalate a task to CEO for final approval (PM-role, PR-gated)."""
        task = await self._load_task_or_raise(task_id)
        await self._validate_escalation_preconditions(
            task, task_id, agent, permissions, notes
        )

        escalated = await self.escalate_to_ceo(task_id, agent.role.value, notes)
        if not escalated:
            raise ValidationError(
                "Cannot escalate to CEO - task must be in awaiting_pm_review status"
            )

        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        delivery = get_notification_delivery_service(self.session)
        await delivery.notify_ceo_of_escalation(
            task=escalated,
            task_id=task_id,
            escalator_agent_id=agent.agent_id,
            escalator_role=agent.role.value,
            notes=notes,
        )
        await self.session.commit()
        return escalated

    async def substitute_task_for_agent(
        self,
        task_id: UUID,
        agent: AgentContext,
        reason_raw: str,
        details: str,
    ) -> TaskTable:
        """Agent releases a task they can't continue; may route to PM review."""
        from roboco.models.base import SubstituteReason

        try:
            reason = SubstituteReason(reason_raw)
        except ValueError as e:
            valid = [r.value for r in SubstituteReason]
            raise ValidationError(
                f"Invalid reason: {reason_raw}. Valid: {valid}"
            ) from e

        task = await self._load_task_or_raise(task_id)
        if task.assigned_to != agent.agent_id:
            raise UnauthorizedError(
                action="substitute",
                reason="You can only substitute out of tasks you own",
            )

        # QA / doc `task_complete` routes to PM review; other reasons use
        # the plain mapping.
        new_status = self._SUBSTITUTE_REASON_TO_STATUS.get(
            reason.value, TaskStatus.PENDING
        )
        if reason == SubstituteReason.TASK_COMPLETE and agent.role in (
            AgentRole.QA,
            AgentRole.DOCUMENTER,
        ):
            new_status = TaskStatus.AWAITING_PM_REVIEW

        update_data, target_pm_slug = await self.build_substitute_update(
            agent_id=agent.agent_id,
            task=task,
            new_status=new_status,
            reason=reason_raw,
            details=details,
        )

        updated = await self.update(task_id, **update_data)
        if not updated:
            raise ServiceError("Update failed")

        if new_status == TaskStatus.AWAITING_PM_REVIEW and target_pm_slug:
            await notify_pm_for_substitute(
                self.session,
                pm_slug=target_pm_slug,
                task_id=task_id,
                from_agent_id=agent.agent_id,
                message=(
                    f"Task needs review: {updated.title or 'Unknown task'}",
                    f"Task {task_id} requires PM review.\n\n"
                    f"Reason: {reason.value}\n"
                    f"Details: {details}\n\n"
                    "Please review and reassign as needed.",
                ),
            )

        await self.session.commit()
        return updated

    async def build_substitute_update(
        self,
        *,
        agent_id: UUID,
        task: TaskTable,
        new_status: TaskStatus,
        reason: str,
        details: str,
    ) -> tuple[dict[str, Any], str | None]:
        """Assemble the update payload + pm_slug for a substitute operation.

        Resolves the submitting agent's slug (for PM-chain lookup) internally
        so routes pass only the agent UUID + primitive strings (no API
        schema types leak down into the service layer). Returns the patch
        dict and the PM slug to notify (None if no handoff).
        """
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent_record = result.scalar_one_or_none()
        agent_slug = agent_record.slug if agent_record else None

        update_data: dict[str, Any] = {
            "status": new_status.value,
            "dev_notes": f"[SUBSTITUTE] Reason: {reason}\n{details}",
            # Keep the task with its current owner. A substitute-out is almost
            # always a transient stall (a verb that kept 500-ing, a retry-limit
            # trip, a low-context bail), so the task re-dispatches to the SAME
            # agent — which resumes from the briefing handoff — instead of being
            # orphaned to pending+unassigned and going dormant. The only handoff
            # that changes owner is the task_complete → PM-review case below.
            "assigned_to": agent_id,
        }

        target_pm_slug: str | None = None
        if new_status == TaskStatus.AWAITING_PM_REVIEW:
            target_pm_slug, pm_uuid = await resolve_pm_for_substitute(
                self.session, agent_slug, task.team
            )
            if pm_uuid:
                update_data["assigned_to"] = pm_uuid
        return update_data, target_pm_slug

    # =========================================================================
    # GATEWAY (CHOREOGRAPHER) BACKFILL
    #
    # Thin wrappers + queries the gateway Choreographer composes into
    # intent-verb sequences. Most are aliases over canonical service
    # methods; a handful (qa_claim, doc_claim, qa_pass, qa_fail, escalate,
    # cell_pm_complete) are flow-specific variants that don't fit cleanly
    # into the existing API.
    # =========================================================================

    # Active states a developer counts as "current work" for an agent.
    _DEV_ACTIVE_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.VERIFYING,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
    }

    # States in which the agent still owns the task for content / journal
    # context, even if it isn't progressing. Used by note / say / dm /
    # evidence so journal entries written from blocked or paused get the
    # task_id auto-attached (otherwise the C8 + tracing gates never see
    # the agent's decisions and the agent spirals).
    _JOURNAL_CONTEXT_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.VERIFYING,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.BLOCKED,
        TaskStatus.PAUSED,
        TaskStatus.NEEDS_REVISION,
    }

    # Statuses that count as "still assignable to the agent" for triage.
    _AGENT_NON_TERMINAL_STATUSES: ClassVar[set[TaskStatus]] = {
        TaskStatus.PENDING,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_REVISION,
        TaskStatus.VERIFYING,
        TaskStatus.AWAITING_QA,
        TaskStatus.AWAITING_DOCUMENTATION,
        TaskStatus.AWAITING_PM_REVIEW,
        TaskStatus.PAUSED,
    }

    async def submit_verification(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Submit task for self-verification (gateway alias of submit_for_verification).

        `notes` is currently advisory — recorded as a progress entry for
        the audit trail. The underlying transition does not require notes.
        """
        if notes:
            await self.add_progress(task_id, agent_id, notes)
        return await self.submit_for_verification(task_id, agent_role="developer")

    async def submit_qa(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Submit task for QA review (gateway alias of submit_for_qa)."""
        if notes:
            await self.add_progress(task_id, agent_id, notes)
        return await self.submit_for_qa(task_id, agent_role="developer")

    async def list_blocked_for_team(self, team: Team) -> list[TaskTable]:
        """List blocked tasks for a single team."""
        return await self.list_blocked(team=team)

    async def list_blocked_all_teams(self) -> list[TaskTable]:
        """List blocked tasks across all teams."""
        return await self.list_blocked()

    async def list_awaiting_pm_review_for_team(self, team: Team) -> list[TaskTable]:
        """List awaiting-PM-review tasks for a single team."""
        return await self.list_awaiting_pm_review(team=team)

    async def list_assigned_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """Active (non-terminal) tasks currently assigned to an agent."""
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status.in_(self._AGENT_NON_TERMINAL_STATUSES),
            )
            .order_by(TaskTable.priority, TaskTable.updated_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def agent_for(self, agent_id: UUID) -> GatewayAgentView | None:
        """Return a gateway-shaped view of the agent (DB + config derived).

        Combines AgentTable's role/team with `agents_config`'s
        escalation_target + skills so the Choreographer reads one object.
        """
        from roboco.agents_config import (
            get_agent_skills,
            get_escalation_target,
        )

        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        slug = agent.slug
        role_value = (
            agent.role.value if hasattr(agent.role, "value") else str(agent.role)
        )
        team_value: str | None = None
        if agent.team is not None:
            team_value = (
                agent.team.value if hasattr(agent.team, "value") else str(agent.team)
            )
        return GatewayAgentView(
            id=UUID(str(agent.id)),
            role=role_value,
            team=team_value,
            escalation_target=get_escalation_target(slug),
            skills=get_agent_skills(slug),
        )

    async def _agent_with_role_and_team(
        self, role: AgentRole, team: Team
    ) -> AgentTable | None:
        """Find an agent matching role + team."""
        result = await self.session.execute(
            select(AgentTable).where(
                AgentTable.role == role,
                AgentTable.team == team,
            )
        )
        return result.scalars().first()

    async def qa_agent_for_team(self, team: Team) -> AgentTable | None:
        """Find the QA agent for a team."""
        return await self._agent_with_role_and_team(AgentRole.QA, team)

    async def documenter_for_team(self, team: Team) -> AgentTable | None:
        """Find the Documenter agent for a team."""
        return await self._agent_with_role_and_team(AgentRole.DOCUMENTER, team)

    async def cell_pm_for_team(self, team: Team) -> AgentTable | None:
        """Find the Cell PM for a team."""
        return await self._agent_with_role_and_team(AgentRole.CELL_PM, team)

    async def main_pm_agent(self) -> AgentTable | None:
        """Find the Main PM (org-wide; takes the earliest-created if many)."""
        result = await self.session.execute(
            select(AgentTable)
            .where(AgentTable.role == AgentRole.MAIN_PM)
            .order_by(AgentTable.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_task_for_agent(self, agent_id: UUID) -> TaskTable | None:
        """Most-recently-updated task currently being worked by the agent."""
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status.in_(self._DEV_ACTIVE_STATUSES),
            )
            .order_by(TaskTable.updated_at.desc().nullslast())
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_journal_context_task_for_agent(
        self, agent_id: UUID
    ) -> TaskTable | None:
        """Most-recently-updated task the agent owns for journal/content context.

        Wider than ``get_active_task_for_agent`` — includes BLOCKED, PAUSED,
        and NEEDS_REVISION so journal entries written while stuck still
        get the task_id auto-attached. Dogfooding surfaced the bug: PMs
        wrote decisions during blocked state, auto-injection returned
        None, entries persisted with task_id=NULL, the C8 tracing gate
        never saw them, agents spiraled forever.
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status.in_(self._JOURNAL_CONTEXT_STATUSES),
            )
            .order_by(TaskTable.updated_at.desc().nullslast())
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_pending_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """Tasks assigned to this agent that are still in PENDING status.

        Pre-gateway parity: give_me_work missed the
        pre-assigned case before this. PMs whose root was seeded with
        assigned_to=<them> + status=pending got 'no work' until they
        triage()'d explicitly.

        A pre-assigned task with unmet (non-terminal) dependencies is held
        back: offering it would let the agent claim and work ahead of a
        dependency that has not resolved (e.g. a frontend dev coding before
        the UX/UI design lands). The pre-assigned path bypasses
        `list_pending(filter_by_dependencies=True)`, so the dependency gate
        must be applied here too.

        F059: a self-heal fix task held for the CEO's Approve-&-Start
        (``source=self_heal`` + ``confirmed_by_human=False``) is NOT offered
        here — an already-alive PM must not grab it via give_me_work before the
        CEO opens the gate. The hold is scoped to self-heal: ordinary delegated
        subtasks default to ``confirmed_by_human=False`` (the PM delegated them,
        which is itself the authorization to start) and MUST still be offered.

        Ordered by sequence asc, then priority asc, then created_at asc so
        earlier-sequence tasks win.
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status == TaskStatus.PENDING,
                or_(
                    TaskTable.source != SELF_HEAL_SOURCE,
                    TaskTable.confirmed_by_human.is_(True),
                ),
            )
            .order_by(
                TaskTable.sequence,
                TaskTable.priority,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        tasks = list(result.scalars().all())
        available: list[TaskTable] = []
        for task in tasks:
            if await self.unmet_dependency_ids(list(task.dependency_ids)):
                continue
            available.append(task)
        return available

    async def list_paused_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """Paused tasks assigned to the agent."""
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status == TaskStatus.PAUSED,
            )
            .order_by(TaskTable.priority, TaskTable.updated_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_awaiting_main_pm_all(self) -> list[TaskTable]:
        """Root tasks (no parent) awaiting PM review across all teams.

        Used by Main PM triage — root tasks have escalated past their
        cell PMs and need final approval/escalation to CEO.
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.parent_task_id.is_(None),
                TaskTable.status == TaskStatus.AWAITING_PM_REVIEW,
            )
            .order_by(
                TaskTable.priority,
                TaskTable.sequence,
                TaskTable.created_at,
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def all_subtasks_terminal(self, task_id: UUID) -> bool:
        """True iff every direct subtask is in a terminal status.

        Empty subtask list returns True (no children = vacuously terminal).
        """
        terminal = {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        result = await self.session.execute(
            select(TaskTable.status).where(TaskTable.parent_task_id == task_id)
        )
        statuses = result.scalars().all()
        return all(s in terminal for s in statuses)

    async def _parent_ac_ref_sets(
        self, task_id: UUID
    ) -> tuple[TaskTable, set[str], set[str], bool] | None:
        """Load a parent and its children's parent-AC-ref coverage sets.

        Shared core of the three AC-coverage primitives. Returns ``None`` when
        the parent is missing or has no stable criterion ids (nothing to cover).
        Otherwise ``(parent, claimed, verified, any_declared)`` where
        ``claimed`` is the union of parent_ac_refs over all non-cancelled
        children, ``verified`` the union over COMPLETED children only, and
        ``any_declared`` whether *any* child declared a ref at all (the
        safe-by-construction inertness signal — a cancelled-only declaration
        still counts as "coverage tracking is active here").
        """
        parent = await self.get(task_id)
        if not parent or not parent.acceptance_criteria_ids:
            return None
        result = await self.session.execute(
            select(TaskTable.status, TaskTable.parent_ac_refs).where(
                TaskTable.parent_task_id == task_id
            )
        )
        claimed: set[str] = set()
        verified: set[str] = set()
        any_declared = False
        for status, refs in result.all():
            refset = self._normalize_ac_refs(parent, refs)
            any_declared = any_declared or bool(refset)
            if status != TaskStatus.CANCELLED:
                claimed |= refset
            if status == TaskStatus.COMPLETED:
                verified |= refset
        return parent, claimed, verified, any_declared

    @staticmethod
    def _normalize_ac_refs(parent: TaskTable, refs: list[str] | None) -> set[str]:
        """Resolve a child's parent_ac_refs to parent criterion ids.

        A PM may declare covers_parent_criteria by either a criterion's stable
        id OR its full text — both happen. Coverage is matched by id (see
        _criteria_texts_not_in), so without this a text-declared ref from a
        COMPLETED child reads "uncovered" and the PM re-delegates the already-
        finished work as an empty phantom subtask (0 commits, no PR) that can
        never close — observed live looping for hours. Map text -> id (via the
        parent's own criteria) so coverage counts regardless of how it was
        declared. An unknown ref (neither id nor a current criterion text)
        passes through and matches nothing, exactly as before.
        """
        ac_ids = parent.acceptance_criteria_ids or []
        valid_ids = set(ac_ids)
        ac_texts = parent.acceptance_criteria or []
        text_to_id = {
            text: ac_ids[idx] for idx, text in enumerate(ac_texts) if idx < len(ac_ids)
        }
        return {(r if r in valid_ids else text_to_id.get(r, r)) for r in (refs or [])}

    @staticmethod
    def _criteria_texts_not_in(parent: TaskTable, covered: set[str]) -> list[str]:
        """Texts of the parent criteria whose id is not in ``covered``."""
        ids = parent.acceptance_criteria_ids or []
        texts = parent.acceptance_criteria or []
        return [
            texts[idx] if idx < len(texts) else ac_id
            for idx, ac_id in enumerate(ids)
            if ac_id not in covered
        ]

    async def uncovered_parent_acceptance_criteria(self, task_id: UUID) -> list[str]:
        """Parent ACs not yet satisfied by a COMPLETED child — for the roll-up gate.

        Safe-by-construction: returns ``[]`` (no enforcement) unless at least one
        child declares ``parent_ac_refs``, so the gate is inert for tasks
        decomposed before coverage tracking and activates automatically once a PM
        declares which child covers which parent criterion. A criterion counts as
        covered only when a child whose ``parent_ac_refs`` includes it has
        COMPLETED (cancelled children do not count — their work did not pass QA).
        Returns the uncovered criterion *texts* for a human-readable rejection.
        """
        loaded = await self._parent_ac_ref_sets(task_id)
        if loaded is None:
            return []
        parent, _claimed, verified, any_declared = loaded
        if not any_declared:
            return []
        return self._criteria_texts_not_in(parent, verified)

    async def unclaimed_parent_acceptance_criteria(self, task_id: UUID) -> list[str]:
        """Parent ACs not claimed by any live child — the decomposition floor.

        Spec-2 counterpart of ``uncovered_parent_acceptance_criteria`` (the
        roll-up gate): where that one asks whether every criterion is satisfied
        by a COMPLETED child, this asks the earlier, weaker question — has every
        criterion been *claimed* by some still-live subtask? Used at PM exit
        (``i_am_idle``) so a PM cannot finish decomposing while a parent
        criterion has no subtask responsible for it (the "two leaves, half the
        ACs dropped" pattern). Safe-by-construction: inert (``[]``) until a PM
        declares coverage on at least one child, mirroring the roll-up gate.
        Returns the uncovered criterion texts for a human-readable rejection.
        """
        loaded = await self._parent_ac_ref_sets(task_id)
        if loaded is None:
            return []
        parent, claimed, _verified, any_declared = loaded
        if not any_declared:
            return []
        return self._criteria_texts_not_in(parent, claimed)

    async def parent_ac_coverage(self, task_id: UUID) -> list[dict[str, Any]]:
        """Per-criterion decomposition coverage for a parent task.

        For each parent acceptance criterion returns its stable id, text, and
        whether some live (non-cancelled) child claims it via ``parent_ac_refs``
        (``claimed``) and whether a COMPLETED child does (``verified``). Returns
        ``[]`` when the task has no ``acceptance_criteria_ids``. Unlike the gate
        primitives this is deliberately NOT inert when coverage is undeclared —
        it always reports the parent's criteria so a decomposing PM can see, per
        delegate, which criteria still lack a subtask. Visibility source for the
        decomposition briefing; the gates read the inert variants instead.
        """
        loaded = await self._parent_ac_ref_sets(task_id)
        if loaded is None:
            return []
        parent, claimed, verified, _any = loaded
        ids = parent.acceptance_criteria_ids or []
        texts = parent.acceptance_criteria or []
        return [
            {
                "id": ac_id,
                "text": texts[idx] if idx < len(texts) else ac_id,
                "claimed": ac_id in claimed,
                "verified": ac_id in verified,
            }
            for idx, ac_id in enumerate(ids)
        ]

    async def set_plan(
        self, task_id: UUID, plan: str | dict[str, Any]
    ) -> TaskTable | None:
        """Write the task's plan field. Strings are wrapped as {'text': plan}.

        Never DOWNGRADE a rich plan to a contentless one: a recovery/re-entry
        claim that omits the rich fields would otherwise clobber the Approach +
        sub_tasks an earlier fresh claim already authored — this was why a
        flaked-then-recovered dev leaf showed an empty Plan tab. If the incoming
        plan has no approach but the stored one does, keep the stored plan.
        """
        task = await self.get(task_id)
        if not task:
            return None
        new_plan = plan if isinstance(plan, dict) else {"text": plan}
        existing = task.plan if isinstance(task.plan, dict) else {}
        if existing.get("approach") and not new_plan.get("approach"):
            return task
        task.plan = new_plan
        await self.session.flush()
        return task

    async def ensure_work_session(
        self,
        task_id: UUID,
        agent_id: UUID,
    ) -> None:
        """Create a WorkSession row if one does not already exist for this claim.

        Pre-gateway parity. The gateway's claim/plan/
        start path calls this after the task reaches in_progress so every
        (agent, task) claim cycle has a WorkSession row that downstream
        subsystems (panel, PR tracking, merge chain) can use. Delegates to
        _create_work_session_if_needed with role=None so both developers and
        PMs get a session (pre-gateway created sessions for all claimants).
        No-ops if work_session_id is already set (re-entry guard).
        """
        task = await self.get(task_id)
        if not task:
            return
        if task.work_session_id:
            return
        await self._create_work_session_if_needed(task, agent_id, agent_role=None)

    async def mark_evidence_inspected(self, task_id: UUID) -> None:
        """Set qa_evidence_inspected=True on the task."""
        task = await self.get(task_id)
        if task is None:
            return
        task.qa_evidence_inspected = True
        await self.session.flush()

    async def set_acceptance_criteria_status(
        self, task_id: UUID, status: list[dict[str, Any]]
    ) -> TaskTable | None:
        """Persist per-criterion addressing status.

        Replaces the full acceptance_criteria_status list with `status`.
        Each entry must have the shape:
            {
                "criterion": str,
                "addressed": bool,
                "artifact_ref": str | None,
                "checked_at": str,  # ISO-8601 UTC
            }

        Returns the updated task, or None if the task no longer exists.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        task.acceptance_criteria_status = status
        await self.session.flush()
        return task

    async def _maybe_divert_board_advisory_reassign(
        self, task: TaskTable, task_id: UUID, new_assignee: UUID | None
    ) -> TaskTable | None:
        """Divert a board/advisory → cell-task reassign to the pool, or None.

        Invariant backstop: never plant a board/advisory role as the owner of a
        cell task. ``apply_escalation`` guards the escalate path; this guards the
        direct reassign setter (gateway handoffs + HTTP route). A refused
        hand-off is diverted to the pool for a role-matched claim. Normal
        handoff targets (qa/documenter/cell_pm) are not board roles, so the
        guard never fires for them.

        Returns the refreshed task when it diverted (the caller returns it as
        the reassign result), or ``None`` when no diversion applies and the
        caller should proceed with the normal handoff.
        """
        if not (
            new_assignee is not None
            and _board_cannot_own(task)
            and await self._is_board_advisory_agent(new_assignee)
        ):
            return None
        await self._divert_owned_task_to_pool(
            task,
            note=(
                "\n\n[REASSIGN REDIRECTED] attempted to assign this cell task"
                " to a board/advisory role that cannot own cell-executed work."
                " Released to the pool for a role-matched claim instead."
            ),
        )
        self.log.info(
            "Cell task reassign to a board/advisory role diverted to pool",
            task_id=str(task_id),
            refused_assignee=str(new_assignee),
        )
        return task

    async def _maybe_divert_main_pm_code_reassign(
        self, task: TaskTable, task_id: UUID, new_assignee: UUID | None
    ) -> TaskTable | None:
        """Divert a main_pm+code → Main-PM reassign to the pool, or None.

        Twin of ``_maybe_divert_board_advisory_reassign`` for the
        impossibility invariant: a Main-PM target must never receive (back) a
        ``main_pm`` + ``code`` task — a coordinator with no code verb cannot
        fix the code, so re-handing it to Main PM perpetuates the mismatch
        (the 2026-06-27 meltdown shape). Scoped to the team+type combo (NOT a
        broad code+main-pm-target rule) so a legacy main_pm+code task can still
        be reassigned to a cell dev — the correct remediation. The combo is
        uncreatable going forward (create backstop + intake coercion +
        approve_and_start retype), so this is a backstop for legacy /
        direct-ORM-write tasks.

        Returns the refreshed task when it diverted, or ``None`` when no
        diversion applies and the caller should proceed with the normal handoff.
        """
        if not (
            new_assignee is not None
            and main_pm_cannot_own_code(team=task.team, task_type=task.task_type)
            and await self._is_main_pm_agent(new_assignee)
        ):
            return None
        await self._divert_owned_task_to_pool(
            task,
            note=(
                "\n\n[REASSIGN REDIRECTED] attempted to hand this main_pm + code"
                " task to a Main PM that cannot own code. Released to the pool"
                " for a role-matched claim instead."
            ),
        )
        self.log.info(
            "main_pm + code task reassign to a Main PM diverted to pool",
            task_id=str(task_id),
            refused_assignee=str(new_assignee),
        )
        return task

    async def reassign(
        self, task_id: UUID, new_assignee: UUID | None
    ) -> TaskTable | None:
        """Set ``task.assigned_to`` (and ``claimed_by``) to ``new_assignee``.

        Used by the gateway choreographer to hand a task off to the agent
        that should drive the next lifecycle stage (e.g. dev → qa, qa →
        documenter, doc → cell_pm). Pass ``None`` to clear assignment so
        no agent gets respawned (e.g. after escalating to CEO, who acts
        via the UI).

        Returns the refreshed task, or None if the task no longer exists.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        # Board/advisory → cell-task hand-off is diverted to the pool (see
        # `_maybe_divert_board_advisory_reassign`); otherwise proceed with the
        # normal handoff + cell-PM redirect.
        diverted = await self._maybe_divert_board_advisory_reassign(
            task, task_id, new_assignee
        )
        if diverted is not None:
            return diverted
        # main_pm + code → Main-PM hand-off is diverted to the pool (see
        # `_maybe_divert_main_pm_code_reassign`); otherwise proceed with the
        # normal handoff + cell-PM redirect.
        diverted = await self._maybe_divert_main_pm_code_reassign(
            task, task_id, new_assignee
        )
        if diverted is not None:
            return diverted
        # Invariant backstop: cell-team planning/research/administrative children
        # must be owned by their cell PM. If the caller tried to hand such a task
        # to main-pm (or another mismatched owner), redirect to the cell PM.
        redirect = await self._resolve_cell_pm_redirect(task, new_assignee)
        effective_assignee = redirect.effective_assignee
        if redirect.redirected:
            self.log.info(
                "Reassign redirected to cell PM",
                task_id=str(task_id),
                requested_assignee=str(new_assignee),
                effective_assignee=str(effective_assignee),
                reason=redirect.reason,
            )
            if redirect.dev_notes_line is not None:
                task.dev_notes = (task.dev_notes or "") + redirect.dev_notes_line

        task.assigned_to = (
            cast("Any", effective_assignee) if effective_assignee else None
        )
        task.claimed_by = (
            cast("Any", effective_assignee) if effective_assignee else None
        )
        await self.session.flush()
        self.log.info(
            "Task reassigned",
            task_id=str(task_id),
            new_assignee=str(effective_assignee) if effective_assignee else None,
        )
        return task

    @dataclass(frozen=True)
    class _CellPmRedirect:
        """Outcome of an `_resolve_cell_pm_redirect` call.

        ``effective_assignee`` is the UUID the caller should persist (possibly
        ``None`` when the task was queued for a missing cell PM).
        ``redirected`` is ``True`` iff ``effective_assignee`` differs from the
        caller's requested assignee — used by callers to decide whether to log
        a redirect. ``reason`` is a short tag that names the redirect reason
        ("to_cell_pm", "queued_no_cell_pm"). ``dev_notes_line`` is the audit
        text to append to ``task.dev_notes`` (only set when a redirect
        happened) so a redirect is visible in the task body, not just in logs.
        """

        effective_assignee: UUID | None
        redirected: bool
        reason: str
        dev_notes_line: str | None

    async def _resolve_cell_pm_redirect(
        self, task: TaskTable, requested_assignee: UUID | None
    ) -> "TaskService._CellPmRedirect":
        """Decide whether ``task`` must be redirected to its cell PM.

        Returns a dataclass describing the outcome. Behavior:

        - Not a cell-team PM-owned child → keep ``requested_assignee`` unchanged.
        - No cell PM exists for the team → **queue the task** (``None``
          assignee, ``ERROR`` log) rather than silently assigning to
          ``requested_assignee``. The system is in a bad state; this is
          observable in logs and the task sits in ``PENDING`` until the agent
          table is repaired.
        - Cell PM exists and matches → keep ``requested_assignee``.
        - Cell PM exists and differs → redirect to the cell PM and write the
          standard ``[ASSIGNMENT REDIRECTED]`` line to ``dev_notes`` so the
          audit is visible in the task body.
        """
        noop = TaskService._CellPmRedirect(
            effective_assignee=requested_assignee,
            redirected=False,
            reason="noop",
            dev_notes_line=None,
        )
        if not _is_cell_pm_owned_task(task):
            return noop
        assert task.team is not None  # guarded by _is_cell_pm_owned_task
        team_enum = Team(str(getattr(task.team, "value", task.team)))
        cell_pm = await self.cell_pm_for_team(team_enum)
        if cell_pm is None:
            self.log.error(
                "Cell-team PM-owned task has no cell PM agent row; "
                "queueing without an assignee. Repair agents table.",
                task_id=str(getattr(task, "id", None)),
                team=team_enum.value,
            )
            type_value = (
                task.task_type.value
                if isinstance(task.task_type, TaskType)
                else task.task_type
            )
            return TaskService._CellPmRedirect(
                effective_assignee=None,
                redirected=(requested_assignee is not None),
                reason="queued_no_cell_pm",
                dev_notes_line=(
                    f"\n\n[ASSIGNMENT REDIRECTED] cell-team {team_enum.value} "
                    f"{type_value} task queued with no assignee because no "
                    f"cell PM agent row exists; was {requested_assignee}."
                ),
            )
        if requested_assignee == cell_pm.id:
            return noop
        type_value = (
            task.task_type.value
            if isinstance(task.task_type, TaskType)
            else task.task_type
        )
        return TaskService._CellPmRedirect(
            effective_assignee=cast("UUID", cell_pm.id),
            redirected=True,
            reason="to_cell_pm",
            dev_notes_line=(
                f"\n\n[ASSIGNMENT REDIRECTED] cell-team {team_enum.value} "
                f"{type_value} task must be owned by its cell PM; reassigned "
                f"from {requested_assignee} to {cell_pm.slug}."
            ),
        )

    async def reassign_active_claim(
        self, task_id: UUID, new_assignee: UUID
    ) -> TaskTable | None:
        """Hand an active (claimed/in_progress) task to a new claimant.

        Distinct from ``reassign`` (review-state handoffs): this reseeds
        ``claimed_at`` / ``last_heartbeat_at`` / ``active_claimant_id`` so the
        new claimant isn't immediately stale to the reaper (the prior dev that
        prompted the reassignment often has a stale heartbeat). The branch is
        keyed to the task, so the work-in-progress survives. Returns None if the
        task is gone or no longer in an active dev-owned state.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if task.status not in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS):
            return None
        # Invariant backstop (mirrors `reassign`): an active claim must not be
        # handed to a board/advisory role on a cell task — divert to the pool.
        if _board_cannot_own(task) and await self._is_board_advisory_agent(
            new_assignee
        ):
            await self._divert_owned_task_to_pool(
                task,
                note=(
                    "\n\n[REASSIGN REDIRECTED] attempted to hand this active cell"
                    " task to a board/advisory role that cannot own cell-executed"
                    " work. Released to the pool for a role-matched claim instead."
                ),
            )
            self.log.info(
                "Active cell task reassign to a board/advisory role diverted",
                task_id=str(task_id),
                refused_assignee=str(new_assignee),
            )
            return task
        # Impossibility backstop (mirrors `reassign`): an active main_pm + code
        # claim must not be handed to a Main PM — divert to the pool. Scoped to
        # the team+type combo so a legacy main_pm+code task can still be
        # reassign-claimed by a cell dev (the correct remediation).
        if main_pm_cannot_own_code(
            team=task.team, task_type=task.task_type
        ) and await self._is_main_pm_agent(new_assignee):
            await self._divert_owned_task_to_pool(
                task,
                note=(
                    "\n\n[REASSIGN REDIRECTED] attempted to hand this active"
                    " main_pm + code task to a Main PM that cannot own code."
                    " Released to the pool for a role-matched claim instead."
                ),
            )
            self.log.info(
                "Active main_pm + code task reassign to a Main PM diverted",
                task_id=str(task_id),
                refused_assignee=str(new_assignee),
            )
            return task
        # Invariant backstop: an active claim on a cell-team planning/research/
        # administrative task must land on the cell PM, not main-pm.
        redirect = await self._resolve_cell_pm_redirect(task, new_assignee)
        effective_assignee = redirect.effective_assignee
        if redirect.redirected:
            self.log.info(
                "Active claim redirected",
                task_id=str(task_id),
                requested_assignee=str(new_assignee),
                effective_assignee=str(effective_assignee),
                reason=redirect.reason,
            )
            if redirect.dev_notes_line is not None:
                task.dev_notes = (task.dev_notes or "") + redirect.dev_notes_line

        now = datetime.now(UTC)
        task.assigned_to = cast("Any", effective_assignee)
        task.claimed_by = cast("Any", effective_assignee)
        task.claimed_at = now
        task.last_heartbeat_at = now
        task.active_claimant_id = cast("Any", effective_assignee)
        await self.session.flush()
        self.log.info(
            "Active task reassigned to a fresh claimant",
            task_id=str(task_id),
            new_assignee=str(effective_assignee),
        )
        return task

    async def mark_agent_idle(self, agent_id: UUID) -> None:
        """Set agent.status = IDLE."""
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return
        agent.status = AgentStatus.IDLE
        await self.session.flush()

    async def _qa_or_doc_claim(
        self,
        agent_id: UUID,
        task_id: UUID,
        expected_status: TaskStatus,
    ) -> TaskTable | None:
        """Claim-without-transition for QA / Documenter review states.

        Status stays at expected_status (it's a review state, not an
        active dev state). Sets assigned_to + claimed_by + claimed_at so
        the gateway can route subsequent verbs to the right agent.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if task.status != expected_status:
            return None
        now = datetime.now(UTC)
        task.assigned_to = cast("Any", agent_id)
        task.claimed_by = cast("Any", agent_id)
        task.claimed_at = now
        # Seed the heartbeat — same rationale as _finalize_claim line 959.
        # `claimant_lock.is_stale` and the reaper both treat
        # last_heartbeat_at IS NULL as stale; without this seed a QA/Doc
        # claim is "stale" the moment it's recorded and any code that
        # consults claimant_lock for awaiting_qa / awaiting_documentation
        # tasks will misclassify the live claim as abandoned.
        task.last_heartbeat_at = now
        # Single-claimant invariant — see _finalize_claim. Same column
        # used by claimant_lock + trigger_filter. Cleared by QA pass/fail
        # and doc-complete when the review hand-off finishes.
        task.active_claimant_id = cast("Any", agent_id)
        await self.session.flush()
        return task

    async def qa_claim(self, qa_agent_id: UUID, task_id: UUID) -> TaskTable | None:
        """QA claims a task in awaiting_qa (no state transition)."""
        return await self._qa_or_doc_claim(qa_agent_id, task_id, TaskStatus.AWAITING_QA)

    async def doc_claim(self, doc_agent_id: UUID, task_id: UUID) -> TaskTable | None:
        """Documenter claims a task in awaiting_documentation."""
        return await self._qa_or_doc_claim(
            doc_agent_id, task_id, TaskStatus.AWAITING_DOCUMENTATION
        )

    async def qa_pass(
        self, qa_agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """QA passes the task (gateway-flavored wrapper of pass_qa).

        The audit row is attributed to QA via task.claimed_by (set by
        qa_claim). We assert qa_agent_id matches claimed_by so any future
        divergence surfaces loudly instead of silently mis-recording the
        actor. Clears the single-claimant lock so the
        documenter can claim cleanly.
        """
        task = await self.get(task_id)
        if task is not None:
            if (
                task.claimed_by is not None
                and to_python_uuid(task.claimed_by) != qa_agent_id
            ):
                self.log.warning(
                    "qa_pass actor mismatch",
                    task_id=str(task_id),
                    qa_agent_id=str(qa_agent_id),
                    claimed_by=str(task.claimed_by),
                )
            task.active_claimant_id = cast("Any", None)
            await self.session.flush()
        return await self.pass_qa(task_id, notes=notes, agent_role="qa")

    async def qa_fail(
        self,
        qa_agent_id: UUID,
        task_id: UUID,
        notes: str,
        issues: list[str],
    ) -> TaskTable | None:
        """QA fails the task with concrete issues.

        `notes` is the QA narrative (stored on `qa_notes`); `issues` is
        appended to `dev_notes` as a checklist for the dev's revision.
        Asserts the actor matches claimed_by.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if (
            task.claimed_by is not None
            and to_python_uuid(task.claimed_by) != qa_agent_id
        ):
            self.log.warning(
                "qa_fail actor mismatch",
                task_id=str(task_id),
                qa_agent_id=str(qa_agent_id),
                claimed_by=str(task.claimed_by),
            )
        if issues:
            issue_block = "[QA ISSUES]\n" + "\n".join(f"- {i}" for i in issues)
            task.dev_notes = _append_capped(task.dev_notes, issue_block)
        # Clear active_claimant_id — fail_qa transitions back to
        # needs_revision and reassigns to the original developer.
        task.active_claimant_id = cast("Any", None)
        await self.session.flush()
        return await self.fail_qa(task_id, notes=notes, agent_role="qa")

    async def _revision_pm_for_task(self, task: TaskTable) -> AgentTable | None:
        """The PM who owns an assembled task's revision.

        Cell PM for a cell team (the cell→root level), else the Main PM (the
        root→master level). Used by pr_fail so the in-path reviewer's rejection
        lands on whoever assembles the work.
        """
        try:
            team = Team(task.team) if task.team else None
        except ValueError:
            team = None
        if team in (Team.BACKEND, Team.FRONTEND, Team.UX_UI):
            return await self.cell_pm_for_team(team)
        return await self.main_pm_agent()

    async def pr_gate_claim(
        self, reviewer_agent_id: UUID, task_id: UUID
    ) -> TaskTable | None:
        """Reviewer claims an awaiting_pr_review task (no state transition).

        The in-path PR-review gate mirrors QA's claim_review: status stays at
        awaiting_pr_review while the reviewer inspects the assembled diff;
        pr_pass / pr_fail perform the transition.

        Single-claimant guard: two reviewers race-claiming the same gate task
        would otherwise overwrite the first's claim (last-write-wins) and the
        first reviewer's subsequent pr_pass / pr_fail would actor-mismatch
        against the new owner — wasting a review cycle. The guard refuses a
        second reviewer when the task is already actively claimed by a
        DIFFERENT PR-reviewer. The gate task is owned by the PM at entry
        (``submit_for_review`` does not clear ownership, unlike
        ``submit_for_qa``), so the guard must distinguish a PM/dev owner —
        which the first reviewer legitimately overclaims — from a competing
        reviewer claim. It does this by checking the existing claimant's
        ROLE: only a PR-reviewer active claimant is a competing review claim.
        The row is locked ``FOR UPDATE`` so concurrent claim attempts serialize
        at the DB level (the second claim sees the first's committed claim),
        mirroring the dev ``claim`` path. A re-claim by the SAME reviewer is
        idempotent (the ``existing != reviewer_agent_id`` check skips the
        guard).
        """
        lock_result = await self.session.execute(
            select(TaskTable)
            .where(TaskTable.id == task_id)
            .with_for_update(of=TaskTable)
        )
        task = lock_result.scalar_one_or_none()
        if task is None or task.status != TaskStatus.AWAITING_PR_REVIEW:
            return None
        existing = to_python_uuid(task.active_claimant_id)
        if existing is not None and existing != reviewer_agent_id:
            existing_agent = await self.agent_for(existing)
            if (
                existing_agent is not None
                and existing_agent.role == AgentRole.PR_REVIEWER.value
            ):
                self.log.warning(
                    "pr_gate_claim rejected - gate task already claimed by"
                    " another reviewer",
                    task_id=str(task_id),
                    existing_claimant=str(existing),
                    requesting_reviewer=str(reviewer_agent_id),
                )
                return None
        return await self._qa_or_doc_claim(
            reviewer_agent_id, task_id, TaskStatus.AWAITING_PR_REVIEW
        )

    async def submit_for_review(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Enter the in-path PR-review gate: in_progress -> awaiting_pr_review.

        Composed by the cell PM's submit_up (cell→root PR) and the main PM's
        submit_root (root→master PR); the assembled PR is already open by the
        time this runs. Mirrors submit_pm_review but targets the gate so a
        reviewer signs off before the PM merge.
        """
        if notes:
            await self.add_progress(task_id, agent_id, notes)
        task = await self.get(task_id)
        if task is None or task.status != TaskStatus.IN_PROGRESS:
            return None
        agent = await self.agent_for(agent_id)
        agent_role = agent.role if agent else "cell_pm"
        self._validate_and_set_status(task, TaskStatus.AWAITING_PR_REVIEW, agent_role)
        await self.session.flush()
        return task

    async def pr_pass(
        self, reviewer_agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Reviewer passes the gate: awaiting_pr_review -> awaiting_pm_review.

        Clears the claim so the PM-closure dispatcher routes the now-unassigned
        task to the owning PM to merge — the same path a leaf takes after
        docs_complete. Mirrors qa_pass.
        """
        task = await self.get(task_id)
        if task is None or task.status != TaskStatus.AWAITING_PR_REVIEW:
            return None
        if (
            task.claimed_by is not None
            and to_python_uuid(task.claimed_by) != reviewer_agent_id
        ):
            self.log.warning(
                "pr_pass actor mismatch",
                task_id=str(task_id),
                reviewer_agent_id=str(reviewer_agent_id),
                claimed_by=str(task.claimed_by),
            )
        captured = to_python_uuid(task.claimed_by)
        self._record_pr_review(task, summary=notes, verdict="passed")
        task.assigned_to = None
        task.claimed_by = None
        task.active_claimant_id = cast("Any", None)
        self._validate_and_set_status(
            task,
            TaskStatus.AWAITING_PM_REVIEW,
            "pr_reviewer",
            audit_agent_id=captured,
        )
        await self.session.flush()
        self.log.info("Assembled PR passed review", task_id=str(task_id))
        return task

    async def pr_fail(
        self,
        reviewer_agent_id: UUID,
        task_id: UUID,
        notes: str,
        issues: list[str],
    ) -> TaskTable | None:
        """Reviewer fails the gate: awaiting_pr_review -> needs_revision.

        Appends the concrete issues for the PM's revision and clears the claim;
        the revision dispatcher routes the assembled task back to its PM.
        Mirrors qa_fail — an assembled PR is the PM's to revise, so there is no
        original-developer reassign; routing is handled at dispatch.
        """
        task = await self.get(task_id)
        if task is None or task.status != TaskStatus.AWAITING_PR_REVIEW:
            return None
        if (
            task.claimed_by is not None
            and to_python_uuid(task.claimed_by) != reviewer_agent_id
        ):
            self.log.warning(
                "pr_fail actor mismatch",
                task_id=str(task_id),
                reviewer_agent_id=str(reviewer_agent_id),
                claimed_by=str(task.claimed_by),
            )
        captured = to_python_uuid(task.claimed_by)
        self._record_pr_review(task, summary=notes, verdict="failed", issues=issues)
        # Hand the failed assembled task to its PM to revise (cell PM for a cell
        # team, Main PM for the root); the revision dispatcher re-spawns whoever
        # owns a needs_revision task. Fall back to unassigned if no PM resolves.
        pm = await self._revision_pm_for_task(task)
        task.assigned_to = cast("Any", pm.id) if pm is not None else None
        task.claimed_by = cast("Any", pm.id) if pm is not None else None
        task.active_claimant_id = cast("Any", None)
        self._validate_and_set_status(
            task,
            TaskStatus.NEEDS_REVISION,
            "pr_reviewer",
            audit_agent_id=captured,
        )
        await self.session.flush()
        self.log.info("Assembled PR failed review", task_id=str(task_id))
        return task

    async def unblock_with_restore(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        *,
        restore: bool,
    ) -> TaskTable | None:
        """PM unblocks a task; restore=True returns it to its pre-block state.

        Pre-block snapshot lives on `pre_block_state` /
        `pre_block_assignee` / `pre_block_metadata` (migration 006). When
        restore=True and a snapshot exists, the task returns to that exact
        state. Otherwise falls through to the legacy unblock() which
        moves the task to in_progress and hands it back to the original
        raiser.
        """
        del pm_agent_id  # gateway already validated PM authority
        task = await self.get(task_id)
        if task is None:
            return None
        if not restore or not task.pre_block_state:
            return await self.unblock(task_id, agent_role="cell_pm")

        if task.status != TaskStatus.BLOCKED:
            return None

        try:
            restored_status = TaskStatus(task.pre_block_state)
        except ValueError:
            return await self.unblock(task_id, agent_role="cell_pm")

        return await self._apply_pre_block_restore(task, restored_status)

    async def _apply_pre_block_restore(
        self, task: TaskTable, restored_status: TaskStatus
    ) -> TaskTable:
        """Restore a blocked task to its snapshotted status + owner.

        Sets status directly (bypassing the strict transition validator) and so
        emits the audit explicitly, applies the branchless guard legacy
        unblock() relies on, restores ownership from the snapshot, and clears
        the pre-block snapshot fields.
        """
        # A task with no branch cannot resume in_progress — the dispatcher
        # refuses a branchless in_progress task and loops — so divert it to
        # pending, exactly as legacy unblock() does.
        if restored_status == TaskStatus.IN_PROGRESS and not task.branch_name:
            restored_status = TaskStatus.PENDING

        pre_status = (
            task.status.value
            if isinstance(task.status, TaskStatus)
            else str(task.status)
        )
        restored_owner = cast("Any", task.pre_block_assignee or task.claimed_by)
        task.status = restored_status
        if task.pre_block_assignee:
            task.assigned_to = cast("Any", task.pre_block_assignee)
            task.claimed_by = cast("Any", task.pre_block_assignee)
        task.pre_block_state = None
        task.pre_block_assignee = None
        task.pre_block_metadata = None
        task.blocker_resolver_type = None
        task.blocker_raised_by = None
        await self.session.flush()
        # This restore path sets the status directly (bypassing the strict
        # transition validator), so emit the audit explicitly — no status
        # change may skip the audit log.
        self._emit_status_transition_audit(
            task,
            from_status=pre_status,
            to_status=restored_status.value,
            agent_role=None,
            audit_agent_id=restored_owner,
        )
        return task

    async def cell_pm_complete(
        self,
        pm_agent_id: UUID,
        task_id: UUID,
        notes: str,
        merge_commit: str | None = None,
    ) -> TaskTable | None:
        """Cell PM completes a task; records the parent-branch merge commit.

        Wraps the canonical complete() to also annotate the task with the
        merge commit SHA on its parent branch. Merge SHA is appended to
        `task.commits` as a synthetic entry tagged 'merge' so downstream
        PR-tracking surfaces it without a separate column.
        """
        task = await self.get(task_id)
        if task is None:
            return None
        if notes:
            self._record_completion_notes(task, notes)
        if merge_commit:
            merge_entry = {
                "hash": merge_commit,
                "message": f"[merge] PR for task {task_id}",
                "timestamp": datetime.now(UTC).isoformat(),
                "author_agent_id": str(pm_agent_id),
                "kind": "merge",
            }
            task.commits = [*task.commits, merge_entry]
            await self.session.flush()
        return await self.complete(task_id, agent_id=pm_agent_id)

    async def escalate(
        self, agent_id: UUID, task_id: UUID, reason: str
    ) -> TaskTable | None:
        """Escalate a task one rung up the agent's escalation chain.

        Looks up the escalation target via `agents_config.ESCALATION_CHAIN`,
        then applies the same state mutations as a chain escalation:
        reassigns the task to the target, marks BLOCKED, and stashes the
        raiser so a future unblock can restore the workflow.
        """
        from roboco.agents_config import get_escalation_target

        task = await self.get(task_id)
        if task is None:
            return None

        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return None

        target_slug = get_escalation_target(agent.slug)
        if not target_slug:
            return None

        target_result = await self.session.execute(
            select(AgentTable).where(AgentTable.slug == target_slug)
        )
        target = target_result.scalar_one_or_none()
        if target is None:
            return None

        # The board/advisory guard lives in apply_escalation so the HTTP
        # escalate route is covered too; nothing extra to do here.
        applied = await self.apply_escalation(
            task=task,
            target_agent_id=UUID(str(target.id)),
            escalator_slug=agent.slug,
            target_slug=target_slug,
            reason=reason,
        )
        return task if applied else None

    async def _is_board_advisory_agent(self, agent_id: UUID) -> bool:
        """True if ``agent_id`` is a board/advisory role (PO / marketing / auditor)."""
        result = await self.session.execute(
            select(AgentTable.role).where(AgentTable.id == agent_id)
        )
        role = result.scalar_one_or_none()
        return role in _BOARD_ADVISORY_ROLES

    async def _is_main_pm_agent(self, agent_id: UUID) -> bool:
        """True if ``agent_id`` is the Main PM role (the coordinator with no code verb).

        Twin of ``_is_board_advisory_agent`` for the impossibility invariant —
        consulted by the escalation / reassign / claim guards that divert or
        refuse a ``main_pm`` + ``code`` hand-off to a Main-PM target.
        """
        result = await self.session.execute(
            select(AgentTable.role).where(AgentTable.id == agent_id)
        )
        role = result.scalar_one_or_none()
        return role == AgentRole.MAIN_PM

    async def _divert_owned_task_to_pool(self, task: TaskTable, *, note: str) -> None:
        """Clear ownership and return ``task`` to PENDING for a role-matched claim.

        Shared backstop for the cell-ownership invariant: a board/advisory
        role must never own — or be revived as the owner of — a cell task. The
        escalation, reassign, and dependency-revival write-sites all funnel a
        refused hand-off here. Sets PENDING directly (bypassing the strict
        transition validator), so emits the ``task.pending`` audit explicitly —
        no status change may skip the audit log. ``note`` is appended to
        ``dev_notes`` explaining why the hand-off was refused.
        """
        pre_status = (
            task.status.value
            if isinstance(task.status, TaskStatus)
            else str(task.status)
        )
        prior_owner = cast("Any", task.claimed_by or task.assigned_to)
        task.assigned_to = cast("Any", None)
        task.claimed_by = cast("Any", None)
        task.active_claimant_id = cast("Any", None)
        task.status = TaskStatus.PENDING
        task.dev_notes = (task.dev_notes or "") + note
        await self.session.flush()
        self._emit_status_transition_audit(
            task,
            from_status=pre_status,
            to_status=TaskStatus.PENDING.value,
            agent_role=None,
            audit_agent_id=prior_owner,
        )

    async def _release_code_task_to_pool(
        self,
        *,
        task: TaskTable,
        escalator_slug: str,
        blocked_target_slug: str,
        reason: str,
    ) -> None:
        """Release a descendant executable task to PENDING for a role-matched claim.

        Used instead of escalating a code / documentation / design task onto a
        board/advisory role. Clears the assignee so the orchestrator's
        role-matched dispatch picks it up cleanly, sets PENDING (a valid
        re-dispatch source), and appends an audit note explaining why the board
        hand-off was refused.
        """
        await self._divert_owned_task_to_pool(
            task,
            note=(
                f"\n\n[ESCALATION REDIRECTED] {escalator_slug} escalated this"
                f" task toward {blocked_target_slug} (a board/advisory role that"
                f" cannot own delivery / coordination work — no build, delegate,"
                f" unblock, or complete verb). Released to the pool for a"
                f" role-matched claim instead."
                f"\nReason: {reason}"
            ),
        )
        self.log.info(
            "Descendant executable task released to pool instead of board escalation",
            task_id=str(task.id),
            escalator=escalator_slug,
            refused_target=blocked_target_slug,
        )

    async def escalate_up_to_role(
        self,
        agent_id: UUID,
        task_id: UUID,
        target_role: str,
        reason: str,
    ) -> TaskTable | None:
        """Escalate a task to an agent holding `target_role`.

        Picks the first agent matching the role; ties broken by created_at.
        Used when the escalation target is known by role rather than slug
        (e.g., 'main_pm' resolves to whichever agent currently holds the
        Main PM role).
        """
        task = await self.get(task_id)
        if task is None:
            return None

        agent_result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return None

        try:
            role_enum = AgentRole(target_role)
        except ValueError:
            return None

        target_result = await self.session.execute(
            select(AgentTable)
            .where(AgentTable.role == role_enum)
            .order_by(AgentTable.created_at)
            .limit(1)
        )
        target = target_result.scalar_one_or_none()
        if target is None:
            return None

        applied = await self.apply_escalation(
            task=task,
            target_agent_id=UUID(str(target.id)),
            escalator_slug=agent.slug,
            target_slug=target.slug,
            reason=reason,
        )
        # apply_escalation refuses terminal tasks (F043); mirror escalate()'s
        # contract so the gateway emits a clean invalid_state envelope.
        return task if applied else None

    async def list_in_progress_for_agent(self, agent_id: UUID) -> list[TaskTable]:
        """Tasks the agent is still on the hook for — in_progress OR blocked.

        F018: ``blocked`` is included because a blocked task is still owned and
        ``unblock_with_restore`` resumes it to ``in_progress``; the claim guard
        (``already_active_guard``) must see it or a dev could claim a second
        task while blocked and end up with two ``in_progress`` tasks once the
        first is unblocked. Callers that only want pausable (in_progress) tasks
        — the ``i_am_idle`` auto-pause path — filter on ``status`` themselves
        (``pause()`` no-ops on non-in_progress regardless).
        """
        query = (
            select(TaskTable)
            .where(
                TaskTable.assigned_to == agent_id,
                TaskTable.status.in_([TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]),
            )
            .order_by(TaskTable.priority, TaskTable.updated_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def pause_for_agent(
        self, agent_id: UUID, task_id: UUID, agent_role: str | None = None
    ) -> TaskTable | None:
        """Gateway-flavored pause: only succeeds when caller owns the task."""
        task = await self.get(task_id)
        if task is None or task.assigned_to != agent_id:
            return None
        return await self.pause(task_id, agent_role=agent_role)

    async def submit_pm_review(
        self, agent_id: UUID, task_id: UUID, notes: str
    ) -> TaskTable | None:
        """Gateway alias of submit_for_pm_review for cell-PM submit_up.

        Flushes any progress note then transitions in_progress →
        awaiting_pm_review with the agent's role inferred from agent_id so
        the lifecycle validator allows the transition.
        """
        if notes:
            await self.add_progress(task_id, agent_id, notes)
        agent = await self.agent_for(agent_id)
        agent_role = agent.role if agent else "cell_pm"
        return await self.submit_for_pm_review(
            task_id, agent_role=agent_role, notes=notes or None
        )

    async def create_subtask(self, req: TaskCreateRequest) -> TaskTable:
        """PM-friendly subtask creation; sets status from assignee presence.

        When ``assigned_to`` is provided the task is created in ``pending`` so
        the orchestrator can spawn the assignee immediately. Without an
        assignee it stays in ``backlog`` and a PM must run ``activate`` later.
        Caller-supplied status takes precedence; otherwise we infer from the
        presence of an assignee.

        Foundation rule: no task without acceptance_criteria. The silent
        fallback that substituted ``["completed and reviewed by assignee"]``
        was deleted on 2026-05-10 (spec §5.2) — it was the proximate cause
        of every skeleton task in the same-day smoke run. Defense-in-depth:
        the gateway and route-layer schemas reject under-filled tasks
        earlier, but this service-layer guard remains as a hard backstop
        for non-gateway / non-route callers.
        """
        from roboco.foundation.policy.task_completeness import (
            TASK_AT_CREATE,
            TaskCompletenessError,
            check,
        )

        if req.parent_task_id is None:
            raise ValueError("create_subtask requires parent_task_id")

        result = check(TASK_AT_CREATE, req)
        if not result.passed:
            raise TaskCompletenessError(
                missing=result.missing,
                field_hints=result.field_hints,
                message=(
                    "create_subtask: task missing required fields: "
                    f"{result.missing}. The silent fallback at "
                    "services/task.py:5061 was removed 2026-05-10 (spec §5.2)."
                ),
            )

        inferred_status = TaskStatus.PENDING if req.assigned_to else TaskStatus.BACKLOG
        prepared = TaskCreateRequest(
            title=req.title,
            description=req.description,
            acceptance_criteria=req.acceptance_criteria,
            parent_ac_refs=req.parent_ac_refs,
            team=req.team,
            created_by=req.created_by,
            project_id=req.project_id,
            product_id=req.product_id,
            parent_task_id=req.parent_task_id,
            assigned_to=req.assigned_to,
            estimated_complexity=req.estimated_complexity,
            task_type=req.task_type,
            nature=req.nature,
            status=req.status or inferred_status,
            # Forward ordering + collision surface so a dev task delegated with
            # surfaces/dependencies keeps them (multi-level sequencing — edge
            # kinds 3 & 4). Previously dropped here, which is why dev-task
            # dependency_ids was always [] and the only ordering was the weak
            # assignee-keyed spawn barrier (live 2026-06-27 out-of-order break).
            sequence=req.sequence,
            dependency_ids=list(req.dependency_ids) if req.dependency_ids else [],
            batch_id=req.batch_id,
            intends_to_touch=req.intends_to_touch,
            adds_migration=req.adds_migration,
            touches_shared=req.touches_shared,
        )
        return await self.create(prepared)


# =============================================================================
# PM RESOLUTION HELPERS
# =============================================================================


async def resolve_pm_for_substitute(
    db: AsyncSession,
    agent_slug: str | None,
    task_team: Team | None,
) -> tuple[str | None, UUID | None]:
    """
    Resolve the PM slug and UUID for a substitute request.

    Args:
        db: Database session
        agent_slug: The agent's slug for PM lookup
        task_team: The task's team for fallback PM lookup

    Returns:
        Tuple of (pm_slug, pm_uuid) or (None, None) if not found
    """
    from roboco.agents_config import get_pm_for_agent, get_pm_for_team

    target_pm_slug = None
    if agent_slug:
        target_pm_slug = get_pm_for_agent(agent_slug)
    if not target_pm_slug and task_team:
        target_pm_slug = get_pm_for_team(task_team.value)

    if not target_pm_slug:
        return None, None

    pm_result = await db.execute(
        select(AgentTable).where(AgentTable.slug == target_pm_slug)
    )
    pm_agent = pm_result.scalar_one_or_none()
    return target_pm_slug, to_python_uuid(pm_agent.id) if pm_agent else None


async def notify_pm_for_substitute(
    db: AsyncSession,
    pm_slug: str,
    task_id: UUID,
    from_agent_id: UUID,
    message: tuple[str, str],
) -> None:
    """
    Create and deliver a notification to PM for substitute request.

    Args:
        db: Database session
        pm_slug: Target PM's slug
        task_id: The task being substituted
        from_agent_id: Agent requesting substitution
        message: Tuple of (subject, body) for the notification
    """
    from roboco.db.tables import NotificationTable
    from roboco.services.notification_delivery import get_notification_delivery_service

    pm_result = await db.execute(select(AgentTable).where(AgentTable.slug == pm_slug))
    pm_agent = pm_result.scalar_one_or_none()
    if not pm_agent:
        return

    subject, body = message
    notification = NotificationTable(
        type="task_assignment",
        priority="high",
        from_agent=from_agent_id,
        to_agents=[pm_agent.id],
        subject=subject,
        body=body,
        related_task_id=task_id,
        requires_ack=True,
    )
    db.add(notification)
    await db.flush()

    delivery_service = get_notification_delivery_service(db)
    await delivery_service.deliver(require_uuid(notification.id))


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_task_service(session: AsyncSession) -> TaskService:
    """Get a TaskService instance."""
    return TaskService(session)
