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
