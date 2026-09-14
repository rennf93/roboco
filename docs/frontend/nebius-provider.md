# Nebius provider - types, API client, and hooks

Foundation layer for Nebius Token Factory as a first-class LLM provider in the panel. This is the reference for the three exports the UI components import: the `ModelProvider.NEBIUS` enum value, the `providersApi` client functions, and the React hooks in `use-providers.ts`. It is the line-for-line twin of the OpenRouter foundation (`docs/frontend/openrouter-provider.md`); only the names, the endpoint, and the model-search semantics differ.

## Where the code lives

| File | What it adds |
|------|--------------|
| `panel/src/types/index.ts` | `ModelProvider.NEBIUS = "nebius"` enum value. |
| `panel/src/lib/api/providers.ts` | `"nebius"` in the `RoutingMode` union; `NebiusKeyStatus`, `NebiusModel`, `SetNebiusKeyRequest` types; `getNebiusKey`, `setNebiusKey`, `searchNebiusModels` on `providersApi`. |
| `panel/src/hooks/use-providers.ts` | `nebiusKey` / `nebiusModels` query keys; `useNebiusKey`, `useSetNebiusKey`, `useSearchNebiusModels` hooks. |

## API contract

| Method | Endpoint | Returns | Notes |
|--------|----------|---------|-------|
| GET | `/providers/nebius-key` | `NebiusKeyStatus` (`{ has_key: boolean, enabled: boolean }`) | Whether a Nebius key is stored + provider enabled. |
| PUT | `/providers/nebius-key` | `NebiusKeyStatus` | Body: `SetNebiusKeyRequest` (`{ api_key: string }`). An empty string clears the key. |
| GET | `/providers/nebius/models?q=<query>` | `NebiusModel[]` | 400 if no key is set. No tools filter (Token Factory's list carries no capability metadata); query-substring filter only. |

### `NebiusModel`

```ts
interface NebiusModel {
  id: string;
  name: string;
  context_length: number | null;
  prompt_price: number | null;
  completion_price: number | null;
}
```

Token Factory's `GET /v1/models` is the generic OpenAI list shape: `name` falls back to the id when absent, and pricing/context fields are typically `null` (the backend reuses the `OpenRouterModelEntry` response schema, so the nullable shape matches). `id` (e.g. `nvidia/nemotron-3-super-120b-a12b`) is the value used as the routing `model_name`.

## API client functions

All three live on the `providersApi` object in `panel/src/lib/api/providers.ts`:

```ts
providersApi.getNebiusKey(): Promise<NebiusKeyStatus>
providersApi.setNebiusKey(apiKey: string): Promise<NebiusKeyStatus>
providersApi.searchNebiusModels(query: string): Promise<NebiusModel[]>
```

## Hooks

All three are in `panel/src/hooks/use-providers.ts` and follow the same TanStack Query shape as the OpenRouter hooks: `useNebiusKey()` (60s staleTime), `useSetNebiusKey()` (invalidates the `nebiusKey` and `mode` query keys), and `useSearchNebiusModels(query, enabled)` (query guarded inside the hook on `enabled && query.length > 0`, keyed on the raw query string, 60s staleTime; debounce the input in the UI).

```ts
const { data: keyStatus } = useNebiusKey();
const hasKey = keyStatus?.has_key ?? false;
const { data: models, isLoading } = useSearchNebiusModels(query, hasKey && query.length > 0);
```

## Usage notes for the UI

- The key row (`NebiusProviderKeyRow` in `provider-key-card.tsx`) mirrors the OpenRouter row: password input with Save/Clear, the green "Saved" badge only after the mutation's `onSuccess`, cleared on any input change.
- The model picker renders null-safe pricing rows ("-" for a missing price) and omits the context line when `context_length` is null.
- `ModelProvider.NEBIUS` is the enum value for provider-type discriminators; `"nebius"` is a valid `RoutingMode` value for the mode buttons and `applyMode` payloads.
- Per-agent Mix-mode pins are out of scope, exactly like OpenRouter: Nebius models are stored via `provider_type_override` outside the backend catalog, so the Mix select has no row to offer.
