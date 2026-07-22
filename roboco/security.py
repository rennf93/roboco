"""RoboCo HTTP security layer — fastapi-guard 7.3.0 / guard-core 3.5.0.

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
from ipaddress import ip_address, ip_network
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
        # "your" is required: injections address the model ("bypass your
        # safety"); neutral engineering prose about the guard subsystem
        # ("disable the security guard for testing") must not block.
        r"(?:override|bypass|circumvent|disable|turn\s+off)\s+"
        r"your\s+(?:safety|guard|filter|restriction|guardrail)",
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
        # The value must look like a real key (b64-ish, 20+ chars), so the
        # documented placeholder lines (`ROBOCO_ENCRYPTION_KEY=<your-fernet-
        # key>` in CLAUDE.md/.env.example) and `${VAR}` interpolations pass.
        r"(?:roboco_encryption_key|roboco_agent_auth_secret|fernet[_-]?key)"
        r"\s*[=:]\s*[A-Za-z0-9+/_\-]{20,}={0,2}",
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
    descriptions, agent note/dm). Not for code/structured bodies.
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
    # Scanner / decoy-path auto-ban (Surface N). A bot probing scanner
    # fingerprints (/.git/config, /wp-login.php, /phpmyadmin, …) is detected on
    # the URL-path scan; these thresholds turn repeated probes into a ban. Only
    # /api|/ws paths reach the app behind nginx — the classic root probes are
    # dropped at the edge (444) in docker/nginx.conf. Requires redis (24h ban >
    # in-memory cap) and only bans in active mode; passive logs the recon hit.
    "recon": ThreatBanConfig(threshold=5, duration=86400),
    "sensitive_file": ThreatBanConfig(threshold=3, duration=86400),
    "cms_probing": ThreatBanConfig(threshold=3, duration=86400),
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
    "criteria_verified",
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


# Loopback + docker's bridge pool: the internal agent mesh ONLY. Agents reach
# the orchestrator DIRECTLY on the docker bridge (172.x →
# roboco-orchestrator:8000, no nginx hop), HMAC-authenticated — the guard's
# WAF/IP-ban/rate-limit is for the EXTERNAL attack surface arriving through
# nginx, not for authenticated internal traffic. Without this the guard
# IP-banned agent containers the moment it went active (2026-07-20): one
# journal/note body tripping a signature banned the whole container's IP,
# wedging every subsequent verb (dm, i_am_idle, ...).
#
# Deliberately NOT 10.0.0.0/8 or 192.168.0.0/16: those also cover any real LAN
# client hitting nginx, not just the docker mesh. With trusted_proxy_depth=1,
# nginx forwards a LAN client's own real IP via XFF (extract_client_ip peels
# it correctly) — so a genuine 192.168.x.x browser would skip WAF/ban/rate-
# limit right alongside actual agent traffic. 172.16.0.0/12 is docker's
# default bridge address-pool range: neither compose file pins an explicit
# `subnet:` for roboco_default/roboco_data, so this has to cover whatever
# docker allocates them.
#
# The variable-depth proxy chain (guard sees a fixed depth) is handled by
# ClientIpResolutionMiddleware below: it resolves the real tailnet/LAN client
# behind a NAMED set of local proxy hops in X-Forwarded-For (see
# `_build_trusted_hop_networks`) and stamps the guard's request.state.client_ip
# cache, so Tailscale-Serve/host-proxied traffic resolves to the real
# tailnet/LAN client instead of loopback and no longer rides this exemption.
_INTERNAL_NETWORKS = [
    "127.0.0.1",
    "::1",
    "172.16.0.0/12",
]

# Docker's default bridge address-pool range — the WHOLE pool, used only by
# the broader connecting-peer gate below (never the operator-scoped hop set).
_DOCKER_BRIDGE_POOL = "172.16.0.0/12"

# Connecting-peer gate for ClientIpResolutionMiddleware: the DIRECT TCP peer
# must be one of these before the middleware consults X-Forwarded-For at
# all. Deliberately the WHOLE docker bridge pool, unlike the hop-peel set
# below — nginx itself connects from an arbitrary bridge-allocated address
# (neither compose file pins a `subnet:`), so it must keep presenting XFF
# regardless of which address it lands on. A bare connecting-peer match
# alone stamps nothing: the XFF entries themselves are still checked against
# the narrower, operator-scoped `_TRUSTED_HOP_NETWORKS`.
_CONNECTING_PEER_NETWORKS = ("127.0.0.1/32", "::1/128", _DOCKER_BRIDGE_POOL)


def _build_trusted_hop_networks() -> tuple[str, ...]:
    """XFF hop-peel set: loopback ALWAYS, plus operator-named chain peers.

    A peeled entry is a RECORDED PROXY HOP inside X-Forwarded-For (e.g. the
    docker bridge gateway nginx sees when Tailscale Serve terminates on the
    host) — not the connecting socket peer (`_CONNECTING_PEER_NETWORKS`
    above is that separate, deliberately broader check). Default empty
    (``guard_trusted_chain_peers``): only a loopback rightmost hop ever
    peels, so a same-bridge container can no longer get its own arbitrary
    172.x address treated as a trusted hop just by being on the docker
    bridge — closing the residual where a forged tailnet-CGNAT XFF prefix
    resolved behind an unnamed 172.x rightmost entry. An operator running
    Tailscale Serve behind a docker gateway sets
    ``ROBOCO_GUARD_TRUSTED_CHAIN_PEERS`` to that gateway's exact address
    (e.g. 172.18.0.1) to keep the chain resolving.

    Peers are parsed with ``ip_address`` — SINGLE addresses only, never a
    CIDR range — and stored as their own /32 (or /128). A subnet-sized entry
    (docker's typical bridge allocation is an entirely plausible copy-paste)
    would readmit every sibling container's real address into the hop set,
    fully reopening the pre-fix forge hole; ``ip_network(strict=True)``
    would also silently accept a host-bits typo like "172.18.0.5/24" the
    operator meant as a plain address. Any entry that isn't a plain IP —
    CIDRs included — is skipped with a warning rather than crashing config
    load.
    """
    networks = ["127.0.0.1/32", "::1/128"]
    for raw in settings.guard_trusted_chain_peers.split(","):
        peer = raw.strip()
        if not peer:
            continue
        try:
            addr = ip_address(peer)
        except (ValueError, TypeError):
            logger.warning(
                "skipping invalid guard_trusted_chain_peers entry: chain "
                "peers are single IP addresses; CIDR ranges are rejected "
                "because a range readmits sibling containers",
                peer=peer,
            )
            continue
        networks.append(f"{addr}/{addr.max_prefixlen}")
    return tuple(networks)


# Built once at config load, mirroring security_config below — pure, no I/O.
_TRUSTED_HOP_NETWORKS = _build_trusted_hop_networks()

# Tailscale assigns every tailnet node an address in the CGNAT range. The
# resolver stamps ONLY a candidate in this range: it makes the fix exactly as
# wide as the broken case (host-proxied tailnet traffic resolving to a
# whitelisted hop IP) and no wider — for every other chain shape the stamp
# abstains and the guard's own depth-1 logic decides. Residual (accepted):
# a compromise of a CONFIGURED chain peer itself (e.g. the docker bridge
# gateway) could still relay a forged prefix behind it — an unconfigured
# peer, or any other bridge-allocated address a real container actually
# has, cannot: it is never in `_TRUSTED_HOP_NETWORKS` by default.
_TAILNET_NETWORK = "100.64.0.0/10"

# The fixable shape needs at least [client, hop] — one real entry behind one
# recorded proxy hop.
_MIN_CHAIN_ENTRIES = 2

# Rightmost gateway IPs already warned about (see
# `_warn_unconfigured_tailnet_gateway_once`) — bounded by the tiny number of
# real docker bridge gateways an operator ever runs behind.
_WARNED_UNCONFIGURED_GATEWAYS: set[str] = set()


def _in_networks(ip: str, networks: tuple[str, ...]) -> bool:
    try:
        addr = ip_address(ip.strip())
        return any(addr in ip_network(net) for net in networks)
    except (ValueError, TypeError):
        return False


def _is_trusted_hop(ip: str) -> bool:
    return _in_networks(ip, _TRUSTED_HOP_NETWORKS)


def _is_trusted_connecting_peer(ip: str) -> bool:
    return _in_networks(ip, _CONNECTING_PEER_NETWORKS)


def _warn_unconfigured_tailnet_gateway_once(rightmost: str, candidate: str) -> None:
    """Surface the ONE regression this fix leaves genuinely silent: with no
    chain peers configured, a host-proxied tailnet chain behind a real
    docker bridge gateway silently reverts /tg to the pre-fix inert-WAF
    state — zero signal otherwise. Only fires while NOTHING is configured
    (once any peer is set the operator has already addressed this); logs
    once per distinct gateway IP per process. Never logs the full XFF —
    only the rightmost IP, the one an operator needs to act.
    """
    if settings.guard_trusted_chain_peers.strip():
        return
    if not _in_networks(rightmost, (_DOCKER_BRIDGE_POOL,)):
        return
    if not _in_networks(candidate, (_TAILNET_NETWORK,)):
        return
    if rightmost in _WARNED_UNCONFIGURED_GATEWAYS:
        return
    _WARNED_UNCONFIGURED_GATEWAYS.add(rightmost)
    logger.warning(
        "host-proxied tailnet chain detected but no trusted chain peer "
        "configured — this traffic resolves to a whitelisted bridge IP; "
        f"set ROBOCO_GUARD_TRUSTED_CHAIN_PEERS to your docker bridge "
        f"gateway ({rightmost})"
    )


def resolve_forwarded_client_ip(forwarded_for: str) -> str | None:
    """Resolve the tailnet client behind NAMED host-proxy hops; None = abstain.

    fastapi-guard peels a FIXED number of XFF hops (trusted_proxy_depth=1:
    the rightmost entry, which nginx itself recorded). That is correct for
    every chain except one: host-proxied tailnet traffic (Tailscale Serve →
    nginx) arrives as ``[tailnet-client, <loopback-or-configured-gateway>]``,
    so depth-1 resolves it to a whitelisted hop IP and WAF/ban/rate-limit go
    inert for the whole /tg surface (the documented ceiling).

    This resolver fixes exactly that shape and nothing else: peel hops from
    the right that are loopback or an operator-named chain peer
    (``guard_trusted_chain_peers``, see `_build_trusted_hop_networks`); the
    remaining candidate is returned ONLY if at least one hop was peeled and
    the candidate is in the tailnet CGNAT range. Every other shape — direct
    LAN client, an agent container relaying via nginx with an UNNAMED 172.x
    address (even carrying a forged public-IP or tailnet-CGNAT prefix),
    all-hops operator traffic, malformed entries — returns None, leaving the
    guard's own depth-1 resolution in charge. With no chain peers configured
    (the default), only a loopback rightmost hop ever peels — when that
    abstain is otherwise shaped exactly like a host-proxied tailnet chain
    behind a real bridge gateway, it is logged once per gateway IP (see
    `_warn_unconfigured_tailnet_gateway_once`) so the regression isn't
    silent.
    """
    entries = [e.strip() for e in forwarded_for.split(",") if e.strip()]
    if len(entries) < _MIN_CHAIN_ENTRIES:
        return None
    idx = len(entries) - 1
    while idx >= 0 and _is_trusted_hop(entries[idx]):
        idx -= 1
    if idx == len(entries) - 1:
        _warn_unconfigured_tailnet_gateway_once(entries[-1], entries[-2])
        return None  # no hop peeled: baseline handles it
    if idx < 0:
        return None  # all hops: baseline handles it
    candidate = entries[idx]
    if not _in_networks(candidate, (_TAILNET_NETWORK,)):
        return None
    return candidate


class ClientIpResolutionMiddleware:
    """Stamp the guard's ``request.state.client_ip`` cache with the real
    client resolved across the variable-depth local proxy chain.

    Pure ASGI, mounted OUTSIDE SecurityMiddleware (added after it, so it runs
    first): guard_core's ``extract_client_ip`` returns a pre-cached
    ``state.client_ip`` verbatim, which is the supported seam for custom
    resolution. Only honors XFF when the CONNECTING peer is itself trusted to
    present it (nginx's docker-bridge address / loopback —
    `_CONNECTING_PEER_NETWORKS`, deliberately broader than and independent of
    the operator-scoped hop-peel set the XFF entries are matched against) —
    a directly-connected client's forged XFF is never consulted here (the
    guard's own depth-1 logic keeps handling that class unchanged).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            client = scope.get("client")
            connecting_ip = client[0] if client else None
            if connecting_ip and _is_trusted_connecting_peer(connecting_ip):
                # First occurrence on a repeated header, matching Starlette's
                # Headers.get — so this layer and the guard's own fallback
                # read the SAME header value.
                forwarded_for = next(
                    (
                        v.decode("latin-1")
                        for k, v in scope.get("headers", [])
                        if k.decode("latin-1").lower() == "x-forwarded-for"
                    ),
                    None,
                )
                if forwarded_for:
                    resolved = resolve_forwarded_client_ip(forwarded_for)
                    if resolved:
                        scope.setdefault("state", {})["client_ip"] = resolved
        await self.app(scope, receive, send)


def _guard_whitelist() -> list[str]:
    extra = [
        x.strip() for x in settings.guard_emergency_whitelist.split(",") if x.strip()
    ]
    # guard-core's whitelist is EXCLUSIVE once non-empty (guard_core.utils.
    # is_ip_allowed: a set whitelist replaces, not supplements, the
    # blacklist check — any non-member IP is refused outright, not merely
    # unexempted). The resolver above now honestly resolves a host-proxied
    # tailnet client's real 100.64.0.0/10 address instead of a loopback/
    # bridge hop, so that address must be a whitelist member or ip_security
    # rejects it — this blocked the CEO's own tailnet IP live (2026-07-22).
    # Coupling allowlist membership with scrutiny-exemption here is
    # deliberate for the tailnet specifically: Tailscale is an authenticated
    # overlay that gates device membership before a packet ever reaches this
    # host, so an already-authenticated tailnet peer skipping WAF/ban/rate-
    # limit is the correct posture, not a gap. The resolver's real-IP
    # stamping still buys correct attribution in logs/telemetry, and any
    # FUTURE non-tailnet public exposure still gets full scrutiny — only
    # 100.64.0.0/10 is exempted here, nothing wider.
    return [*_INTERNAL_NETWORKS, _TAILNET_NETWORK, *extra]


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
        # Always off: nginx is the single entry point, so the app only ever
        # sees proxy-HTTP — TLS (and any http->https redirect) is nginx's
        # layer. Keying this off environment==production blocked the NAS's
        # entire request stream the moment the guard went active (2026-07-19).
        enforce_https=False,
        fail_secure=settings.guard_fail_secure,
        # Flip-on kill switch.
        emergency_mode=settings.guard_emergency,
        emergency_whitelist=_emergency_whitelist(),
        # The internal agent mesh skips all checks (WAF + IP-ban) — see
        # _INTERNAL_NETWORKS. External traffic via nginx carries the real
        # client IP (XFF, depth 1) and is still fully scrutinized.
        whitelist=_guard_whitelist(),
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
        # Added AFTER SecurityMiddleware so it wraps it (runs first) and can
        # stamp state.client_ip before the guard's extraction reads it.
        app.add_middleware(ClientIpResolutionMiddleware)
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
