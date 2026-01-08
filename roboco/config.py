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
    app_name: str = "RoboCo"
    app_version: str = "0.1.0"
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
    reload: bool = Field(default=True, description="Auto-reload on code changes")
    workers: int = Field(default=1, ge=1)

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
            return f"{self.api_url.rstrip('/')}/api/v1"
        connect_host = "127.0.0.1" if self.host == "0.0.0.0" else self.host  # nosec B104
        return f"http://{connect_host}:{self.port}/api/v1"

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
    openai_api_key: str | None = None  # For embeddings

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
        default="glm-4.7:cloud",
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
    secret_key: str = Field(
        default="change-me-in-production-this-is-insecure",
        min_length=32,
        description="Secret key for JWT signing",
    )
    encryption_key: str = Field(
        default="",
        description="Fernet encryption key for secrets.",
    )
    access_token_expire_minutes: int = Field(default=60 * 24, ge=1)  # 24 hours
    algorithm: str = "HS256"

    # ==========================================================================
    # Logging
    # ==========================================================================
    log_level: str = Field(
        default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"
    )
    log_format: str = Field(default="json", pattern="^(json|console)$")

    # ==========================================================================
    # Sessions & Messages
    # ==========================================================================
    session_default_timeout_seconds: int = Field(default=300, ge=0)
    session_max_time_window_minutes: int = Field(default=30, ge=1)
    session_max_message_count: int = Field(default=100, ge=1)
    session_max_content_length: int = Field(default=50000, ge=1)
    message_max_length: int = Field(default=10000, ge=1)

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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
