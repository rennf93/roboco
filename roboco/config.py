"""
RoboCo Configuration

Environment-based settings using Pydantic Settings.
"""

from functools import lru_cache

from pydantic import Field, computed_field
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
    app_version: str = "0.2.0"
    debug: bool = False
    environment: str = Field(
        default="development", pattern="^(development|staging|production)$"
    )

    # ==========================================================================
    # API Server
    # ==========================================================================
    host: str = Field(default="127.0.0.1", description="Use 0.0.0.0 for containers")
    port: int = 8000
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
    # RAG (piragi with pgvector)
    # ==========================================================================
    rag_persist_dir: str = ".piragi"
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
    rag_use_hyde: bool = Field(
        default=True,
        description="Use HyDE (hypothetical document embeddings). "
        "Makes one LLM call per query for better semantic matching.",
    )
    rag_use_hybrid_search: bool = Field(
        default=True, description="Use BM25 + vector hybrid search"
    )
    rag_use_cross_encoder: bool = Field(
        default=True, description="Use neural reranking (slower but more accurate)"
    )
    rag_auto_update_enabled: bool = Field(default=True)
    rag_auto_update_interval: int = Field(
        default=300, ge=60, description="Seconds between auto-updates"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rag_store_url(self) -> str:
        """PostgreSQL connection URL for piragi vector store."""
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

    # Local LLM for RAG (HyDE, reranking, etc.)
    local_llm_model: str = Field(
        default="glm-5:cloud",
        description="Local LLM for HyDE/RAG (non-thinking models are faster)",
    )
    local_llm_base_url: str = Field(
        default="http://roboco-ollama:11434/v1",
        description="Base URL for local LLM (Ollama OpenAI-compat API)",
    )
    ollama_base_url: str = Field(
        default="http://roboco-ollama:11434",
        description="Base URL for Ollama native API (embeddings, model mgmt)",
    )

    # ==========================================================================
    # Security
    # ==========================================================================
    encryption_key: str = Field(
        default="",
        description="Fernet encryption key for secrets.",
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

    session_idle_timeout_seconds: int = Field(
        default=3600,
        ge=30,
        description=(
            "Idle seconds before a messaging session is swept closed. The "
            "previous 300s default was shorter than a human conversation pause, "
            "so a person's chat session expired and reopened between messages."
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

    # Gateway coordination thresholds
    # Single source of truth for "claim heartbeat is stale": consumed both by
    # `trigger_filter` (deciding whether to QUEUE a fresh spawn) and by
    # `_reap_stale_claims` (deciding whether to RELEASE the claim back to
    # pending). Keeping them on one field guarantees both layers agree on
    # the same tick — the reaper runs first, releases the row, and the
    # queued spawn finds an unclaimed task. Splitting them into two fields
    # opens a window where trigger_filter queues duplicate spawns against a
    # claim the reaper hasn't yet released — pure dispatcher churn.
    claim_stale_seconds: int = Field(
        default=180,
        ge=60,
        description="Claim heartbeat staleness threshold (seconds)",
    )
    # Reaper window for stale-claim detection. Dogfooding reaped agents at
    # ~180s while they were actively
    # retrying — LLM inference + retry loops routinely exceed 3 min
    # between verb successes. 600s is large enough to accommodate that
    # without letting a genuinely-stuck container linger.
    # Distinct from claim_stale_seconds (which drives trigger_filter
    # spawn queueing); keeping them separate avoids a window where a
    # higher reap threshold would also delay spawn-queue decisions.
    stale_claim_reap_seconds: int = Field(
        default=600,
        ge=60,
        description=(
            "Reaper-only stale claim threshold (seconds); "
            "override via ROBOCO_STALE_CLAIM_REAP_SECONDS"
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
    spawn_cooldown_seconds: int = Field(
        default=60,
        ge=1,
        description="Per-task spawn rate cooldown (seconds)",
    )
    role_spawn_rate_per_minute: int = Field(
        default=6,
        ge=1,
        description="Per-role spawn rate limit (per minute)",
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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
