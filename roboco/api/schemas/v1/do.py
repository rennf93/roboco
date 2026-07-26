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
    # handoff scope: the agent's dedicated SECTION fields (dev_notes /
    # quick_context / auditor_notes …), free-form per content type — e.g.
    # {"summary": "...", "changes": [...]} (developer), {"done": "...",
    # "next": "..."} (PM/resumption), {"summary": "...", "severity": "risk"}
    # (auditor). Validated by the content model server-side. Omit it to write a
    # developer summary straight from ``text``.
    section: dict[str, Any] | None = None
    # handoff scope — the PM/coordinator RESUMPTION fields, promoted to
    # top-level typed strings so the tool schema declares them machine-visible.
    # ``section: dict[str, Any]`` renders a schema with no visible sub-fields,
    # so a weak model (minimax-m3) emits ``section={}`` and the resumption gate
    # rejects ``done — Field required`` — the 2026-06-27 PM respawn-loop
    # meltdown. The same model fills top-level ``string`` decision fields fine
    # (proven live), so ``done``/``next`` follow that precedent (typed
    # ``str = ""`` → schema declares ``string`` not ``anyOf[string, null]``).
    # Filled into ``section`` server-side; ignored for non-handoff scopes.
    done: str = ""
    next: str = ""
    where_to_look: list[str] | None = None

    # List-typed fields tolerate a lone scalar: a single string (or, for
    # ``options``, a single dict) is wrapped into a one-element list before
    # field validation. Without this a well-intentioned ``consequences="x"``
    # 422'd at the route and the agent's retry loop tripped the circuit
    # breaker. ``mode="before"`` runs ahead of type coercion so
    # the wrapped value satisfies the declared ``list[...]`` type.
    @field_validator(
        "options", "consequences", "next_steps", "where_to_look", mode="before"
    )
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


class RoadmapItemInput(BaseModel):
    """One roadmap item draft within a Product Owner's themed cycle."""

    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    project_slug: str = Field(..., min_length=1)
    team: str = Field(..., min_length=1)
    priority: int = 2
    rationale: str = Field(..., min_length=1)


class ProposeRoadmapRequest(BaseModel):
    """Product Owner's themed roadmap cycle: a goal + 3-7 item drafts."""

    cycle_goal: str = Field(..., min_length=1)
    items: list[RoadmapItemInput] = Field(..., min_length=1)


class PestHuntItemInput(BaseModel):
    """One evidence-backed bug item draft within a Product Owner's Pest
    Control hunt."""

    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    project_slug: str = Field(..., min_length=1)
    team: str = Field(..., min_length=1)
    priority: int = 2
    evidence: str = Field(..., min_length=1)


class ProposeBugHuntRequest(BaseModel):
    """Product Owner's Pest Control bug hunt: 1-N evidence-backed item
    drafts (no top-level theme, unlike the roadmap cycle)."""

    items: list[PestHuntItemInput] = Field(..., min_length=1)


class GapFillItemInput(BaseModel):
    """One evidence-backed gap-fill item draft within a Product Owner's
    Spackle audit. Mirrors ``PestHuntItemInput`` — ``evidence`` must name
    both sides of the gap (e.g. the route that exists and the panel surface
    that doesn't)."""

    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    project_slug: str = Field(..., min_length=1)
    team: str = Field(..., min_length=1)
    priority: int = 2
    evidence: str = Field(..., min_length=1)


class ProposeGapFillRequest(BaseModel):
    """Product Owner's Spackle gap-fill audit: 1-N evidence-backed item
    drafts (no top-level theme, unlike the roadmap cycle)."""

    items: list[GapFillItemInput] = Field(..., min_length=1)


class RebalanceItemInput(BaseModel):
    """One re-priority/cancellation item within a Product Owner's Scales
    portfolio-rebalance plan. Unlike a roadmap/pest-control item draft, this
    references a LIVE task (``task_ref``) rather than describing a new one."""

    task_ref: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    new_priority: int | None = None
    rationale: str = Field(..., min_length=1)


class ProposeRebalanceRequest(BaseModel):
    """Product Owner's Scales portfolio-rebalance plan: 1-N re-priority/
    cancellation item drafts against the live backlog (no top-level theme,
    mirroring the pest-control bug hunt's shape)."""

    items: list[RebalanceItemInput] = Field(..., min_length=1)


