"""roboco-do MCP server — smart-wrapped content tools.

Forwards to /api/v1/do/* on the orchestrator. Tools are role-scoped at *spawn*
time: the orchestrator writes ``do_tools`` into the per-agent manifest and we
register only those names on this server. The orchestrator's API is not
role-scoped here (any allowed role can call commit/note/dm/notify/evidence),
so the path is fixed (no role segment). Per-tool role gates (e.g., notify
restricting to PMs/Board) live inside the gateway verbs.

If the manifest is missing or unreadable (local test runs without the bind
mount) the full registry is registered as a failsafe and a warning is logged.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import structlog
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import get_agent_team

ORCHESTRATOR_URL = os.environ.get(
    "ROBOCO_ORCHESTRATOR_URL",
    "http://roboco-orchestrator:8000",
)
# Per-agent SDK loopback for the per-verb circuit breaker.
SDK_URL = os.environ.get("ROBOCO_SDK_URL", "http://localhost:9000")
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]

_TIMEOUT = 30
# commit() stages + `git commit`s in-process (no push — push is the flow
# verb open_pr, already covered by flow_server's per-verb timeout), bounded
# server-side by git_commit_timeout_seconds (default 180s: a large changeset,
# e.g. the panel's hundreds of files, can legitimately take that long). The
# shared _TIMEOUT above is tuned for fast content-tool calls (note/dm/
# evidence) and would give up first — client must outlast the server op.
_COMMIT_TIMEOUT = 190
# request_sandbox provisions inline; ensure_sandbox now always provisions the
# project's FULL opted-in set on first call (kills the superset/teardown
# race — see ensure_sandbox's docstring), so an all-three-cold first request
# is the norm, not the rare case. Worst case: 3 x 300s cold pulls
# (SandboxProvisioner._DOCKER_PULL_TIMEOUT_SECONDS) + readiness (~135s) ~=
# 1035s — images are pre-pulled at startup in practice, so cold pulls here
# are the exception, but the timeout must cover the worst case anyway.
_SANDBOX_TIMEOUT = 1080
# Tight timeout for SDK loopback — local sidecar; gateway path must not stall.
_SDK_TIMEOUT = 2.0
# FastAPI's default missing-route status. Every /api/v1/do/* route returns
# 200 with an Envelope (including not_found rejections), so a 404 from the
# orchestrator is always a manifest-registered tool whose HTTP route is
# missing — synthesize an invalid_state Envelope for it.
_MISSING_ROUTE_STATUS = 404

# Envelope error kinds that count toward the per-verb circuit breaker.
# Mirrors flow_server._CIRCUIT_REJECTION_KINDS — agent_sdk.server is the
# authoritative side; the same set must be applied here so the do-server
# (content tools) gets the same protection as flow-server (intent verbs).
# Dogfooding surfaced the gap: `note(scope='decision')` looped 8 times
# returning `incomplete_input` with no breaker.
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
# counted kind without a map update. Mirrors flow_server.
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


def _remediate_for_kind(kind: str, verb: str) -> str:
    """A directed recovery hint for a synthesized Envelope kind.

    Mirrors flow_server: the orchestrator's exception handlers return a dict
    `error` with no `remediate`; without a hint the agent has no directed next
    action and flails until the breaker trips.
    """
    if kind == "not_found":
        return (
            "the call targeted a resource that does not exist; re-fetch state"
            " and retry on a current id; do not retry the same id."
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
    return (
        f"service error on {verb} — re-fetch state and re-issue; call"
        f" i_am_blocked or i_am_idle if it persists; do not retry blindly."
    )


def _normalize_exception_envelope(
    payload: dict[str, Any], path: str
) -> dict[str, Any] | None:
    """Synthesize an Envelope-wire-format dict from an exception-handler body.

    Mirrors flow_server (#232): FastAPI's exception handlers return ``error``
    as a DICT (``{code, message, details?}``) or a bare ``detail`` list (422) —
    neither is the Envelope wire format the agent trusts (string ``error`` +
    ``message`` + ``remediate`` + ``missing``). This lifts the body into a real
    Envelope so the agent gets a directed remediate instead of flailing until
    the breaker trips. Returns None for a real Envelope (string ``error`` or
    success) so those pass through unchanged.
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
        verb = _verb_from_path(path)
        return {
            "error": "incomplete_input",
            "message": f"the {verb} call was rejected as incomplete input",
            "remediate": _remediate_for_kind("incomplete_input", verb),
            "missing": [],
            "detail": payload.get("detail"),
        }
    return None


mcp = FastMCP("roboco-do")
log = structlog.get_logger()


def _build_headers() -> dict[str, str]:
    """Build per-call headers including a fresh X-Correlation-ID.

    Mirrors flow_server: each MCP call mints its own correlation id so the
    orchestrator's middleware can bind it to structlog and the audit row,
    and the envelope echoes it back to the agent.
    """
    # X-Agent-Token + X-Agent-Team must travel with every do verb or the
    # API's ROBOCO_AGENT_AUTH_REQUIRED gate 401s — mirrors flow_server and
    # the ApiClient header path used by the other MCP servers.
    headers = {
        "X-Agent-ID": AGENT_ID,
        "X-Agent-Role": AGENT_ROLE,
        "X-Correlation-ID": str(uuid.uuid4()),
    }
    team = get_agent_team(AGENT_ID)
    if team:
        headers["X-Agent-Team"] = team
    token = os.environ.get("ROBOCO_AGENT_TOKEN")
    # See flow_server._build_headers: forwarding the "UNSIGNED" sentinel 401s
    # even in dev mode; omit so a missing token is accepted in dev.
    if token and token != "UNSIGNED":
        headers["X-Agent-Token"] = token
    return headers


def _post(
    path: str, body: dict[str, Any], *, timeout: float = _TIMEOUT
) -> dict[str, Any]:
    """POST a request to the orchestrator and return the JSON envelope.

    Mirrors flow_server._post: surfaces the orchestrator's envelope on
    both 2xx and 4xx so the agent always sees ``remediate``. Only
    fabricates a transport_error envelope when the body is unparseable.

    Rejection envelopes (error in _CIRCUIT_REJECTION_KINDS) are forwarded
    to the local SDK's /verb/attempted so the per-verb circuit breaker
    can track them. If the SDK reports open, the original rejection is
    REPLACED with circuit_open. Dogfooding surfaced the gap: do-server had
    no breaker and `note(scope='decision')` looped 8 times returning
    incomplete_input.

    ``timeout`` overrides the default for a slow tool (e.g. commit's
    _COMMIT_TIMEOUT) — must always outlast that tool's server-side budget.
    """
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{ORCHESTRATOR_URL}{path}",
            headers=_build_headers(),
            json=body,
        )
        # A 404 here usually means a manifest-registered content tool has no
        # matching route on the orchestrator: every /api/v1/do/* route returns
        # 200 with an Envelope (including not_found rejections), so FastAPI's
        # default 404 body (``{"detail": "Not Found"}``) is a missing route —
        # synthesize an ``invalid_state`` Envelope so the breaker counts it and
        # the agent gets a wiring-gap remediation hint. A 404 carrying a real
        # Envelope (``error`` field) is surfaced as-is; a 404 with a
        # *descriptive* ``detail`` (not the bare default) is a real resource
        # not_found, surfaced as ``not_found`` (#61). Mirrors flow_server._post.
        if response.status_code == _MISSING_ROUTE_STATUS:
            try:
                body_404 = response.json()
            except (ValueError, json.JSONDecodeError):
                body_404 = None
            if isinstance(body_404, dict) and "error" in body_404:
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
                        f" state and retry on a current id; do not retry the"
                        f" same id."
                    ),
                    "missing": [],
                }
            else:
                verb = _verb_from_path(path)
                payload_404 = {
                    "error": "invalid_state",
                    "message": (
                        f"content tool '{verb}' has no route on the"
                        f" orchestrator (path {path})"
                    ),
                    "remediate": (
                        f"the {verb} tool is advertised in your manifest but"
                        f" its HTTP route is missing — this is a server-side"
                        f" wiring gap. Call"
                        f" i_am_blocked(reason='tool {verb} 404s: no route')"
                        f" or i_am_idle() so the operator can fix the route;"
                        f" do not retry."
                    ),
                    "missing": [],
                }
            return _record_and_check_circuit(path, body, payload_404)
        try:
            payload: dict[str, Any] = response.json()
        except (ValueError, json.JSONDecodeError):
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
    # Outside the orchestrator client so the SDK call is its own connection.
    # A non-404 JSON body that is NOT a real Envelope (dict `error` from an
    # exception handler, or a 422 `detail` list) is normalized to the Envelope
    # wire format so the agent gets a string `error` kind + remediate (#232).
    # Mirrors flow_server; the synthesized Envelope still flows through the
    # breaker below — its string kind is in the counted set.
    normalized = _normalize_exception_envelope(payload, path)
    if normalized is not None:
        payload = normalized
    return _record_and_check_circuit(path, body, payload)


