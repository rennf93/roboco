# Configuration Reference

Environment variables for RoboCo (prefix: `ROBOCO_`).

## API Server

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_HOST` | `127.0.0.1` | API host (0.0.0.0 for containers) |
| `ROBOCO_PORT` | `8000` | API port |
| `ROBOCO_DEBUG` | `false` | Debug mode |
| `ROBOCO_ENVIRONMENT` | `development` | development/staging/production |

## Database

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_DATABASE_HOST` | `localhost` | PostgreSQL host |
| `ROBOCO_DATABASE_PORT` | `5432` | PostgreSQL port |
| `ROBOCO_DATABASE_USER` | `roboco` | Database user |
| `ROBOCO_DATABASE_PASSWORD` | `roboco` | Database password |
| `ROBOCO_DATABASE_NAME` | `roboco` | Database name |

## Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_REDIS_HOST` | `localhost` | Redis host |
| `ROBOCO_REDIS_PORT` | `6379` | Redis port |
| `ROBOCO_REDIS_DB` | `0` | Redis database |

## Workspaces

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_WORKSPACES_ROOT` | `/data/workspaces` | Root for agent workspaces |
| `ROBOCO_WORKSPACE_AUTO_CLONE` | `true` | Auto-clone on first access |
| `ROBOCO_WORKSPACE_CLONE_TIMEOUT` | `300` | Clone timeout (seconds) |

## RAG/Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_DEFAULT_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Embedding model |
| `ROBOCO_EMBEDDING_DIMENSIONS` | `1024` | Embedding dimensions |
| `ROBOCO_RAG_CHUNK_STRATEGY` | `fixed` | fixed/semantic/hierarchical/contextual |
| `ROBOCO_RAG_CHUNK_SIZE` | `512` | Base chunk size |
| `ROBOCO_RAG_CHUNK_SIZE_DOCS` | `1536` | Chunk size for docs |
| `ROBOCO_RAG_CHUNK_SIZE_JOURNALS` | `1024` | Chunk size for journals |
| `ROBOCO_RAG_CHUNK_OVERLAP` | `128` | Chunk overlap |
| `ROBOCO_RAG_USE_HYDE` | `true` | Use HyDE for queries |
| `ROBOCO_RAG_USE_HYBRID_SEARCH` | `true` | BM25 + vector search |
| `ROBOCO_RAG_USE_CROSS_ENCODER` | `true` | Neural reranking |
| `ROBOCO_RAG_AUTO_UPDATE_ENABLED` | `true` | Auto-update indexes |
| `ROBOCO_RAG_AUTO_UPDATE_INTERVAL` | `300` | Update interval (seconds) |

## LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_LOCAL_LLM_MODEL` | `glm-5:cloud` | Local LLM for RAG |
| `ROBOCO_LOCAL_LLM_BASE_URL` | `http://roboco-ollama:11434/v1` | OpenAI-compat API |
| `ROBOCO_OLLAMA_BASE_URL` | `http://roboco-ollama:11434` | Native Ollama API |

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_SECRET_KEY` | (required) | JWT signing key (32+ chars) |
| `ROBOCO_ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token expiry (24 hours) |

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `ROBOCO_LOG_FORMAT` | `json` | json or console |

## Sessions

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOCO_SESSION_DEFAULT_TIMEOUT_SECONDS` | `300` | Session timeout |
| `ROBOCO_SESSION_MAX_MESSAGE_COUNT` | `100` | Max messages per session |
| `ROBOCO_MESSAGE_MAX_LENGTH` | `10000` | Max message length |
