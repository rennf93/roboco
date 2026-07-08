"""Request schemas for /api/v1/flow/* intent verbs."""

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from roboco.foundation.policy.content.validators import coerce_str_list
from roboco.models.base import Complexity

# A ``list[str]`` field that tolerates the Claude SDK's XML-ish tool-input
# parsing: an LLM emitting a bullet list as ``<item>…</item>`` elements arrives
# nested (``[[["…"]]]`` / ``[{"item": {"$text": "…"}}, …]``), which a bare
# ``list[str]`` hard-rejects at validation time (the live ``i_will_plan`` crash:
# ``technical_considerations.1 Input should be a valid string``). The
# ``BeforeValidator`` flattens it to a flat ``list[str]`` first — same
# ``coerce_str_list`` used at the intake→DB boundary (Bug 3, MegaTask memory).
# Applied to EVERY LLM-authored list-of-strings on the flow surface so the SDK
# can't crash any of them (acceptance_criteria, issues, files, ac_verdicts,
# technical_considerations, covers_parent_criteria).
StrList = Annotated[list[str], BeforeValidator(coerce_str_list)]

# AC discipline (the 2026-07-07 task-quality defect: restated, over-long
# acceptance criteria). Mirrors roboco.foundation.policy.task_completeness
# so an over-long / over-count AC list is rejected at the request boundary.
_AC_MAX_ITEMS = 7
_AC_MAX_ITEM_CHARS = 200


class SubTaskCreate(BaseModel):
    """A PM sub_task — a delegate target AND a progress-checklist item.

    Mirrors DelegateRequest title/description caps so an over-long sub_task
    can't bloat the plan (the 2026-07-07 task-quality defect). The server
    assigns id + order; callers supply title + description only.
    """

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=20, max_length=600)


class RiskCreate(BaseModel):
    """A {risk, mitigation} entry — what could go wrong and how it's handled."""

    risk: str = Field(..., min_length=1, max_length=300)
    mitigation: str = Field(..., min_length=1, max_length=600)


class OpenQuestionCreate(BaseModel):
    """An open question the PM wants answered before/during work."""

    question: str = Field(..., min_length=1, max_length=300)
    answered: bool = False


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
    technical_considerations: StrList = Field(default_factory=list)
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


class SyncBranchRequest(BaseModel):
    """HTTP body for the dev `sync_branch` verb.

    Rebases the task's branch onto its resolved base (parent branch) through the
    gate, so a developer whose branch has fallen behind can re-sync without raw
    git (which is denied to agents). Git-only — no DB state transition.
    """

    task_id: UUID


class IAmIdleRequest(BaseModel):
    """Empty request body."""


class ClaimReviewRequest(BaseModel):
    task_id: UUID


class PassReviewRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
    ac_verdicts: StrList | None = Field(
        default=None,
        description=(
            "One verification entry per acceptance criterion (in criterion "
            "order) stating how QA verified it. Every criterion must be "
            "covered before a pass is allowed."
        ),
    )


class FailReviewRequest(BaseModel):
    task_id: UUID
    issues: StrList = Field(..., min_length=1)


class ClaimPrReviewRequest(BaseModel):
    task_id: UUID


class PostPrReviewRequest(BaseModel):
    task_id: UUID
    body: str = Field(..., min_length=1)
    event: str = Field(
        default="REQUEST_CHANGES",
        description=(
            "APPROVE, REQUEST_CHANGES, or COMMENT. The verdict must match the "
            "findings: REQUEST_CHANGES needs >=1 finding; APPROVE may not carry a "
            "blocker/major finding. Pass APPROVE explicitly to approve a clean PR."
        ),
    )
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
    issues: StrList = Field(..., min_length=1)


class ClaimDocTaskRequest(BaseModel):
    task_id: UUID


class IDocumentedRequest(BaseModel):
    task_id: UUID
    notes: str = Field(..., min_length=1)
    files: StrList = Field(..., min_length=1)


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


class RequestChangesRequest(BaseModel):
    task_id: UUID
    issues: StrList = Field(..., min_length=1)


class EscalateUpRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)


class EscalateToCeoRequest(BaseModel):
    task_id: UUID
    reason: str = Field(..., min_length=1)


class IWillPlanRequest(BaseModel):
    task_id: UUID
    plan: str = Field(..., min_length=1, max_length=2000)
    # Pre-gateway parity: Approach is REQUIRED — agents
    # could not transition claimed → in_progress without filling this in the
    # pre-gateway flow. The Plan tab depends on it; smoke run 3 confirmed
    # the empty default lets agents through with thin plans.
    # min_length must match choreographer._impl._PM_APPROACH_MIN_LEN. Raised
    # 20→150: a 20-char approach was a one-liner; the approach +
    # sub_tasks are also the progress checklist, so they must be substantive.
    # max_length caps the bloat defect (approach that ran to thousands of
    # chars restating the description). Must match the gate ceiling.
    approach: str = Field(..., min_length=150, max_length=800)
    sub_tasks: list[SubTaskCreate] = Field(
        default_factory=list,
        description="List of {title, description} — server assigns id + order",
    )
    technical_considerations: StrList = Field(default_factory=list)
    risks: list[RiskCreate] = Field(default_factory=list)
    open_questions: list[OpenQuestionCreate] = Field(default_factory=list)


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
    estimated_complexity: Complexity
    # acceptance_criteria is required and non-empty; downstream policy
    # also denylist-checks each item against placeholder phrases. The
    # per-item cap (<=200 chars) + list cap (<=7) mirror the policy so an
    # over-long / over-count AC list is rejected at the boundary (422), not
    # only by the service-layer completeness check.
    acceptance_criteria: StrList = Field(..., min_length=1, max_length=7)

    @field_validator("acceptance_criteria")
    @classmethod
    def _ac_items_bounded(cls, v: list[str]) -> list[str]:
        for i, item in enumerate(v):
            if len(item.strip()) > _AC_MAX_ITEM_CHARS:
                raise ValueError(
                    f"acceptance_criteria[{i}] is {len(item.strip())} chars "
                    f"(max {_AC_MAX_ITEM_CHARS}) — a criterion that long is a "
                    "restated description, not a verifiable outcome. Split it."
                )
        return v

    # Optional per-subtask project override. When omitted, the choreographer
    # resolves the project from the parent's Product map for this cell, then
    # falls back to the parent's project. Plain optional field — no validator.
    project_id: UUID | None = None
    # Parent acceptance-criterion ids this subtask is responsible for. Lets the
    # coverage + roll-up AC gates verify every parent AC is claimed and satisfied.
    covers_parent_criteria: StrList | None = None
    # Dev-task collision surface (the multi-level sequencing model — edge kind
    # 3). The cell PM states what each dev task touches so the choreographer can
    # run SequencingService and wire the dev-task collision DAG (file-overlap
    # serializes, migration-adders chain, shared-surface edits run last).
    # Optional: a delegate without surfaces joins no collision edges (parallel).
    intends_to_touch: StrList | None = None
    adds_migration: bool = False
    touches_shared: bool = False
    # Explicit dependency override (edge the surface rules would miss, e.g. a
    # non-collision ordering the PM knows). Optional; wired verbatim as
    # dependency_ids on the created dev task.
    depends_on: list[UUID] | None = None

    # Pre-gateway parity: cross-field validators that catch the most common
    # LLM-vs-schema confusions. Pre-gateway lived in
    # roboco/mcp/schemas/__init__.py::TaskCreateInput at 0c3d15a.
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
