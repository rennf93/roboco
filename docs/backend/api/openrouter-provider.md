# OpenRouter Provider: Key Management, Model Search, Routing

## Overview

OpenRouter (https://openrouter.ai) is a metered API-key gateway that speaks the OpenAI Chat Completions protocol and routes to hundreds of models (DeepSeek, GLM, Qwen, Claude, GPT, …). It is now a first-class `ModelProvider` (`ModelProvider.OPENROUTER`, `roboco/models/base.py`), mirroring the Grok key-management pattern — but unlike the subscription-CLI providers (Grok, Gemini, Codex, Kimi, which share an OAuth login from `~/.<cli>`), OpenRouter authenticates with a **metered API key stored Fernet-encrypted on the provider row**, exactly like the Grok/`grok-key` endpoints.

The model catalog is **live and lazy**: OpenRouter models are never preloaded into the static `MODEL_CATALOG`. Instead the operator searches OpenRouter's model list on demand via a proxy endpoint, and a chosen model id (e.g. `deepseek/deepseek-chat`) is stored directly on the assignment row via `provider_type_override` — bypassing catalog validation like the self-hosted names do.

Scope of this doc: the API surface (`roboco/api/routes/provider.py`, `roboco/api/schemas/provider.py`), the routing-service integration (`roboco/services/llm.py`), and the two Alembic migrations (094/095). The OpenRouter **runtime provider** (agent container execution via the opencode CLI spike, provider class, Docker image) and the related prompt-injection / exit-78 entrypoint preflight clauses are a sibling delivery (task `3ac549f8`) and are documented there — this PR ships the key management, model search, and routing layers only.

## Data model & migrations

- **`095_modelprovider_openrouter`** — adds `'openrouter'` to the PostgreSQL `modelprovider` enum via `ALTER TYPE ... ADD VALUE IF NOT EXISTS` inside an `autocommit_block()` (Postgres forbids using a freshly added enum value in the same transaction that added it, and 096 seeds a row that uses it in the next step). Mirrors migration 090 (kimi); idempotent. Downgrade is intentionally a no-op — removing an enum value requires a full type rebuild, and the extra value is harmless when unused.
- **`096_seed_openrouter_provider`** — idempotently seeds the single `provider_configs` row (`name='OpenRouter'`, `type='openrouter'`, `enabled=false`, `base_url`/`auth_token_encrypted` NULL, `ON CONFLICT (name) DO NOTHING`). Seeded **disabled** because unlike the subscription-CLI providers there is a key to gate on: `PUT /providers/openrouter-key` encrypts the key and enables the row in the same transaction (the Grok pattern). Downgrade deletes `model_assignments` pointing at the row first to avoid the FK RESTRICT.

The service layer operates on this single pre-seeded row — `_get_seeded_provider(ModelProvider.OPENROUTER)`; no provider creation ever happens at runtime.

## Key management

### `PUT /api/providers/openrouter-key`

Sets or clears the OpenRouter API key. Body: `{"api_key": "<str>"}` (`SetOpenRouterKeyRequest`; default empty string).

- **Non-empty value** → the key is Fernet-encrypted into `provider_configs.auth_token_encrypted` and the provider row is marked `enabled=true` (one transaction, `ModelRoutingService.set_openrouter_api_key` → `ProviderService.update_provider`).
- **Empty string** → clears the stored key (`clear_auth_token`) and disables the provider.

The route carries the standard write-path guard stack (`rate_limit` 10/60s, `max_request_size` 8 KiB, `block_clouds`, `content_type_filter`, `honeypot_detection`, `usage_monitor`) and requires PM-or-above. Responds `404` if the OpenRouter row isn't seeded (the remediation is `alembic upgrade head`).

### `GET /api/providers/openrouter-key`

Returns `OpenRouterKeyStatus`:

```json
{"has_key": true, "enabled": true}
```

`has_key` is `bool(auth_token_encrypted)` — **the key itself is never returned**, in body or any error detail, for any caller (self/CEO included). Requires PM-or-above.

There is no standalone "test connection" endpoint; a wrong key surfaces through the model-search proxy (below) as a 503 carrying OpenRouter's own error.

## Model search (live catalog proxy)

### `GET /api/providers/openrouter/models?q=<substring>`

Proxies `https://openrouter.ai/api/v1/models` server-side and returns only models that **support tool calls** (`"tools"` in the model's `supported_parameters`), optionally filtered by `q` — a case-insensitive substring match against the model id or display name (`probe_openrouter_models` → `_filter_openrouter_models`, `roboco/services/llm.py`; the upstream call passes the key as a Bearer header with a 10s timeout).

Error contract:

| Condition | Response |
|---|---|
| No key configured (unset, cleared, or undecryptable) | `400` — "Set it first via PUT /providers/openrouter-key" |
| OpenRouter unreachable / timeout / non-200 upstream | `503` carrying the upstream failure (`401` upstream → "API key is invalid or expired") |
| Row not seeded | `404` |

Response items are `OpenRouterModelEntry`:

```json
{
  "id": "deepseek/deepseek-chat",
  "name": "DeepSeek V3 Chat",
  "context_length": 128000,
  "prompt_price": 0.27,
  "completion_price": 1.1
}
```

`id` is the OpenRouter slug — the value to use as the routing `model_name`. `prompt_price`/`completion_price` are OpenRouter's string per-token rates converted to floats (`safe_float`, `roboco/api/utils/provider.py`; an unparseable rate becomes `null`). The decrypted key exists only server-side for the duration of the upstream call and never reaches the browser — a pinned test asserts the response body contains no key material.

## Routing service integration

`ModelRoutingService` (`roboco/services/llm.py`) treats OpenRouter like the other single-global-mode providers (`_SINGLE_GLOBAL_MODE_BY_PROVIDER` now includes `OPENROUTER: "openrouter"`):

- **`derive_mode()`** returns `"openrouter"` when exactly one GLOBAL assignment targets the OPENROUTER provider.
- **`apply_mode("openrouter", default_model=…)`** (route `POST /api/providers/mode/apply`) clears every assignment, force-enables the seeded OpenRouter row, and sets the GLOBAL default to `default_model` (default `deepseek/deepseek-chat`) via `provider_type_override=ModelProvider.OPENROUTER` — the catalog check is bypassed precisely because OpenRouter models live in the live catalog, not `MODEL_CATALOG`. The mode requires the key to be set first; the assignment-upsert path surfaces `provider_remediation`'s new message ("Save the OpenRouter API key first (PUT /providers/openrouter-key).") when the provider is disabled.

### Cost-tier limitation (documented in the module + `_apply_openrouter` docstrings)

There are deliberately **no OpenRouter rows in the `_PRICING` table** — real cost attribution comes from OpenRouter's own metered `usage.cost`, not the static per-token table. Consequence: `input_price_per_million()` returns `0.0` for any OpenRouter model, so the cost-tiered complexity-override comparator (downgrade-only, refuses to move a role to a *more expensive* model) treats every OpenRouter model as the cheapest tier. An operator who auto-generates complexity overrides from static pricing can therefore pin a role to an OpenRouter model that is actually more expensive than the role's current assignment — the comparator can only downgrade, never warn about this mispricing. This is the known trade-off for shipping the live catalog without mirroring hundreds of prices locally.

## Testing

Coverage lives in `tests/integration/test_provider_routes.py` (key set/clear round-trip, GET never leaks the key, model-search 400/503/proxy filtering, the strict `?q` filter assertion) and `tests/integration/test_llm_routing.py` (mode derive/apply with `provider_type_override`, routing resolution to OpenRouter, pricing-table absence). QA round 2 ran both suites green against sandbox Postgres (55/55 + 349/349 including routing+llm).