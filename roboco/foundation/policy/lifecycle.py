"""Canonical lifecycle + permissions spec.

Single source of truth for:
  - task lifecycle status transitions
  - per-role permissions on atomic actions
  - per-role permissions on gateway intent verbs
  - claim restrictions
  - team-based access rules
  - self-review prevention rules

Every consumer (choreographer, MCP manifest, RAG corpus, agent prompts,
panel UI, tests, middleware) reads its behavior from this module.

Predecessor canon (prose):
  - docs/internal/old/workflows/STATUS_TRANSITIONS.md
  - docs/internal/old/workflows/PERMISSIONS.md

If this module disagrees with those documents, the discrepancy is
recorded in the spec design doc:
  docs/superpowers/specs/2026-05-09-lifecycle-canonical-spec-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

# Role enum is canonicalized in roboco/foundation/identity.py.
# Re-exported here so callers can import `Role` from this module alongside
# the lifecycle tables that depend on it. New consumers may also import
# from `roboco.foundation.identity` directly.
from roboco.foundation.identity import Role

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID


class Status(StrEnum):
    BACKLOG = "backlog"
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PAUSED = "paused"
    VERIFYING = "verifying"
    AWAITING_QA = "awaiting_qa"
    NEEDS_REVISION = "needs_revision"
    AWAITING_DOCUMENTATION = "awaiting_documentation"
    AWAITING_PM_REVIEW = "awaiting_pm_review"
    AWAITING_CEO_APPROVAL = "awaiting_ceo_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskType(StrEnum):
    CODE = "code"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    PLANNING = "planning"
    DESIGN = "design"
    ADMINISTRATIVE = "administrative"


RejectionKind = Literal[
    "not_authorized",
    "invalid_state",
    "tracing_gap",
    "self_review",
    "not_found",
]


@dataclass(frozen=True)
class Decision:
    """Single shape every consumer maps onto its native rejection format.

    `allow()`, `reject(kind, ...)`, and `tracing_gap(missing, remediate)`
    are the three canonical constructors. Direct __init__ is supported
    but enforces the invariants below so callers can't build a malformed
    Decision.

    Invariants (enforced in __post_init__):
      * allowed=True  ⇒ rejection_kind is None and missing == []
      * allowed=False ⇒ rejection_kind is not None
    """

    allowed: bool
    rejection_kind: RejectionKind | None
    message: str | None
    missing: list[str] = field(default_factory=list)
    remediate: str | None = None

    def __post_init__(self) -> None:
        if self.allowed and self.rejection_kind is not None:
            raise ValueError(
                "Decision invariant: allowed=True requires rejection_kind=None"
            )
        if not self.allowed and self.rejection_kind is None:
            raise ValueError(
                "Decision invariant: allowed=False requires rejection_kind set"
            )
        if self.allowed and (self.missing or self.remediate is not None):
            raise ValueError("allowed=True requires missing=[] and remediate=None")

    @classmethod
    def allow(cls) -> Decision:
        return cls(
            allowed=True,
            rejection_kind=None,
            message=None,
            missing=[],
            remediate=None,
        )

    @classmethod
    def reject(
        cls,
        *,
        kind: RejectionKind,
        message: str,
        remediate: str,
    ) -> Decision:
        return cls(
            allowed=False,
            rejection_kind=kind,
            message=message,
            missing=[],
            remediate=remediate,
        )

    @classmethod
    def tracing_gap(cls, *, missing: list[str], remediate: str) -> Decision:
        return cls(
            allowed=False,
            rejection_kind="tracing_gap",
            message=None,
            missing=list(missing),
            remediate=remediate,
        )


@dataclass(frozen=True)
class Precondition:
    """Declarative gate-table row.

    `check` returns True if the precondition holds. `remediate` is the
    human-readable hint surfaced verbatim on rejection. `missing_token`
    is what shows up in the `tracing_gap.missing[]` field of the
    envelope (so agents can do exact-string checks).
    """

    key: str
    check: Callable[[Any, Any, Any], bool]
    remediate: str
    missing_token: str


@dataclass(frozen=True)
class ActionSpec:
    """Atomic, pre-gateway-style action (claim, start, submit_qa, ...).

    `target_status=None` means the action does not transition the task
    (e.g. progress-recording actions). `allowed_task_types=None` means
    no restriction. `needs_team_match` is the agent.team == task.team
    rule from PERMISSIONS.md (Team-Based Restrictions).
    """

    name: str
    allowed_roles: frozenset[Role]
    source_statuses: frozenset[Status]
    target_status: Status | None
    allowed_task_types: frozenset[TaskType] | None
    preconditions: tuple[Precondition, ...]
    self_review_block: bool
    needs_team_match: bool


@dataclass(frozen=True)
class IntentSpec:
    """Gateway intent verb — a named, atomic composition of ActionSpecs.

    `composes` lists the atomic action names in the order they execute.
    `extra_preconditions` are verb-level checks the composing actions
    don't cover (e.g. open_pr's "no PR already open" check).
    `side_effects` is a tuple of named git/branch/PR operations the
    runner invokes after the DB savepoint commits.
    `pre_side_effects` are git/branch/PR operations the runner invokes
    BEFORE the composing actions — for transitions that depend on a git
    op having already run (e.g. submit_up must open the cell→root PR
    before submit_pm_review's pr_created gate can pass).
    """

    name: str
    allowed_roles: frozenset[Role]
    description: str
    composes: tuple[str, ...]
    extra_preconditions: tuple[Precondition, ...]
    side_effects: tuple[str, ...]
    next_hint: Callable[[Any], str]
    pre_side_effects: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatusTransition:
    """A row from STATUS_TRANSITIONS.md, machine-readable.

    `role_constraint=None` means: inherit whatever the
    `triggered_by_action`'s ActionSpec.allowed_roles says. Set explicitly
    only when the transition's role gate differs from the action's.
    """

    source: Status
    target: Status
    triggered_by_action: str
    role_constraint: frozenset[Role] | None


# ---------------------------------------------------------------------------
# Status transitions (predecessor canon: STATUS_TRANSITIONS.md)
# ---------------------------------------------------------------------------

_STATUS_TRANSITIONS: tuple[StatusTransition, ...] = (
    # PM setup
    StatusTransition(Status.BACKLOG, Status.PENDING, "activate", None),
    # Claim path. role_constraint=None on rows below means "any role —
    # the per-role-vs-status filtering is in CLAIM_RULES".
    # A None here is NOT an oversight; it is the explicit handoff
    # point between the StatusTransition table (state machine) and
    # CLAIM_RULES (per-role claim authority).
    StatusTransition(Status.PENDING, Status.CLAIMED, "claim", None),
    StatusTransition(Status.AWAITING_QA, Status.CLAIMED, "claim", frozenset({Role.QA})),
    StatusTransition(
        Status.AWAITING_DOCUMENTATION,
        Status.CLAIMED,
        "claim",
        frozenset({Role.DOCUMENTER}),
    ),
    StatusTransition(Status.NEEDS_REVISION, Status.CLAIMED, "claim", None),
    # Start
    StatusTransition(Status.CLAIMED, Status.IN_PROGRESS, "start", None),
    # Block / pause / resume
    StatusTransition(Status.IN_PROGRESS, Status.BLOCKED, "block", None),
    StatusTransition(Status.IN_PROGRESS, Status.PAUSED, "pause", None),
    StatusTransition(Status.BLOCKED, Status.IN_PROGRESS, "unblock", None),
    # A task blocked before it was ever claimed (a dependency-gated claim that
    # got escalated) has no branch — unblocking it returns it to the claim pool
    # rather than a branchless in_progress the dispatcher refuses to spawn.
    StatusTransition(Status.BLOCKED, Status.PENDING, "unblock", None),
    StatusTransition(Status.PAUSED, Status.IN_PROGRESS, "resume", None),
    # Dev verify + submit
    StatusTransition(Status.IN_PROGRESS, Status.VERIFYING, "submit_verification", None),
    StatusTransition(Status.VERIFYING, Status.AWAITING_QA, "submit_qa", None),
    # PR reviewer posts its change-request and the review task is done
    StatusTransition(
        Status.IN_PROGRESS,
        Status.COMPLETED,
        "pr_review_done",
        frozenset({Role.PR_REVIEWER}),
    ),
    # QA pass / fail
    StatusTransition(
        Status.AWAITING_QA,
        Status.AWAITING_DOCUMENTATION,
        "qa_pass",
        frozenset({Role.QA}),
    ),
    StatusTransition(
        Status.AWAITING_QA,
        Status.NEEDS_REVISION,
        "qa_fail",
        frozenset({Role.QA}),
    ),
    # Documenter completes
    StatusTransition(
        Status.AWAITING_DOCUMENTATION,
        Status.AWAITING_PM_REVIEW,
        "docs_complete",
        frozenset({Role.DOCUMENTER}),
    ),
    # PM completes / escalates
    StatusTransition(
        Status.AWAITING_PM_REVIEW,
        Status.COMPLETED,
        "complete",
        frozenset({Role.CELL_PM, Role.MAIN_PM}),
    ),
    StatusTransition(
        Status.AWAITING_PM_REVIEW,
        Status.AWAITING_CEO_APPROVAL,
        "escalate_to_ceo",
        frozenset({Role.MAIN_PM, Role.PRODUCT_OWNER, Role.HEAD_MARKETING}),
    ),
    # A blocked task the PM cannot resolve can be surfaced to the CEO directly.
    StatusTransition(
        Status.BLOCKED,
        Status.AWAITING_CEO_APPROVAL,
        "escalate_to_ceo",
        frozenset({Role.MAIN_PM, Role.PRODUCT_OWNER, Role.HEAD_MARKETING}),
    ),
    # CEO approve / reject
    StatusTransition(
        Status.AWAITING_CEO_APPROVAL,
        Status.COMPLETED,
        "ceo_approve",
        frozenset({Role.CEO}),
    ),
    StatusTransition(
        Status.AWAITING_CEO_APPROVAL,
        Status.NEEDS_REVISION,
        "ceo_reject",
        frozenset({Role.CEO}),
    ),
    # Direct PM submission for non-dev tasks
    StatusTransition(
        Status.IN_PROGRESS,
        Status.AWAITING_PM_REVIEW,
        "submit_pm_review",
        None,
    ),
    # Cancel — PM/CEO can cancel from any non-terminal status
    *(
        StatusTransition(
            src,
            Status.CANCELLED,
            "cancel",
            frozenset({Role.CELL_PM, Role.MAIN_PM, Role.CEO}),
        )
        for src in Status
        if src not in (Status.COMPLETED, Status.CANCELLED)
    ),
)


def _build_status_graph() -> dict[Status, frozenset[Status]]:
    """`source → frozenset(targets)` view derived from _STATUS_TRANSITIONS."""
    graph: dict[Status, set[Status]] = {s: set() for s in Status}
    for t in _STATUS_TRANSITIONS:
        graph[t.source].add(t.target)
    return {src: frozenset(targets) for src, targets in graph.items()}


STATUS_GRAPH: dict[Status, frozenset[Status]] = _build_status_graph()


# ---------------------------------------------------------------------------
# Atomic actions (predecessor canon: PERMISSIONS.md "Task Management Tools")
# ---------------------------------------------------------------------------

_PM_ROLES: frozenset[Role] = frozenset({Role.CELL_PM, Role.MAIN_PM})
_DEV_ROLES: frozenset[Role] = frozenset({Role.DEVELOPER})
_QA_ROLES: frozenset[Role] = frozenset({Role.QA})
_DOC_ROLES: frozenset[Role] = frozenset({Role.DOCUMENTER})


_ATOMIC_ACTIONS: dict[str, ActionSpec] = {
    "activate": ActionSpec(
        name="activate",
        allowed_roles=_PM_ROLES,
        source_statuses=frozenset({Status.BACKLOG}),
        target_status=Status.PENDING,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    # claim's source_statuses is the UNION across all roles — see CLAIM_RULES
    # for per-role authority. Both tables are authoritative; a validator
    # checks consistency between them.
    "claim": ActionSpec(
        name="claim",
        allowed_roles=frozenset(
            _DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES | {Role.PR_REVIEWER}
        ),
        source_statuses=frozenset(
            {
                Status.PENDING,
                Status.NEEDS_REVISION,
                Status.AWAITING_QA,
                Status.AWAITING_DOCUMENTATION,
            }
        ),
        target_status=Status.CLAIMED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "start": ActionSpec(
        name="start",
        allowed_roles=frozenset(
            _DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES | {Role.PR_REVIEWER}
        ),
        source_statuses=frozenset({Status.CLAIMED}),
        target_status=Status.IN_PROGRESS,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "set_plan": ActionSpec(
        name="set_plan",
        allowed_roles=frozenset(_DEV_ROLES | _PM_ROLES),
        source_statuses=frozenset({Status.CLAIMED}),
        target_status=None,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "block": ActionSpec(
        name="block",
        allowed_roles=frozenset(_DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES),
        source_statuses=frozenset({Status.IN_PROGRESS}),
        target_status=Status.BLOCKED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "unblock": ActionSpec(
        name="unblock",
        allowed_roles=_PM_ROLES,
        source_statuses=frozenset({Status.BLOCKED}),
        target_status=Status.IN_PROGRESS,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    "pause": ActionSpec(
        name="pause",
        allowed_roles=frozenset(_DEV_ROLES | _PM_ROLES),
        source_statuses=frozenset({Status.IN_PROGRESS}),
        target_status=Status.PAUSED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "resume": ActionSpec(
        name="resume",
        allowed_roles=frozenset(_DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES),
        source_statuses=frozenset({Status.PAUSED}),
        target_status=Status.IN_PROGRESS,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    "submit_verification": ActionSpec(
        name="submit_verification",
        allowed_roles=_DEV_ROLES,
        source_statuses=frozenset({Status.IN_PROGRESS}),
        target_status=Status.VERIFYING,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "submit_qa": ActionSpec(
        name="submit_qa",
        allowed_roles=_DEV_ROLES,
        source_statuses=frozenset({Status.VERIFYING}),
        target_status=Status.AWAITING_QA,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "qa_pass": ActionSpec(
        name="qa_pass",
        allowed_roles=_QA_ROLES,
        source_statuses=frozenset({Status.AWAITING_QA}),
        target_status=Status.AWAITING_DOCUMENTATION,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=True,
        needs_team_match=True,
    ),
    "qa_fail": ActionSpec(
        name="qa_fail",
        allowed_roles=_QA_ROLES,
        source_statuses=frozenset({Status.AWAITING_QA}),
        target_status=Status.NEEDS_REVISION,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=True,
        needs_team_match=True,
    ),
    "pr_review_done": ActionSpec(
        name="pr_review_done",
        allowed_roles=frozenset({Role.PR_REVIEWER}),
        source_statuses=frozenset({Status.IN_PROGRESS}),
        target_status=Status.COMPLETED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    "docs_complete": ActionSpec(
        name="docs_complete",
        allowed_roles=_DOC_ROLES,
        source_statuses=frozenset({Status.AWAITING_DOCUMENTATION}),
        target_status=Status.AWAITING_PM_REVIEW,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=True,
        needs_team_match=True,
    ),
    "complete": ActionSpec(
        name="complete",
        allowed_roles=_PM_ROLES,
        source_statuses=frozenset({Status.AWAITING_PM_REVIEW}),
        target_status=Status.COMPLETED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "submit_pm_review": ActionSpec(
        name="submit_pm_review",
        allowed_roles=frozenset(_PM_ROLES | _QA_ROLES | _DOC_ROLES | _DEV_ROLES),
        source_statuses=frozenset({Status.IN_PROGRESS}),
        target_status=Status.AWAITING_PM_REVIEW,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
    "escalate_to_ceo": ActionSpec(
        name="escalate_to_ceo",
        allowed_roles=frozenset(
            {
                Role.MAIN_PM,
                Role.PRODUCT_OWNER,
                Role.HEAD_MARKETING,
            }
        ),
        # Reachable from a completed review (the normal sign-off escalation) AND
        # from a blocked task the PM cannot resolve — so a wedged task has a
        # clean verb to a human decision instead of only the admin override.
        source_statuses=frozenset({Status.AWAITING_PM_REVIEW, Status.BLOCKED}),
        target_status=Status.AWAITING_CEO_APPROVAL,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    "ceo_approve": ActionSpec(
        name="ceo_approve",
        allowed_roles=frozenset({Role.CEO}),
        source_statuses=frozenset({Status.AWAITING_CEO_APPROVAL}),
        target_status=Status.COMPLETED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    "ceo_reject": ActionSpec(
        name="ceo_reject",
        allowed_roles=frozenset({Role.CEO}),
        source_statuses=frozenset({Status.AWAITING_CEO_APPROVAL}),
        target_status=Status.NEEDS_REVISION,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    "cancel": ActionSpec(
        name="cancel",
        allowed_roles=frozenset(_PM_ROLES | {Role.CEO}),
        source_statuses=frozenset(
            s for s in Status if s not in (Status.COMPLETED, Status.CANCELLED)
        ),
        target_status=Status.CANCELLED,
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=False,
    ),
    "create_subtask": ActionSpec(
        name="create_subtask",
        allowed_roles=_PM_ROLES,
        source_statuses=frozenset({Status.IN_PROGRESS}),  # parent must be in_progress
        target_status=None,  # creates a NEW task; doesn't transition the parent
        allowed_task_types=None,
        preconditions=(),
        self_review_block=False,
        needs_team_match=True,
    ),
}


# ---------------------------------------------------------------------------
# Claim rules (predecessor canon: PERMISSIONS.md "Claim Restrictions by Role")
# ---------------------------------------------------------------------------

CLAIM_RULES: dict[Role, frozenset[Status]] = {
    Role.DEVELOPER: frozenset({Status.PENDING, Status.NEEDS_REVISION}),
    Role.QA: frozenset({Status.AWAITING_QA}),
    Role.DOCUMENTER: frozenset({Status.PENDING, Status.AWAITING_DOCUMENTATION}),
    Role.CELL_PM: frozenset({Status.PENDING}),
    Role.MAIN_PM: frozenset({Status.PENDING}),
    Role.PRODUCT_OWNER: frozenset(),
    Role.HEAD_MARKETING: frozenset(),
    Role.AUDITOR: frozenset(),
    Role.PR_REVIEWER: frozenset({Status.PENDING}),
    Role.CEO: frozenset(),
}


# ---------------------------------------------------------------------------
# Team rules (predecessor canon: PERMISSIONS.md "Team-Based Restrictions")
# Per-slug. None means "any team" (cross-cell or board roles).
# ---------------------------------------------------------------------------

ROLE_TEAM_RULES: dict[str, str | None] = {
    "be-dev-1": "backend",
    "be-dev-2": "backend",
    "be-qa": "backend",
    "be-pm": "backend",
    "be-doc": "backend",
    "fe-dev-1": "frontend",
    "fe-dev-2": "frontend",
    "fe-qa": "frontend",
    "fe-pm": "frontend",
    "fe-doc": "frontend",
    "ux-dev-1": "ux_ui",
    "ux-dev-2": "ux_ui",
    "ux-qa": "ux_ui",
    "ux-pm": "ux_ui",
    "ux-doc": "ux_ui",
    "main-pm": None,
    "product-owner": None,
    "head-marketing": None,
    "auditor": None,
    "pr-reviewer-1": None,
    "ceo": None,
}


# ---------------------------------------------------------------------------
# Intent verbs (gateway-facing surface; each composes >=0 atomic actions)
# ---------------------------------------------------------------------------


def _next_hint_idle(_t: Any) -> str:
    return "idle until next work arrives"


def _next_hint_open_pr(_t: Any) -> str:
    return "PR opened; call i_am_done(task_id, notes='...') when self-verified"


def _next_hint_after_claim(_t: Any) -> str:
    return (
        "edit + commit(message) for each meaningful change,"
        " then open_pr(task_id) and i_am_done(task_id)"
    )


def _next_hint_after_plan(_t: Any) -> str:
    return (
        "delegate(parent_task_id, title, description, assigned_to,"
        " team, task_type) for each subtask"
    )


def _next_hint_continue_delegating(_t: Any) -> str:
    return "continue delegating subtasks, or i_am_idle when done"


def _next_hint_qa_review(_t: Any) -> str:
    return (
        "review the diff. Then call pass(notes) to accept or fail(issues) to"
        " request changes."
    )


def _next_hint_dev_revise(_t: Any) -> str:
    return "idle - dev will revise and re-submit"


def _next_hint_doc_after_claim(_t: Any) -> str:
    return (
        "write docs in your workspace, commit them, then call"
        " i_documented(task_id, notes, files)"
    )


def _next_hint_doc_done(_t: Any) -> str:
    return "idle until PM completes"


def _next_hint_pm_complete(_t: Any) -> str:
    return "merged into target; triage() for next item"


def _next_hint_pm_idle(_t: Any) -> str:
    return "idle until subtasks finish"


# ---------------------------------------------------------------------------
# Context — the third arg to Precondition.check (caller-supplied state)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """Carrier for caller-supplied state the spec needs to evaluate
    preconditions (e.g. the agent's `plan` argument on i_will_work_on,
    the journal:decision presence flag).

    Pure data; no behavior. The choreographer builds one of these per
    request before calling spec.can_invoke_intent.
    """

    actor_id: UUID | None = None
    plan: str | dict[str, Any] | None = None
    has_journal_decision: bool = False
    has_journal_reflect: bool = False
    has_journal_learning: bool = False
    progress_count: int = 0
    qa_evidence_inspected: bool = False
    actor_slug: str | None = None
    original_developer_slug: str | None = None
    notes: str | None = None
    issues: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Pre-defined preconditions wired into IntentSpecs
# ---------------------------------------------------------------------------


def _p_has_plan_or_supplied(task: Any, _agent: Any, ctx: Any) -> bool:
    return bool(getattr(task, "plan", None)) or bool(getattr(ctx, "plan", None))


def _p_has_commits(task: Any, _agent: Any, _ctx: Any) -> bool:
    return bool(getattr(task, "commits", None))


def _p_no_pr_yet(task: Any, _agent: Any, _ctx: Any) -> bool:
    return getattr(task, "pr_number", None) is None


def _p_owns_task(task: Any, _agent: Any, ctx: Any) -> bool:
    return getattr(task, "assigned_to", None) == getattr(ctx, "actor_id", None)


PRECONDITION_PLAN = Precondition(
    key="plan",
    check=_p_has_plan_or_supplied,
    remediate=(
        "call again with plan='<one-paragraph plan describing what you will do>'"
    ),
    missing_token="plan",
)

PRECONDITION_COMMITS = Precondition(
    key="commits>=1",
    check=_p_has_commits,
    remediate=(
        "commit at least one change before opening a PR — call commit(message='...')"
    ),
    missing_token="commits>=1",
)

PRECONDITION_NO_PR = Precondition(
    key="no_prior_pr",
    check=_p_no_pr_yet,
    remediate="a PR is already open for this task; call i_am_done(task_id, notes=...)",
    missing_token="no_prior_pr",
)

PRECONDITION_OWNERSHIP = Precondition(
    key="owns_task",
    check=_p_owns_task,
    remediate="task is not assigned to you; call give_me_work() to find your work",
    missing_token="owns_task",
)


_INTENT_VERBS: dict[str, IntentSpec] = {
    # Phase 1: developer verbs
    "give_me_work": IntentSpec(
        name="give_me_work",
        allowed_roles=frozenset(
            {
                Role.DEVELOPER,
                Role.QA,
                Role.DOCUMENTER,
                Role.CELL_PM,
                Role.MAIN_PM,
                Role.PR_REVIEWER,
            }
        ),
        description="Return your most-actionable task or signal idle.",
        composes=(),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "act on the task returned, or i_am_idle if none",
    ),
    "i_will_work_on": IntentSpec(
        name="i_will_work_on",
        allowed_roles=_DEV_ROLES,
        description=(
            "Claim a task, set the plan, and transition to in_progress."
            " Atomic - preconditions checked before any state mutation."
        ),
        composes=("claim", "set_plan", "start"),
        extra_preconditions=(PRECONDITION_PLAN,),
        side_effects=(),
        next_hint=_next_hint_after_claim,
    ),
    "i_will_plan": IntentSpec(
        name="i_will_plan",
        allowed_roles=_PM_ROLES,
        description=(
            "PM mirror of i_will_work_on for parent tasks. Claim, plan,"
            " transition to in_progress; from there delegate subtasks."
        ),
        composes=("claim", "set_plan", "start"),
        extra_preconditions=(PRECONDITION_PLAN,),
        side_effects=(),
        next_hint=_next_hint_after_plan,
    ),
    "delegate": IntentSpec(
        name="delegate",
        allowed_roles=_PM_ROLES,
        description=(
            "Create a subtask under the current task. Validates the"
            " delegation chain (main_pm->cell_pm; cell_pm->its team's devs)"
            " and the assignee-vs-task_type rule (Cell PMs get planning-typed"
            " tasks; devs get code/research, UX devs also design)."
            " documentation is NOT delegatable — the lifecycle auto-creates"
            " the doc phase after the code subtask passes QA."
        ),
        composes=("create_subtask",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_continue_delegating,
    ),
    "open_pr": IntentSpec(
        name="open_pr",
        allowed_roles=_DEV_ROLES,
        description=(
            "Push the branch and open a PR. Atomic - preconditions"
            " (assignee, >=1 commit, no prior PR) checked BEFORE any git"
            " operation. After success, call i_am_done."
        ),
        composes=(),
        extra_preconditions=(
            PRECONDITION_OWNERSHIP,
            PRECONDITION_COMMITS,
            PRECONDITION_NO_PR,
        ),
        side_effects=("push_branch", "create_pr"),
        next_hint=_next_hint_open_pr,
    ),
    "i_am_done": IntentSpec(
        name="i_am_done",
        allowed_roles=_DEV_ROLES,
        description=(
            "Submit work for QA. Auto-runs in_progress->verifying then"
            " verifying->awaiting_qa. Strict - PR must be open (call"
            " open_pr first) and >=1 commit."
        ),
        composes=("submit_verification", "submit_qa"),
        extra_preconditions=(PRECONDITION_OWNERSHIP, PRECONDITION_COMMITS),
        side_effects=(),
        next_hint=_next_hint_idle,
    ),
    "i_am_blocked": IntentSpec(
        name="i_am_blocked",
        allowed_roles=frozenset(_DEV_ROLES | _QA_ROLES | _DOC_ROLES),
        description="Escalate to PM. Logs a struggle journal entry.",
        composes=("block",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "idle - PM will resolve and notify",
    ),
    "unclaim": IntentSpec(
        name="unclaim",
        allowed_roles=frozenset(_DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES),
        description=(
            "Voluntarily release a claim back to pending. The"
            " work-in-progress branch is preserved."
        ),
        composes=(),  # special - cleared in service layer
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: (
            "task returned to pending; another agent (or you, fresh) can claim"
        ),
    ),
    "reassign": IntentSpec(
        name="reassign",
        allowed_roles=frozenset({Role.CELL_PM}),
        description=(
            "Hand a claimed/in_progress task to another developer in your own"
            " cell. The branch is keyed to the task (not the agent), so it is"
            " preserved — the new developer continues the work-in-progress. No"
            " status change."
        ),
        composes=(),  # special — the verb body owns the assignee write
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: (
            "reassigned; the new developer will be respawned to continue"
        ),
    ),
    "resume": IntentSpec(
        name="resume",
        allowed_roles=frozenset(_DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES),
        description="Resume a paused task you own. paused -> in_progress.",
        composes=("resume",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "resumed; continue working",
    ),
    "i_am_idle": IntentSpec(
        name="i_am_idle",
        allowed_roles=frozenset(
            _DEV_ROLES
            | _QA_ROLES
            | _DOC_ROLES
            | _PM_ROLES
            | {
                Role.PRODUCT_OWNER,
                Role.HEAD_MARKETING,
                Role.AUDITOR,
                Role.PROMPTER,
                Role.SECRETARY,
                Role.PR_REVIEWER,
            }
        ),
        description=(
            "Signal you have no active work. PMs auto-pause owned in_progress tasks."
        ),
        composes=(),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_idle,
    ),
    # Phase 2: QA verbs
    "claim_review": IntentSpec(
        name="claim_review",
        allowed_roles=_QA_ROLES,
        description="Claim a task in awaiting_qa for review. Returns evidence inline.",
        composes=(),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_qa_review,
    ),
    "pass_review": IntentSpec(
        name="pass_review",
        allowed_roles=_QA_ROLES,
        description="Pass QA. Transitions awaiting_qa -> awaiting_documentation.",
        composes=("qa_pass",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_idle,
    ),
    "fail_review": IntentSpec(
        name="fail_review",
        allowed_roles=_QA_ROLES,
        description="Fail QA with concrete issues. Transitions to needs_revision.",
        composes=("qa_fail",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_dev_revise,
    ),
    # PR reviewer verbs (inbound external/fork PRs — distinct from QA's surface)
    "claim_pr_review": IntentSpec(
        name="claim_pr_review",
        allowed_roles=frozenset({Role.PR_REVIEWER}),
        description=(
            "Claim an inbound external-PR review task and start work."
            " pending -> claimed -> in_progress."
        ),
        composes=("claim", "start"),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: (
            "review the contributor's diff, then post_pr_review(task_id, ...)"
        ),
    ),
    "post_pr_review": IntentSpec(
        name="post_pr_review",
        allowed_roles=frozenset({Role.PR_REVIEWER}),
        description=(
            "Post one complete change-request to the external PR and finish the"
            " review task. in_progress -> completed."
        ),
        composes=("pr_review_done",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_idle,
    ),
    # Phase 3: documenter verbs
    "claim_doc_task": IntentSpec(
        name="claim_doc_task",
        allowed_roles=_DOC_ROLES,
        description="Claim awaiting_documentation. Returns evidence inline.",
        composes=(),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_doc_after_claim,
    ),
    "i_documented": IntentSpec(
        name="i_documented",
        allowed_roles=_DOC_ROLES,
        description="Signal docs complete. Transitions to awaiting_pm_review.",
        composes=("docs_complete",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=_next_hint_doc_done,
    ),
    # Phase 4: PM verbs
    "complete": IntentSpec(
        name="complete",
        allowed_roles=_PM_ROLES,
        description=(
            "Cell PM merges leaf PR + transitions to completed; Main PM"
            " merges root PR + escalates to CEO."
        ),
        composes=("complete",),
        extra_preconditions=(),
        side_effects=("pr_merge",),
        next_hint=_next_hint_pm_complete,
    ),
    "escalate_up": IntentSpec(
        name="escalate_up",
        allowed_roles=_PM_ROLES,
        description="Escalate to your role's escalation_target.",
        composes=(),  # special - uses TaskService.escalate
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "idle until escalation target acts",
    ),
    "escalate_to_ceo": IntentSpec(
        name="escalate_to_ceo",
        allowed_roles=frozenset(
            {
                Role.MAIN_PM,
                Role.PRODUCT_OWNER,
                Role.HEAD_MARKETING,
            }
        ),
        description=(
            "Escalate to CEO with reason. Transitions to awaiting_ceo_approval."
        ),
        composes=("escalate_to_ceo",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "idle until CEO acts via UI",
    ),
    "submit_up": IntentSpec(
        name="submit_up",
        allowed_roles=frozenset({Role.CELL_PM}),
        description=(
            "Cell PM opens the cell→root PR and moves the cell task to"
            " awaiting_pm_review. The same Cell PM then completes it."
        ),
        composes=("submit_pm_review",),
        extra_preconditions=(),
        # The cell→root PR must exist BEFORE submit_pm_review runs — its
        # pr_created gate rejects (returning None) otherwise, which then
        # crashed the trailing create_pr on a None task. create_pr persists
        # pr_number onto the task row, so submit_pm_review (which re-fetches)
        # sees pr_created=True. Mirrors the dev's open_pr→i_am_done split.
        pre_side_effects=("create_pr",),
        side_effects=(),
        # The Cell PM owns cell completion — it merges the cell→root PR
        # via complete(). Main PM only completes the ROOT task.
        next_hint=lambda _t: "complete(task_id) to merge the cell→root PR",
    ),
    "unblock": IntentSpec(
        name="unblock",
        allowed_roles=_PM_ROLES,
        description="PM unblocks a blocked task; restores pre-block state.",
        composes=("unblock",),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "task restored; original assignee will resume",
    ),
    "triage": IntentSpec(
        name="triage",
        allowed_roles=frozenset(
            _PM_ROLES | {Role.PRODUCT_OWNER, Role.HEAD_MARKETING, Role.AUDITOR}
        ),
        description="List actionable tasks in your scope.",
        composes=(),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "act on a listed task or i_am_idle",
    ),
    "triage_all": IntentSpec(
        name="triage_all",
        allowed_roles=frozenset({Role.MAIN_PM}),
        description="List actionable tasks across all teams (Main PM only).",
        composes=(),
        extra_preconditions=(),
        side_effects=(),
        next_hint=lambda _t: "act on a listed task or i_am_idle",
    ),
}


# ---------------------------------------------------------------------------
# Public lookups
# ---------------------------------------------------------------------------


def can_claim(role: Role, task: Any) -> Decision:
    """Return Decision for whether `role` can claim `task` right now.

    Thin wrapper around can_invoke_action("claim", ...) for backward
    compatibility. The actual enforcement happens in can_invoke_action
    when action == "claim", which applies CLAIM_RULES per-role narrowing.

    Rejection-kind disambiguation:
      * `not_authorized` — the status belongs to a DIFFERENT role's claim
        domain (e.g. dev tries to claim awaiting_qa, which is QA-only),
        OR the role has no claim privileges at all.
      * `invalid_state` — the role principally CAN claim, but the task is
        in a terminal/non-claimable state (e.g. completed, in_progress).
    """
    return can_invoke_action(role, "claim", task)


def _check_role_status_type(
    role: Role, action: str, spec_action: ActionSpec, task: Any
) -> Decision | None:
    """Role + source-status + task_type gate. Returns rejection or None."""
    if role not in spec_action.allowed_roles:
        return Decision.reject(
            kind="not_authorized",
            message=f"role '{role.value}' may not call '{action}'",
            remediate=(
                f"action '{action}' is restricted to:"
                f" {sorted(r.value for r in spec_action.allowed_roles)}"
            ),
        )
    status = Status(getattr(task, "status", ""))
    if status not in spec_action.source_statuses:
        return Decision.reject(
            kind="invalid_state",
            message=(
                f"task is in '{status.value}', '{action}' requires:"
                f" {sorted(s.value for s in spec_action.source_statuses)}"
            ),
            remediate=(
                f"call give_me_work() to find a task in"
                f" {sorted(s.value for s in spec_action.source_statuses)}"
            ),
        )
    if (
        spec_action.allowed_task_types is not None
        and TaskType(getattr(task, "task_type", "code"))
        not in spec_action.allowed_task_types
    ):
        return Decision.reject(
            kind="invalid_state",
            message=(
                f"task_type='{task.task_type}' invalid for '{action}'; allowed:"
                f" {sorted(t.value for t in spec_action.allowed_task_types)}"
            ),
            remediate="adjust task_type or pick a different verb",
        )
    return None


def _check_self_review_and_preconditions(
    action: str, spec_action: ActionSpec, task: Any, ctx: Context
) -> Decision | None:
    """self_review + declarative preconditions. Returns rejection or None."""
    if spec_action.self_review_block:
        original = ctx.original_developer_slug
        actor = ctx.actor_slug
        if original is not None and actor is not None and original == actor:
            return Decision.reject(
                kind="self_review",
                message=(
                    f"'{action}' blocked: you are the original developer of"
                    f" this task ({actor})"
                ),
                remediate=(
                    "another agent of this role must perform the review;"
                    " self-review is not permitted"
                ),
            )
    missing = [
        p.missing_token
        for p in spec_action.preconditions
        if not p.check(task, None, ctx)
    ]
    if missing:
        first_missing = next(
            p for p in spec_action.preconditions if p.missing_token == missing[0]
        )
        return Decision.tracing_gap(missing=missing, remediate=first_missing.remediate)
    return None


def _check_claim_rules_narrow(role: Role, task: Any) -> Decision | None:
    """Per-role narrowing for the `claim` atomic action.

    The atomic `claim` action's source_statuses is the UNION across all
    claim-eligible roles (PENDING for dev/doc, NEEDS_REVISION for dev,
    AWAITING_QA for qa, AWAITING_DOCUMENTATION for doc, etc.).
    CLAIM_RULES narrows by role. Without this narrowing
    can_invoke_action("claim", ...) would let a developer claim
    awaiting_qa just because QA can.

    not_authorized vs invalid_state disambiguation matches `can_claim`:
    if some other role can claim from this status, the rejection is
    role-mismatch (not_authorized); else it's a wrong-state issue.
    """
    status = Status(getattr(task, "status", ""))
    role_claim_statuses = CLAIM_RULES.get(role, frozenset())
    if status in role_claim_statuses:
        return None
    allowed_list = sorted(s.value for s in role_claim_statuses)
    other_role_owns_status = any(
        status in r_statuses for r, r_statuses in CLAIM_RULES.items() if r != role
    )
    if other_role_owns_status:
        return Decision.reject(
            kind="not_authorized",
            message=(
                f"role '{role.value}' may not claim from status"
                f" '{status.value}'; that status is reserved for another role"
            ),
            remediate=(f"call give_me_work() to find a task in one of: {allowed_list}"),
        )
    return Decision.reject(
        kind="invalid_state",
        message=(
            f"role '{role.value}' cannot claim from status '{status.value}'"
            f"; allowed: {allowed_list}"
        ),
        remediate=(f"call give_me_work() to find a task in one of: {allowed_list}"),
    )


def can_invoke_action(
    role: Role, action: str, task: Any, context: Context | None = None
) -> Decision:
    """Decide whether `role` can invoke atomic `action` on `task`.

    Order: action exists -> role allowed -> source status allowed ->
    task_type allowed -> self_review check -> preconditions ->
    claim rules (if action == "claim").
    """
    spec_action = _ATOMIC_ACTIONS.get(action)
    if spec_action is None:
        return Decision.reject(
            kind="invalid_state",
            message=f"unknown action '{action}'",
            remediate="action is not declared in the lifecycle spec",
        )
    rejection = _check_role_status_type(role, action, spec_action, task)
    if rejection is not None:
        return rejection
    ctx = context or Context()
    rejection = _check_self_review_and_preconditions(action, spec_action, task, ctx)
    if rejection is not None:
        return rejection
    if action == "claim":
        rejection = _check_claim_rules_narrow(role, task)
        if rejection is not None:
            return rejection
    return Decision.allow()


def _check_intent_preconditions(
    spec_intent: IntentSpec, task: Any, ctx: Context
) -> Decision | None:
    """Verb-level extra_preconditions gate. Returns rejection or None."""
    missing = [
        p.missing_token
        for p in spec_intent.extra_preconditions
        if not p.check(task, None, ctx)
    ]
    if not missing:
        return None
    first_missing = next(
        p for p in spec_intent.extra_preconditions if p.missing_token == missing[0]
    )
    return Decision.tracing_gap(missing=missing, remediate=first_missing.remediate)


def can_invoke_intent(
    role: Role, intent: str, task: Any, context: Context | None = None
) -> Decision:
    """Decide whether `role` can invoke gateway intent verb `intent` on `task`.

    Composition: intent's allowed_roles -> task in source statuses of the
    FIRST composed action (or any of the composed if `composes==()`) ->
    intent's extra_preconditions -> each composed atomic action's
    can_invoke_action check.
    """
    spec_intent = _INTENT_VERBS.get(intent)
    if spec_intent is None:
        return Decision.reject(
            kind="invalid_state",
            message=f"unknown intent verb '{intent}'",
            remediate="verb is not declared in the lifecycle spec",
        )
    if role not in spec_intent.allowed_roles:
        return Decision.reject(
            kind="not_authorized",
            message=f"role '{role.value}' may not call '{intent}'",
            remediate=(
                f"verb '{intent}' is restricted to:"
                f" {sorted(r.value for r in spec_intent.allowed_roles)}"
            ),
        )
    ctx = context or Context()
    rejection = _check_intent_preconditions(spec_intent, task, ctx)
    if rejection is not None:
        return rejection
    # Composed atomic actions: all must be invocable from current state.
    # For composition, only check the FIRST action's source-status — subsequent
    # actions transition through their target_status.
    if spec_intent.composes:
        first_action = spec_intent.composes[0]
        d = can_invoke_action(role, first_action, task, ctx)
        if not d.allowed:
            return d
    # Special handling for claim-like verbs with empty composition (claim_review,
    # claim_doc_task). These verbs don't compose "claim" action, but still need
    # to enforce claim-status rules via CLAIM_RULES narrowing.
    elif intent in ("claim_review", "claim_doc_task"):
        rejection = _check_claim_rules_narrow(role, task)
        if rejection is not None:
            return rejection
    return Decision.allow()


def valid_next_verbs(role: Role, task: Any) -> list[str]:
    """Return sorted list of verb names `role` can usefully call on `task` now.

    This is the role+state applicability list — caller-supplied
    preconditions (plan, ownership, commits, etc.) are NOT evaluated
    here. The agent-facing semantics: "these are the verbs that fit
    your role and the task's current status; missing preconditions
    surface as `tracing_gap` when you actually invoke them."
    """
    out: list[str] = []
    for name, iv in _INTENT_VERBS.items():
        if role not in iv.allowed_roles:
            continue
        if iv.composes:
            first_action = iv.composes[0]
            d = can_invoke_action(role, first_action, task)
            # Skip ONLY for state-incompatibility; tracing_gap (missing
            # action-level preconditions) is also surfaced lazily.
            if not d.allowed and d.rejection_kind in (
                "not_authorized",
                "invalid_state",
            ):
                continue
        out.append(name)
    return sorted(out)


def composed_actions_for(intent: str) -> tuple[str, ...]:
    spec_intent = _INTENT_VERBS.get(intent)
    if spec_intent is None:
        raise KeyError(f"unknown intent verb '{intent}'")
    return spec_intent.composes


def intents_for_role(role: Role) -> tuple[str, ...]:
    """Sorted tuple of intent verbs declared for `role` (regardless of state).

    Used by role_config.py to build per-role MCP manifests.
    """
    return tuple(
        sorted(name for name, iv in _INTENT_VERBS.items() if role in iv.allowed_roles)
    )


def status_after(action: str, current: Status) -> Status | None:
    """The post-`action` status, or None if `action` doesn't transition."""
    spec_action = _ATOMIC_ACTIONS.get(action)
    if spec_action is None:
        return None
    if current not in spec_action.source_statuses:
        return None
    return spec_action.target_status


# ---------------------------------------------------------------------------
# Known-debt tracking — Phase 3 invariant
# ---------------------------------------------------------------------------

UNMIGRATED: frozenset[str] = frozenset(
    {
        "enforcement.task_lifecycle._LEGACY_OPERATIONAL_EDGES",
        "enforcement.task_lifecycle._LEGACY_ROLE_GATES",
    }
)
"""Names of consumers / data still NOT migrated to the spec.

Each entry represents a real production path the spec doesn't yet cover.
Validator (`_check_unmigrated_is_subset`) asserts UNMIGRATED is a subset
of _KNOWN_UNMIGRATED_CONSUMERS — adding an entry not in the known set
fails import. Phase 3's terminal invariant is `UNMIGRATED == frozenset()`,
locked in as a permanent test once the last entry moves to the spec.
"""

_KNOWN_UNMIGRATED_CONSUMERS: frozenset[str] = frozenset(
    {
        "enforcement.task_lifecycle._LEGACY_OPERATIONAL_EDGES",
        "enforcement.task_lifecycle._LEGACY_ROLE_GATES",
    }
)


# ---------------------------------------------------------------------------
# Import-time self-consistency checks
# ---------------------------------------------------------------------------
#
# Validating the spec at module-load time means a misconfigured spec
# prevents the orchestrator container from starting — by design. The
# validators themselves live in ``roboco.foundation._validate_lifecycle``
# (a sibling of ``foundation/_validate.py`` for identity); placing them
# alongside the identity validators would create an import cycle because
# ``roboco.foundation.__init__`` eagerly imports ``foundation/_validate``.
from roboco.foundation._validate_lifecycle import (  # noqa: E402
    run_all_lifecycle_validators as _run_all_lifecycle_validators,
)

_run_all_lifecycle_validators()
