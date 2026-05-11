"""Request schemas for /api/v2/flow/* intent verbs."""

from uuid import UUID

from pydantic import BaseModel, Field


class GiveMeWorkRequest(BaseModel):
    """Empty request body — agent_id comes from header."""


class IWillWorkOnRequest(BaseModel):
    task_id: UUID
    plan: str | None = None


class OpenPrRequest(BaseModel):
    task_id: UUID


class IAmDoneRequest(BaseModel):
    task_id: UUID
    notes: str = ""


class IAmBlockedRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)


class UnclaimRequest(BaseModel):
    task_id: UUID


class ResumeRequest(BaseModel):
    task_id: UUID


class IAmIdleRequest(BaseModel):
    """Empty request body."""


class ClaimReviewRequest(BaseModel):
    task_id: UUID


class PassReviewRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)


class FailReviewRequest(BaseModel):
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
    # Optional rich-plan fields. These persist into Task.plan as a structured
    # dict matching roboco.models.task.TaskPlan, so the panel's Plan tab
    # shows Approach / Sub-Tasks / Technical Considerations / Risks /
    # Open Questions instead of an empty pane. Pre-gateway parity.
    approach: str = ""
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


class SubmitUpRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
