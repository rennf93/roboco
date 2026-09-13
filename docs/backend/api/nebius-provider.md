# Nebius Provider: Key Management, Model Search, Routing

## Overview

Nebius Token Factory (https://tokenfactory.nebius.com) is Nebius's OpenAI-compatible inference API serving 60+ open models (NVIDIA Nemotron, DeepSeek, Qwen, Llama) behind one metered API key at `https://api.tokenfactory.nebius.com/v1`. It is a first-class `ModelProvider` (`ModelProvider.NEBIUS`, `roboco/models/base.py`), built as the twin of the OpenRouter provider and mirroring the Grok key-management pattern: the key is a **metered API key stored Fernet-encrypted on the provider row**, never a subscription credential.

The model catalog is **live and lazy**: Token Factory models are never preloaded into the static `MODEL_CATALOG`. The operator searches the live list on demand via a proxy endpoint, and a chosen model id (e.g. `nvidia/nemotron-3-super-120b`) is stored directly on the assignment row via `provider_type_override`, bypassing catalog validation like the self-hosted names do. The provider's default model is an NVIDIA Nemotron 3 id (settings.nebius_cli_model), so a Nebius-mode fleet runs an NVIDIA open model by construction.

Scope of this doc: the API surface (`roboco/api/routes/provider.py`, `roboco/api/schemas/provider.py`), the routing-service integration (`roboco/services/llm.py`), and the two Alembic migrations (097/098). The Nebius **runtime provider** (agent containers via the opencode CLI, `roboco.llm.providers.nebius`, the `roboco-agent-nebius` image, the exit 75/78 entrypoint preflight) is a line-for-line twin of the OpenRouter runtime documented in the openrouter-provider docs; every difference is called out below.

## Differences from OpenRouter (the short list)

- Endpoint: `https://api.tokenfactory.nebius.com/v1` (Bearer auth, standard OpenAI Chat Completions). No attribution headers (OpenRouter's `HTTP-Referer` / `X-Title` dashboard surface does not exist here).
- Model search has **no tools filter**: Token Factory's `GET /v1/models` is the generic OpenAI list shape (`{"data": [{"id": ...}]}`) and carries no `supported_parameters` capability metadata, no pricing, and usually no context length. The only filter is the query substring; `name` falls back to the id when absent.
- The model-search proxy reuses the `OpenRouterModelEntry` response schema (same nullable field shape), so the panel's model picker renders both identically.
- Default model: `settings.nebius_cli_model` (`nvidia/nemotron-3-super-120b`), not a DeepSeek default.
- Everything else - key management, mode machinery, park handlers, usage capture, interactive-role guard - is the openrouter pattern with `openrouter` renamed to `nebius` and env names `NEBIUS_API_KEY` / `NEBIUS_BASE_URL`.

## Data model & migrations

- **`097_modelprovider_nebius`** - adds `'nebius'` to the PostgreSQL `modelprovider` enum via `ALTER TYPE ... ADD VALUE IF NOT EXISTS` inside an `autocommit_block()` (Postgres forbids using a freshly added enum value in the same transaction that added it, and 098 seeds a row that uses it in the next step). Mirrors migration 095 (openrouter); idempotent. Downgrade is intentionally a no-op.
- **`098_seed_nebius_provider`** - idempotently seeds the single `provider_configs` row (`name='Nebius'`, `type='nebius'`, `enabled=false`, `base_url`/`auth_token_encrypted` NULL, `ON CONFLICT (name) DO NOTHING`). Seeded **disabled** because there is a key to gate on: `PUT /providers/nebius-key` encrypts the key and enables the row in the same transaction (the Grok/openrouter pattern). Downgrade deletes `model_assignments` pointing at the row first to avoid the FK RESTRICT.

The service layer operates on this single pre-seeded row - `_get_seeded_provider(ModelProvider.NEBIUS)`; no provider creation ever happens at runtime.

## Key management

### `PUT /api/providers/nebius-key`

Sets or clears the Nebius API key. Body: `{"api_key": "<str>"}` (`SetNebiusKeyRequest`; default empty string).

- **Non-empty value** - the key is Fernet-encrypted into `provider_configs.auth_token_encrypted` and the provider row is marked `enabled=true` (one transaction, `ModelRoutingService.set_nebius_api_key` -> `ProviderService.update_provider`).
- **Empty string** - clears the stored key (`clear_auth_token`) and disables the provider.

The route carries the standard write-path guard stack (`rate_limit` 10/60s, `max_request_size` 8 KiB, `block_clouds`, `content_type_filter`, `honeypot_detection`, `usage_monitor`) and requires PM-or-above. Responds `404` if the Nebius row isn't seeded (the remediation is `alembic upgrade head`).

### `GET /api/providers/nebius-key`

Returns `NebiusKeyStatus`:

```json
{"has_key": true, "enabled": true}
```

`has_key` is `bool(auth_token_encrypted)` - **the key itself is never returned**, in body or any error detail, for any caller. Requires PM-or-above.

There is no standalone "test connection" endpoint; a wrong key surfaces through the model-search proxy (below) as a 503 carrying Nebius's own error, or at spawn time as the entrypoint's exit-78 auth park.

## Model search (live catalog proxy)

### `GET /api/providers/nebius/models?q=<substring>`

Proxies `{nebius_base_url}/models` server-side (10s timeout, Bearer key), optionally filtered by `q` - a case-insensitive substring match against the model id or display name (`probe_nebius_models` -> `_filter_nebius_models`, `roboco/services/llm.py`).

Error contract:

| Condition | Response |
|---|---|
| No key configured (unset, cleared, or undecryptable) | `400` - "Set it first via PUT /providers/nebius-key" |
| Nebius unreachable / timeout / non-200 upstream | `503` carrying the upstream failure (`401` upstream -> "API key is invalid or expired") |
| Row not seeded | `404` |

Response items reuse `OpenRouterModelEntry` (`id`, `name`, `context_length`, `prompt_price`, `completion_price` - all nullable except `id`). Token Factory carries no pricing or context metadata on the list endpoint, so those fields are typically `null`; `id` is the value to use as the routing `model_name`. The decrypted key exists only server-side for the duration of the upstream call and never reaches the browser.

## Routing service integration

`ModelRoutingService` (`roboco/services/llm.py`) treats Nebius like the other single-global-mode providers (`_SINGLE_GLOBAL_MODE_BY_PROVIDER` includes `NEBIUS: "nebius"`):

- **`derive_mode()`** returns `"nebius"` when exactly one GLOBAL assignment targets the NEBIUS provider.
- **`apply_mode("nebius", default_model=...)`** (route `POST /api/providers/mode/apply`) clears every assignment, force-enables the seeded Nebius row, and sets the GLOBAL default to `default_model` (default `settings.nebius_cli_model`) via `provider_type_override=ModelProvider.NEBIUS` - the catalog check is bypassed because Token Factory models live in the live catalog, not `MODEL_CATALOG`. Like openrouter, there is no key check at mode-apply time; the spawn-time key check (ProviderError + entrypoint exit 78) is the gate, and the assignment-upsert path surfaces `provider_remediation`'s message ("Save the Nebius API key first (PUT /providers/nebius-key).") when the provider is disabled.
- **Interactive guard**: NEBIUS is in `INTERACTIVE_UNSUPPORTED_PROVIDERS` - intake/secretary keep the legacy Anthropic path under fleet-wide Nebius mode, and an explicit AGENT_SLUG pin is refused loudly by the orchestrator guard.
- **Upsert auto-enable**: NEBIUS is in the auto-enable tuple (the openrouter precedent) - an explicit assignment is a deliberate operator choice; the spawn-time check catches a missing key.

### Cost-tier limitation (same as OpenRouter)

There are deliberately **no Nebius rows in the `_PRICING` table** - real cost attribution comes from Token Factory's metered `usage.cost`, not the static per-token table. Consequence: `input_price_per_million()` returns `0.0` for any Nebius model, so the cost-tiered complexity-override comparator treats every Nebius model as the cheapest tier. Known trade-off, documented in `_apply_nebius`.

## Testing

Coverage mirrors the OpenRouter suites: `tests/unit/llm/providers/test_nebius_*.py` (provider spawn contract, CLI config render, sniff, usage), `tests/unit/runtime/test_nebius_rate_limit.py`, `tests/unit/runtime/test_nebius_usage_finalize.py`, `tests/unit/runtime/test_interactive_provider_guard.py`, `tests/integration/test_migration_098_seed_nebius_provider.py`, `tests/integration/test_llm_routing.py` (mode derive/apply), and `tests/integration/test_provider_routes.py` (key round-trip, no key leak, model-search 400/503/filtering).