def _verb_from_path(path: str) -> str:
    """Extract the verb name from a do-server path.

    ``/api/v1/do/<verb>`` → ``<verb>``. Returns the original path if it
    doesn't match the expected shape (defensive — breaker falls open
    downstream when the verb is unrecognized).
    """
    return path.rsplit("/", 1)[-1]


def _record_and_check_circuit(
    path: str,
    body: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Forward a content-tool rejection to the SDK breaker; maybe substitute.

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
        log.warning(
            "do_server: SDK /verb/attempted unreachable; breaker bypassed",
            verb=verb,
            task_id=task_id,
            error=str(exc),
        )
        return payload

    if status.get("open") and isinstance(status.get("circuit_envelope"), dict):
        # Copy so the SDK's envelope dict is not mutated in place. Nest the
        # original fixable rejection as ``inner`` so its kind/message/remediate
        # survive the substitution — the circuit_open envelope only says the
        # breaker tripped, not WHY the verb failed (#60). Mirrors flow_server.
        circuit_env: dict[str, Any] = dict(status["circuit_envelope"])
        circuit_env["inner"] = payload
        # Lift task_id/correlation_id from the original rejection to the top
        # level (the SDK's envelope omits them) so the agent's envelope contract
        # and ops audit-join still work — not just nested in `inner` (#359).
        circuit_env["task_id"] = payload.get("task_id")
        circuit_env["correlation_id"] = payload.get("correlation_id")
        log.info(
            "do_server: circuit_open substituted for rejection",
            verb=verb,
            task_id=task_id,
            attempts=status.get("attempts"),
            limit=status.get("limit"),
        )
        return circuit_env
    return payload


def commit(message: str, files: list[str] | None = None) -> dict[str, Any]:
    """Make a git commit. [task-id] prefix auto-applied. Validates message."""
    return _post(
        "/api/v1/do/commit",
        {"message": message, "files": files},
        timeout=_COMMIT_TIMEOUT,
    )


def note(
    text: str,
    scope: str = "note",
    task_id: str | None = None,
    title: str | None = None,
    context: str = "",
    options: list[dict[str, str]] | dict[str, str] | None = None,
    chosen: str = "",
    rationale: str = "",
    consequences: list[str] | str | None = None,
    what_done: str = "",
    what_learned: str = "",
    what_struggled: str = "",
    next_steps: list[str] | str | None = None,
    section: dict[str, Any] | None = None,
    done: str = "",
    next: str = "",
    where_to_look: list[str] | None = None,
) -> dict[str, Any]:
    """Write a journal entry, or (scope='handoff') your note SECTION.

    scope in note|decision|reflect|learning|struggle|handoff.

    ``text`` is always the short summary (one paragraph max). For ``decision``
    and ``reflect`` scopes the structured fields are RECOMMENDED — fill what
    you can. The note is always recorded; missing narrative fields default to
    a visible placeholder rather than being rejected.

    - decision: ``context`` (situation), ``options`` (list of dicts
      ``{name, pros, cons}`` — a single dict is accepted), ``chosen`` (which
      option), ``rationale`` (why), ``consequences`` (list of strings — what
      this commits us to; a single string is accepted)
    - reflect: ``what_done`` (literal output), ``what_learned`` (new info),
      ``what_struggled`` (where you got stuck), ``next_steps`` (list of
      follow-up strings; a single string is accepted)

    List-typed fields (``options``, ``consequences``, ``next_steps``) tolerate
    a lone value — pass either a list or a single item.

    Other journal scopes (note / learning / struggle) just need ``text``.

    scope='handoff' writes your dedicated SECTION (dev_notes / quick_context /
    auditor_notes) instead of the journal. For a PM/coordinator RESUMPTION
    section pass the TOP-LEVEL fields ``done`` (what's been done) and ``next``
    (the immediate next step) — these are the required fields and they show
    up here as discrete string params; ``where_to_look`` is optional. (Do NOT
    pass an empty ``section={}``; the ``section`` dict is the free-form path
    for other content types — developer ``{summary, changes}``, auditor
    ``{summary, severity}``.) Or just ``text`` for a developer summary.
    """
    return _post(
        "/api/v1/do/note",
        {
            "text": text,
            "scope": scope,
            "task_id": task_id,
            "title": title,
            "context": context,
            "options": options,
            "chosen": chosen,
            "rationale": rationale,
            "consequences": consequences,
            "what_done": what_done,
            "what_learned": what_learned,
            "what_struggled": what_struggled,
            "next_steps": next_steps,
            "section": section,
            "done": done,
            "next": next,
            "where_to_look": where_to_look,
        },
    )


def pitch(
    title: str,
    slug: str,
    problem: str,
    proposed_solution: str,
    target_cells: list[str],
) -> dict[str, Any]:
    """Board: propose a product. Queues for the CEO's approval, then auto-provisions.

    Args:
        title: Short product name.
        slug: URL-safe id (lowercase letters, digits, hyphens), e.g. 'widget-store'.
        problem: The problem this product solves.
        proposed_solution: How you propose to solve it.
        target_cells: Cells that should build it — any of 'backend', 'frontend',
            'ux_ui'.
    """
    return _post(
        "/api/v1/do/pitch",
        {
            "title": title,
            "slug": slug,
            "problem": problem,
            "proposed_solution": proposed_solution,
            "target_cells": target_cells,
        },
    )


def propose_roadmap(cycle_goal: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Product Owner: propose a themed roadmap cycle (3-7 item drafts).

    Call this exactly ONCE per exploration cycle, after exploring the
    charter, recent releases, metrics, and each project's state. The CEO
    reviews and approves/rejects each item individually; approved items land
    in the backlog (nothing auto-starts).

    Args:
        cycle_goal: One-line theme tying the cycle's items together.
        items: 3-7 drafts, each a dict with: title, description,
            acceptance_criteria (list[str]), project_slug, team
            ('backend'|'frontend'|'ux_ui'), priority (int, default 2),
            rationale (why this, why now).
    """
    return _post(
        "/api/v1/do/propose_roadmap",
        {"cycle_goal": cycle_goal, "items": items},
    )


def propose_feature_spotlight(
    feature_slug: str = "",
    feature_title: str = "",
    body: str = "",
    wants_video: bool = False,
    video_script: str = "",
    skip: bool = False,
    skip_reason: str = "",
) -> dict[str, Any]:
    """Head of Marketing: draft ONE feature-spotlight marketing post, or skip.

    Call this exactly ONCE per exploration cycle, after investigating the
    CHANGELOG, feature-flags ledger, docs/map, charter, and KB to pick a real,
    under-publicized capability. The draft is held in the X post queue for the
    CEO to edit/approve — nothing auto-posts.

    If nothing shipped is genuinely worth spotlighting this cycle, pass
    skip=True with a substantive skip_reason instead of forcing a weak post —
    a forced spotlight is worse than skipping. A skip still completes the
    exploration task (no draft materialized, no feature marked seen) and
    counts as this cycle's activity for the engine's cadence, so it won't just
    re-fire daily into the same quiet period.

    Args:
        feature_slug: Stable slug identifying the feature (the dedup key).
            Ignored when skip=True.
        feature_title: Short human title of the feature. Ignored when
            skip=True.
        body: The tweet text (plain, <=280 chars, no invented facts). Ignored
            when skip=True.
        wants_video: Also request a companion video (held separately for CEO
            approval, when the video engine is armed for spotlights).
        video_script: Optional script for that video; falls back to the
            feature title/body when omitted.
        skip: True to declare "nothing worth spotlighting this cycle" instead
            of authoring a draft.
        skip_reason: Required (non-empty, >=8 chars) explanation when
            skip=True.
    """
    return _post(
        "/api/v1/do/propose_feature_spotlight",
        {
            "feature_slug": feature_slug,
            "feature_title": feature_title,
            "body": body,
            "wants_video": wants_video,
            "video_script": video_script,
            "skip": skip,
            "skip_reason": skip_reason,
        },
    )


def propose_video(
    composition_id: str,
    x_caption: str,
    tiktok_caption: str,
    platforms: list[str],
    input_props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """UX/UI dev: propose your video's composition + captions. Metadata only —
    this does NOT render (rendering happens later, off this path).

    Before authoring, read motion/README.md for the design bar and
    motion/kit/README.md for the panel-demo kit — build in the panel-demo
    register on motion/kit/ (extend motion/compositions/panel-demo/) rather
    than starting from scratch or shipping a text card, unless the occasion
    has no product visual to show.

    Call this exactly ONCE per authoring task, after building the HyperFrames
    composition in motion/compositions/<id>/. Then commit + open_pr to send
    it through the normal PR-review gate.

    Args:
        composition_id: The HyperFrames composition id (the directory name
            under motion/compositions/, e.g. 'release-announcement').
        x_caption: X post text for this clip (<=280 chars).
        tiktok_caption: TikTok caption for this clip (<=2200 chars).
        platforms: Target platforms for this clip — any of 'x', 'tiktok'.
        input_props: Optional props passed into the composition at render time.
    """
    return _post(
        "/api/v1/do/propose_video",
        {
            "composition_id": composition_id,
            "x_caption": x_caption,
            "tiktok_caption": tiktok_caption,
            "platforms": platforms,
            "input_props": input_props,
        },
    )


def dm(
    recipient: str,
    text: str,
    task_id: str | None = None,
    skill: str | None = None,
) -> dict[str, Any]:
    """A2A message. Auto-creates conversation; auto-resolves skill if needed.

    Args:
        recipient: Target agent slug (e.g. `be-pm`, `be-dev-1`, `ceo`).
        text: Message body.
        task_id: Optional; auto-filled from your active task if omitted.
        skill: Optional skill slug to scope the conversation.
    """
    return _post(
        "/api/v1/do/dm",
        {"recipient": recipient, "text": text, "task_id": task_id, "skill": skill},
    )


def notify(
    target: str,
    text: str,
    priority: str = "normal",
    task_id: str | None = None,
) -> dict[str, Any]:
    """Send a formal ack-required notification (PMs and Board only).

    Distinct from dm (informal A2A): notify creates
    a notification the recipient must acknowledge. priority in
    normal|high|urgent. task_id auto-injected from active task when omitted.
    """
    return _post(
        "/api/v1/do/notify",
        {
            "target": target,
            "text": text,
            "priority": priority,
            "task_id": task_id,
        },
    )


def evidence(task_id: str) -> dict[str, Any]:
    """Inspect a task's PR diff, commits, files. Fetches dev branch into workspace."""
    return _post("/api/v1/do/evidence", {"task_id": task_id})


def request_sandbox(
    services: list[str] | None = None,
    extensions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Provision (or reuse) a throwaway sandbox DB/Redis/Mongo for YOUR active task.

    On-demand — nothing is provisioned at spawn. Omit ``services`` to get the
    project's whole opted-in set; requesting a service the project didn't opt
    into is rejected with the allowed set named. ``extensions`` (e.g.
    ``{"postgres": ["vector", "postgis"]}``) is an additive per-call override
    unioned with the project's standing ``sandbox_extensions`` and bounded by
    the opted set + the allowlist — a name outside the allowlist (e.g.
    ``plpython3u``) is rejected with the allowed set named. Creds come back in
    ``evidence``, one entry per service: ``{host, port, user, password,
    database, env: {ROBOCO_TEST_*: value}, available_extensions?: [...]}`` —
    export the ``env`` values verbatim for gate tooling that reads them. The
    whole opted-in set is provisioned on first call, so calling this again for
    any subset or superset of it is a cheap no-op (same creds, no
    re-provisioning); a project that never opted into sandbox services will
    reject this.
    """
    return _post(
        "/api/v1/do/request_sandbox",
        {"services": services, "extensions": extensions},
        timeout=_SANDBOX_TIMEOUT,
    )


def draft_playbook(
    title: str,
    problem: str,
    procedure: str,
    tags: list[str] | None = None,
    source_task_id: str | None = None,
) -> dict[str, Any]:
    """Draft a reusable playbook (when-to-use + procedure) for the company KB.

    A learning records "this happened"; a playbook records "here is how to do X".
    Delivery roles draft; the Auditor approves. tags aid retrieval;
    source_task_id links the task that inspired it.
    """
    return _post(
        "/api/v1/do/draft_playbook",
        {
            "title": title,
            "problem": problem,
            "procedure": procedure,
            "tags": tags or [],
            "source_task_id": source_task_id,
        },
    )


def approve_playbook(playbook_id: str) -> dict[str, Any]:
    """Auditor only: approve a draft playbook so it is indexed + auto-suggested."""
    return _post("/api/v1/do/approve_playbook", {"playbook_id": playbook_id})


def reject_playbook(playbook_id: str, reason: str) -> dict[str, Any]:
    """Auditor only: reject a playbook (archive it) with a reason."""
    return _post(
        "/api/v1/do/reject_playbook",
        {"playbook_id": playbook_id, "reason": reason},
    )


def archive_playbook(playbook_id: str) -> dict[str, Any]:
    """Auditor only: archive (retire) an existing playbook."""
    return _post("/api/v1/do/archive_playbook", {"playbook_id": playbook_id})


def curate_vault(task_id: str, narrative: str) -> dict[str, Any]:
    """Auditor only: write a root task-tree's Obsidian-vault narrative section
    (what happened, decisions, rework story). No-op error if the vault flag
    is off."""
    return _post(
        "/api/v1/do/curate_vault",
        {"task_id": task_id, "narrative": narrative},
    )


# ---------- Wave 1 — pre-gateway parity ----------


def progress(
    task_id: str,
    message: str,
    plan_step: str | None = None,
    percentage: int | None = None,
) -> dict[str, Any]:
    """Record progress on YOUR active task — the % is computed for you.

    Your plan's steps (sub_tasks) ARE the progress checklist. As you
    FINISH each step, call this with ``plan_step`` set to that step's id
    or its 1-based order; it is marked complete and the percentage is
    derived from completed/total — you do NOT set the percentage and
    cannot game it.

    You may ALSO post a narrative update WITHOUT ``plan_step`` for an
    important mid-step milestone (it documents the "why" and carries the
    current derived %). Keep these to meaningful moments — not every
    tool call.

    Args:
        task_id: UUID of the task you're working on.
        message: One-paragraph summary of what just landed.
        plan_step: The sub_task id (or its 1-based order) you just
            COMPLETED. Omit for a narrative-only milestone update.
        percentage: Ignored when the task has a plan checklist (the norm).
            Only used as a fallback for tasks with no sub_tasks.

    Populates the panel's Progress tab. Use in addition to ``commit`` —
    commits are git refs; progress maps to your plan.
    """
    body: dict[str, Any] = {"task_id": task_id, "message": message}
    if plan_step is not None:
        body["plan_step"] = plan_step
    if percentage is not None:
        body["percentage"] = percentage
    return _post("/api/v1/do/progress", body)


def notify_list(
    unread_only: bool = True,
    pending_ack_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Read your notification inbox.

    Call this when ``i_am_idle()`` soft-blocks you with an "unread A2A or
    @mentions" message — list, read each one with ``notify_get``, ack with
    ``notify_ack``, then idle again.
    """
    return _post(
        "/api/v1/do/notify_list",
        {
            "unread_only": unread_only,
            "pending_ack_only": pending_ack_only,
            "limit": limit,
        },
    )


def notify_get(notification_id: str) -> dict[str, Any]:
    """Read one notification (marks it as read)."""
    return _post(
        "/api/v1/do/notify_get",
        {"notification_id": notification_id},
    )


def notify_ack(notification_id: str) -> dict[str, Any]:
    """Acknowledge a notification you've handled.

    The gateway tracks who has acked which notification — required for
    ``requires_ack=true`` notifications before ``i_am_idle`` will let you
    exit cleanly.
    """
    return _post(
        "/api/v1/do/notify_ack",
        {"notification_id": notification_id},
    )


def pr_update(
    task_id: str,
    title: str | None = None,
    body: str | None = None,
    reviewers: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing PR's title, body, and/or requested reviewers.

    Use after ``open_pr`` when you need to correct the title/body or
    request a reviewer. At least one of ``title``, ``body``, or
    ``reviewers`` must be provided — passing all three None is rejected
    with ``invalid_state`` before any GitHub call.

    Args:
        task_id: UUID of the task whose PR you're editing.
        title: Replacement PR title (omit to leave unchanged).
        body: Replacement PR body markdown (omit to leave unchanged).
        reviewers: List of agent slugs to request as reviewers (e.g.
            ``["be-dev-2", "be-qa"]``). The gateway maps slugs to GitHub
            usernames where the project records that mapping, otherwise
            the slugs are forwarded as-is.

    Authorization: caller must be the task's assignee OR a PM on the
    task's team (cell_pm same-team, or main_pm cross-team). Anyone else
    receives ``not_authorized``.
    """
    return _post(
        "/api/v1/do/pr_update",
        {
            "task_id": task_id,
            "title": title,
            "body": body,
            "reviewers": reviewers,
        },
    )


def read_messages() -> dict[str, Any]:
    """Mark all your unread A2A direct messages as read.

    Call this when ``i_am_idle()`` soft-blocks you on unread A2A — it clears
    your direct-message inbox so you can idle. Notifications are separate: use
    ``notify_list`` / ``notify_get`` / ``notify_ack`` for those.
    """
    return _post("/api/v1/do/read_messages", {})


def read_a2a() -> dict[str, Any]:
    """Read the bodies of your unread A2A direct messages (and mark them read).

    Unlike ``read_messages`` (which only clears the unread counter), this
    returns what other agents actually said to you so you can act on it.
    """
    return _post("/api/v1/do/read_a2a", {})


# ---------- Tool registry ----------
#
# Maps the tool name an agent calls (matches manifest entries and the
# orchestrator's API path) to the Python implementation.

_TOOLS: dict[str, Any] = {
    "commit": commit,
    "note": note,
    "pitch": pitch,
    "propose_roadmap": propose_roadmap,
    "propose_feature_spotlight": propose_feature_spotlight,
    "propose_video": propose_video,
    "dm": dm,
    "notify": notify,
    "evidence": evidence,
    "request_sandbox": request_sandbox,
    "progress": progress,
    "notify_list": notify_list,
    "notify_get": notify_get,
    "notify_ack": notify_ack,
    "pr_update": pr_update,
    "read_messages": read_messages,
    "read_a2a": read_a2a,
    "draft_playbook": draft_playbook,
    "approve_playbook": approve_playbook,
    "reject_playbook": reject_playbook,
    "archive_playbook": archive_playbook,
    "curate_vault": curate_vault,
}


def _load_manifest_do_tools() -> list[str] | None:
    """Read the spawn manifest and return its ``do_tools`` list.

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
            "do_server: cannot read manifest",
            path=str(manifest_path),
            error=str(exc),
        )
        return None
    do_tools = manifest.get("do_tools")
    if not isinstance(do_tools, list):
        log.warning(
            "do_server: manifest missing do_tools list",
            path=str(manifest_path),
        )
        return None
    return [str(verb) for verb in do_tools]


def _register_tools() -> list[str]:
    """Register MCP tools according to the manifest. Fails loud if absent.

    Mirrors flow_server's behaviour: refuse to start if the manifest is
    missing rather than silently exposing the full do-tool set (which
    includes ``commit`` — the role-gate would reject it server-side, but
    the agent shouldn't see it on its tool palette in the first place).
    ``ROBOCO_ALLOW_FULL_TOOLSET`` is a dev/test escape hatch that registers
    the full tool set instead of raising (#162); default-off.

    Returns the list of tool names actually registered.
    """
    allowed = _load_manifest_do_tools()
    if allowed is None:
        manifest_path = os.environ.get(
            "ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"
        )
        if os.environ.get("ROBOCO_ALLOW_FULL_TOOLSET"):
            log.warning(
                "do_server: manifest missing — ROBOCO_ALLOW_FULL_TOOLSET set,"
                " registering the full tool set (dev/test only)",
                role=AGENT_ROLE,
                path=manifest_path,
            )
            allowed = list(_TOOLS.keys())
        else:
            msg = (
                f"do_server: manifest unavailable at {manifest_path};"
                f" refusing to register all-tools fallback for role"
                f" {AGENT_ROLE!r}. Check the orchestrator manifest mount."
            )
            log.error(
                "do_server: manifest missing", role=AGENT_ROLE, path=manifest_path
            )
            raise RuntimeError(msg)
    unknown = [verb for verb in allowed if verb not in _TOOLS]
    if unknown:
        log.warning(
            "do_server: manifest references unknown do tools",
            role=AGENT_ROLE,
            missing=sorted(unknown),
        )
    names = [verb for verb in allowed if verb in _TOOLS]

    for verb in names:
        mcp.tool(name=verb)(_TOOLS[verb])
    log.info(
        "do_server: registered tools",
        role=AGENT_ROLE,
        tools=sorted(names),
    )
    return names


_REGISTERED_TOOLS = _register_tools()


if __name__ == "__main__":
    mcp.run()