class MessagingFixItemInput(BaseModel):
    """One evidence-backed messaging-fix item draft within a Head of
    Marketing's Mirror audit. Mirrors ``GapFillItemInput`` — ``evidence``
    must name the drifted claim and the reality it contradicts."""

    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    project_slug: str = Field(..., min_length=1)
    team: str = Field(..., min_length=1)
    priority: int = 2
    evidence: str = Field(..., min_length=1)


class ProposeMessagingFixesRequest(BaseModel):
    """Head of Marketing's Mirror positioning audit: 1-N evidence-backed
    item drafts (no top-level theme, unlike the roadmap cycle)."""

    items: list[MessagingFixItemInput] = Field(..., min_length=1)


class FrictionFixItemInput(BaseModel):
    """One evidence-backed UX-friction item draft within a Product Owner's
    Dogfood walk. Mirrors ``MessagingFixItemInput`` — ``evidence`` must name
    the walked path (the actual clicks/pages) and what broke or felt wrong,
    prose only, no screenshots."""

    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    project_slug: str = Field(..., min_length=1)
    team: str = Field(..., min_length=1)
    priority: int = 2
    evidence: str = Field(..., min_length=1)


class ProposeFrictionFixesRequest(BaseModel):
    """Product Owner's Dogfood friction audit: 1-N evidence-backed item
    drafts (no top-level theme, unlike the roadmap cycle)."""

    items: list[FrictionFixItemInput] = Field(..., min_length=1)


class ProposeFeatureSpotlightRequest(BaseModel):
    """Head of Marketing's feature-spotlight draft: a picked feature + a
    ready-to-post body, plus an optional companion-video request — or a
    ``skip`` verdict when nothing is worth spotlighting this cycle (the
    feature/title/body fields are then ignored; the handler enforces the
    real "required unless skipping" rule since a plain Field(..., min_length)
    can't express that conditional)."""

    feature_slug: str = Field(default="", max_length=128)
    feature_title: str = ""
    body: str = ""
    wants_video: bool = False
    video_script: str = ""
    skip: bool = False
    skip_reason: str = ""


class ProposeEditorialPostRequest(BaseModel):
    """Head of Marketing's Megaphone editorial-calendar post: an angle + a
    ready-to-post body + a rationale. Materializes into the same X held-draft
    queue ``propose_feature_spotlight`` uses — no separate approval surface."""

    angle: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)


class MarketBriefFindingInput(BaseModel):
    """One cited market finding within a Head of Marketing's Periscope brief.
    ``source_url`` is REQUIRED — an uncited market claim is noise."""

    claim: str = Field(..., min_length=1)
    source_url: str = Field(..., min_length=1)
    relevance: str = Field(..., min_length=1)


class ProposeMarketBriefRequest(BaseModel):
    """Head of Marketing's Periscope weekly market-research brief: a
    headline + 1-7 cited findings, plus optional threats/opportunities/
    positioning notes. Delivered as a held report — no per-item CEO queue."""

    headline: str = Field(..., min_length=1)
    findings: list[MarketBriefFindingInput] = Field(..., min_length=1)
    threats: list[str] | None = None
    opportunities: list[str] | None = None
    positioning_note: str = ""


class ConversationReplyItemInput(BaseModel):
    """One drafted reply within a Head of Marketing's Barfly conversation-
    reply cycle. ``tweet_id`` must name one of the exploration task's real
    screened candidates — enforced server-side, not by this schema."""

    tweet_id: str = Field(..., min_length=1)
    reply_body: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)


class ProposeConversationRepliesRequest(BaseModel):
    """Head of Marketing's Barfly conversation replies: 1-N drafted replies
    to screened candidate X conversations (no top-level theme, mirrors the
    pest-control/gap-fill/rebalance item-only shape)."""

    items: list[ConversationReplyItemInput] = Field(..., min_length=1)


class ProcessChangeInput(BaseModel):
    """The ONE process change a Coroner postmortem proposes."""

    kind: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)


class PostmortemPlaybookInput(BaseModel):
    """Required iff ``process_change.kind == 'playbook'``."""

    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class ProposePostmortemRequest(BaseModel):
    """Auditor's Coroner postmortem: incident summary + root cause + ONE
    process change, drafted after autopsying an incident task the Coroner
    engine's event hooks opened an autopsy for."""

    incident_summary: str = Field(..., min_length=1)
    root_cause: str = Field(..., min_length=1)
    failed_stage: str = Field(..., min_length=1)
    process_change: ProcessChangeInput
    playbook: PostmortemPlaybookInput | None = None


class QualityReportItemInput(BaseModel):
    """One drift item within an Auditor's Sentinel quality report."""

    area: str = Field(..., min_length=1)
    observation: str = Field(..., min_length=1)
    evidence: str = Field(..., min_length=1)
    suggested_action: str = Field(..., min_length=1)


