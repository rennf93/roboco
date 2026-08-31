# OpenRouter provider — types, API client, and hooks

Foundation layer for OpenRouter as a first-class LLM provider in the panel. This is the reference for the three exports the UI components import: the `ModelProvider.OPENROUTER` enum value, the `providersApi` client functions, and the React hooks in `use-providers.ts`. No UI ships here — this is the typed seam the OpenRouter settings cards and model picker build on.

## Where the code lives

| File | What it adds |
|------|--------------|
| `panel/src/types/index.ts` | `ModelProvider.OPENROUTER = "openrouter"` enum value. |
| `panel/src/lib/api/providers.ts` | `"openrouter"` in the `RoutingMode` union; `OpenRouterKeyStatus`, `OpenRouterModel`, `SetOpenRouterKeyRequest` types; `getOpenRouterKey`, `setOpenRouterKey`, `searchOpenRouterModels` on `providersApi`. |
| `panel/src/hooks/use-providers.ts` | `openRouterKey` / `openRouterModels` query keys; `useOpenRouterKey`, `useSetOpenRouterKey`, `useSearchOpenRouterModels` hooks. |

The pattern mirrors the existing Grok key (`GrokKeyStatus` / `getGrokKey` / `setGrokKey` / `useGrokKey`) and self-hosted model search (`getSelfHostedModels` / `useSelfHostedModels`) reference implementations.

## API contract

The client is built against this contract; the backend implements it in parallel.

| Method | Endpoint | Returns | Notes |
|--------|----------|---------|-------|
| GET | `/providers/openrouter-key` | `OpenRouterKeyStatus` (`{ key_set: boolean }`) | Whether an OpenRouter key is stored. |
| PUT | `/providers/openrouter-key` | `OpenRouterKeyStatus` | Body: `SetOpenRouterKeyRequest` (`{ api_key: string }`). An empty string clears the key. |
| GET | `/providers/openrouter/models?q=<query>` | `OpenRouterModel[]` | 400 if no key is set. Filtered to tools-supporting models. |

## Types

### `OpenRouterKeyStatus`

```ts
interface OpenRouterKeyStatus {
  key_set: boolean;
}
```

Note the field is `key_set`, not `has_key` + `enabled` like `GrokKeyStatus` / `OllamaKeyStatus` — OpenRouter has no separate enable toggle, only the set/clear state.

### `OpenRouterModel`

```ts
interface OpenRouterModel {
  model_name: string;
  display_name: string;
  context_length: number | null;
  pricing: {
    prompt: string | null;
    completion: string | null;
  } | null;
}
```

`pricing` is null when the backend has no pricing data for a model; the inner `prompt` / `completion` fields are string-encoded per-token costs and may individually be null. `context_length` is null for models that don't report it.

### `SetOpenRouterKeyRequest`

```ts
interface SetOpenRouterKeyRequest {
  api_key: string;
}
```

## API client functions

All three live on the `providersApi` object in `panel/src/lib/api/providers.ts`.

```ts
providersApi.getOpenRouterKey(): Promise<OpenRouterKeyStatus>
providersApi.setOpenRouterKey(apiKey: string): Promise<OpenRouterKeyStatus>
providersApi.searchOpenRouterModels(query: string): Promise<OpenRouterModel[]>
```

`setOpenRouterKey` sends `{ api_key: apiKey }` via `satisfies SetOpenRouterKeyRequest`. `searchOpenRouterModels` passes the query as the `q` query param.

## Hooks

All three are in `panel/src/hooks/use-providers.ts` and follow the same TanStack Query shape as the Grok and self-hosted hooks.

### `useOpenRouterKey()`

Reads the key status. 60-second `staleTime`.

```ts
const { data, isLoading } = useOpenRouterKey();
// data: OpenRouterKeyStatus | undefined
```

### `useSetOpenRouterKey()`

Mutation that stores or clears the key. On success it invalidates the `openRouterKey` and `mode` query keys — the latter because applying a routing mode reads key state, same as the Grok hook.

```ts
const { mutate, isPending } = useSetOpenRouterKey();
mutate(apiKey); // empty string clears
```

### `useSearchOpenRouterModels(query, enabled)`

Searches OpenRouter's tools-supporting models. The hook takes two arguments: the debounced query, plus a caller-composed `enabled` boolean that ANDs the key-set flag (from `useOpenRouterKey`'s `key_set`) with a non-empty query. **The query is guarded inside the hook**: `enabled && query.length > 0`, so an idle picker and an unset key both make zero requests. Debounce the input in the UI so a user pause fires the search rather than every keystroke. 60-second `staleTime`.

```ts
const { data: keyStatus } = useOpenRouterKey();
const hasKey = keyStatus?.key_set ?? false;
const { data: models, isLoading } = useSearchOpenRouterModels(query, hasKey && query.length > 0);
// models: OpenRouterModel[] | undefined
```

The query key is `providerKeys.openRouterModels(query)` — keyed on the raw query string, so each distinct query caches independently.

## Usage notes for the UI task

- The key card mirrors the Grok key card: `useOpenRouterKey` for the status badge, `useSetOpenRouterKey` for the save/clear action.
- The model picker calls `useSearchOpenRouterModels` with a debounced query; the 400-on-no-key backend behavior should surface as a "set a key first" prompt, not a crash.
- `ModelProvider.OPENROUTER` is the enum value to use in any provider-type discriminator (catalog entries, assignment rows).
- `"openrouter"` is a valid `RoutingMode` value for the AI routing mode buttons and `applyMode` payload.