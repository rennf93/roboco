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

if TYPE_CHECKING:
    from collections.abc import Callable


class Role(StrEnum):
    DEVELOPER = "developer"
    QA = "qa"
    DOCUMENTER = "documenter"
    CELL_PM = "cell_pm"
    MAIN_PM = "main_pm"
    PRODUCT_OWNER = "product_owner"
    HEAD_MARKETING = "head_marketing"
    AUDITOR = "auditor"
    CEO = "ceo"


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
    """

    name: str
    allowed_roles: frozenset[Role]
    description: str
    composes: tuple[str, ...]
    extra_preconditions: tuple[Precondition, ...]
    side_effects: tuple[str, ...]
    next_hint: Callable[[Any], str]


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
    # the per-role-vs-status filtering is in CLAIM_RULES (Task 5)".
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
    StatusTransition(Status.PAUSED, Status.IN_PROGRESS, "resume", None),
    # Dev verify + submit
    StatusTransition(Status.IN_PROGRESS, Status.VERIFYING, "submit_verification", None),
    StatusTransition(Status.VERIFYING, Status.AWAITING_QA, "submit_qa", None),
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
    # for per-role authority. Both tables are authoritative; Task 8 validates
    # consistency between them.
    "claim": ActionSpec(
        name="claim",
        allowed_roles=frozenset(_DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES),
        source_statuses=frozenset(
            {
                Status.PENDING,
                Status.NEEDS_REVISION,
                Status.AWAITING_QA,
                Status.AWAITING_DOCUMENTATION,
                Status.BACKLOG,
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
        allowed_roles=frozenset(_DEV_ROLES | _QA_ROLES | _DOC_ROLES | _PM_ROLES),
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
        allowed_roles=frozenset(_DEV_ROLES | _PM_ROLES),
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
        source_statuses=frozenset({Status.VERIFYING, Status.IN_PROGRESS}),
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
        source_statuses=frozenset({Status.AWAITING_PM_REVIEW}),
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
    Role.CELL_PM: frozenset({Status.PENDING, Status.BACKLOG}),
    Role.MAIN_PM: frozenset({Status.PENDING, Status.BACKLOG}),
    Role.PRODUCT_OWNER: frozenset(),
    Role.HEAD_MARKETING: frozenset(),
    Role.AUDITOR: frozenset(),
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
    "ceo": None,
}