class ProposeQualityReportRequest(BaseModel):
    """Auditor's Sentinel weekly "state of quality" report: a headline +
    1-7 evidence-backed drift items + an overall assessment. Delivered as a
    held report — no per-item CEO queue."""

    headline: str = Field(..., min_length=1)
    items: list[QualityReportItemInput] = Field(..., min_length=1)
    overall_assessment: str = Field(..., min_length=1)


class PlaybookDraftInput(BaseModel):
    """One playbook draft within an Auditor's Librarian mining cycle.
    ``pattern_evidence`` is REQUIRED — a draft with no cited repeated
    pattern is noise."""

    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    pattern_evidence: str = Field(..., min_length=1)


class ProposePlaybookDraftsRequest(BaseModel):
    """Auditor's Librarian playbook-mining cycle: 1-3 playbook drafts, each
    created directly as a DRAFT playbook riding the normal pending-playbook
    curation queue. Delivered as a held mining report — no per-item CEO
    queue (the drafts themselves ARE the queue item, via playbook curation)."""

    drafts: list[PlaybookDraftInput] = Field(..., min_length=1)


class CampaignPostInput(BaseModel):
    """One ordered post within a Head of Marketing's War Room campaign.
    ``publish_after`` is GUIDANCE (V1 manual-cadence) rendered in the panel
    queue — the CEO approves each draft at its own moment; nothing here
    schedules or auto-posts."""

    body: str = Field(..., min_length=1)
    publish_after: str = Field(..., min_length=1)
    stage_label: str = Field(..., min_length=1)


class ProposeCampaignRequest(BaseModel):
    """Head of Marketing's War Room campaign: a name + 2-6 ordered posts
    (teaser -> launch -> follow-up -> spotlight), each with a recommended
    publish_after timestamp. Materializes N held X drafts and completes the
    exploration task in one call — no per-item CEO queue beyond the normal
    X post queue each draft already lands in."""

    campaign_name: str = Field(..., min_length=1)
    posts: list[CampaignPostInput] = Field(..., min_length=1)


class ProposeVideoRequest(BaseModel):
    """UX/UI dev's video metadata draft: a composition ref + per-platform
    captions. Metadata only — no render."""

    composition_id: str = Field(..., min_length=1)
    x_caption: str = Field(..., min_length=1)
    tiktok_caption: str = Field(..., min_length=1)
    platforms: list[str] = Field(..., min_length=1)
    input_props: dict[str, Any] | None = None


class NothingToProposeRequest(BaseModel):
    """Any board explorer's "this cycle found nothing worth proposing" exit
    — a substantive reason explaining what was looked at and why none of it
    qualified. ``task_id`` names the caller's own exploration task explicitly
    (one board role can own several open cycles from different programs at
    once) — which program/role it resolves against comes from THAT task."""

    task_id: UUID
    reason: str = Field(..., min_length=1)


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


class RequestSandboxRequest(BaseModel):
    """On-demand sandbox DB/Redis/Mongo. Omitted `services` = the project's
    whole opted-in set. ``extensions`` (per-service extensions/modules) is an
    additive per-call override, allowlist-validated."""

    services: list[str] | None = None
    extensions: dict[str, list[str]] | None = None


class RequestRenderRequest(BaseModel):
    """Render a video composition to preview frames for artifact
    verification. Omitted `composition_id` falls back to the task's
    `video_draft` marker."""

    composition_id: str | None = None
    orientation: str = "vertical"
    frame_count: int = 8
    input_props: dict[str, Any] | None = None


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


class DraftPlaybookRequest(BaseModel):
    """Draft a curated playbook (when-to-use + procedure) for the KB."""

    title: str = Field(..., min_length=1)
    problem: str = Field(..., min_length=1)
    procedure: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    source_task_id: UUID | None = None


class ApprovePlaybookRequest(BaseModel):
    """Auditor approves a draft playbook (-> approved + indexed)."""

    playbook_id: UUID


class RejectPlaybookRequest(BaseModel):
    """Auditor rejects a playbook (-> archived) with a reason."""

    playbook_id: UUID
    reason: str = Field(..., min_length=1)


class ArchivePlaybookRequest(BaseModel):
    """Auditor archives a playbook (-> archived)."""

    playbook_id: UUID


class CurateVaultRequest(BaseModel):
    """Auditor writes a root task-tree's vault narrative section."""

    task_id: UUID
    narrative: str = Field(..., min_length=1)
