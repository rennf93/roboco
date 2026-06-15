"""Request schemas for /api/v1/do/* content tools."""

from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


def _coerce_to_list(value: Any) -> Any:
    """Wrap a lone scalar into a one-element list; pass lists/None through.

    Agents routinely pass a single string (or a single dict for ``options``)
    where the schema declares a list — ``consequences="ships v2"`` instead of
    ``consequences=["ships v2"]``. Pre-coercion that 422'd at the route before
    the agent ever saw a remediable envelope, and the retry loop tripped the
    do-server circuit breaker. A bare scalar is the well-intentioned
    single-item case, so wrap it rather than reject it. Lists, None, and other
    shapes pass through untouched for normal field validation to handle.
    """
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str | dict):
        return [value]
    return value


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1)
    files: list[str] | None = None


class NoteRequest(BaseModel):
    """Journal entry. ``text`` is always the short summary line.

    Scope-specific fields are optional but pre-gateway parity expected
    them filled for `decision` and `reflect`:

    - decision: ``context``, ``options``, ``chosen``, ``rationale``,
      ``consequences``
    - reflect: ``what_done``, ``what_learned``, ``what_struggled``,
      ``next_steps``

    When provided, these are formatted into the journal entry's content
    as structured markdown — the panel UI's decision/reflect views show
    them as named sections instead of a one-line phrase.
    """

    text: str = Field(..., min_length=1)
    scope: str = "note"
    task_id: UUID | None = None
    title: str | None = None
    # decision scope (all required at gateway when scope='decision').
    # Typed as non-nullable str (default "") so the MCP tool schema declares
    # the field as `string` not `anyOf[string, null]` — dogfooding showed
    # minimax-m3 passing literal `null` for these and the server-side
    # gate looping forever on `incomplete_input`. Empty string still counts
    # as missing at the gate.
    context: str = ""
    options: list[dict[str, str]] | None = None  # [{name, pros, cons}, ...]
    chosen: str = ""
    rationale: str = ""
    consequences: list[str] | None = None
    # reflect scope (what_done/learned/struggled required when scope='reflect')
    what_done: str = ""
    what_learned: str = ""
    what_struggled: str = ""
    next_steps: list[str] | None = None

    # List-typed fields tolerate a lone scalar: a single string (or, for
    # ``options``, a single dict) is wrapped into a one-element list before
    # field validation. Without this a well-intentioned ``consequences="x"``
    # 422'd at the route and the agent's retry loop tripped the circuit
    # breaker. ``mode="before"`` runs ahead of type coercion so
    # the wrapped value satisfies the declared ``list[...]`` type.
    @field_validator("options", "consequences", "next_steps", mode="before")
    @classmethod
    def _wrap_scalar_in_list(cls, value: Any) -> Any:
        return _coerce_to_list(value)


class PitchRequest(BaseModel):
    """Board pitch — a product proposal queued for CEO approval."""

    title: str = Field(..., min_length=1)
    slug: str = Field(..., min_length=1)
    problem: str = Field(..., min_length=1)
    proposed_solution: str = Field(..., min_length=1)
    target_cells: list[str] = Field(..., min_length=1)


class SayRequest(BaseModel):
    channel: str
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None


class DmRequest(BaseModel):
    recipient: str  # agent slug
    text: str = Field(..., min_length=1)
    task_id: UUID | None = None
    skill: str | None = None


class NotifyRequest(BaseModel):
    target: str  # agent slug
    text: str = Field(..., min_length=1)
    priority: str = "normal"  # normal | high | urgent
    task_id: UUID | None = None


class EvidenceRequest(BaseModel):
    task_id: UUID


# =============================================================================
# Wave 1 — Pre-gateway parity restoration
# =============================================================================


class OpenSessionRequest(BaseModel):
    """PM creates a discussion session for one or more tasks.

    Backs the `open_session` do-verb. Populates the
    panel's Sessions tab.
    """

    task_id: UUID
    channel: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1, max_length=200)
    relationship_type: str = "discussion"  # discussion|planning|review|retrospective
    group_id: UUID | None = None


class LinkSessionRequest(BaseModel):
    """Link an existing session to a task. Idempotent."""

    session_id: UUID
    task_id: UUID
    is_primary: bool = False
    relationship_type: str = "discussion"


class ProgressRequest(BaseModel):
    """Progress update; % is DERIVED from the plan checklist.

    Pass ``plan_step`` (a sub_task id or its 1-based order) as you finish
    each plan step — it is marked complete and the percentage is computed
    from completed/total. A narrative entry without ``plan_step`` is
    allowed for important mid-step documentation. ``percentage`` is an
    optional fallback only for tasks with no sub_task checklist.
    Populates the panel's Progress tab.
    """

    task_id: UUID
    message: str = Field(..., min_length=1)
    plan_step: str | None = Field(
        default=None,
        description="sub_task id or 1-based order to mark complete",
    )
    percentage: int | None = Field(default=None, ge=0, le=100)


class NotifyListRequest(BaseModel):
    """Read this agent's notification inbox."""

    unread_only: bool = True
    pending_ack_only: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class NotifyGetRequest(BaseModel):
    notification_id: UUID


class NotifyAckRequest(BaseModel):
    notification_id: UUID


class ChannelsRequest(BaseModel):
    """No params — caller's identity comes from X-Agent-ID header."""


class ReadMessagesRequest(BaseModel):
    """No params — clears the caller's unread A2A inbox (X-Agent-ID header)."""


class PRUpdateRequest(BaseModel):
    """Update an open PR's title/body and/or request reviewers.

    Dogfooding surfaced the gap: agents who needed to fix the PR title or
    request a reviewer after `open_pr` had no verb for it and got blocked
    by the bash-guard on `gh pr edit`. This is the gateway-native fix.

    At least one of `title`, `body`, or `reviewers` must be provided —
    enforced via a `model_validator` so the route returns 422 before the
    request ever reaches `ContentActions`.

    `reviewers` is a list of agent slugs (e.g. `["be-dev-2", "be-qa"]`);
    the gateway maps to GitHub usernames where the project records that
    mapping, otherwise the slugs go through as-is.
    """

    task_id: UUID
    title: str | None = None
    body: str | None = None
    reviewers: list[str] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if self.title is None and self.body is None and self.reviewers is None:
            raise ValueError(
                "at least one of title, body, or reviewers must be provided"
            )
        return self
