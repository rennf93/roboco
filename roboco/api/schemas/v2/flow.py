"""Request schemas for /api/v2/flow/* intent verbs."""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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
            "low", "medium", "high", "critical",
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
            "code", "documentation", "research",
            "planning", "design", "administrative",
        }:
            raise ValueError(
                f"task_type must be one of: code | documentation | research | "
                f"planning | design | administrative. Got {v!r}."
            )
        return v


class SubmitUpRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
