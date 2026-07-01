"""RoboCo HTTP security layer — fastapi-guard 7.2.0 / guard-core 3.3.0.

A ``SecurityMiddleware`` + per-route decorator layer, gated by
``settings.guard_enabled`` (default off). Importing this module is always safe:
the middleware is mounted and enforcement happens ONLY when the flag is on
(``create_app`` calls :func:`apply_guard`). ``guard_deco`` is a module singleton
— route files decorate with ``@guard_deco.<verb>``; the decorators only take
effect once the middleware is mounted, so decorating is a harmless no-op while
the flag is off (the guard-core-api pattern).

Cloud-host-ready but env-driven: ``enforce_https`` follows
``ROBOCO_ENVIRONMENT``, and a personal NAS deploy stays relaxed via
``ROBOCO_GUARD_FAIL_SECURE=false`` + ``ROBOCO_ENVIRONMENT=development``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from guard import SecurityConfig, SecurityDecorator, SecurityMiddleware
from guard.adapters import StarletteGuardResponse
from guard.lifespan import make_lifespan
from guard_core.models import BehaviorRuleConfig, ThreatBanConfig
from starlette.responses import Response as _StarletteResponse

from roboco.config import settings
from roboco.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from guard_core.protocols.request_protocol import GuardRequest
    from guard_core.protocols.response_protocol import GuardResponse

logger = get_logger(__name__)

# Body bytes scanned by the custom validators — enough to catch injected
# preambles without reading unbounded payloads.
_MAX_SCAN_BYTES = 16384
_BLOCK_BODY = '{"detail":"request blocked by security policy"}'

# --------------------------------------------------------------------------
# Custom content validators (threats guard's signature WAF cannot cover).
# Attach per-route with @guard_deco.custom_validation(<validator>); each has the
# guard hook signature (GuardRequest -> GuardResponse | None). A generic 400 is
# returned so a probe cannot learn which rule fired.
# --------------------------------------------------------------------------

_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+"
        r"(?:instructions?|prompts?|context)",
        r"disregard\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|rules)",
        r"you\s+are\s+now\s+(?:a|an|the|dan|jailbroken|in\s+developer\s+mode)",
        r"(?:reveal|show|print|repeat|expose)\s+(?:your|the)\s+(?:system\s+prompt"
        r"|hidden\s+instructions?|initial\s+prompt)",
        r"(?:pretend|act|roleplay|simulate)\s+(?:you\s+are|as\s+if|to\s+be)",
        r"do\s+not\s+(?:follow|obey|respect)\s+(?:your|the)\s+"
        r"(?:instructions?|rules|guidelines)",
        r"(?:override|bypass|circumvent|disable|turn\s+off)\s+"
        r"(?:your\s+)?(?:safety|guard|filter|restriction|guardrail)",
        r"<\|(?:im_start|im_end|system|user|assistant)\|>",
        r"\[/?INST\]|\[\[SYSTEM\]\]",
    )
)

_SECRET_EXFIL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"sk-ant-[a-z0-9\-_]{15,}",
        r"ghp_[a-z0-9]{30,}",
        r"github_pat_[a-z0-9_]{30,}",
        r"xai-[a-z0-9]{20,}",
        r"postgres(?:ql)?://[^\s:]+:[^\s@]{4,}@",
        r"redis://:[^\s@]{4,}@",
        r"(?:roboco_encryption_key|roboco_agent_auth_secret|fernet[_-]?key)"
        r"\s*[=:]\s*\S{10,}",
        r"(?:reveal|show|print|leak|exfiltrate|send\s+me)\s+(?:your|the|all)\s+"
        r"(?:api[_\s-]?keys?|tokens?|secrets?|credentials?|passwords?|"
        r"encryption\s+keys?)",
    )
)

# SSRF: bodies/params aiming a fetch at roboco-internal or cloud-metadata hosts.
_INTERNAL_HOST_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"https?://(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\])",
        r"https?://(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)",
        r"https?://169\.254\.169\.254",  # cloud instance-metadata endpoint
        r"https?://[a-z0-9.\-]*(?:roboco|ollama|postgres|redis|orchestrator)"
        r"[a-z0-9.\-]*(?::\d+)?/",
        r"https?://[a-z0-9.\-]+\.(?:internal|local|svc|cluster\.local)\b",
    )
)


def _block() -> GuardResponse:
    """A generic 400 block response (leaks no rule detail)."""
    return StarletteGuardResponse(
        _StarletteResponse(
            content=_BLOCK_BODY, status_code=400, media_type="application/json"
        )
    )


async def _scan_body(request: GuardRequest) -> str:
    try:
        return (await request.body()).decode("utf-8", errors="ignore")[:_MAX_SCAN_BYTES]
    except Exception:
        return ""


async def prompt_injection_validator(request: GuardRequest) -> GuardResponse | None:
    """Block prompt-injection / role-override phrasing in free-text bodies.

    Attach to human/agent free-text ingress (intake + secretary chat, task
    descriptions, agent note/say). Not for code/structured bodies.
    """
    body = await _scan_body(request)
    if body and any(p.search(body) for p in _PROMPT_INJECTION_PATTERNS):
        return _block()
    return None


async def secret_exfil_validator(request: GuardRequest) -> GuardResponse | None:
    """Block bodies carrying literal credential strings or exfil requests.

    Do NOT attach to the provider-key routes (they legitimately receive keys).
    """
    body = await _scan_body(request)
    if body and any(p.search(body) for p in _SECRET_EXFIL_PATTERNS):
        return _block()
    return None


async def internal_ssrf_validator(request: GuardRequest) -> GuardResponse | None:
    """Block research/fetch bodies aimed at roboco-internal or metadata hosts."""
    body = await _scan_body(request)
    if body and any(p.search(body) for p in _INTERNAL_HOST_PATTERNS):
        return _block()
    return None


# --------------------------------------------------------------------------
# SecurityConfig
# --------------------------------------------------------------------------

# Tracing/observability headers that must never trip the signature WAF.
_TRACING_HEADERS = {
    "x-correlation-id",
    "x-request-id",
    "x-trace-id",
    "traceparent",
    "tracestate",
    "baggage",
    "sentry-trace",
}

# Paths the middleware must never touch: WS upgrades, health, API docs, the
# A2A well-known discovery, and static.
_EXCLUDE_PATHS = [
    "/ws",
    "/health",
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/.well-known",
    "/static",
]

_SECURITY_HEADERS: dict[str, Any] = {
    "enabled": True,
    "hsts": {"max_age": 31536000, "include_subdomains": True, "preload": False},
    "csp": {
        "default-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "connect-src": ["'self'"],
    },
    "frame_options": "SAMEORIGIN",
    "content_type_options": "nosniff",
    "referrer_policy": "strict-origin-when-cross-origin",
    "permissions_policy": "geolocation=(), microphone=(), camera=()",
    "custom": None,
}

_THREAT_BAN_CONFIG: dict[str, ThreatBanConfig] = {
    "sqli": ThreatBanConfig(threshold=3, duration=7200),
    "cmd_injection": ThreatBanConfig(threshold=1, duration=86400),
    "ssrf": ThreatBanConfig(threshold=2, duration=7200),
    "file_inclusion": ThreatBanConfig(threshold=2, duration=7200),
}

# WAF false-positive calibration. RoboCo is an internal, authenticated API whose
# request bodies legitimately carry code, SQL, diffs, file paths, HTML and URLs
# (task specs, agent notes/commits, RAG queries, git bodies, chat). The signature
# WAF (SQLi/XSS/path-traversal/URL) false-positives on ~half of that traffic when
# active, so these free-text TOP-LEVEL body fields are excluded from scanning.
# guard scans each non-excluded top-level field's whole stringified value, so the
# free-form container fields (plan/risks/findings/section/payload/…) are excluded
# too — otherwise their nested prose is stringified and scanned. The actual roboco
# threats (prompt-injection, secret-exfil, internal SSRF) are caught by the custom
# validators, which run independently of this exclusion; the WAF still scans every
# non-excluded (id/enum/slug/branch) field. Matching is case-insensitive and
# top-level only. Field set derived from the real request models; passive-mode NAS
# logs calibrate any stragglers.
_WAF_FREETEXT_BODY_FIELDS: set[str] = {
    "ac_verdicts",
    "acceptance_criteria",
    "actual",
    "approach",
    "auditor_notes",
    "base_url",
    "body",
    "chosen",
    "code",
    "cons",
    "consequences",
    "content",
    "context",
    "decision",
    "description",
    "details",
    "dev_notes",
    "doc_notes",
    "done",
    "draft",
    "drafts",
    "error_message",
    "expected",
    "file",
    "file_path",
    "files",
    "findings",
    "initial_message",
    "initial_prompt",
    "intends_to_touch",
    "issues",
    "justification",
    "message",
    "mitigation",
    "next",
    "next_steps",
    "notes",
    "notes_structured",
    "open_questions",
    "options",
    "payload",
    "plan",
    "pr_reviewer_notes",
    "problem",
    "procedure",
    "proposed_solution",
    "pros",
    "qa_notes",
    "query",
    "question",
    "quick_context",
    "rationale",
    "reason",
    "remaining_work",
    "required_changes",
    "resolution",
    "risks",
    "scope",
    "section",
    "solution",
    "sources",
    "state_summary",
    "steps",
    "sub_tasks",
    "technical_considerations",
    "text",
    "title",
    "topic",
    "url",
    "value",
    "what_done",
    "what_learned",
    "what_needed",
    "what_struggled",
    "where_to_look",
}

# Log-only 404-scan sweep detection (calibration signal, never bans).
_BEHAVIOR_RULES: list[BehaviorRuleConfig] = [
    BehaviorRuleConfig(
        rule_type="return_pattern",
        threshold=30,
        window=300,
        pattern="404",
        action="log",
        correlate_with_detection=True,
    )
]


def _redis_url() -> str:
    return f"redis://{settings.redis_host}:{settings.redis_port}/0"


def _emergency_whitelist() -> list[str]:
    extra = [
        x.strip() for x in settings.guard_emergency_whitelist.split(",") if x.strip()
    ]
    return ["127.0.0.1", "::1", *extra]


def _agent_kwargs() -> dict[str, Any]:
    """guard-agent telemetry kwargs — empty unless telemetry is armed."""
    if not settings.guard_telemetry_enabled:
        return {}
    return {
        "enable_agent": True,
        "agent_api_key": settings.guard_agent_api_key or None,
        "agent_project_id": settings.guard_project_id or None,
        "agent_endpoint": "https://api.guard-core.com",
        "agent_enable_events": True,
        "agent_enable_metrics": True,
        "agent_strict": False,
        "enable_dynamic_rules": True,
    }


def build_security_config() -> SecurityConfig:
    """Assemble roboco's global guard config from settings (behind nginx)."""
    return SecurityConfig(
        # Real client IP behind nginx (single hop) + the docker bridge ranges.
        trusted_proxies=[
            "127.0.0.1",
            "::1",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ],
        trusted_proxy_depth=1,
        trust_x_forwarded_proto=True,
        # Calibrate-then-enforce.
        passive_mode=settings.guard_passive_mode,
        # Distributed state (redis is always in the compose stack).
        enable_redis=True,
        redis_url=_redis_url(),
        redis_prefix="roboco:guard:",
        # Baseline throttling; per-endpoint overrides tighten sensitive routes.
        rate_limit=120,
        rate_limit_window=60,
        auto_ban_duration=300,
        # Env-driven: enforced only in production (localhost/NAS has no TLS here).
        enforce_https=(settings.environment == "production"),
        fail_secure=settings.guard_fail_secure,
        # Flip-on kill switch.
        emergency_mode=settings.guard_emergency,
        emergency_whitelist=_emergency_whitelist(),
        exclude_paths=_EXCLUDE_PATHS,
        security_headers=_SECURITY_HEADERS,
        threat_ban_config=_THREAT_BAN_CONFIG,
        global_behavior_rules=_BEHAVIOR_RULES,
        # Signature WAF on, but calibrated: free-text bodies excluded (below).
        enable_penetration_detection=True,
        excluded_detection_headers=_TRACING_HEADERS,
        excluded_detection_body_fields=_WAF_FREETEXT_BODY_FIELDS,
        # roboco keeps its own CORSMiddleware (single origin via nginx).
        enable_cors=False,
        **_agent_kwargs(),
    )


