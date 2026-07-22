"""
RoboCo Configuration

Environment-based settings using Pydantic Settings.
"""

import asyncio
import importlib
import ipaddress
import os
import posixpath
from collections.abc import Callable
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Environment variables are prefixed with ROBOCO_ by default.
    """

    model_config = SettingsConfigDict(
        env_prefix="ROBOCO_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==========================================================================
    # Application
    # ==========================================================================
    app_version: str = "0.26.0"
    debug: bool = False
    environment: str = Field(
        default="development", pattern="^(development|staging|production)$"
    )

    # ==========================================================================
    # API Server
    # ==========================================================================
    host: str = Field(default="127.0.0.1", description="Use 0.0.0.0 for containers")
    port: int = 8000
    uvicorn_loop: Literal["asyncio", "uvloop"] = Field(
        default="asyncio",
        description=(
            "Event loop for the production orchestrator's API server and the "
            "e2e smoke harness's in-thread uvicorn (env ROBOCO_UVICORN_LOOP). "
            "Default 'asyncio': this API is a control plane, not a high-QPS "
            "service — deterministic beats fast, and a uvloop+asyncpg "
            "segfault class (uvloop 0.22 + asyncpg 0.31 + Python 3.13, GitHub "
            "CI) never reproduces on stock asyncio. 'uvloop' opts back in; "
            "uvloop stays an installed dependency either way. See "
            "resolve_uvicorn_loop_factory() for the asyncio.run() call sites "
            "(uvicorn's own Config.loop is only read by Server.run())."
        ),
    )
    api_url: str | None = Field(
        default=None,
        description="Override API URL for containerized agents (e.g., http://roboco-orchestrator:8000)",
    )
    # CORS
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:3000",
            "http://localhost:5173",
        ]
    )
    cors_allow_credentials: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def internal_api_url(self) -> str:
        """
        Internal API base URL for service-to-service communication.

        Uses api_url if set (for containerized agents), otherwise builds from host/port.
        Note: 0.0.0.0 is only valid for binding, not connecting - use 127.0.0.1 instead.
        """
        if self.api_url:
            return f"{self.api_url.rstrip('/')}/api"
        connect_host = "127.0.0.1" if self.host == "0.0.0.0" else self.host  # nosec B104
        return f"http://{connect_host}:{self.port}/api"

    # ==========================================================================
    # Database
    # ==========================================================================
    database_host: str = "localhost"
    database_port: int = 5432
    database_user: str = "roboco"
    database_password: str = "roboco"
    database_name: str = "roboco"
    database_echo: bool = Field(default=False, description="Log SQL queries")
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout: int = Field(default=10, ge=1)
    database_pool_recycle: int = Field(default=1800, ge=60)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url_sync(self) -> str:
        """Sync PostgreSQL connection URL (for Alembic)."""
        return (
            f"postgresql://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    # ==========================================================================
    # Redis
    # ==========================================================================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        """Redis connection URL."""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ==========================================================================
    # RAG (in-house engine with pgvector)
    # ==========================================================================
    rag_persist_dir: str = ".roboco"
    rag_chunk_strategy: str = Field(
        default="fixed",
        pattern="^(fixed|semantic|hierarchical|contextual)$",
        description="Chunking strategy (fixed recommended, semantic loads extra model)",
    )
    rag_chunk_size: int = Field(default=512, ge=100)
    rag_chunk_size_docs: int = Field(
        default=1536, ge=100, description="Chunk size for docs (larger for 8K context)"
    )
    rag_chunk_size_journals: int = Field(
        default=1024, ge=100, description="Chunk size for journals/reflections"
    )
    rag_chunk_overlap: int = Field(default=128, ge=0)
    rag_auto_update_enabled: bool = Field(default=True)
    rag_auto_update_interval: int = Field(
        default=300, ge=60, description="Seconds between auto-updates"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rag_store_url(self) -> str:
        """PostgreSQL connection URL for the in-house vector store."""
        return (
            f"postgres://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    # ==========================================================================
    # AI/LLM Providers
    # ==========================================================================
    anthropic_api_key: str | None = None

    # Default models
    default_embedding_model: str = Field(
        default="qwen3-embedding:0.6b",
        description="Embedding model. Qwen3 Embedding for quality + 32K context.",
    )
    embedding_dimensions: int = Field(
        default=1024,
        description="Embedding dimensions (1024 for qwen3-embedding)",
    )

    # Local LLM for RAG answer synthesis
    local_llm_model: str = Field(
        default="glm-5.2:cloud",
        description="Local LLM for RAG answer synthesis "
        "(non-thinking models are faster)",
    )
    local_llm_base_url: str = Field(
        default="http://roboco-ollama:11434/v1",
        description="Base URL for local LLM (Ollama OpenAI-compat API)",
    )

    @field_validator("local_llm_base_url")
    @classmethod
    def _local_llm_base_url_internal_only(cls, v: str) -> str:
        # The fire-and-forget hot path (distillation, RAG synthesis, X/video
        # drafting) reads this verbatim. Reject non-internal hosts so a one-line
        # env mistake can't route quiet generation through a paid cloud LLM.
        host = (urlparse(v).hostname or "").lower()
        if not host:
            raise ValueError("local_llm_base_url must have a host")
        if host in {"localhost", "127.0.0.1", "::1", "roboco-ollama"}:
            return v
        if host.endswith(".svc.cluster.local"):
            return v
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(
                f"local_llm_base_url host {host!r} is not an internal address"
            ) from None
        if ip.is_private or ip.is_loopback:
            return v
        raise ValueError(f"local_llm_base_url host {host!r} is not an internal address")

    ollama_base_url: str = Field(
        default="http://roboco-ollama:11434",
        description="Base URL for Ollama native API (embeddings, model mgmt)",
    )

    routing_strict: bool = Field(
        default=False,
        description=(
            "Fail-closed model routing: when an agent has a configured "
            "model_assignment whose provider is disabled (or otherwise "
            "unroutable), raise instead of silently downgrading to the "
            "legacy Anthropic path. Off (default) => graceful degradation "
            "with a warning, so a misconfigured provider never stalls a "
            "spawn; the warning still surfaces the bypass so it isn't silent."
        ),
    )

    # ==========================================================================
    # Agent runtime toolchain matching (default-off)
    # ==========================================================================
    # When enabled, an agent's workspace is provisioned with the Python the
    # TARGET project declares (uv resolves requires-python), and a delivery role
    # that cannot execute the suite blocks instead of passing on a source read.
    # When off, provisioning behaves exactly as today (system interpreter).
    toolchain_match_enabled: bool = Field(
        default=False,
        description=(
            "Provision the agent workspace with the target project's Python "
            "(uv resolves requires-python) and block delivery gates when the "
            "suite cannot be executed. Off => today's behavior."
        ),
    )

    overload_break_enabled: bool = Field(
        default=True,
        description=(
            "Park a provider on a persistent server overload (HTTP 529 / 500 / "
            "503 from the model API) the same way a 429 rate limit is parked: "
            "queue that provider's spawns and probe until it recovers, instead "
            "of crash-retrying into the overload. Off => crash-retry behavior."
        ),
    )
    notification_spawn_cooldown_seconds: int = Field(
        default=600,
        description=(
            "Cross-tick cooldown for notification-triggered spawns (escalation/"
            "approval/audit/a2a): one spawn per (agent, notification) per window. "
            "The notification stays pending, so the next window retries if still "
            "unacknowledged. 0 disables the damper (legacy every-tick respawn)."
        ),
    )
    notification_spawn_max_attempts: int = Field(
        default=5,
        ge=0,
        description=(
            "Hard cap on notification-triggered spawns per (agent, notification): "
            "past this many attempts without the notification being acknowledged, "
            "stop re-spawning (the notification-driven analogue of the PM respawn "
            "breaker — these dispatchers carry no task_id so that breaker never "
            "sees them). Prevents one wedged escalation/alert from respawning its "
            "recipient every cooldown window indefinitely. 0 disables the cap."
        ),
    )
    notification_spawn_max_age_seconds: int = Field(
        default=21600,
        ge=0,
        description=(
            "Skip notification-triggered spawns for a notification older than "
            "this (default 6h). A still-pending notification this stale is wedged "
            "or reloaded from before a restart — reviving an agent for it acts on "
            "dead work. Independent of the per-notification expiry and the "
            "terminal-related-task check. 0 disables the staleness gate."
        ),
    )
    notification_ack_ttl_hours: int = Field(
        default=48,
        ge=0,
        description=(
            "Hours until an ack-required notification's expires_at is stamped "
            "at creation. sweep_expired_notifications re-escalates a still-"
            "unacked row past that deadline to the recipient's up-role. "
            "Informational (non-ack-required) notifications never get a "
            "deadline regardless of this setting. 0 disables stamping "
            "(legacy: expires_at stays NULL, notifications never expire)."
        ),
    )
    audit_interval_seconds: int = Field(
        default=21600,
        ge=0,
        description=(
            "Seconds between scheduled auditor sweeps (default 6 hours). The "
            "orchestrator spawns the auditor only when the interval has elapsed "
            "and recent delivery activity exists. 0 disables scheduled sweeps."
        ),
    )
    spawn_preflight_enabled: bool = Field(
        default=False,
        description=(
            "Refuse to spawn a non-human delivery role that isn't in "
            "GATEWAY_ENABLED_ROLES — it could never claim its work and would "
            "respawn on the same task forever; alert the overseer once instead. "
            "Off => legacy behavior (respawn until the strike breaker trips)."
        ),
    )
    gateway_health_enabled: bool = Field(
        default=True,
        description=(
            "Detect a broken-but-alive agent gateway (a corrupted /app venv so no "
            "gateway verb can fire) and kill + respawn the container, instead of "
            "the reaper protecting it forever as a 'live' agent. Off => live "
            "containers are spared on verb-heartbeat liveness alone."
        ),
    )
    gateway_health_grace_seconds: int = Field(
        default=180,
        description=(
            "How long an agent gateway may probe as broken before the reaper "
            "recovers it — tolerates a transient probe miss (the gateway mid-call)."
        ),
    )

    # ==========================================================================
    # Architectural Conventions (per-project placement + house-style standard)
    # ==========================================================================
    # A repo-canonical .roboco/conventions.yml plus the roboco-conventions
    # validator gate i_am_done / pr_pass on block-level placement and hygiene
    # violations. Default-off; every hook (scaffold, ambient injection, baseline
    # constraints, the gates) is inert when off.
    conventions_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the architectural-conventions standard: "
            "auto-scaffold .roboco/conventions.yml, inject the architecture map, "
            "attach baseline constraints, and block gates on violations. Off => "
            "fully inert."
        ),
    )
    possibilities_matrix_enabled: bool = Field(
        default=False,
        description=(
            "Possibilities matrix: when a task's work is already done (commits "
            "+ PR open + all acceptance criteria addressed + no open findings), "
            "let i_am_done submit it for QA in one call, skipping the retroactive "
            "rich-plan, journal tracing, and local quality (CI-green proxy) "
            "gates. Off => the standard i_am_done path is unchanged."
        ),
    )

    # ==========================================================================
    # Web Research (pluggable external search/fetch for Board + PM roles)
    # ==========================================================================
    # Calls go agent -> roboco-search MCP -> /api/research/* -> ResearchService
    # -> provider. The provider key lives ONLY in this server-side process; it
    # is never injected into agent containers, and agents never egress — the
    # provider's own API does. Unset key => graceful NullProvider (empty
    # results, no hard fail).
    research_enabled: bool = Field(
        default=True,
        description=(
            "Master switch for the web-research capability. When false the "
            "roboco-search MCP server is not mounted into any agent container."
        ),
    )
    research_provider: str = Field(
        default="tavily",
        pattern="^(tavily|brave|exa|null)$",
        description=(
            "Web-search provider adapter. 'tavily' (LLM-native cited results "
            "+ extract), 'brave' (independent index; no fetch), 'exa' "
            "(neural search + contents), or 'null' (always-empty stub). "
            "Swapping providers is a config change only."
        ),
    )
    research_api_key: str | None = Field(
        default=None,
        description=(
            "API key for the selected research provider. Server-side only — "
            "never reaches an agent container. Unset => NullProvider."
        ),
    )
    research_max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Hard cap on web_search results per call (top-k clamp).",
    )
    research_fetch_max_chars: int = Field(
        default=20000,
        ge=500,
        description="Hard cap on extracted characters returned by web_fetch.",
    )
    research_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Per-request timeout for outbound provider HTTP calls.",
    )
    research_daily_quota_per_agent: int = Field(
        default=50,
        ge=1,
        description=(
            "Maximum web_search + web_fetch calls per agent per UTC day. "
            "Tracked in Redis; fails open if Redis is unreachable."
        ),
    )

    # ==========================================================================
    # Security
    # ==========================================================================
    encryption_key: str = Field(
        default="",
        description="Fernet encryption key for secrets.",
    )

    # ==========================================================================
    # Cloud auth (FastAPI Users) — DEFAULT OFF
    # ==========================================================================
    # Lets the panel/API be safely exposed beyond localhost without changing the
    # CEO's local no-login flow while off. Off: get_agent_context behaves
    # byte-for-byte as today (header-trust). On: a valid session cookie for the
    # single seeded CEO login authenticates; a spoofed CEO header without a
    # valid session or agent HMAC token is rejected. Not armed by any compose
    # file by default — arm only behind TLS (cookies are secure-only).
    cloud_auth_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for cloud auth. OFF by default; when off, "
            "get_agent_context and the WS panel-token gate behave byte-for-byte "
            "as today. On, no registration router is mounted — exactly one "
            "user, seeded from cloud_auth_email/cloud_auth_password."
        ),
    )
    cloud_auth_email: str | None = Field(
        default=None,
        description="Email for the single seeded CEO login user.",
    )
    cloud_auth_password: str | None = Field(
        default=None,
        description=(
            "Password for the single seeded CEO login user. Hashed at startup "
            "and never stored in plain text."
        ),
    )
    cloud_auth_secret: str | None = Field(
        default=None,
        description=(
            "Session-signing secret for the login cookie's JWT. Required when "
            "cloud_auth_enabled is true (startup fails loud if unset). Generate "
            "with: python -c 'import secrets; print(secrets.token_hex(32))'"
        ),
    )
    cloud_auth_cookie_max_age: int = Field(
        default=2592000,
        ge=60,
        description=(
            "Session cookie lifetime in seconds (default 30 days). Sliding: "
            "the cookie is re-minted only near expiry (see "
            "``cloud_auth_remint_threshold_seconds``), so an active session "
            "never expires — only genuine inactivity past this window logs out."
        ),
    )
    cloud_auth_remint_threshold_seconds: int = Field(
        default=86400,
        ge=60,
        description=(
            "Re-mint the sliding session cookie only when its exp is within "
            "this many seconds of now (default 24h). Outside the window the "
            "cookie is left untouched so a stolen cookie's expiry is fixed "
            "rather than rolling forward with the legitimate user."
        ),
    )
    login_max_attempts: int = Field(
        default=10,
        ge=1,
        description=(
            "Max login attempts per IP within the 60s rolling window before "
            "the cloud-auth login endpoint returns 429."
        ),
    )
    # Telegram Mini App sign-in: validates Telegram's signed WebApp initData
    # and mints the SAME cloud-auth session cookie /api/auth/login issues —
    # zero changes to deps.py/websocket.py, whose cookie gate already accepts
    # it. Security/TLS-coupled like cloud_auth_enabled, so deliberately NOT
    # on the panel's runtime feature-flags card (see FEATURE_FLAGS in
    # roboco/services/settings.py).
    telegram_miniapp_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for Telegram Mini App sign-in "
            "(POST /api/telegram/webapp-auth). OFF by default. Requires "
            "cloud_auth_enabled (startup fails loud if on without it) since "
            "the Mini App mints a cloud-auth session cookie and there is "
            "nothing to mint without it. Env-only — excluded from the panel "
            "feature-flags card, same reasoning as cloud_auth_enabled."
        ),
    )
    telegram_initdata_max_age_seconds: int = Field(
        default=600,
        ge=1,
        description=(
            "Max age (seconds) of a Telegram WebApp initData payload's "
            "auth_date before POST /api/telegram/webapp-auth refuses it as "
            "stale."
        ),
    )
    agent_token_ttl_seconds: int = Field(
        default=604800,
        ge=60,
        description=(
            "Lifetime in seconds of an agent auth token minted at spawn "
            "(default 7 days). Each spawn mints a fresh token with this TTL "
            "so a stolen token is bounded; the static panel token is "
            "unaffected. Refresh happens on every respawn."
        ),
    )

    @model_validator(mode="after")
    def _validate_cloud_auth(self) -> "Settings":
        """Fail loud at startup rather than silently minting unsigned sessions."""
        if self.cloud_auth_enabled and not self.cloud_auth_secret:
            raise ValueError(
                "ROBOCO_CLOUD_AUTH_SECRET is required when "
                "ROBOCO_CLOUD_AUTH_ENABLED=true."
            )
        # nginx injects ROBOCO_PANEL_AGENT_TOKEN as a CEO-signed HMAC header on
        # every /api/ request. Under cloud auth that token is an alternative
        # human-auth tier that bypasses the login cookie — layering both is a
        # public-exposure footgun. Refuse to start; the operator must unset it.
        if (
            self.cloud_auth_enabled
            and os.environ.get("ROBOCO_PANEL_AGENT_TOKEN", "").strip()
        ):
            raise ValueError(
                "ROBOCO_CLOUD_AUTH_ENABLED=true is incompatible with a set "
                "ROBOCO_PANEL_AGENT_TOKEN (nginx CEO-token injection bypasses "
                "the login cookie). Unset ROBOCO_PANEL_AGENT_TOKEN for a "
                "publicly-exposed cloud-auth deploy."
            )
        # The Mini App auth route mints a cloud-auth session cookie — with
        # cloud auth off there is no session mechanism to hand it to.
        if self.telegram_miniapp_enabled and not self.cloud_auth_enabled:
            raise ValueError(
                "ROBOCO_TELEGRAM_MINIAPP_ENABLED=true requires "
                "ROBOCO_CLOUD_AUTH_ENABLED=true (the Mini App mints a "
                "cloud-auth session cookie; there is nothing to mint "
                "without it)."
            )
        return self

    # ==========================================================================
    # GitHub repository provisioning (pitch -> approve -> auto-provision)
    # ==========================================================================
    # The only place that CREATES GitHub repos (vs. clone/branch/PR existing
    # ones). Server-side only; never injected into agent containers. Unset
    # token/org => disabled => the pitch approval path is inert (no repo is
    # created) until the CEO configures it.
    provisioning_enabled: bool = Field(
        default=True,
        description=(
            "Master switch for pitch auto-provisioning. With no token/org set "
            "the capability is inert regardless of this flag."
        ),
    )
    provisioning_token: str = Field(
        default="",
        description=(
            "GitHub PAT used to create repos in the provisioning org "
            "(needs repo + org admin scope). Server-side only."
        ),
    )
    provisioning_org: str = Field(
        default="",
        description="GitHub organization where new repos are provisioned.",
    )
    github_api_base_url: str = Field(
        default="https://api.github.com",
        description="GitHub REST API base URL (override for GitHub Enterprise).",
    )
    provisioning_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Per-request timeout for outbound GitHub provisioning calls.",
    )
    provisioning_repo_private: bool = Field(
        default=True,
        description="Whether provisioned repos are created private.",
    )
    provisioning_provider: Literal["github", "gitlab", "gitea"] = Field(
        default="github",
        description=(
            "Forge that pitch auto-provisioning targets (default 'github', "
            "byte-for-byte unchanged behavior). 'gitlab'/'gitea' additionally "
            "require ROBOCO_PROVISIONING_HOST — without it provisioning stays "
            "disabled exactly like a missing token/org."
        ),
    )
    provisioning_host: str = Field(
        default="",
        description=(
            "Self-hosted forge instance host for gitlab/gitea provisioning "
            "(e.g. 'gitlab.example.com'). Ignored when provisioning_provider "
            "is 'github'."
        ),
    )

    # ==========================================================================
    # Autonomous strategy engine ("engine 2") — DORMANT by default
    # ==========================================================================
    # A separate background loop that watches the company against its standing
    # goals and surfaces drift/idle/stranded work to the CEO (notify-only —
    # never spends or builds). Default OFF: the loop never starts and the
    # existing delivery lifecycle is untouched until the CEO opts in.
    strategy_engine_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the autonomous strategy engine. OFF by default; "
            "when off the background loop does not run at all."
        ),
    )
    strategy_engine_interval_seconds: int = Field(
        default=1800,
        ge=60,
        description="Seconds between strategy-engine assessment passes.",
    )
    strategy_stranded_blocked_minutes: int = Field(
        default=120,
        ge=5,
        description=(
            "A task blocked longer than this is surfaced as stranded "
            "(needs a human decision)."
        ),
    )

    # ==========================================================================
    # External-PR review ("engine 3") — DORMANT by default
    # ==========================================================================
    # An inbound path: a background loop that lists open PRs per active project,
    # flags ones from external/fork authors, and creates a one-shot review task
    # for the dedicated reviewer agent. Default OFF — the loop never starts and
    # no inbound GitHub call is made until the CEO opts in. Untrusted contributor
    # code is never fetched or executed until ``confirmed_by_human`` is set.
    external_pr_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for inbound external-PR review. OFF by default; "
            "when off the poll loop does not run at all."
        ),
    )
    external_pr_poll_interval_seconds: int = Field(
        default=300,
        ge=60,
        description="Seconds between inbound external-PR discovery passes.",
    )
    external_pr_author_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "GitHub usernames trusted as known contributors. Empty means no "
            "author is auto-trusted; every external PR needs human confirmation."
        ),
    )
    external_pr_require_human_confirm: bool = Field(
        default=True,
        description=(
            "Require an explicit human confirmation before any agent fetches, "
            "checks out, or executes external contributor code."
        ),
    )
    internal_pr_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the internal-PR safety reviewer. OFF by default. "
            "When on, the same poll also reviews org-repo (non-fork) PRs that are "
            "NOT tied to an active task — i.e. branches pushed outside the agent "
            "task-flow. The org's own in-flight integration PRs (whose branch a "
            "live task owns) are skipped, since they already pass QA + PM review."
        ),
    )

    # ==========================================================================
    # HTTP security hardening (fastapi-guard 7.2.0) — DEFAULT OFF
    # ==========================================================================
    # A fastapi-guard SecurityMiddleware + per-route decorator layer (IP/rate/geo
    # controls, WAF signature detection, security headers, honeypots, and custom
    # prompt-injection / secret-exfil validators). Entirely inert unless
    # guard_enabled is set: create_app never adds the middleware when off, so the
    # request path is byte-for-byte unchanged. Cloud-host-ready but env-driven, so
    # a personal NAS deploy stays relaxed (ROBOCO_ENVIRONMENT=development).
    guard_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the fastapi-guard HTTP security layer. OFF by "
            "default; when on, create_app mounts SecurityMiddleware and the "
            "per-route guard decorators become active."
        ),
    )
    guard_fail_secure: bool = Field(
        default=True,
        description=(
            "Fail CLOSED: when a security check raises an unhandled error, block "
            "the request instead of letting it through. Secure default for "
            "cloud/public hosting; the NAS compose overrides this to false so a "
            "guard-internal bug never 500s the operator's personal deploy."
        ),
    )
    guard_passive_mode: bool = Field(
        default=False,
        description=(
            "Detect-and-log without blocking. The calibration switch: turn on "
            "when first arming guard on live traffic to surface false positives "
            "before enforcing, then turn off to enforce. Default off (enforce)."
        ),
    )
    guard_telemetry_enabled: bool = Field(
        default=False,
        description=(
            "Report security events/metrics to a guard-core platform via "
            "guard-agent. OFF by default; flip on and set guard_agent_api_key + "
            "guard_project_id to enable. No data leaves the box while off."
        ),
    )
    guard_agent_api_key: str = Field(
        default="",
        description="guard-core API key for guard-agent telemetry (telemetry only).",
    )
    guard_project_id: str = Field(
        default="",
        description="guard-core project id for guard-agent telemetry (telemetry only).",
    )
    guard_emergency: bool = Field(
        default=False,
        description=(
            "Emergency lockdown: block every non-whitelisted IP with 503. A "
            "flip-on-without-redeploy kill switch for an active attack. OFF by "
            "default."
        ),
    )
    guard_emergency_whitelist: str = Field(
        default="",
        description=(
            "Comma-separated IPs / CIDRs always allowed during emergency "
            "lockdown, in addition to loopback. Empty = loopback only."
        ),
    )
    guard_trusted_chain_peers: str = Field(
        default="",
        description=(
            "Comma-separated exact IP address(es), never a range, beyond "
            "loopback, trusted to appear as a recorded PROXY HOP inside "
            "X-Forwarded-For when resolving the real client behind a "
            "host-proxied chain (e.g. Tailscale Serve terminating on the "
            "docker host in front of nginx). A CIDR/subnet entry is "
            "rejected (logged, config load still succeeds) rather than "
            "accepted, since a range would readmit every sibling "
            "container's real address into the hop set. Empty by default: "
            "only a loopback rightmost hop peels, so a same-bridge "
            "container can no longer get its own 172.x address treated as a "
            "trusted hop just by being on the docker bridge. If Tailscale "
            "Serve sits behind this host's docker gateway, set this to that "
            "gateway's exact address (e.g. 172.18.0.1) to keep the "
            "Serve-behind-gateway chain resolving. Distinct from the docker "
            "bridge pool nginx itself connects FROM (still trusted "
            "unconditionally so nginx can keep presenting XFF at all) — this "
            "only scopes which XFF entries are treated as hops."
        ),
    )

    # ==========================================================================
    # Production self-healing ("engine 4") — DORMANT by default
    # ==========================================================================
    # RoboCo heals ITSELF. A closed loop that watches RoboCo's OWN repo CI (the
    # single project named by self_heal_project_slug — NOT other/client repos),
    # detects a regression (a failing CI run on its default branch), notifies the
    # CEO, and — behind a second opt-in — opens a PENDING fix task into RoboCo's
    # own delivery lifecycle and STOPS. It never starts, merges, or deploys; every
    # downstream step stays a human decision. Default OFF: the loop never runs and
    # no GitHub call is made.
    self_heal_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the self-healing loop (detect + notify the CEO). "
            "OFF by default; when off the background loop does not run at all and "
            "no CI telemetry is fetched."
        ),
    )
    self_heal_project_slug: str = Field(
        default="",
        description=(
            "The registered project that IS RoboCo itself — the self-heal loop "
            "watches ONLY this repo's CI and opens fix tasks ONLY into it (RoboCo "
            "healing itself, not other repos). Empty = no target; the loop no-ops "
            "even when enabled."
        ),
    )
    self_heal_ci_workflow: str = Field(
        default="ci.yml",
        description=(
            "GitHub Actions workflow file name to scope the CI signal to. "
            "Defaults to 'ci.yml' (RoboCo's own gate). Set empty ONLY for a "
            "single-workflow repo — an empty value reads the latest completed run "
            "across ALL workflows on the default branch, which on a "
            "multi-workflow repo lets an unrelated green run mask a red CI run "
            "and makes the self-heal signal flicker."
        ),
    )
    self_heal_originate_enabled: bool = Field(
        default=False,
        description=(
            "Second opt-in: when on (and self_heal_enabled), a detected regression "
            "also opens a PENDING fix task into the regressed project's lifecycle. "
            "OFF by default — the loop is notify-only. The loop NEVER starts, "
            "approves, merges, or deploys the task; it stops at PENDING for the CEO."
        ),
    )
    self_heal_interval_seconds: int = Field(
        default=1800,
        ge=60,
        description="Seconds between self-healing telemetry assessment passes.",
    )
    self_heal_max_open_tasks: int = Field(
        default=3,
        ge=1,
        description=(
            "Rolling cap on concurrently-open self-heal tasks across all repos; "
            "the loop originates nothing more while this many are still open."
        ),
    )
    self_heal_max_per_cycle: int = Field(
        default=1,
        ge=1,
        description="Max self-heal fix tasks the loop may originate in one cycle.",
    )
    self_heal_notify_dedupe_seconds: int = Field(
        default=7200,
        ge=60,
        description=(
            "Per-fingerprint CEO-notify dedupe window. A regression that stays"
            " red across cycles notifies the CEO once per episode, not every"
            " tick; the dedupe key expires after this window so a regression"
            " that clears and later recurs notifies again. Fail-open: a Redis"
            " outage in the check still lets the notify through."
        ),
    )

    # Multi-repo CI-watch — generalizes the single-repo self-heal CI loop to any
    # opted-in project (per-project `ci_watch_enabled` column). Default-off;
    # never auto-merges (fix tasks ride the normal delivery + PR-review gate).
    ci_watch_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the multi-repo CI-watch loop. OFF by default; "
            "when off the loop does not run and no CI telemetry is fetched. "
            "Generalizes self-heal to every project with ci_watch_enabled set."
        ),
    )
    ci_watch_default_workflow: str = Field(
        default="ci.yml",
        description=(
            "Default GitHub Actions workflow file to scope the CI signal to when "
            "a watched project does not set its own ci_watch_workflow. Empty "
            "reads the latest run across ALL workflows on the default branch, "
            "which on a multi-workflow repo lets a green run mask a red CI run."
        ),
    )
    ci_watch_interval_seconds: int = Field(
        default=1800,
        ge=60,
        description="Seconds between CI-watch telemetry assessment passes.",
    )
    ci_watch_max_open_tasks: int = Field(
        default=3,
        ge=1,
        description=(
            "Rolling cap on concurrently-open ci_watch tasks across all repos; "
            "the loop originates nothing more while this many are still open."
        ),
    )
    ci_watch_max_per_cycle: int = Field(
        default=1,
        ge=1,
        description="Max ci_watch fix tasks the loop may originate in one cycle.",
    )

    # Dependency-update bot — periodically detects available dependency updates
    # per opted-in project (a read-clone lockfile-diff probe) and opens an
    # "update dependencies" task. Default-off; never auto-merges (rides the
    # normal delivery + PR-review gate).
    dep_update_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the dependency-update bot. OFF by default; when "
            "off the loop does not run and no probe is executed. Only projects "
            "with a dep_update_command set participate."
        ),
    )
    dep_update_interval_seconds: int = Field(
        default=604800,
        ge=300,
        description="Seconds between dependency-update probe passes (default weekly).",
    )
    dep_update_max_open_tasks: int = Field(
        default=3,
        ge=1,
        description=(
            "Rolling cap on concurrently-open dep_update tasks across all repos."
        ),
    )
    dep_update_max_per_cycle: int = Field(
        default=1,
        ge=1,
        description="Max dep_update tasks the loop may originate in one cycle.",
    )

    # Env-sync engine — cascades each opted-in project's env ladder prod→…→head
    # (a clean merge auto-pushes to the lower rung; a conflict opens ONE sync PR)
    # so dev never falls behind prod. Default-off; never pushes to prod (the
    # cascade's lower/target rung is never prod by construction).
    env_sync_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the env-sync cascade loop. OFF by default; when "
            "off the loop does not run. Only projects with a declared env "
            "ladder (environments set) AND a git token participate."
        ),
    )
    env_sync_interval_seconds: int = Field(
        default=1800,
        ge=60,
        description="Seconds between env-sync cascade passes.",
    )
    env_sync_max_open_tasks: int = Field(
        default=3,
        ge=1,
        description=(
            "Rolling cap on concurrently-open env_sync conflict tasks across "
            "all repos; the loop originates nothing more while this many are open."
        ),
    )
    env_sync_max_per_cycle: int = Field(
        default=1,
        ge=1,
        description="Max projects the env-sync loop may cascade in one cycle.",
    )

    # Gated release manager — at a logical point (accumulated unreleased changes
    # past a threshold + green gate) the Secretary runs a deterministic readiness
    # sweep and PROPOSES a release for the CEO to approve/reject. Default-off;
    # never publishes without CEO approval (mirrors the self-heal CEO-gate).
    release_manager_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the gated release manager. OFF by default; when "
            "off the background loop does not run and no release is proposed. "
            "Even when on it only PROPOSES — the CEO approves before any publish."
        ),
    )
    release_git_name: str = Field(
        default="RoboCo Release Manager",
        description="Committer identity for the executor's release commit.",
    )
    release_git_email: str = Field(
        default="release-manager@roboco.local",
        description="Committer email for the executor's release commit.",
    )
    release_sign_commits: bool = Field(
        default=False,
        description=(
            "Sign the release commit (-S). Off by default: the orchestrator "
            "container carries no signing key; arm only with a mounted key."
        ),
    )
    release_min_commits: int = Field(
        default=8,
        ge=1,
        description=(
            "Minimum unreleased commits since the last tag before the release "
            "manager proposes a release (a feat/security change also qualifies)."
        ),
    )
    release_manager_interval_seconds: int = Field(
        default=3600,
        ge=60,
        description="Seconds between release-readiness assessment passes.",
    )
    release_ci_workflow: str = Field(
        default="ci.yml",
        description=(
            "GitHub Actions workflow file name the release fail-closed CI gate "
            "scopes to. Decoupled from self_heal_ci_workflow — that setting "
            "documents an empty-string mode for single-workflow repos which, "
            "inherited here, would degrade the release gate to the "
            "all-workflows mode git.py itself flags as unreliable (a green "
            "secondary workflow masking a red primary CI). The release gate "
            "always resolves a NAMED workflow; empty falls back to 'ci.yml', "
            "never None."
        ),
    )

    # Docs-divergence sync — when enabled, the release-proposal publish-success
    # path invokes the docs-sync engine to originate one bounded, deduped
    # docs-update task against the roboco-website project per release. Default-off;
    # the engine no-ops when disabled and logs a warning when roboco-website is
    # not registered as a project.
    docs_sync_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the docs-divergence sync engine. OFF by default; "
            "when off the engine is never invoked on release publish. When on, "
            "a successful release proposal may originate one docs-update task "
            "per release tag against the roboco-website project."
        ),
    )
    docs_sync_max_open_tasks: int = Field(
        default=3,
        ge=1,
        description=(
            "Rolling cap on concurrently-open docs-sync tasks; the engine "
            "originates nothing more while this many are still open."
        ),
    )
    docs_sync_max_per_cycle: int = Field(
        default=1,
        ge=1,
        description=(
            "Max docs-sync tasks the engine may originate in one invocation. "
            "A release publish is a single invocation, so this bounds it to "
            "one task per publish event."
        ),
    )

    # Docs-site identity — the user-facing docs repo/URL a documenter is
    # steered toward when refusing a doc_type="user_facing" write_doc call
    # (roboco/services/docs.py). Distinct from docs_sync_* above (that engine
    # stays roboco-only by design); this pair just keeps the refusal message
    # itself deployer-configurable instead of hardcoding our own docs site.
    docs_site_project_slug: str = Field(
        default="roboco-website",
        description=(
            "Project slug of the deployer's user-facing docs-site repo, named "
            "in the write_doc(doc_type='user_facing') refusal message."
        ),
    )
    docs_site_public_url: str = Field(
        default="docs.roboco.tech",
        description=(
            "Public URL of the deployer's docs site, named in the "
            "write_doc(doc_type='user_facing') refusal message."
        ),
    )

    # Organizational-memory loop — distill a high-signal lesson at task
    # completion, index journal reflections, and auto-inject similar past
    # lessons/playbooks into the agent briefing on claim. Default-off; when off
    # capture falls back to today's behavior and nothing is auto-injected.
    org_memory_enabled: bool = Field(
        default=False,
        description=(
            "Organizational memory loop (default off): distill a lesson at task "
            "completion, index journal reflections, and auto-inject similar past "
            "lessons/playbooks into the agent briefing on claim."
        ),
    )
    org_memory_top_k: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max institutional-memory items injected into a briefing.",
    )
    org_memory_min_score: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Cosine-similarity floor for injected memory; below it, none.",
    )

    # Sandboxed per-agent-spawn DB/Redis — orchestrator-provisioned throwaway
    # Postgres/Redis sibling containers so a dev agent's gate runs against an
    # isolated DB instead of RoboCo's own production Postgres. Default-off;
    # even when on, a project only participates with its `sandbox_services`
    # column set. Replaces (never coexists with) the legacy `_append_gate_env`
    # prod-creds injection for an opted-in project's spawns.
    sandbox_db_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the sandboxed per-agent test DB/Redis. OFF by "
            "default; when off, spawning behaves exactly as today (the legacy "
            "prod-creds gate-env injection, gated by toolchain_match_enabled). "
            "Only opted-in projects (sandbox_services column set) participate."
        ),
    )

    # X (Twitter) account — HoM drafts release posts + mention replies, ALL
    # held for per-post CEO approval (mirrors the release-manager CEO gate).
    # Default-off; even when on, posting requires CEO-supplied credentials AND
    # an explicit per-post CEO approval in the panel — nothing auto-posts.
    x_engine_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the X (Twitter) engine. OFF by default; when "
            "off no draft is originated and no X API call is ever made. Even "
            "when on, posting requires stored credentials AND an explicit "
            "per-post CEO approval — nothing auto-posts."
        ),
    )
    x_replies_enabled: bool = Field(
        default=False,
        description=(
            "Sub-switch for the mention-reply half of the X engine. OFF by "
            "default: even with x_engine_enabled on, the engine only drafts "
            "release-announcement posts — it does not poll mentions or draft "
            "replies. Reading mentions needs a paid X API tier, so replies are "
            "a deliberate opt-in on top of release posting."
        ),
    )
    x_mentions_interval_seconds: int = Field(
        default=1800,
        ge=60,
        description="Seconds between mentions-poll passes.",
    )
    x_mentions_max_per_cycle: int = Field(
        default=5,
        ge=1,
        description=(
            "Max held reply proposals the mentions poll may originate in one cycle."
        ),
    )
    x_mentions_min_engagement: int = Field(
        default=0,
        ge=0,
        description=(
            "Minimum like+reply+retweet count for a mention to count as "
            "'meaningful' (the engagement floor half of the mention filter)."
        ),
    )
    x_max_open_posts: int = Field(
        default=10,
        ge=1,
        description=(
            "Rolling cap on concurrently-open held X posts/replies (both "
            "sources combined); the engine originates nothing more past it."
        ),
    )
    x_account_user_id: str = Field(
        default="",
        description=(
            "Numeric X user id of the account's own account. Empty resolves it "
            "once per mentions cycle via GET /2/users/me (one extra call)."
        ),
    )
    x_request_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Per-request timeout for outbound X API HTTP calls.",
    )
    x_feature_spotlight_enabled: bool = Field(
        default=False,
        description=(
            "Sub-switch for the feature-spotlight half of the X engine. OFF by "
            "default: even with x_engine_enabled on, the engine drafts only "
            "release-announcement posts (and mention replies if enabled) — it does "
            "not spawn the Head of Marketing to investigate shipped capabilities. "
            "Unlike the local-model-only release/reply drafts, this spawns a real "
            "cloud-LLM agent per cycle, so it is a deliberate, costlier opt-in."
        ),
    )
    x_feature_spotlight_interval_seconds: int = Field(
        default=86400,  # 1 day — tunable; marketing cadence is a CEO call, not a
        # technical constant. This is the BASE loop tick only: the engine's own
        # smart-cadence guard (XEngine._feature_activity_stretch_skip) stretches
        # the effective cadence to 3x this whenever nothing has shipped since
        # the last spotlight activity, so a quiet week doesn't fire daily.
        ge=3600,
        description="Seconds between feature-spotlight exploration cycles.",
    )

    # Video generation (HyperFrames) — a UX/UI dev authors a bespoke motion-video
    # composition per release/spotlight/on-demand trigger through the normal
    # delivery lifecycle; a later render pass renders it to MP4 and holds the
    # clip as a CEO-approval draft (mirrors the X engine's held-draft shape).
    # Default-off; even when on, distribution requires an explicit per-clip
    # CEO approval — nothing auto-posts.
    video_engine_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the video-generation engine. OFF by default; "
            "when off no video-authoring task is ever opened. Even when on, "
            "distribution requires an explicit per-clip CEO approval — "
            "nothing auto-posts."
        ),
    )
    video_on_release: bool = Field(
        default=False,
        description=(
            "Sub-switch: open a video-authoring task when a release publishes. "
            "OFF by default even with video_engine_enabled on."
        ),
    )
    video_on_spotlight: bool = Field(
        default=False,
        description=(
            "Sub-switch: open a video-authoring task when the CEO approves a "
            "feature-spotlight draft that requests one. OFF by default even "
            "with video_engine_enabled on."
        ),
    )
    video_max_open_posts: int = Field(
        default=5,
        ge=1,
        description=(
            "Rolling cap on concurrently-open video tasks (authoring plus held "
            "post drafts combined); the engine opens nothing more past it."
        ),
    )
    video_render_timeout_seconds: float = Field(
        default=600.0,
        gt=0,
        description="Deadline for one render pass on the rendering sidecar.",
    )
    video_request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Per-request timeout for outbound video-engine HTTP calls.",
    )
    video_renderer_base_url: str = Field(
        default="http://roboco-video-renderer:3001",
        description=(
            "Base URL of the video-renderer sidecar. The orchestrator tars "
            "the merged motion/ source and POSTs it here; the sidecar returns "
            "MP4 bytes in the response (no cross-container shared volume)."
        ),
    )
    video_render_interval_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "Seconds between video-render loop passes (scans completed "
            "authoring tasks with an unrendered composition)."
        ),
    )
    video_render_scan_limit: int = Field(
        default=200,
        ge=1,
        description=(
            "Cap on how many completed video-authoring tasks the render "
            "loop scans per pass; the composition_id/render_status filter "
            "runs in Python on this bounded set. Backed by "
            "ix_tasks_source_status_created."
        ),
    )
    video_output_dir: str = Field(
        default="/data/video-renders",
        description=(
            "Orchestrator-local directory where rendered MP4s are written. "
            "The sidecar never writes here directly — it only returns bytes."
        ),
    )
    # MinIO object storage for rendered MP4s. Empty endpoint = disabled (the
    # media route falls back to FileResponse from the local video-renders dir).
    # Armed in the NAS compose; intentionally left OFF in the registry compose.
    minio_endpoint: str = Field(
        default="",
        description=(
            "MinIO endpoint, e.g. http://roboco-minio:9000. Empty = disabled "
            "(FileResponse fallback)."
        ),
    )
    minio_access_key: str = Field(
        default="",
        description="MinIO access key. Required when minio_endpoint is set.",
    )
    minio_secret_key: str = Field(
        default="",
        description="MinIO secret key. Required when minio_endpoint is set.",
    )
    minio_bucket: str = Field(
        default="roboco-video-renders",
        description="MinIO bucket for rendered videos.",
    )
    minio_region: str = Field(
        default="us-east-1",
        description="MinIO region.",
    )

    # Board roadmap engine — weekly, the Product Owner explores the company's
    # projects and proposes a themed cycle of roadmap items; the CEO approves
    # each item individually into the backlog. Default-off; even when on
    # nothing auto-starts (approved items land in BACKLOG for normal PM
    # activation).
    roadmap_engine_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the board roadmap engine. OFF by default; "
            "when off no exploration cycle is originated and the Product "
            "Owner is never spawned for this. Even when on, nothing "
            "auto-starts — approved items land in BACKLOG for normal PM "
            "activation."
        ),
    )
    roadmap_interval_seconds: int = Field(
        default=604800,
        ge=300,
        description="Seconds between roadmap-exploration cycles (default weekly).",
    )
    roadmap_min_items_per_cycle: int = Field(
        default=3,
        ge=1,
        description="Minimum roadmap item drafts a themed cycle must propose.",
    )
    roadmap_max_items_per_cycle: int = Field(
        default=7,
        ge=1,
        description="Maximum roadmap item drafts a themed cycle may propose.",
    )

    # ==========================================================================
    # Fable-mode (opus-fable-playbook + ponytail build-laziness adoption) — DEFAULT OFF
    # ==========================================================================
    # Composes the Fable 5 behavioral doctrine into every agent's system prompt
    # (compose_prompt's fable_doctrine_layer) and installs the matching
    # turn-discipline/honesty/verification hooks at spawn on both runtimes
    # (ClaudeCodeProvider's per-agent settings.json; grok's write_grok_hooks).
    # Bundled under the same flag: ponytail_doctrine_layer composes the
    # ponytail "lazy senior dev" build-laziness doctrine (role-scoped —
    # developers get the full ladder at doctrine/ponytail.md, other roles get
    # the ethos-only cut at doctrine/ponytail-ethos.md). Ponytail is prompt-
    # only (no hooks), so bundling adds no grok/hook surface.
    # Sources: github.com/rennf93/opus-fable-playbook (MIT), vendored at
    # agents/prompts/doctrine/fable.md; ponytail plugin (MIT, Copyright (c)
    # 2026 DietrichGebert), vendored trimmed at agents/prompts/doctrine/
    # ponytail.md (+ ethos sibling). Off by default; the spawn path is
    # byte-for-byte unchanged when off.
    fable_mode_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for opus-fable-playbook + ponytail build-laziness "
            "adoption: the Fable doctrine ambient layer in every composed "
            "system prompt, plus the matching turn-discipline/honesty/"
            "verification hooks installed at spawn (Claude Code settings.json "
            "+ grok ~/.grok/hooks), AND the role-scoped Ponytail build-"
            "laziness doctrine (developers get the full ladder, other roles "
            "get the ethos-only cut; prompt-only, no hooks). Off => spawn "
            "path byte-for-byte unchanged."
        ),
    )
    ponytail_intensity: Literal["lite", "full", "ultra"] = Field(
        default="full",
        description=(
            "Operative intensity for the developer Ponytail doctrine (env "
            "ROBOCO_PONYTAIL_INTENSITY, default 'full'). 'lite' = build what's "
            "asked, name the lazier alternative; 'full' = ladder enforced "
            "(default); 'ultra' = YAGNI extremist, deletion before addition, "
            "challenge the requirement. Applied to developers only; "
            "non-developers run a fixed restrained stance regardless. A "
            "string value, NOT a feature flag — no FEATURE_FLAGS entry."
        ),
    )

    # Set by the compose file that carries the roboco_data topology
    # (postgres/redis on a data-only network agents never join). NOT a panel
    # feature flag: it must travel with the compose networks: stanzas, and a
    # runtime toggle cannot change network membership.
    db_network_isolated: bool = Field(
        default=False,
        description=(
            "True when the deployment's compose topology isolates "
            "postgres/redis from agent containers (roboco_data network). "
            "Suppresses the legacy prod-creds gate-env injection, which "
            "would hand agents credentials for an unreachable host."
        ),
    )

    # ==========================================================================
    # Workspaces (Multi-Agent Git)
    # ==========================================================================
    workspaces_root: str = Field(
        default="/data/workspaces",
        description="Root directory for all agent workspaces",
    )
    workspace_auto_clone: bool = Field(
        default=True,
        description="Automatically clone repos when workspace is first accessed",
    )
    workspace_clone_timeout: int = Field(
        default=300,
        ge=30,
        description="Timeout in seconds for git clone operations",
    )
    workspace_refresh_fetch_timeout_seconds: int = Field(
        default=60,
        ge=5,
        description=(
            "Timeout in seconds for the best-effort `git fetch origin` "
            "that runs on every healthy-clone re-entry into "
            "ensure_workspace. Refresh fetches transfer small deltas only "
            "— blocking 300s (the full-clone timeout) on every spawn "
            "against a hung remote is operationally bad."
        ),
    )
    workspace_install_dev_deps: bool = Field(
        default=True,
        description=(
            "After cloning an agent workspace, install the project's dev "
            "dependencies into the workspace's own environment so the "
            "`make quality` gate (ruff/mypy/pytest for Python, the lint/"
            "typecheck toolchain for the TS panel) is available without "
            "the agent re-downloading tooling per task. Detects Python "
            "(pyproject.toml → `uv sync`) and Node/TS (package.json → "
            "`pnpm install`/`npm install`). Idempotent; skipped when the "
            "relevant lockfile is unchanged since the last install."
        ),
    )
    workspace_dep_install_timeout_seconds: int = Field(
        default=600,
        ge=30,
        description=(
            "Timeout in seconds for the post-clone dev-dependency install "
            "(`uv sync` / `pnpm install`). Cold installs of a large TS "
            "panel or a Python project with native wheels can take several "
            "minutes; the default clone timeout is too short for this."
        ),
    )

    # ==========================================================================
    # Agent container images (spawn source)
    # ==========================================================================
    # By default the orchestrator builds each specialized agent image locally
    # from docker/agent-*.Dockerfile the first time it spawns that role (the
    # build/test flow). Set a registry to run the PRE-BUILT images the release
    # workflow publishes instead — the orchestrator then pulls
    # `{registry}/roboco-agent-*[:tag]` rather than building, so a deployment
    # never needs the source tree or a build toolchain. Empty = local build
    # (unchanged behavior).
    agent_image_registry: str = Field(
        default="",
        description=(
            "Registry namespace for pre-built agent images, e.g. "
            "'ghcr.io/rennf93' or 'docker.io/renzof93'. Empty builds locally."
        ),
    )
    agent_image_tag: str = Field(
        default="",
        description=(
            "Tag for pre-built agent images (e.g. 'latest' or '0.17.0'). Empty "
            "leaves the tag implicit (':latest'); only meaningful with "
            "agent_image_registry set."
        ),
    )

    # ==========================================================================
    # Transcript retention (agent Claude Code transcripts under ~/.claude)
    # ==========================================================================
    transcript_retention_days: int = Field(
        default=14,
        ge=1,
        description=(
            "Default retention window, in days, for agent Claude Code "
            "transcripts (the *.jsonl files agents write under "
            "~/.claude/projects). A background sweep prunes agent-owned "
            "transcripts older than this. Panel-editable: a stored "
            "`transcript_retention_days` system setting overrides this default "
            "when present; this is the fallback used before one is set."
        ),
    )
    image_prune_enabled: bool = Field(
        default=True,
        description=(
            "Whether the orchestrator background sweep prunes dangling (<none>) "
            "Docker images. Each agent-image rebuild orphans the prior build's "
            "layers as an untagged image; over many deploys these pile up. "
            "Only DANGLING images are removed — a tagged image or one backing a "
            "running container is never dangling. Disable to keep them."
        ),
    )
    image_prune_interval_seconds: int = Field(
        default=21600,
        ge=300,
        description=(
            "Minimum seconds between dangling-image prune passes (default 6h)."
        ),
    )
    transcript_prune_enabled: bool = Field(
        default=True,
        description=(
            "Whether the orchestrator background sweep prunes old agent "
            "transcripts. Disable to keep every transcript indefinitely."
        ),
    )
    transcript_prune_interval_seconds: int = Field(
        default=3600,
        ge=300,
        description=(
            "Minimum seconds between transcript-retention prune passes. The "
            "prune is age-based (days), so it need not run more than hourly."
        ),
    )

    # ==========================================================================
    # Git command execution
    # ==========================================================================
    git_command_timeout_seconds: int = Field(
        default=30,
        ge=5,
        description=(
            "Default timeout in seconds for a single orchestrator-side git "
            "subprocess (status, log, checkout, fetch, push, …). Short by "
            "design — most git operations are sub-second."
        ),
    )
    flow_verb_timeout_seconds: float = Field(
        default=120.0,
        ge=1.0,
        description=(
            "Server-side wall-clock timeout for a single gateway intent-verb "
            "request (/api/v1/flow/*). A verb whose transaction hangs — e.g. "
            "claim() blocked on a FOR UPDATE row lock held by a prior stuck "
            "transaction — would otherwise hold its request transaction open "
            "indefinitely: uvicorn does not cancel the endpoint coroutine on "
            "client disconnect, so the row lock is never released and every "
            "later task-row write on that task wedges. On expiry the inner "
            "app is cancelled, get_db rolls back (releasing the lock), and a "
            "retryable 504 envelope is returned. Generous by default so "
            "legitimate verbs are unaffected."
        ),
    )
    flow_verb_slow_timeout_seconds: int = Field(
        default=900,
        ge=1,
        description=(
            "Server-side timeout for the slow flow verbs (i_am_done, "
            "submit_up, submit_root, open_pr) — a git push plus a "
            "per-command-budgeted quality gate, or a multi-step PR-create "
            "chain, routinely exceeds flow_verb_timeout_seconds. "
            "FlowVerbTimeoutMiddleware picks this budget for those verbs by "
            "request path instead of the default."
        ),
    )
    db_commit_cancel_grace_seconds: float = Field(
        default=5.0,
        ge=0.0,
        description=(
            "Grace period DbCommitMiddleware gives an in-flight "
            "session.commit() that FlowVerbTimeoutMiddleware's asyncio.timeout "
            "cancelled mid-wire, before giving up and invalidating the "
            "session. The commit runs shielded from that cancellation so it "
            "can finish naturally within the grace window instead of being "
            "severed on the spot — severing an asyncpg operation mid-protocol "
            "is implicated in a uvloop segfault class, so this bounds how "
            "often that ever happens instead of eliminating it outright."
        ),
    )
    git_commit_timeout_seconds: int = Field(
        default=180,
        ge=30,
        description=(
            "Timeout in seconds for staging + committing a changeset "
            "(`git add` / `git commit`). Large multi-file changesets (e.g. "
            "the Next.js panel) can exceed the 30s default-git timeout while "
            "git hashes every object and the orchestrator re-chowns the "
            "tree, so the commit choreography uses this longer budget."
        ),
    )
    git_network_timeout_seconds: int = Field(
        default=120,
        ge=30,
        description=(
            "Timeout in seconds for git ops that talk to origin (fetch / pull "
            "/ push). A push or fetch on a large private monorepo from a "
            "self-hosted runner can far exceed the sub-second local-op "
            "default; short-budgeting it is what made open_pr time out before "
            "the branch reached the remote."
        ),
    )

    protected_git_urls: list[str] = Field(
        default_factory=list,
        description=(
            "Repo URL substrings a project may not point at (e.g. the roboco "
            "source repo). Blocks agent commits/merges from reaching a protected "
            "repository; set this to sandbox smoke-test projects."
        ),
    )

    # ==========================================================================
    # Agent Guardrails (per-session budgets, loop detection, SLAs)
    # ==========================================================================
    agent_tool_call_warn: int = Field(
        default=50,
        ge=1,
        description="Soft warning threshold for per-session tool calls",
    )
    agent_tool_call_halt: int = Field(
        default=150,
        ge=1,
        description="Hard cap for per-session tool calls; orchestrator stops container",
    )
    agent_loop_threshold: int = Field(
        default=3,
        ge=2,
        description="Identical tool+args repeats in the window that flag a loop",
    )
    agent_loop_window: int = Field(
        default=10,
        ge=2,
        description="How many recent tool calls to inspect for loop detection",
    )
    agent_stop_attempt_allowance: int = Field(
        default=1,
        ge=1,
        description="Stop-without-terminal attempts before auto-substitute",
    )

    # Per-(role, state) SLAs for stuck-task sweep; seconds.
    agent_sla_developer_in_progress: int = Field(default=2 * 3600, ge=60)
    agent_sla_developer_verifying: int = Field(default=30 * 60, ge=60)
    agent_sla_qa_claimed: int = Field(default=30 * 60, ge=60)
    agent_sla_documenter_claimed: int = Field(default=60 * 60, ge=60)
    agent_sla_cell_pm_claimed: int = Field(default=4 * 3600, ge=60)

    # ==========================================================================
    # Agent Gateway
    # ==========================================================================
    manifest_host_dir: str = Field(
        default="/app/manifests",
        description=(
            "Orchestrator-side directory where per-agent tool manifests are "
            "written. Must be a path that's bind-mounted from the host "
            "(see docker-compose.yml) so the docker daemon can in turn mount "
            "the file into spawned agent containers as /app/tool-manifest.json."
        ),
    )
    public_base_url: str = Field(
        default="http://127.0.0.1:8000",
        description="Public base URL for commit-trailer links",
    )

    # Telegram notifications bridge — best-effort DMs to the CEO on escalation +
    # completion. Default-off; sending requires stored credentials AND
    # telegram_enabled. Server-side fan-out, never raises into the producer.
    telegram_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the Telegram notifications bridge. OFF by "
            "default; when off no Telegram API call is ever made. Even when "
            "on, sending requires stored bot-token + chat-id credentials."
        ),
    )
    telegram_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        description="Timeout (seconds) for a Telegram Bot API sendMessage call.",
    )
    panel_base_url: str = Field(
        default="",
        description=(
            "External panel base URL for Telegram message deep-links. Empty "
            "omits the link; e.g. https://panel.example.com -> "
            ".../tasks/<id8>."
        ),
    )
    # Telegram V2 — inbound commands (/status, /queue, /task) + actionable
    # approve/reject callback buttons on escalation DMs. Sub-switch on top of
    # telegram_enabled: both must be on, AND credentials stored, for the poll
    # loop to do anything. Never expands what triggers an outbound DM beyond
    # the V1 escalation/completion senders — this only makes the escalation
    # send actionable and adds a poll loop that reacts to the CEO's replies.
    telegram_inbound_enabled: bool = Field(
        default=False,
        description=(
            "Sub-switch for Telegram inbound commands + actionable "
            "approve/reject buttons. OFF by default: even with "
            "telegram_enabled on, the bot only sends notifications, never "
            "polls or reacts. Needs telegram_enabled on AND stored "
            "credentials to do anything."
        ),
    )
    telegram_poll_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        description=(
            "Seconds between getUpdates long-poll re-issues. Each call itself "
            "blocks server-side up to telegram_poll_timeout_seconds, so this "
            "is a floor between re-issues, not the effective latency."
        ),
    )
    telegram_poll_timeout_seconds: int = Field(
        default=25,
        ge=1,
        le=50,
        description="Bot API getUpdates long-poll `timeout` param (seconds).",
    )
    telegram_max_updates_per_cycle: int = Field(
        default=50,
        ge=1,
        description="Max updates processed in one poll cycle.",
    )
    telegram_pending_reply_ttl_seconds: float = Field(
        default=300.0,
        ge=30.0,
        description=(
            "How long a force_reply prompt (e.g. 'reply with your rejection "
            "reason') stays live before the pending action expires."
        ),
    )

    # Gateway coordination thresholds
    # Single source of truth for "claim heartbeat is stale", consumed via
    # `claimant_lock.is_stale` wherever a claim's freshness gates an action
    # (e.g. `_reap_stale_claims` deciding whether to RELEASE the claim back
    # to pending). One field keeps every consumer on the same tick.
    claim_stale_seconds: int = Field(
        default=180,
        ge=60,
        description="Claim heartbeat staleness threshold (seconds)",
    )
    # Debounce for respawning a PM to CLOSE a paused parent once its subtasks
    # are terminal. It guards only the narrow i_am_idle race (the parent
    # auto-pauses, then the agent is marked IDLE + its container tears down) —
    # the live-session case is already covered by the `_is_agent_active` check.
    # It must therefore be SHORT (a few dispatch ticks), NOT the multi-minute
    # reaper window: a paused parent's heartbeat reflects when the PM last
    # worked, so a PM that worked right up to idle leaves a fresh heartbeat and
    # any large window strands the whole chain until it expires. Was wrongly
    # bound to stale_claim_reap_seconds (600s default, 1800s on the NAS), which
    # delayed every cell/main closure by up to 10-30 minutes.
    pm_closure_recently_paused_seconds: int = Field(
        default=45,
        ge=5,
        description=(
            "Debounce (seconds) before respawning a PM to close a recently "
            "paused parent; override via ROBOCO_PM_CLOSURE_RECENTLY_PAUSED_SECONDS"
        ),
    )
    # Reaper window for stale-claim detection. Dogfooding reaped agents at
    # ~180s while they were actively
    # retrying — LLM inference + retry loops routinely exceed 3 min
    # between verb successes. 600s is large enough to accommodate that
    # without letting a genuinely-stuck container linger.
    # Distinct from claim_stale_seconds (the general claim-freshness
    # threshold); keeping them separate lets the reaper run on a longer
    # window than other claim-staleness consumers.
    stale_claim_reap_seconds: int = Field(
        default=600,
        ge=60,
        description=(
            "Reaper-only stale claim threshold (seconds); "
            "override via ROBOCO_STALE_CLAIM_REAP_SECONDS"
        ),
    )
    # A GROK agent that wedges — an idle model call / stream with no gateway
    # verb — is ACTIVE-yet-silent, so the heartbeat reaper's live-container skip
    # would shield its task forever (the grok CLI emits no SDK budget signal and
    # advances no heartbeat while parked, unlike a Claude agent that at least
    # reports). After this longer window the orchestrator kills + evicts the
    # container so the reaper releases the task. Longer than
    # stale_claim_reap_seconds so only a truly-dead run trips it, never a
    # slow-but-working agent.
    grok_idle_kill_seconds: int = Field(
        default=900,
        ge=120,
        description=(
            "Idle-container kill threshold for GROK agents (seconds); "
            "override via ROBOCO_GROK_IDLE_KILL_SECONDS"
        ),
    )
    # A non-GROK agent (Claude / Ollama-cloud / etc.) that gets stuck in a
    # non-verb loop — alive but firing no gateway verb, so its heartbeat never
    # advances and the reaper's live-container skip shields its claim forever
    # (#73). Past this MUCH longer window the orchestrator kills + evicts the
    # container so the reaper releases the task. Deliberately far beyond any
    # legit edit/test cycle (a working agent fires gateway verbs every few
    # minutes) so only a truly-stuck run trips it, never a slow-but-working one.
    claude_stuck_kill_seconds: int = Field(
        default=3600,
        ge=600,
        description=(
            "Stuck-in-non-verb-loop kill threshold for non-GROK agents "
            "(seconds); override via ROBOCO_CLAUDE_STUCK_KILL_SECONDS"
        ),
    )
    # Budget kill-switch parity for GROK. Claude Code's per-agent token-budget
    # hook fires against the SDK :9000 server; the grok CLI exposes no live usage
    # hook, so the orchestrator enforces the cap by reading each live GROK
    # container's captured cost from its usage.json and killing it when it crosses
    # this ceiling (also catches runaway-loop token burn). USD; 0 = off.
    grok_max_cost_usd: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Per-agent GROK cost ceiling (USD) before the container is killed; "
            "0 disables. Override via ROBOCO_GROK_MAX_COST_USD"
        ),
    )
    # An interactive intake/secretary chat the human abandoned (closed the tab
    # without confirming/stopping) otherwise leaks its container until the
    # orchestrator restarts. The sweeper reaps a live session whose
    # time-since-last-turn (push/deliver) exceeds this; measured on activity, NOT
    # connection state, so an active or page-reloaded chat that keeps exchanging
    # turns is never reaped (board-review-parked sessions are also exempt).
    # Seconds; 0 disables. Provider-agnostic (Claude + Grok interactive).
    interactive_idle_reap_seconds: int = Field(
        default=1800,
        ge=0,
        description=(
            "Idle-reap threshold for live intake/secretary chats (seconds); "
            "0 disables. Override via ROBOCO_INTERACTIVE_IDLE_REAP_SECONDS"
        ),
    )
    # A task left CLAIMED/IN_PROGRESS with an assignee but no running container
    # (e.g. a reassignment that didn't spawn) is invisibly stuck — the heartbeat
    # reaper can't see it because its heartbeat was seeded fresh at claim time.
    # After this short grace window the dispatcher (re)spawns the assignee, or
    # releases the task to pending for re-dispatch. Shorter than the
    # heartbeat reaper window: this is the "no agent at all" case, not the
    # "agent went silent mid-run" case.
    claimed_no_agent_grace_seconds: int = Field(
        default=120,
        ge=30,
        description=(
            "Grace window (seconds) before the orchestrator (re)spawns or "
            "releases a claimed/in_progress task that has no running agent; "
            "override via ROBOCO_CLAIMED_NO_AGENT_GRACE_SECONDS"
        ),
    )
    # Pre-gateway parity: PMs wrote a fresh
    # journal:decision around each decision point, not once at task
    # creation. The PM-decision tracing gate (delegate, unblock,
    # escalate_up, escalate_to_ceo) treats decisions older than this
    # window as missing, forcing a new note(scope='decision', ...) on
    # each pass through the gate.
    pm_decision_window_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "Recency window (seconds) for PM journal:decision to satisfy "
            "gating verbs; override via ROBOCO_PM_DECISION_WINDOW_SECONDS"
        ),
    )

    # Tracing-gate thresholds
    qa_notes_min_chars: int = Field(
        default=80,
        ge=1,
        description="Minimum characters for QA notes",
    )
    docs_notes_min_chars: int = Field(
        default=20,
        ge=1,
        description="Minimum characters for docs notes",
    )
    dev_notes_min_chars: int = Field(
        default=40,
        ge=1,
        description="Minimum characters for a developer's dev_notes section",
    )
    pr_reviewer_notes_min_chars: int = Field(
        default=40,
        ge=1,
        description="Minimum characters for a PR reviewer's pr_reviewer_notes section",
    )
    quick_context_min_chars: int = Field(
        default=30,
        ge=1,
        description="Minimum characters for a PM's quick_context resumption section",
    )

    # Commit-validator thresholds (wired into the gateway commit() gate)
    commit_subject_min_chars: int = Field(
        default=20,
        ge=1,
        description="Minimum characters for a commit subject",
    )
    commit_banned_words: tuple[str, ...] = Field(
        default=(
            "wip",
            "tmp",
            "asdf",
            "oops",
            "fix",
            "update",
            "change",
            "stuff",
            "things",
        ),
        description="Banned single-word commit subjects",
    )

    # ==========================================================================
    # Obsidian vault projection (V1 — projection core) — DEFAULT OFF
    # ==========================================================================
    # The org's human-readable memory palace: tasks/journals/A2A conversations
    # materialize as markdown notes with wikilinks, browsable in Obsidian
    # (Dataview/Kanban/graph plugins). Off by default and fully inert — every
    # seam (journal write, A2A send, task transition) no-ops when off.
    obsidian_vault_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the Obsidian vault projection. OFF by default; "
            "when off no note is ever written and every event seam is a no-op."
        ),
    )
    vault_path: str = Field(
        default="/data/vault",
        description=(
            "Root directory the vault materializes into (bind-mounted on the "
            "NAS in production). Only consulted when obsidian_vault_enabled."
        ),
    )

    # Vault intake — the vexa-inspired input loop (V1 item 4): notes tagged
    # #roboco in an opt-in vault folder become HELD intake drafts. Inert
    # unless BOTH obsidian_vault_enabled AND vault_intake_enabled are on.
    vault_intake_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for the vault intake watcher. OFF by default; when "
            "off no note is ever scanned and nothing is drafted."
        ),
    )
    vault_intake_interval_seconds: int = Field(
        default=300,
        ge=30,
        description="Seconds between vault-intake scan cycles.",
    )
    vault_intake_dir: str = Field(
        default="RoboCo/Inbox",
        description=("Vault-relative folder scanned for tagged notes (non-recursive)."),
    )
    vault_intake_max_per_cycle: int = Field(
        default=3,
        ge=1,
        description="Max held drafts the intake watcher may originate in one cycle.",
    )
    vault_intake_max_open_drafts: int = Field(
        default=10,
        ge=1,
        description=(
            "Rolling cap on concurrently-open held vault-note drafts; the "
            "watcher originates nothing more past it."
        ),
    )

    # Vault janitor (V2): drift repair + archival + weekly report, all folded
    # into one daily-gated loop tick. Gated on obsidian_vault_enabled only.
    vault_archive_days: int = Field(
        default=30,
        ge=0,
        description=(
            "Age (terminal timestamp) past which a completed/cancelled task's "
            "note moves to RoboCo/Archive/<year>/. 0 disables archival."
        ),
    )
    vault_report_enabled: bool = Field(
        default=True,
        description=(
            "Materialize a weekly RoboCo/Reports/<ISO-week>.md org-report note "
            "(deterministic, no LLM) and notify the CEO. Needs "
            "obsidian_vault_enabled."
        ),
    )

    # Vault KB ingest (V2 item 4): human-authored note folders become one more
    # RAG corpus (IndexType.VAULT_NOTES) — the CEO's own notes become
    # retrievable by the fleet. Inert unless BOTH obsidian_vault_enabled AND
    # vault_kb_enabled are on.
    vault_kb_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for vault KB ingest. OFF by default; when off no "
            "note is ever embedded and the index stays empty."
        ),
    )
    vault_kb_dirs: str = Field(
        default="RoboCo/Notes",
        description=(
            "CSV of vault-relative folders scanned recursively for KB ingest. "
            "Must never overlap vault_intake_dir or a reserved projection dir "
            "(enforced at config load)."
        ),
    )
    vault_kb_interval_seconds: int = Field(
        default=900,
        ge=60,
        description="Seconds between vault-KB ingest scan cycles.",
    )

    @model_validator(mode="after")
    def _validate_vault_kb_dirs(self) -> "Settings":
        """Reject a vault_kb_dirs entry that could escape the vault (absolute
        path or a ``..`` segment — path traversal into the fleet-retrievable
        corpus) or that overlaps the intake inbox or a reserved projection
        dir — KB ingest must never double-index what's already a first-class
        DB-backed corpus (Tasks/Journals/A2A/Agents) or the intake watcher's
        own folder."""
        if not self.vault_kb_enabled:
            return self
        reserved = (
            "RoboCo/Tasks",
            "RoboCo/Journals",
            "RoboCo/A2A",
            "RoboCo/Agents",
            "RoboCo/Archive",
            "RoboCo/Reports",
            "RoboCo/_meta",
            ".obsidian",
            self.vault_intake_dir,
        )
        for kb_dir in (d.strip() for d in self.vault_kb_dirs.split(",")):
            if not kb_dir:
                continue
            if kb_dir.startswith("/") or ".." in kb_dir.split("/"):
                raise ValueError(
                    f"ROBOCO_VAULT_KB_DIRS entry {kb_dir!r} must be a clean "
                    "vault-relative path — no absolute paths, no '..' "
                    "segments (KB ingest would index files outside the vault)."
                )
            # Overlap checks run on the normalized form so './RoboCo/Tasks'
            # or a vault-root-equivalent '.' can't slip past the guard.
            normalized = posixpath.normpath(kb_dir)
            if normalized == ".":
                raise ValueError(
                    f"ROBOCO_VAULT_KB_DIRS entry {kb_dir!r} resolves to the "
                    "vault root itself — KB ingest must target a subfolder, "
                    "never the whole vault (that would double-index every "
                    "projection dir, including private journals)."
                )
            for reserved_dir in reserved:
                if _vault_dirs_overlap(normalized, reserved_dir):
                    raise ValueError(
                        f"ROBOCO_VAULT_KB_DIRS entry {kb_dir!r} overlaps "
                        f"reserved vault path {reserved_dir!r} — KB ingest "
                        "must not double-index a projection/intake dir."
                    )
        return self


def _vault_dirs_overlap(a: str, b: str) -> bool:
    """True if vault-relative dirs ``a``/``b`` are equal or one nests the other."""
    a_norm, b_norm = a.strip("/"), b.strip("/")
    return (
        a_norm == b_norm
        or a_norm.startswith(b_norm + "/")
        or b_norm.startswith(a_norm + "/")
    )


def resolve_uvicorn_loop_factory(
    loop: Literal["asyncio", "uvloop"],
) -> Callable[[], asyncio.AbstractEventLoop] | None:
    """``asyncio.run(..., loop_factory=...)`` input for ``settings.uvicorn_loop``.

    uvicorn's own ``Config.loop`` only takes effect through ``Server.run()`` /
    ``uvicorn.run()`` (they resolve it via ``asyncio.run(loop_factory=...)``
    internally); a launch site that calls ``Server.serve()`` inside an
    already-running loop (the production orchestrator's bootstrap) never
    consults it at all — the loop was already chosen by whatever called
    ``asyncio.run()`` first. This is that resolver for those call sites.
    """
    if loop != "uvloop":
        return None
    # importlib (not a top-level `import uvloop`): uvloop rides in via
    # uvicorn[standard], not a direct dependency, and this keeps PLC0415 happy.
    uvloop = importlib.import_module("uvloop")
    factory: Callable[[], asyncio.AbstractEventLoop] = uvloop.new_event_loop
    return factory


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
