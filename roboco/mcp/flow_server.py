"""roboco-flow MCP server — exposes intent verbs to agents.

Tools are role-scoped via the agent's spawn manifest. The orchestrator writes
``/app/tool-manifest.json`` into each spawned agent container with that role's
``flow_tools`` array; this module reads the manifest at import time and only
registers the verbs listed there. If the manifest is missing or unreadable
(e.g. local test runs without the bind mount) the full registry is registered
as a failsafe and a warning is logged.

The orchestrator's API still rejects verbs that don't match the agent's role
(defence-in-depth), but per-agent registration prevents the model from ever
*seeing* an off-role verb in its tool palette.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Annotated, Any

import httpx
import structlog
from mcp.server.fastmcp import FastMCP
from pydantic import BeforeValidator

from roboco.agents_config import get_agent_team
from roboco.foundation.policy.content.validators import coerce_str_list
from roboco.foundation.policy.flow_timeouts import CLIENT_HEADROOM_SECONDS, SLOW_VERBS

# A ``list[str]`` field that tolerates the Claude SDK's XML-ish tool-input
# parsing: an LLM emitting a bullet list as ``<item>…</item>`` elements arrives
# as ``[[["…"]]]`` / ``[{"item": {"$text": "…"}}, …]`` — nested arrays / dicts,
# not strings. A bare ``list[str]`` annotation hard-rejects element 1 (a list,
# not a str) at the MCP validation layer BEFORE the verb body runs, surfacing as
# ``1 validation error for i_will_planArguments technical_considerations.1
# Input should be a valid string``. The ``BeforeValidator`` flattens it to a
# flat ``list[str]`` first (same ``coerce_str_list`` used at the intake→DB
# boundary — see Bug 3 in the MegaTask memory).
StrList = Annotated[list[str], BeforeValidator(coerce_str_list)]

ORCHESTRATOR_URL = os.environ.get(
    "ROBOCO_ORCHESTRATOR_URL",
    "http://roboco-orchestrator:8000",
)
# Where the per-agent SDK server lives (per-container loopback). The
# flow server POSTs /verb/attempted here so the per-verb circuit breaker
# can record rejections and tell us when to substitute circuit_open.
SDK_URL = os.environ.get("ROBOCO_SDK_URL", "http://localhost:9000")
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]

# Client wall = the matching server wall (FlowVerbTimeoutMiddleware) plus
# headroom, so the client always outlasts the server's asyncio.timeout and
# sees the clean 504 gateway_timeout envelope instead of a raw transport
# timeout. This module can't read Settings (it's a subprocess in the agent
# container), so the two server budgets are mirrored via env vars the
# orchestrator injects at spawn from settings.flow_verb_timeout_seconds /
# flow_verb_slow_timeout_seconds; the literal fallbacks match those settings'
# own defaults (120 / 900).
_SERVER_TIMEOUT_SECONDS = float(
    os.environ.get("ROBOCO_FLOW_VERB_TIMEOUT_SECONDS", "120")
)
_SERVER_SLOW_TIMEOUT_SECONDS = float(
    os.environ.get("ROBOCO_FLOW_VERB_SLOW_TIMEOUT_SECONDS", "900")
)
_TIMEOUT = _SERVER_TIMEOUT_SECONDS + CLIENT_HEADROOM_SECONDS
_SLOW_TIMEOUT = _SERVER_SLOW_TIMEOUT_SECONDS + CLIENT_HEADROOM_SECONDS
# Tight timeout for SDK loopback — the SDK is a local sidecar; anything
# slower than 2s is unhealthy and the gateway path must not stall on it.
_SDK_TIMEOUT = 2.0
# FastAPI's default missing-route status. Every gateway route returns 200
# with an Envelope (including not_found rejections), so a 404 from the
# orchestrator is always a manifest-registered verb whose HTTP route is
# missing — synthesize an invalid_state Envelope for it.
_MISSING_ROUTE_STATUS = 404

# Envelope error kinds that count toward the per-verb circuit breaker.
# Mirrors agent_sdk.server._CIRCUIT_REJECTION_KINDS; the SDK is the
# authoritative side, but we filter here too so we only emit one POST
# for kinds the SDK will actually count.
_CIRCUIT_REJECTION_KINDS: frozenset[str] = frozenset(
    {"tracing_gap", "invalid_state", "not_authorized", "incomplete_input"}
)


# Dict-shaped `error.code` values (from FastAPI's exception handlers —
# `roboco_exception_handler` / `http_exception_handler` / `generic_exception_handler`)
# mapped to the counted breaker kind they are semantically equivalent to. A
# 422 / 500 / 4xx-exception storm is retry-storm-worthy but the response body
# carries `error` as a DICT (not a string kind), so the breaker's string-only
# check skipped it — unbounded retries. We classify by `error.code` so the SDK
# actually records the attempt. Kinds not in `_CIRCUIT_REJECTION_KINDS` are
# never forwarded (the SDK ignores unknown kinds anyway).
#
# The exact map is authoritative for the codes the handlers actually emit, so
# a known code is never misclassified by an accidental substring — e.g.
# AUTHENTICATION_REQUIRED carries no AUTHORIZED/DENIED/PERMISSION substring and
# under a substring-only rule dropped to ``invalid_state`` instead of
# ``not_authorized`` (#161). The NOT_FOUND family maps to None — parity with
# the string-error contract that a `not_found` rejection does NOT count
# (retrying a missing resource won't help until state changes). Unknown codes
# fall through to a substring branch so a new RobocoError code still lands on a
# counted kind without a map update.
_DICT_ERROR_CODE_MAP: dict[str, str | None] = {
    "AUTHENTICATION_REQUIRED": "not_authorized",
    "JOURNAL_ACCESS_DENIED": "not_authorized",
    "PERMISSION_DENIED": "not_authorized",
    "INVALID_INPUT": "incomplete_input",
    "VALIDATION_ERROR": "incomplete_input",
    "NOT_FOUND": None,
    "INVALID_STATE": "invalid_state",
    "TASK_LIFECYCLE_ERROR": "invalid_state",
    "TASK_OWNERSHIP_ERROR": "invalid_state",
    "SERVICE_ERROR": "invalid_state",
    "FETCH_FAILED": "invalid_state",
    "LIST_FAILED": "invalid_state",
    "READ_FAILED": "invalid_state",
    "SEARCH_FAILED": "invalid_state",
    "WRITE_FAILED": "invalid_state",
}


def _classify_dict_error_code(code: str) -> str | None:
    upper = code.upper()
    if upper in _DICT_ERROR_CODE_MAP:
        return _DICT_ERROR_CODE_MAP[upper]
    # Unknown code — substring fallback for forward-compat with new codes.
    if "NOT_FOUND" in upper:
        return None
    if (
        "DENIED" in upper
        or "AUTHORIZED" in upper
        or "AUTH" in upper
        or "FORBIDDEN" in upper
        or "PERMISSION" in upper
    ):
        return "not_authorized"
    if "VALIDATION" in upper:
        return "incomplete_input"
    return "invalid_state"


def _remediate_for_kind(kind: str, verb: str) -> str:
    """A directed recovery hint for a synthesized Envelope kind.

    The orchestrator's exception handlers return a dict `error` with no
    `remediate`; without a hint the agent has no directed next action and
    flails/respawn-loops until the breaker trips. This gives each counted kind
    (and not_found) a one-line remedy that mirrors the string-kind envelopes.
    """
    if kind == "not_found":
        return (
            "the call targeted a resource that does not exist; re-fetch the"
            " task state (give_me_work / resume) and retry on a current id;"
            " do not retry the same id."
        )
    if kind == "incomplete_input":
        return (
            f"the {verb} call was rejected as incomplete input — re-issue it"
            f" with the missing/invalid fields (see `detail` for the exact"
            f" validation errors); do not retry blindly."
        )
    if kind == "not_authorized":
        return (
            f"you are not authorized for this {verb} action; use delegate /"
            f" escalate_up, or call i_am_blocked(reason=...) if the gate is"
            f" genuinely wrong; do not retry the same action."
        )
    # invalid_state + any fallback (service/INTERNAL_ERROR).
    return (
        f"service error on {verb} — the task may be in the wrong state for"
        f" this verb. Re-fetch state (resume / give_me_work) and re-issue;"
        f" call i_am_blocked or i_am_idle if it persists; do not retry blindly."
    )


def _normalize_exception_envelope(
    payload: dict[str, Any], path: str
) -> dict[str, Any] | None:
    """Synthesize an Envelope-wire-format dict from an exception-handler body.

    FastAPI's exception handlers return ``error`` as a DICT
    (``{code, message, details?}`` from ``roboco_exception_handler`` /
    ``http_exception_handler`` / ``generic_exception_handler``) or a bare
    ``detail`` list (``request_validation_handler``, 422) — neither is the
    Envelope wire format the agent is prompted to trust (string ``error`` kind
    + ``message`` + ``remediate`` + ``missing``). Returning either raw violates
    the contract: the agent has no ``remediate``/``next`` and flails until the
    breaker trips (#232). This lifts the dict/422 body into a real Envelope:

    - dict ``error`` → map ``code`` via :func:`_classify_dict_error_code` to a
      counted string kind (NOT_FOUND → ``not_found``), lift ``message``, synthesize
      a ``remediate``, ``missing=[]``, and tuck the original body under
      ``details`` for correlation traceability.
    - 422 ``detail`` (no ``error``) → ``error='incomplete_input'`` with the
      validation ``detail`` preserved so the agent sees WHICH fields failed.

    Returns None when ``payload`` is already a real Envelope (string ``error``
    or success) so successful/string-kind rejections pass through unchanged.
    """
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        kind = _classify_dict_error_code(code)
        if kind is None:
            kind = "not_found"
        verb = _verb_from_path(path)
        message = str(error.get("message") or "") or f"orchestrator error ({code})"
        return {
            "error": kind,
            "message": message,
            "remediate": _remediate_for_kind(kind, verb),
            "missing": [],
            "details": error,
        }
    if "detail" in payload and "error" not in payload:
        # 422 request-validation body ({"detail": [...], "body": ...}).
        verb = _verb_from_path(path)
        return {
            "error": "incomplete_input",
            "message": f"the {verb} call was rejected as incomplete input",
            "remediate": _remediate_for_kind("incomplete_input", verb),
            "missing": [],
            "detail": payload.get("detail"),
        }
    return None


def _classify_rejection(payload: dict[str, Any]) -> str | None:
    """Return the breaker kind to forward for this payload, or None.

    The breaker only counts rejections whose kind is in
    ``_CIRCUIT_REJECTION_KINDS`` (the SDK's authoritative catalog). Three
    reachable rejection shapes must all map to a counted kind so a storm of
    any of them trips the breaker:

    1. Envelope rejection: ``error`` is a STRING kind. Forward it if in
       the counted set (existing behaviour). Uncounted string kinds (e.g.
       ``not_found``, ``transport_error``, ``circuit_open``) return None —
       preserves the prior contract that those don't touch the SDK.
    2. Exception-handler dict: ``error`` is a DICT
       (``{code, message, details?}`` from ``roboco_exception_handler`` /
       ``http_exception_handler`` / ``generic_exception_handler``). Map its
       ``code`` to a counted kind — auth/permission/denied → ``not_authorized``,
       ``INVALID_INPUT`` / validation → ``incomplete_input``, anything else
       (INTERNAL_ERROR, API_ERROR, TASK_WRONG_STATUS, …) → ``invalid_state``.
       NOT_FOUND-family codes return None (parity with string ``not_found``).
    3. 422 validation failure: no ``error`` field, a ``detail`` list
       (``request_validation_handler``). → ``incomplete_input``.

    Successful envelopes (``status`` set, ``error`` None) and uncounted
    string kinds return None — the SDK is not touched.
    """
    error = payload.get("error")
    if isinstance(error, str):
        return error if error in _CIRCUIT_REJECTION_KINDS else None
    if isinstance(error, dict):
        return _classify_dict_error_code(str(error.get("code") or ""))
    if "detail" in payload:
        # 422 request-validation body ({"detail": [...], "body": ...}).
        return "incomplete_input"
    return None


mcp = FastMCP("roboco-flow")
log = structlog.get_logger()


def _build_headers() -> dict[str, str]:
    """Build per-call headers including a fresh X-Correlation-ID.

    The agent runtime is the first hop, so we mint a UUID per MCP call.
    The orchestrator's ``CorrelationIdMiddleware`` will accept this as the
    inbound id and bind it to the structlog context for the request, so
    every log line and the audit row carry the same id and the agent
    receives it back on the envelope.
    """
    # X-Agent-Token (HMAC over id:role:team, issued by the orchestrator at
    # spawn) and X-Agent-Team must travel with every flow verb or the API's
    # ROBOCO_AGENT_AUTH_REQUIRED gate 401s with "Missing X-Agent-Token" —
    # the same headers ApiClient injects for the other MCP servers.
    headers = {
        "X-Agent-ID": AGENT_ID,
        "X-Agent-Role": AGENT_ROLE,
        "X-Correlation-ID": str(uuid.uuid4()),
    }
    team = get_agent_team(AGENT_ID)
    if team:
        headers["X-Agent-Team"] = team
    token = os.environ.get("ROBOCO_AGENT_TOKEN")
    # The orchestrator injects "UNSIGNED" when ROBOCO_AGENT_AUTH_SECRET is
    # unset at spawn. The middleware rejects a presented-but-unverifiable
    # token with 401 "signature mismatch" even in dev mode, so forwarding
    # UNSIGNED turns every flow verb into a 401. Omit the header instead —
    # dev (auth not required) accepts a missing token; prod (auth required)
    # 401s with "Missing X-Agent-Token", the clear respawn-with-secret signal.
    if token and token != "UNSIGNED":
        headers["X-Agent-Token"] = token
    return headers


def _client_timeout_for(verb: str) -> float:
    """The httpx client timeout for one verb — must outlast its server wall.

    Mirrors ``FlowVerbTimeoutMiddleware``'s own budget selection so the two
    walls agree: a slow verb gets the slow server budget + headroom, every
    other verb gets the default budget + headroom.
    """
    return _SLOW_TIMEOUT if verb in SLOW_VERBS else _TIMEOUT


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST a request to the orchestrator and return the JSON envelope.

    The orchestrator returns the standardized envelope on both success
    (2xx) and rejection (4xx). The MCP-side bridge surfaces the envelope
    in either case so agents see ``remediate`` / ``missing`` even on a
    4xx response. Only raises if the response has no parseable body
    (e.g., a 5xx with HTML error page or a network failure).

    Rejection envelopes (error in _CIRCUIT_REJECTION_KINDS) are forwarded
    to the local SDK's /verb/attempted so the per-verb circuit breaker
    can track them. If the SDK reports the breaker is now open, the
    original rejection is REPLACED with the circuit_open envelope before
    being returned to the agent — preventing further hammering on a verb
    that won't succeed. Successful (ok) envelopes never touch the SDK.
    """
    # Client must outlast the server middleware budget so agents get the
    # 504 envelope, not a raw timeout.
    with httpx.Client(timeout=_client_timeout_for(_verb_from_path(path))) as client:
        response = client.post(
            f"{ORCHESTRATOR_URL}{path}",
            headers=_build_headers(),
            json=body,
        )
        # A 404 here usually means a manifest-registered verb has no matching
        # route on the orchestrator: every gateway route returns 200 with an
        # Envelope (including not_found rejections), so FastAPI's default 404
        # body (``{"detail": "Not Found"}``, no ``error`` field) is a missing
        # route — synthesize an ``invalid_state`` Envelope so the breaker
        # counts it and the agent gets a wiring-gap remediation hint. Two other
        # 404 shapes are surfaced more accurately (#61): a 404 carrying a real
        # Envelope (an ``error`` field — e.g. a proxy re-status a 200 rejection
        # to 404) is surfaced as-is; a 404 carrying a *descriptive* ``detail``
        # (not the bare default) is a real resource not_found, not a missing
        # route — surface it as ``not_found`` so the agent re-fetches state
        # instead of being told the route is unwired.
        if response.status_code == _MISSING_ROUTE_STATUS:
            try:
                body_404 = response.json()
            except (ValueError, json.JSONDecodeError):
                body_404 = None
            if isinstance(body_404, dict) and "error" in body_404:
                # Real Envelope rejection surfaced under a 404 status —
                # surface it as-is so the agent sees the real kind/remediate.
                payload_404: dict[str, Any] = body_404
            elif (
                isinstance(body_404, dict)
                and isinstance(body_404.get("detail"), str)
                and body_404["detail"] != "Not Found"
            ):
                # A real HTTP 404 with a descriptive detail — a resource
                # not_found, not a missing route (#61).
                verb = _verb_from_path(path)
                payload_404 = {
                    "error": "not_found",
                    "message": body_404["detail"],
                    "remediate": (
                        f"the {verb} call targeted a resource that does not"
                        f" exist (HTTP 404: {body_404['detail']}). Re-fetch"
                        f" the task state (give_me_work / resume) and retry on"
                        f" a current id; do not retry the same id."
                    ),
                    "missing": [],
                }
            else:
                verb = _verb_from_path(path)
                payload_404 = {
                    "error": "invalid_state",
                    "message": (
                        f"verb '{verb}' has no route on the orchestrator for"
                        f" role {AGENT_ROLE!r} (path {path})"
                    ),
                    "remediate": (
                        f"the {verb} verb is advertised in your manifest but"
                        f" its HTTP route is missing — this is a server-side"
                        f" wiring gap. Call"
                        f" i_am_blocked(reason='verb {verb} 404s: no route')"
                        f" or i_am_idle() so the operator can fix the route;"
                        f" do not retry."
                    ),
                    "missing": [],
                }
            return _record_and_check_circuit(path, body, payload_404)
        try:
            payload: dict[str, Any] = response.json()
        except (ValueError, json.JSONDecodeError):
            # No JSON body (HTML error page, empty body, etc). Surface the
            # status as a synthetic envelope so the agent gets a remediate
            # hint instead of a Python traceback.
            return {
                "error": "transport_error",
                "message": (
                    f"orchestrator returned HTTP {response.status_code}"
                    f" with no JSON body for {path}"
                ),
                "remediate": (
                    "check that the orchestrator is up and the route exists;"
                    " contact the human operator if this persists"
                ),
                "missing": [],
            }
    # Outside the orchestrator client context so the SDK call is its own
    # connection — keeps semantics independent and timeouts separated.
    # A non-404 JSON body that is NOT a real Envelope (dict `error` from an
    # exception handler, or a 422 `detail` list) is normalized to the Envelope
    # wire format so the agent gets a string `error` kind + remediate (#232).
    # The synthesized Envelope still flows through the breaker below — its
    # string kind is in the counted set, so a 500/422 storm trips it.
    normalized = _normalize_exception_envelope(payload, path)
    if normalized is not None:
        payload = normalized
    return _record_and_check_circuit(path, body, payload)


def _verb_from_path(path: str) -> str:
    """Extract the verb name from a role-scoped flow path.

    ``/api/v1/flow/<role>/<verb>`` → ``<verb>``. Returns the original
    path if it doesn't match the expected shape (defensive — the breaker
    falls open downstream when the verb is unrecognized).
    """
    return path.rsplit("/", 1)[-1]


def _record_and_check_circuit(
    path: str,
    body: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Forward a gateway rejection to the SDK breaker; maybe substitute.

    For successful (ok) envelopes this is a no-op — only rejections of
    kind tracing_gap / invalid_state / not_authorized / incomplete_input
    are reported. When the SDK responds with ``open=true`` we replace the
    original rejection with the wire-format ``circuit_open`` envelope so
    the agent stops retrying.

    Best-effort: SDK unreachable, slow, or malformed response → return
    the original payload. The breaker is a safety net; it must never
    break the gateway path.
    """
    # Gateway envelopes use a string `error` (kind); RobocoError-derived
    # exceptions surface a dict-shaped error via FastAPI's middleware, and
    # 422 validation failures carry a `detail` list with no `error` field
    # at all. Classify all three rejection shapes so a storm of 500s or 422s
    # counts toward the breaker (previously bypassed → unbounded retries). The
    # dict-shape defence against `TypeError: unhashable type: 'dict'` lives in
    # `_classify_rejection` (isinstance checks, never a `dict in frozenset`
    # membership test).
    rejection_kind = _classify_rejection(payload)
    if rejection_kind is None:
        return payload

    verb = _verb_from_path(path)
    task_id = body.get("task_id")
    try:
        with httpx.Client(timeout=_SDK_TIMEOUT) as client:
            resp = client.post(
                f"{SDK_URL}/verb/attempted",
                json={
                    "verb": verb,
                    "task_id": str(task_id) if task_id is not None else None,
                    "rejection_kind": rejection_kind,
                },
            )
            status = resp.json()
    except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
        # Fail open: agent sees the original rejection. Log so operators
        # notice an SDK that's down — the gateway keeps working.
        log.warning(
            "flow_server: SDK /verb/attempted unreachable; breaker bypassed",
            verb=verb,
            task_id=task_id,
            error=str(exc),
        )
        return payload

    if status.get("open") and isinstance(status.get("circuit_envelope"), dict):
        # Copy so the SDK's envelope dict is not mutated in place (the SDK may
        # reuse it across calls). Nest the original fixable rejection as
        # ``inner`` so its kind/message/remediate survive the substitution —
        # the circuit_open envelope only says the breaker tripped, not WHY the
        # verb failed, and the agent still needs the underlying hint (#60).
        circuit_env: dict[str, Any] = dict(status["circuit_envelope"])
        circuit_env["inner"] = payload
        # The SDK's circuit_envelope omits task_id/correlation_id; lift them
        # from the original rejection to the top level so the agent's envelope
        # contract (read top-level task_id/correlation_id) and ops audit-join of
        # the trip event still work — not just nested in `inner` (#359).
        circuit_env["task_id"] = payload.get("task_id")
        circuit_env["correlation_id"] = payload.get("correlation_id")
        log.info(
            "flow_server: circuit_open substituted for rejection",
            verb=verb,
            task_id=task_id,
            attempts=status.get("attempts"),
            limit=status.get("limit"),
        )
        return circuit_env
    return payload


# Board route serves Product Owner + Head Marketing under one prefix.
# AgentRole values that map to a different URL segment go here; everything
# else passes through unchanged so route prefix == role name (developer,
# qa, documenter, cell_pm, main_pm, auditor).
_ROLE_TO_ROUTE_PREFIX: dict[str, str] = {
    "product_owner": "board",
    "head_marketing": "board",
}
_ROUTE_PREFIX = _ROLE_TO_ROUTE_PREFIX.get(AGENT_ROLE, AGENT_ROLE)


def _role_path(verb: str) -> str:
    """Build the role-scoped /api/v1/flow/<route>/<verb> path."""
    return f"/api/v1/flow/{_ROUTE_PREFIX}/{verb}"


# ---------- Dev verbs ----------


def give_me_work() -> dict[str, Any]:
    """Get your current task or report idle. Returns task + context_briefing."""
    return _post(_role_path("give_me_work"), {})


def i_will_work_on(
    task_id: str,
    plan: str | None = None,
    steps: list[dict[str, str]] | None = None,
    technical_considerations: StrList | None = None,
    risks: list[dict[str, str]] | None = None,
    open_questions: list[dict[str, str | bool]] | None = None,
) -> dict[str, Any]:
    """Claim/start/recover a task. Works for pending, claimed, needs_revision.

    On a FRESH claim a developer authors the SAME rich plan a PM does, so the
    task's Plan tab is fully populated for audit/tracing — the gateway's
    ``_dev_plan_gate`` rejects a thin one. Re-entry / recovery claims
    (already-claimed, in_progress, needs_revision) do NOT re-supply any of
    this; the gateway short-circuits before the gate.

    Args:
        task_id: UUID of the task you are claiming.
        plan: 2-4 sentences (>= 150 chars) describing HOW you will implement
            this. Doubles as the plan's "Approach".
        steps: Ordered execution checklist — list of
            ``{"title": "...", "description": "..."}`` with every description
            substantive. Becomes the plan's sub-tasks AND the progress
            checklist (completing a step advances %).
        technical_considerations: Bullet list (strings) of architectural /
            library / approach notes.
        risks: List of ``{"risk": "...", "mitigation": "..."}`` entries.
        open_questions: Optional list of ``{"question": "...",
            "answered": false}`` entries.
    """
    return _post(
        _role_path("i_will_work_on"),
        {
            "task_id": task_id,
            "plan": plan,
            "steps": steps or [],
            "technical_considerations": technical_considerations or [],
            "risks": risks or [],
            "open_questions": open_questions or [],
        },
    )


def open_pr(task_id: str) -> dict[str, Any]:
    """Push your branch and open a PR.

    Atomic: validates ALL preconditions (assignee, commits, no-prior-PR)
    BEFORE running any git side effects. After this verb returns success,
    call ``i_am_done(task_id, notes='...')`` to actually submit for QA.
    Renamed from ``submit_for_qa`` (2026-05-08) — the old name suggested
    this verb advanced the lifecycle, but it only opens the PR.
    """
    return _post(_role_path("open_pr"), {"task_id": task_id})


def i_am_done(
    task_id: str,
    notes: str = "",
    resolved_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Submit for QA. Strict — PR must be open (call open_pr first).

    resolved_findings: required when the task has open revision-ledger
    findings (from a prior qa_fail/pr_fail/request_changes/ceo_reject) —
    one entry per finding you addressed: {finding_id, commit?, note?}.
    finding_id is the 8-char id shown in the finding's '[F-xxxxxxxx]'
    rendering (in qa_notes/pm_notes/pr_reviewer_notes) — a full id also
    matches. Every open finding must be named or i_am_done rejects.
    """
    return _post(
        _role_path("i_am_done"),
        {
            "task_id": task_id,
            "notes": notes,
            "resolved_findings": resolved_findings or [],
        },
    )


def i_am_blocked(
    task_id: str,
    reason: str,
    blocker_type: str | None = None,
    what_needed: str | None = None,
) -> dict[str, Any]:
    """Escalate to PM. Logs a struggle journal entry.

    Args:
        task_id: UUID of the task you're stuck on.
        reason: One paragraph describing the blocker.
        blocker_type: One of ``external`` | ``internal`` | ``question`` |
            ``dependency``. Optional but strongly preferred — the PM
            triages by class. Pre-gateway parity.
        what_needed: Concrete description of what would unblock the
            task. Pre-gateway parity.
    """
    return _post(
        _role_path("i_am_blocked"),
        {
            "task_id": task_id,
            "reason": reason,
            "blocker_type": blocker_type,
            "what_needed": what_needed,
        },
    )


def unclaim(task_id: str) -> dict[str, Any]:
    """Release this claim back to pending. Branch survives; task is unassigned."""
    return _post(_role_path("unclaim"), {"task_id": task_id})


def reassign(task_id: str, new_assignee: str) -> dict[str, Any]:
    """Cell PM: hand a claimed/in_progress task to another dev in your own cell.

    The branch is keyed to the task, so the work-in-progress survives;
    `new_assignee` is a developer slug in your cell (e.g. `be-dev-2`).
    """
    return _post(
        _role_path("reassign"), {"task_id": task_id, "new_assignee": new_assignee}
    )


def resume(task_id: str) -> dict[str, Any]:
    """Resume a paused task. Transitions paused → in_progress for the assignee."""
    return _post(_role_path("resume"), {"task_id": task_id})


def sync_branch(task_id: str, stash: bool = False) -> dict[str, Any]:
    """Re-sync your branch onto its base through the gate.

    Rebases the task's branch onto its resolved parent/base branch (fetch +
    rebase + force-with-lease push). Use this when your branch has fallen
    behind its base and you need to pick up merged work before continuing —
    raw git is denied, so this is the gate-level way to rebase. No lifecycle
    transition: after it returns, keep editing + commit, then open_pr /
    i_am_done as normal. On ``conflicts`` status the envelope's ``next`` tells
    you the rebase aborted and your branch is unchanged — resolve the conflict
    in your working tree first (the gate does not force a conflicted rebase).

    Args:
        task_id: UUID of the task whose branch you're re-syncing.
        stash: If your workspace has uncommitted changes, pass True to
            auto-stash them (tracked + untracked), rebase, then restore them
            — instead of refusing DIRTY_WORKSPACE. A conflicted restore
            leaves the stash in place (never dropped); the envelope's
            ``next`` tells you to resolve it by hand.
    """
    return _post(_role_path("sync_branch"), {"task_id": task_id, "stash": stash})


def i_am_idle() -> dict[str, Any]:
    """Report no more work. Soft-blocks if you have unread A2A/mentions."""
    return _post(_role_path("i_am_idle"), {})


# ---------- QA verbs ----------


def claim_review(task_id: str) -> dict[str, Any]:
    """QA: claim a task for review. Returns PR diff + evidence inline."""
    return _post(_role_path("claim_review"), {"task_id": task_id})


def pass_review(
    task_id: str,
    notes: str,
    ac_verdicts: StrList | None = None,
    criteria_verified: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """QA: accept the work. notes >= 80 chars; journal:learning required.

    criteria_verified: MANDATORY when the task has acceptance criteria — one
    {criterion, evidence} entry per criterion. criterion must match an AC by
    id or exact text; evidence must be concrete (file:line, screenshot ref,
    rendered-frame path, test name), not a gestalt "looks good". Every
    criterion must be covered, or the pass is rejected naming exactly which
    are missing/unmatched. If any criterion does not hold, call fail_review
    instead of passing a partial.

    ac_verdicts: legacy free-text, one entry per acceptance criterion — still
    folded into the persisted notes but no longer gates the pass.
    """
    payload: dict[str, Any] = {"task_id": task_id, "notes": notes}
    if ac_verdicts is not None:
        payload["ac_verdicts"] = ac_verdicts
    if criteria_verified is not None:
        payload["criteria_verified"] = criteria_verified
    return _post(_role_path("pass"), payload)


def fail_review(
    task_id: str,
    issues: StrList | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """QA: reject the work with structured findings — transitions to needs_revision.

    findings: the structured revision-findings ledger entry — each
    {file?, line?, severity (blocker|major|minor|nit), expected, actual,
    fix?, evidence?}. issues (plain strings) is still accepted this release
    but deprecated — pass findings instead. At least one of the two is
    required. Nudge above 5 findings, hard reject above 10 — split or
    prioritize.
    """
    return _post(
        _role_path("fail"),
        {"task_id": task_id, "issues": issues or [], "findings": findings or []},
    )


# ---------- PR reviewer verbs ----------


def claim_pr_review(task_id: str) -> dict[str, Any]:
    """PR reviewer: claim an inbound external/fork-PR review task.

    Returns the contributor's unified diff inline (read-only — the fork code is
    never checked out or run). Inspect it, then call post_pr_review.
    """
    return _post(_role_path("claim_pr_review"), {"task_id": task_id})


def post_pr_review(
    task_id: str,
    body: str,
    event: str = "REQUEST_CHANGES",
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """PR reviewer: post ONE complete change-request to the PR and finish the task.

    body: a one-paragraph summary. findings: the per-criterion list — each
    {file, line?, severity (blocker|major|minor|nit), expected, actual}. When
    findings are given, the GitHub comment is GENERATED in the RoboCo format
    (summary + a findings table + verdict) — do not hand-format it in body.
    event: REQUEST_CHANGES (default), APPROVE, or COMMENT. The verdict must
    match the findings: to APPROVE a clean PR pass event='APPROVE' (do not rely
    on the default); REQUEST_CHANGES must cite at least one finding, and APPROVE
    may not carry a blocker/major finding. Requires a journal:learning entry
    first.
    """
    return _post(
        _role_path("post_pr_review"),
        {
            "task_id": task_id,
            "body": body,
            "event": event,
            "findings": findings or [],
        },
    )


# ---------- Doc verbs ----------


def claim_doc_task(task_id: str) -> dict[str, Any]:
    """Doc: claim a task in awaiting_documentation state."""
    return _post(_role_path("claim_doc_task"), {"task_id": task_id})


def i_documented(task_id: str, notes: str, files: StrList) -> dict[str, Any]:
    """Doc: mark documentation complete. files=['<doc-path>', ...]."""
    return _post(
        _role_path("i_documented"),
        {"task_id": task_id, "notes": notes, "files": files},
    )


# ---------- PM verbs ----------
# Cell PM + Main PM share: triage, unblock, complete, escalate_up


def triage() -> dict[str, Any]:
    """PM: get the most important task to act on next."""
    return _post(_role_path("triage"), {})


def triage_all() -> dict[str, Any]:
    """Main PM: triage across all teams."""
    return _post(_role_path("triage_all"), {})


def unblock(task_id: str, reason: str, restore: bool = True) -> dict[str, Any]:
    """PM: unblock a task. `reason` states why the block is cleared and is
    recorded as your decision (no separate note needed). restore=True
    (default) restores pre_block_state."""
    return _post(
        _role_path("unblock"),
        {"task_id": task_id, "reason": reason, "restore": restore},
    )


def declare_coverage(task_id: str, criteria: StrList) -> dict[str, Any]:
    """PM: stamp parent acceptance criteria onto an existing child (task_id).

    Use when a completed subtask already implements a parent AC but was
    delegated without `covers_parent_criteria` (e.g. it's a replacement for a
    cancelled sibling) — the roll-up gate (submit_up/submit_root) keeps
    demanding coverage otherwise. `criteria` are the parent's acceptance
    criteria, by id or exact text (copy them straight out of the gate's
    rejection listing). Returns evidence.remaining_uncovered_parent_acs so you
    know if submit_up will now pass.

    Root-owned mode: pass YOUR OWN root/coordination task_id (not a child)
    to declare criteria only you can satisfy — PR ops in your own branch
    namespace, closing a contributor PR, a root-level merge. These are
    satisfied by your own machinery at submit/supersede time; never push
    them into a cell's acceptance_criteria, a cell cannot act on them.
    """
    return _post(
        _role_path("declare_coverage"),
        {"task_id": task_id, "criteria": criteria},
    )


def complete(task_id: str, notes: str) -> dict[str, Any]:
    """PM: complete a task. Cell PM auto-merges PR; Main PM opens PR + escalates."""
    return _post(_role_path("complete"), {"task_id": task_id, "notes": notes})


def request_changes(
    task_id: str,
    issues: StrList | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """PM: reject the merge review with structured findings → needs_revision.

    Use for an AC/scope violation caught at awaiting_pm_review — never
    i_am_blocked/escalate, which have no revision routing. findings: the
    structured revision-findings ledger entry (same shape as fail_review /
    pr_fail). issues (plain strings) is still accepted this release but
    deprecated. At least one of the two is required.
    """
    return _post(
        _role_path("request_changes"),
        {"task_id": task_id, "issues": issues or [], "findings": findings or []},
    )


def escalate_up(task_id: str, reason: str) -> dict[str, Any]:
    """PM/Doc/Dev: escalate to your role's escalation target."""
    return _post(_role_path("escalate_up"), {"task_id": task_id, "reason": reason})


# ---------- Board + Auditor verbs ----------
# Board (PO + Head Marketing) + Main PM share: escalate_to_ceo
# Auditor uses triage (already registered above) for read-only anomaly surfacing.


def escalate_to_ceo(task_id: str, reason: str) -> dict[str, Any]:
    """Board / Main PM: escalate a strategic task to CEO for final approval."""
    return _post(_role_path("escalate_to_ceo"), {"task_id": task_id, "reason": reason})


def waive_finding(finding_id: str, note: str) -> dict[str, Any]:
    """Auditor: waive one minor/nit review finding by id with a required note.

    Blocker/major findings must be fixed, never waived. The finding id is the
    ``[F-<id8>]`` prefix shown in task notes / triage. No task status changes.
    """
    return _post(
        _role_path("waive_finding"),
        {"finding_id": finding_id, "note": note},
    )


# ---------- Cell PM + Main PM extras ----------
# i_will_plan, delegate, submit_up, give_me_work — restore the pre-Phase-4
# PM lifecycle so PMs can drive parent tasks instead of stalling.


def i_will_plan(
    task_id: str,
    plan: str,
    approach: str = "",
    sub_tasks: list[dict[str, str]] | None = None,
    technical_considerations: StrList | None = None,
    risks: list[dict[str, str]] | None = None,
    open_questions: list[dict[str, str | bool]] | None = None,
) -> dict[str, Any]:
    """PM: claim+start a pending parent task with a structured plan.

    Args:
        task_id: UUID of the task you are planning.
        plan: One-paragraph narrative (the agent-facing summary).
        approach: 2-4 sentences describing the high-level approach for the
            Plan tab. Required for non-trivial tasks; empty string is allowed
            but produces an unpopulated Plan view.
        sub_tasks: Decomposition of this task into sub-units, each
            ``{"title": "...", "description": "..."}``. The gateway assigns
            stable ids + order server-side. Populates the Plan tab's
            Sub-Tasks section. Pre-gateway parity.
        technical_considerations: Bullet list of architectural / library /
            constraint notes. Each item is a single string.
        risks: List of {"risk": "...", "mitigation": "..."} entries.
        open_questions: List of {"question": "...", "answered": false} entries.
    """
    return _post(
        _role_path("i_will_plan"),
        {
            "task_id": task_id,
            "plan": plan,
            "approach": approach,
            "sub_tasks": sub_tasks or [],
            "technical_considerations": technical_considerations or [],
            "risks": risks or [],
            "open_questions": open_questions or [],
        },
    )


def delegate(
    parent_task_id: str,
    title: str,
    description: str,
    assigned_to: str,
    team: str,
    task_type: str,
    nature: str,
    acceptance_criteria: StrList,
    estimated_complexity: str = "medium",
    covers_parent_criteria: StrList | None = None,
    intends_to_touch: StrList | None = None,
    adds_migration: bool = False,
    touches_shared: bool = False,
    depends_on: StrList | None = None,
) -> dict[str, Any]:
    """PM: create a subtask of parent_task_id.

    Args:
        parent_task_id: UUID of the parent task.
        title: Short imperative title.
        description: Multi-paragraph description with context (>=20 chars).
        assigned_to: Agent slug receiving the task (e.g. "be-dev-1").
        team: One of "backend" | "frontend" | "ux_ui" | "board" | "main_pm".
        task_type: One of "code" | "documentation" | "research" | "planning"
            | "design" | "administrative".
        nature: One of "technical" | "non_technical".
        acceptance_criteria: Non-empty list of verifiable outcome strings.
        estimated_complexity: One of "low" | "medium" | "high". Default "medium".
        covers_parent_criteria: The parent task's acceptance-criterion ids (or
            exact text) this subtask is responsible for. REQUIRED — non-empty
            and resolvable — whenever the parent carries acceptance criteria;
            delegate rejects a child that maps to none of them or to a ref
            that matches no real criterion, naming the parent's actual
            criteria in the rejection. A single delegate need not cover every
            criterion (a wave may leave some for a later delegate — see the
            response's evidence.parent_ac_coverage), but every child that IS
            delegated must map to something real.
        intends_to_touch: Collision surface — file paths/globs this subtask
            will modify. REQUIRED for task_type="code": the sibling collision
            DAG can only sequence what is declared.
        adds_migration: True if the subtask adds a DB migration (migration
            adders are chained serially).
        touches_shared: True if the subtask edits a shared surface.
        depends_on: Task UUIDs this subtask must wait for — wired verbatim as
            dependency edges (use for ordering the surface rules would miss).
    """
    return _post(
        _role_path("delegate"),
        {
            "parent_task_id": parent_task_id,
            "title": title,
            "description": description,
            "assigned_to": assigned_to,
            "team": team,
            "task_type": task_type,
            "nature": nature,
            "acceptance_criteria": acceptance_criteria,
            "estimated_complexity": estimated_complexity,
            "covers_parent_criteria": covers_parent_criteria,
            "intends_to_touch": intends_to_touch,
            "adds_migration": adds_migration,
            "touches_shared": touches_shared,
            "depends_on": depends_on,
        },
    )


def submit_up(
    task_id: str,
    notes: str,
    resolved_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Cell PM: bubble a finished cell-scope task up to the Main PM.

    resolved_findings: required when the root has open pr_gate/pm/ceo-origin
    revision-ledger findings (from a prior pr_fail/request_changes/ceo_reject)
    — one entry per finding you addressed: {finding_id, commit?, note?}. Same
    shape as ``i_am_done``'s ``resolved_findings``.
    """
    return _post(
        _role_path("submit_up"),
        {
            "task_id": task_id,
            "notes": notes,
            "resolved_findings": resolved_findings or [],
        },
    )


def submit_root(
    task_id: str,
    notes: str,
    resolved_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Main PM: open the root→master PR and enter the in-path PR-review gate.

    resolved_findings: same shape as ``submit_up``'s — required when the root
    has open pr_gate/pm/ceo-origin revision-ledger findings.
    """
    return _post(
        _role_path("submit_root"),
        {
            "task_id": task_id,
            "notes": notes,
            "resolved_findings": resolved_findings or [],
        },
    )


def claim_gate_review(task_id: str) -> dict[str, Any]:
    """PR reviewer: claim an assembled-PR review task. Returns the diff inline."""
    return _post(_role_path("claim_gate_review"), {"task_id": task_id})


def pr_pass(task_id: str, notes: str) -> dict[str, Any]:
    """PR reviewer: pass the assembled PR (→ awaiting_pm_review)."""
    return _post(_role_path("pr_pass"), {"task_id": task_id, "notes": notes})


def pr_fail(
    task_id: str,
    issues: StrList | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """PR reviewer: fail the assembled PR with structured findings → needs_revision.

    findings: the structured revision-findings ledger entry (same shape as
    fail_review). issues (plain strings) is still accepted this release but
    deprecated. At least one of the two is required.
    """
    return _post(
        _role_path("pr_fail"),
        {"task_id": task_id, "issues": issues or [], "findings": findings or []},
    )


# ---------- Tool registry ----------
#
# Maps the verb name an agent calls (matches manifest entries and the
# orchestrator's role-scoped API path) to the Python implementation.
# ``pass`` and ``fail`` are reserved keywords, so their Python implementations
# are renamed but registered under the original names.

_TOOLS: dict[str, Any] = {
    # dev
    "give_me_work": give_me_work,
    "i_will_work_on": i_will_work_on,
    "open_pr": open_pr,
    "i_am_done": i_am_done,
    "i_am_blocked": i_am_blocked,
    "unclaim": unclaim,
    "reassign": reassign,
    "resume": resume,
    "sync_branch": sync_branch,
    "i_am_idle": i_am_idle,
    # qa — keys are the public MCP tool names (what agents see and prompts
    # advertise). `pass`/`fail` are Python keywords so the IntentSpec uses
    # `pass_review`/`fail_review` internally; the public-name mapping below
    # bridges the two so the manifest's IntentSpec entries register as
    # `mcp__roboco-flow__pass` / `mcp__roboco-flow__fail`.
    "claim_review": claim_review,
    "pass": pass_review,
    "fail": fail_review,
    # pr reviewer (inbound external/fork PRs)
    "claim_pr_review": claim_pr_review,
    "post_pr_review": post_pr_review,
    # pr reviewer (in-path assembled-PR gate)
    "claim_gate_review": claim_gate_review,
    "pr_pass": pr_pass,
    "pr_fail": pr_fail,
    # doc
    "claim_doc_task": claim_doc_task,
    "i_documented": i_documented,
    # pm
    "triage": triage,
    "triage_all": triage_all,
    "unblock": unblock,
    "complete": complete,
    "request_changes": request_changes,
    "escalate_up": escalate_up,
    "i_will_plan": i_will_plan,
    "delegate": delegate,
    "submit_up": submit_up,
    "submit_root": submit_root,
    "declare_coverage": declare_coverage,
    # board / main pm
    "escalate_to_ceo": escalate_to_ceo,
    # auditor
    "waive_finding": waive_finding,
}


# IntentSpec verb names → MCP public tool names. The IntentSpec layer uses
# Python-friendly identifiers (no reserved keywords); the MCP layer exposes
# the user-facing verb name. Dogfooding surfaced this gap: the manifest carried
# `pass_review`/`fail_review` (IntentSpec names) but flow_server only had
# `pass`/`fail` keys, so QA's tools were silently dropped at registration.
_INTENT_TO_PUBLIC: dict[str, str] = {
    "pass_review": "pass",
    "fail_review": "fail",
}


def _load_manifest_flow_tools() -> list[str] | None:
    """Read the spawn manifest and return its ``flow_tools`` list.

    Returns ``None`` when the manifest is missing or unreadable so callers can
    fall back to registering the full tool set. Never raises.
    """
    manifest_path = Path(
        os.environ.get("ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"),
    )
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "flow_server: cannot read manifest",
            path=str(manifest_path),
            error=str(exc),
        )
        return None
    flow_tools = manifest.get("flow_tools")
    if not isinstance(flow_tools, list):
        log.warning(
            "flow_server: manifest missing flow_tools list",
            path=str(manifest_path),
        )
        return None
    return [str(verb) for verb in flow_tools]


def _register_tools() -> list[str]:
    """Register MCP tools according to the manifest. Fails loud if absent.

    The manifest is the role-authoritative tool list. Falling back to
    all-verbs registration (the previous behaviour) caused PMs to see
    developer/QA verbs and call them at wrong URLs (404s). We now refuse
    to start without the manifest — unless ``ROBOCO_ALLOW_FULL_TOOLSET`` is
    set, a dev/test escape hatch that registers the full tool set instead of
    raising so the server modules import without a hand-written manifest
    (#162). Default-off so production behaviour is unchanged.

    Returns the list of verb names actually registered.
    """
    allowed = _load_manifest_flow_tools()
    if allowed is None:
        manifest_path = os.environ.get(
            "ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"
        )
        if os.environ.get("ROBOCO_ALLOW_FULL_TOOLSET"):
            log.warning(
                "flow_server: manifest missing — ROBOCO_ALLOW_FULL_TOOLSET set,"
                " registering the full tool set (dev/test only)",
                role=AGENT_ROLE,
                path=manifest_path,
            )
            allowed = list(_TOOLS.keys())
        else:
            msg = (
                f"flow_server: manifest unavailable at {manifest_path};"
                f" refusing to register all-verbs fallback (would let"
                f" {AGENT_ROLE!r} call off-role verbs at wrong URLs)."
                f" Check that the orchestrator wrote the manifest to its"
                f" /app/manifests/ directory and that the agent container"
                f" has the bind-mount."
            )
            log.error(
                "flow_server: manifest missing", role=AGENT_ROLE, path=manifest_path
            )
            raise RuntimeError(msg)
    public = [_INTENT_TO_PUBLIC.get(verb, verb) for verb in allowed]
    unknown = [verb for verb in public if verb not in _TOOLS]
    if unknown:
        log.warning(
            "flow_server: manifest references unimplemented verbs",
            role=AGENT_ROLE,
            missing=sorted(unknown),
        )
    names = [verb for verb in public if verb in _TOOLS]

    for verb in names:
        mcp.tool(name=verb)(_TOOLS[verb])
    log.info(
        "flow_server: registered tools",
        role=AGENT_ROLE,
        tools=sorted(names),
    )
    return names


_REGISTERED_TOOLS = _register_tools()


if __name__ == "__main__":
    mcp.run()