# Built once at import. Construction is pure (no I/O); the middleware only
# connects to redis / loads geo data when mounted by apply_guard.
security_config = build_security_config()
guard_deco = SecurityDecorator(security_config)


def apply_guard(app: FastAPI) -> None:
    """Mount SecurityMiddleware + register the decorator, only when armed.

    No-op when guard is disabled, so the request path is unchanged. Best-effort:
    a mount failure logs and leaves the app running rather than crashing boot.
    """
    if not settings.guard_enabled:
        return
    try:
        app.add_middleware(SecurityMiddleware, config=security_config)
        app.state.guard_decorator = guard_deco
        logger.info(
            "fastapi-guard armed",
            passive=settings.guard_passive_mode,
            fail_secure=settings.guard_fail_secure,
            telemetry=settings.guard_telemetry_enabled,
        )
    except Exception as exc:  # pragma: no cover - defensive boot guard
        logger.error("failed to arm fastapi-guard", error=str(exc))


def guarded_lifespan(existing: Any) -> Any:
    """Wrap roboco's existing lifespan with guard's, only when armed.

    guard's lifespan drives the middleware's async init/teardown (redis, geo,
    the agent). When disabled, the existing lifespan is returned untouched.
    """
    if not settings.guard_enabled:
        return existing
    return make_lifespan(existing_lifespan=existing)
