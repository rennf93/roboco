"""Content-tool HTTP endpoints. Thin handlers; delegate to ContentActions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from guard_core.handlers.behavior_handler import BehaviorRule

from roboco.api.deps import get_content_actions
from roboco.api.routes.v1._role_dep import (
    envelope_to_response,
    require_any_authenticated_agent,
)
from roboco.api.schemas.v1.do import (
    ApprovePlaybookRequest,
    ArchivePlaybookRequest,
    CommitRequest,
    CurateVaultRequest,
    DmRequest,
    DraftPlaybookRequest,
    EvidenceRequest,
    NoteRequest,
    NothingToProposeRequest,
    NotifyAckRequest,
    NotifyGetRequest,
    NotifyListRequest,
    NotifyRequest,
    PitchRequest,
    ProgressRequest,
    ProposeBugHuntRequest,
    ProposeCampaignRequest,
    ProposeConversationRepliesRequest,
    ProposeEditorialPostRequest,
    ProposeFeatureSpotlightRequest,
    ProposeFrictionFixesRequest,
    ProposeGapFillRequest,
    ProposeMarketBriefRequest,
    ProposeMessagingFixesRequest,
    ProposePlaybookDraftsRequest,
    ProposePostmortemRequest,
    ProposeQualityReportRequest,
    ProposeRebalanceRequest,
    ProposeRoadmapRequest,
    ProposeVideoRequest,
    PRUpdateRequest,
    ReadMessagesRequest,
    RejectPlaybookRequest,
    RequestRenderRequest,
    RequestSandboxRequest,
)
from roboco.security import (
    guard_deco,
    prompt_injection_validator,
    secret_exfil_validator,
)
from roboco.services.gateway.content_actions import ContentActions

_RUNAWAY_RULES = [
    BehaviorRule(rule_type="frequency", threshold=120, window=60, action="log")
]

router = APIRouter(
    prefix="/api/v1/do",
    tags=["v1-do"],
    # F003/F014: bind X-Agent-ID to a verified HMAC token — same gate the
    # flow routers enforce via their role guards. The do router serves all
    # roles, so this is token-only (no role assertion).
    dependencies=[require_any_authenticated_agent],
)

_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ContentActionsDep = Annotated[ContentActions, Depends(get_content_actions)]


@router.post("/commit")
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_commit(
    request: Request,
    body: CommitRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.commit(
        agent_id=x_agent_id,
        message=body.message,
        files=body.files,
    )
    return envelope_to_response(env, request)


@router.post("/note")
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_note(
    request: Request,
    body: NoteRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.note(
        agent_id=x_agent_id,
        text=body.text,
        scope=body.scope,
        task_id=body.task_id,
        structured={
            "title": body.title,
            "context": body.context,
            "options": body.options,
            "chosen": body.chosen,
            "rationale": body.rationale,
            "consequences": body.consequences,
            "what_done": body.what_done,
            "what_learned": body.what_learned,
            "what_struggled": body.what_struggled,
            "next_steps": body.next_steps,
        },
        section=body.section,
        done=body.done,
        next=body.next,
        where_to_look=body.where_to_look,
    )
    return envelope_to_response(env, request)


@router.post("/pitch")
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_pitch(
    request: Request,
    body: PitchRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.pitch(
        agent_id=x_agent_id,
        title=body.title,
        slug=body.slug,
        problem=body.problem,
        proposed_solution=body.proposed_solution,
        target_cells=body.target_cells,
    )
    return envelope_to_response(env, request)


@router.post("/propose_roadmap")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_roadmap(
    request: Request,
    body: ProposeRoadmapRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_roadmap(
        agent_id=x_agent_id,
        cycle_goal=body.cycle_goal,
        items=[item.model_dump() for item in body.items],
    )
    return envelope_to_response(env, request)


@router.post("/propose_bug_hunt")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_bug_hunt(
    request: Request,
    body: ProposeBugHuntRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_bug_hunt(
        agent_id=x_agent_id,
        items=[item.model_dump() for item in body.items],
    )
    return envelope_to_response(env, request)


@router.post("/propose_gap_fill")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_gap_fill(
    request: Request,
    body: ProposeGapFillRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_gap_fill(
        agent_id=x_agent_id,
        items=[item.model_dump() for item in body.items],
    )
    return envelope_to_response(env, request)


@router.post("/propose_rebalance")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_rebalance(
    request: Request,
    body: ProposeRebalanceRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_rebalance(
        agent_id=x_agent_id,
        items=[item.model_dump() for item in body.items],
    )
    return envelope_to_response(env, request)


@router.post("/propose_messaging_fixes")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_messaging_fixes(
    request: Request,
    body: ProposeMessagingFixesRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_messaging_fixes(
        agent_id=x_agent_id,
        items=[item.model_dump() for item in body.items],
    )
    return envelope_to_response(env, request)


@router.post("/propose_friction_fixes")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_friction_fixes(
    request: Request,
    body: ProposeFrictionFixesRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_friction_fixes(
        agent_id=x_agent_id,
        items=[item.model_dump() for item in body.items],
    )
    return envelope_to_response(env, request)


@router.post("/propose_feature_spotlight")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_feature_spotlight(
    request: Request,
    body: ProposeFeatureSpotlightRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_feature_spotlight(
        agent_id=x_agent_id,
        feature_slug=body.feature_slug,
        feature_title=body.feature_title,
        body=body.body,
        wants_video=body.wants_video,
        video_script=body.video_script,
        skip=body.skip,
        skip_reason=body.skip_reason,
    )
    return envelope_to_response(env, request)


@router.post("/propose_conversation_replies")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_conversation_replies(
    request: Request,
    body: ProposeConversationRepliesRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_conversation_replies(
        agent_id=x_agent_id,
        items=[item.model_dump() for item in body.items],
    )
    return envelope_to_response(env, request)


@router.post("/propose_market_brief")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_market_brief(
    request: Request,
    body: ProposeMarketBriefRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_market_brief(
        agent_id=x_agent_id,
        headline=body.headline,
        findings=[f.model_dump() for f in body.findings],
        threats=body.threats,
        opportunities=body.opportunities,
        positioning_note=body.positioning_note,
    )
    return envelope_to_response(env, request)


@router.post("/propose_editorial_post")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_editorial_post(
    request: Request,
    body: ProposeEditorialPostRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_editorial_post(
        agent_id=x_agent_id,
        angle=body.angle,
        body=body.body,
        rationale=body.rationale,
    )
    return envelope_to_response(env, request)


@router.post("/propose_postmortem")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_postmortem(
    request: Request,
    body: ProposePostmortemRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_postmortem(
        agent_id=x_agent_id,
        incident_summary=body.incident_summary,
        root_cause=body.root_cause,
        failed_stage=body.failed_stage,
        process_change=body.process_change.model_dump(),
        playbook=body.playbook.model_dump() if body.playbook is not None else None,
    )
    return envelope_to_response(env, request)


@router.post("/propose_quality_report")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_quality_report(
    request: Request,
    body: ProposeQualityReportRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_quality_report(
        agent_id=x_agent_id,
        headline=body.headline,
        items=[item.model_dump() for item in body.items],
        overall_assessment=body.overall_assessment,
    )
    return envelope_to_response(env, request)


@router.post("/propose_playbook_drafts")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_playbook_drafts(
    request: Request,
    body: ProposePlaybookDraftsRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_playbook_drafts(
        agent_id=x_agent_id,
        drafts=[d.model_dump() for d in body.drafts],
    )
    return envelope_to_response(env, request)


@router.post("/propose_campaign")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_campaign(
    request: Request,
    body: ProposeCampaignRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_campaign(
        agent_id=x_agent_id,
        campaign_name=body.campaign_name,
        posts=[p.model_dump() for p in body.posts],
    )
    return envelope_to_response(env, request)


@router.post("/propose_video")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_propose_video(
    request: Request,
    body: ProposeVideoRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.propose_video(
        agent_id=x_agent_id,
        composition_id=body.composition_id,
        x_caption=body.x_caption,
        tiktok_caption=body.tiktok_caption,
        platforms=body.platforms,
        input_props=body.input_props,
    )
    return envelope_to_response(env, request)


@router.post("/nothing_to_propose")
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_nothing_to_propose(
    request: Request,
    body: NothingToProposeRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.nothing_to_propose(
        agent_id=x_agent_id,
        task_id=body.task_id,
        reason=body.reason,
    )
    return envelope_to_response(env, request)


@router.post("/dm")
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.suspicious_detection(enabled=True)
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_dm(
    request: Request,
    body: DmRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.dm(
        agent_id=x_agent_id,
        recipient=body.recipient,
        text=body.text,
        task_id=body.task_id,
        skill=body.skill,
    )
    return envelope_to_response(env, request)


@router.post("/notify")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_notify(
    request: Request,
    body: NotifyRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify(
        agent_id=x_agent_id,
        target=body.target,
        text=body.text,
        priority=body.priority,
        task_id=body.task_id,
    )
    return envelope_to_response(env, request)


@router.post("/evidence")
async def do_evidence(
    request: Request,
    body: EvidenceRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.evidence(agent_id=x_agent_id, task_id=body.task_id)
    return envelope_to_response(env, request)


@router.post("/request_sandbox")
async def do_request_sandbox(
    request: Request,
    body: RequestSandboxRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.request_sandbox(
        agent_id=x_agent_id, services=body.services, extensions=body.extensions
    )
    return envelope_to_response(env, request)


@router.post("/request_render")
async def do_request_render(
    request: Request,
    body: RequestRenderRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.request_render(
        agent_id=x_agent_id,
        composition_id=body.composition_id,
        orientation=body.orientation,
        frame_count=body.frame_count,
        input_props=body.input_props,
    )
    return envelope_to_response(env, request)


# ---------------------------------------------------------------------------
# Wave 1 — pre-gateway parity
# ---------------------------------------------------------------------------


@router.post("/progress")
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_progress(
    request: Request,
    body: ProgressRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.progress(
        agent_id=x_agent_id,
        task_id=body.task_id,
        message=body.message,
        plan_step=body.plan_step,
        percentage=body.percentage,
    )
    return envelope_to_response(env, request)


@router.post("/notify_list")
async def do_notify_list(
    request: Request,
    body: NotifyListRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify_list(
        agent_id=x_agent_id,
        unread_only=body.unread_only,
        pending_ack_only=body.pending_ack_only,
        limit=body.limit,
    )
    return envelope_to_response(env, request)


@router.post("/notify_get")
async def do_notify_get(
    request: Request,
    body: NotifyGetRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify_get(
        agent_id=x_agent_id,
        notification_id=body.notification_id,
    )
    return envelope_to_response(env, request)


@router.post("/notify_ack")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_notify_ack(
    request: Request,
    body: NotifyAckRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify_ack(
        agent_id=x_agent_id,
        notification_id=body.notification_id,
    )
    return envelope_to_response(env, request)


@router.post("/read_messages")
async def do_read_messages(
    request: Request,
    _body: ReadMessagesRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.read_messages(agent_id=x_agent_id)
    return envelope_to_response(env, request)


@router.post("/read_a2a")
async def do_read_a2a(
    request: Request,
    _body: ReadMessagesRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.read_a2a(agent_id=x_agent_id)
    return envelope_to_response(env, request)


@router.post("/pr_update")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_pr_update(
    request: Request,
    body: PRUpdateRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.pr_update(
        agent_id=x_agent_id,
        task_id=body.task_id,
        title=body.title,
        body=body.body,
        reviewers=body.reviewers,
    )
    return envelope_to_response(env, request)


@router.post("/draft_playbook")
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_draft_playbook(
    request: Request,
    body: DraftPlaybookRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.draft_playbook(
        agent_id=x_agent_id,
        title=body.title,
        problem=body.problem,
        procedure=body.procedure,
        tags=body.tags,
        source_task_id=body.source_task_id,
    )
    return envelope_to_response(env, request)


@router.post("/approve_playbook")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_approve_playbook(
    request: Request,
    body: ApprovePlaybookRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.approve_playbook(
        agent_id=x_agent_id, playbook_id=body.playbook_id
    )
    return envelope_to_response(env, request)


@router.post("/reject_playbook")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_reject_playbook(
    request: Request,
    body: RejectPlaybookRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.reject_playbook(
        agent_id=x_agent_id, playbook_id=body.playbook_id, reason=body.reason
    )
    return envelope_to_response(env, request)


@router.post("/archive_playbook")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_archive_playbook(
    request: Request,
    body: ArchivePlaybookRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.archive_playbook(
        agent_id=x_agent_id, playbook_id=body.playbook_id
    )
    return envelope_to_response(env, request)


@router.post("/curate_vault")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def do_curate_vault(
    request: Request,
    body: CurateVaultRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.curate_vault(
        agent_id=x_agent_id, task_id=body.task_id, narrative=body.narrative
    )
    return envelope_to_response(env, request)
