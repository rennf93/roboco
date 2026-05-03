"""Request schemas for /api/v2/flow/* intent verbs."""

from uuid import UUID

from pydantic import BaseModel, Field


class GiveMeWorkRequest(BaseModel):
    """Empty request body — agent_id comes from header."""


class IWillWorkOnRequest(BaseModel):
    task_id: UUID
    plan: str | None = None


class IHaveCommittedRequest(BaseModel):
    message: str = Field(..., min_length=1)


class SubmitForQaRequest(BaseModel):
    task_id: UUID


class IAmDoneRequest(BaseModel):
    task_id: UUID
    notes: str = ""


class IAmBlockedRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)


class UnclaimRequest(BaseModel):
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


class DelegateRequest(BaseModel):
    parent_task_id: UUID
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    assigned_to: str = Field(..., min_length=1)
    team: str = Field(..., min_length=1)
    task_type: str = "code"
    acceptance_criteria: list[str] | None = None
    estimated_complexity: str = "medium"


class SubmitUpRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
