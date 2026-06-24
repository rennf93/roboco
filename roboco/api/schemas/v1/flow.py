"""Request schemas for /api/v1/flow/* intent verbs."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class GiveMeWorkRequest(BaseModel):
    """Empty request body — agent_id comes from header."""


class IWillWorkOnRequest(BaseModel):
    task_id: UUID
    plan: str | None = None
    # The executing developer's plan is a step checklist (same
    # SubTask shape as IWillPlanRequest.sub_tasks). It is both the
    # execution plan AND the progress checklist: completing a
    # step advances progress. Depth is enforced server-side in
    # choreographer._dev_steps_gate (a title with no real description is
    # not a step). Server assigns id + order.
    steps: list[dict[str, str]] = Field(
        default_factory=list,
        description="Ordered execution steps — list of {title, description}",
    )
    # Full parity with IWillPlanRequest so a dev leaf's Plan tab renders the
    # same rich structure PMs author. Defaults stay permissive (NOT min_length)
    # so re-entry/recovery calls that omit them still pass route validation;
    # depth + presence are enforced on FRESH dev claims by
    # choreographer._dev_plan_gate. The dev's `plan` doubles as the approach.
    technical_considerations: list[str] = Field(default_factory=list)
    risks: list[dict[str, str]] = Field(default_factory=list)
    open_questions: list[dict[str, str | bool]] = Field(default_factory=list)


class OpenPrRequest(BaseModel):
    task_id: UUID


class IAmDoneRequest(BaseModel):
    task_id: UUID
    notes: str = ""


class IAmBlockedRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)
    # Pre-gateway parity (G8 part b). The old TaskBlockInput at
    # 0c3d15a:roboco/mcp/schemas/__init__.py required blocker_type and
    # what_needed so PMs could triage by class. Optional here for
    # back-compat with i_am_blocked(reason) callers; supplied fields are
    # rendered into the struggle journal entry so the panel surfaces them.
    blocker_type: str | None = Field(
        default=None,
        description=(
            "external | internal | question | dependency. Required from "
            "newly-spawned agents (per the developer.md verb table); "
            "older agents that don't supply it default to 'internal'."
        ),
    )
    what_needed: str | None = Field(
        default=None,
        description="Concrete description of what would unblock the task.",
    )

    @field_validator("blocker_type", mode="before")
    @classmethod
    def _blocker_type_enum(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, str) and v.lower() not in {
            "external",
            "internal",
            "question",
            "dependency",
        }:
            raise ValueError(
                f"blocker_type must be one of: external | internal | "
                f"question | dependency. Got {v!r}."
            )
        return v


class UnclaimRequest(BaseModel):
    task_id: UUID


class ReassignRequest(BaseModel):
    """HTTP body for the cell_pm `reassign` verb.

    ``new_assignee`` is a developer slug in the caller's own cell (e.g.
    ``be-dev-2``). The choreographer resolves and validates it.
    """

    task_id: UUID
    new_assignee: str = Field(..., min_length=1)


class ResumeRequest(BaseModel):
    task_id: UUID


class IAmIdleRequest(BaseModel):
    """Empty request body."""


class ClaimReviewRequest(BaseModel):
    task_id: UUID


class PassReviewRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
    ac_verdicts: list[str] | None = Field(
        default=None,
        description=(
            "One verification entry per acceptance criterion (in criterion "
            "order) stating how QA verified it. Every criterion must be "
            "covered before a pass is allowed."
        ),
    )


class FailReviewRequest(BaseModel):
    task_id: UUID
    issues: list[str] = Field(..., min_length=1)


class ClaimPrReviewRequest(BaseModel):
    task_id: UUID


class PostPrReviewRequest(BaseModel):
    task_id: UUID
    body: str = Field(..., min_length=1)
    event: str = "REQUEST_CHANGES"
    findings: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured per-criterion findings — each {file, line?, severity "
            "(blocker|major|minor|nit), expected, actual}. When provided, the "
            "GitHub comment is generated from them in the RoboCo format."
        ),
    )


class ClaimGateReviewRequest(BaseModel):
    task_id: UUID


class PrPassRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)


class PrFailRequest(BaseModel):
    task_id: UUID
    issues: list[str] = Field(..., min_length=1)


class ClaimDocTaskRequest(BaseModel):
    task_id: UUID


class IDocumentedRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
    files: list[str] = Field(..., min_length=1)


class TriageRequest(BaseModel):
    """Empty request body."""


class UnblockRequest(BaseModel):
    task_id: UUID
    reason: str = Field(
        ...,
        min_length=10,
        description=(
            "Why the block is cleared — recorded as the PM's journal:decision "
            "so no separate note(scope='decision') call is needed."
        ),
    )
    restore: bool = True


class CompleteRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)


class EscalateUpRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)


class EscalateToCeoRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)


class IWillPlanRequest(BaseModel):
    task_id: UUID
    plan: str = Field(..., min_length=1)
    # Pre-gateway parity: Approach is REQUIRED — agents
    # could not transition claimed → in_progress without filling this in the
    # pre-gateway flow. The Plan tab depends on it; smoke run 3 confirmed
    # the empty default lets agents through with thin plans.
    # min_length must match choreographer._impl._PM_APPROACH_MIN_LEN. Raised
    # 20→150: a 20-char approach was a one-liner; the approach +
    # sub_tasks are also the progress checklist, so they must be substantive.
    approach: str = Field(..., min_length=150)
    sub_tasks: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {title, description} — server assigns id + order",
    )
    technical_considerations: list[str] = Field(default_factory=list)
    risks: list[dict[str, str]] = Field(default_factory=list)
    open_questions: list[dict[str, str | bool]] = Field(default_factory=list)


class DelegateRequest(BaseModel):
    """HTTP body for cell_pm + main_pm `delegate` verbs.

    Mirrors :data:`roboco.foundation.policy.task_completeness.TASK_AT_CREATE`
    so under-filled payloads fail at the request boundary with a 422 — no
    silent defaults, no "code"/"medium" fallbacks. Each constraint matches
    the hint string returned by the foundation policy.
    """

    parent_task_id: UUID
    title: str = Field(..., min_length=1, max_length=200)
    # 20-char minimum mirrors TASK_AT_CREATE.description (MIN_LENGTH=20).
    # Forces a real one-line summary instead of "x" or "see title".
    description: str = Field(..., min_length=20)
    assigned_to: str = Field(..., min_length=1)
    team: str = Field(..., min_length=1)
    # task_type, nature, estimated_complexity are EXPLICITLY_DECLARED in
    # TASK_AT_CREATE. The 2026-05-08 trace showed agents omitting task_type
    # and the old default of 'code' deadlocking the lifecycle; the same
    # silent-default trap exists for nature ("technical") and complexity
    # ("medium"). Force callers to declare intent.
    task_type: str = Field(..., min_length=1)
    nature: str = Field(..., min_length=1)
    estimated_complexity: str = Field(..., min_length=1)
    # acceptance_criteria is required and non-empty; downstream policy
    # also denylist-checks each item against placeholder phrases.
    acceptance_criteria: list[str] = Field(..., min_length=1)
    # Optional per-subtask project override. When omitted, the choreographer
    # resolves the project from the parent's Product map for this cell, then
    # falls back to the parent's project. Plain optional field — no validator.
    project_id: UUID | None = None
    # Parent acceptance-criterion ids this subtask is responsible for. Lets the
    # coverage + roll-up AC gates verify every parent AC is claimed and satisfied.
    covers_parent_criteria: list[str] | None = None

    # Pre-gateway parity: cross-field validators that catch the most common
    # LLM-vs-schema confusions. Pre-gateway lived in
    # roboco/mcp/schemas/__init__.py::TaskCreateInput at 0c3d15a.
    @field_validator("estimated_complexity", mode="before")
    @classmethod
    def _complexity_must_be_string(cls, v: object) -> object:
        """Reject ints — agents sometimes send 1/2/3 thinking it's priority."""
        if isinstance(v, int) and not isinstance(v, bool):
            raise ValueError(
                f"estimated_complexity must be a string "
                f"(low|medium|high|critical), got int {v!r}. "
                f"Priority is not a delegate parameter — drop it."
            )
        if isinstance(v, str) and v.lower() not in {
            "low",
            "medium",
            "high",
            "critical",
        }:
            raise ValueError(
                f"estimated_complexity must be one of: low, medium, high, "
                f"critical. Got {v!r}."
            )
        return v

    @field_validator("nature", mode="before")
    @classmethod
    def _nature_must_be_known(cls, v: object) -> object:
        """Reject invented nature values (e.g., 'standard') with the enum hint."""
        if isinstance(v, str) and v.lower() not in {"technical", "non_technical"}:
            raise ValueError(
                f"nature must be one of: technical | non_technical. Got {v!r}. "
                f"This was the 2026-05-11 'standard' regression — drop the "
                f"invented value and use the enum."
            )
        return v

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type_must_be_known(cls, v: object) -> object:
        """Reject invented task_type values with the enum hint."""
        if isinstance(v, str) and v.lower() not in {
            "code",
            "documentation",
            "research",
            "planning",
            "design",
            "administrative",
        }:
            raise ValueError(
                f"task_type must be one of: code | documentation | research | "
                f"planning | design | administrative. Got {v!r}."
            )
        return v


class SubmitUpRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)


class SubmitRootRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
