# Nebius Routing UI

The AI Routing card (`panel/src/components/settings/ai-routing-card.tsx`) and the Nebius key row (`panel/src/components/settings/provider-key-card.tsx`) expose Nebius Token Factory as a fleet-wide routing mode: save a key, hit the Nebius mode button, and pick a default model from Token Factory's catalog via the debounced search picker. This is the line-for-line twin of the OpenRouter routing UI (`docs/frontend/components/openrouter-routing-ui.md`); only the provider identity, the key gate, and the model-search semantics differ.

## Shared types + hooks

The Nebius API contract lives in `panel/src/lib/api/providers.ts`:

- `NebiusModel` - one entry from `GET /providers/nebius/models?q=`. The backend reuses the OpenRouter entry shape; pricing and context fields are nullable and typically null (Token Factory's list carries no capability metadata).
- `NebiusKeyStatus` - `{ has_key: boolean, enabled: boolean }` from `GET/PUT /providers/nebius-key`.
- `providersApi.getNebiusKey` / `setNebiusKey` / `searchNebiusModels`.

`panel/src/hooks/use-providers.ts` exposes `useNebiusKey`, `useSetNebiusKey`, and `useSearchNebiusModels` keyed off `providerKeys.nebiusKey()` / `providerKeys.nebiusModels(query)`. Both consumers (the "Nebius API key" row and the mode card) go through these.

`RoutingMode` includes `"nebius"`, so `applyMode` payloads pass `mode: "nebius"` directly with no casts, and the card's `currentMode` is plainly typed `RoutingMode`.

## UI behavior

- **Key row** (`NebiusProviderKeyRow`): password input with Save/Clear; the green "Saved" badge appears only after the mutation's `onSuccess` and clears on any input change, never optimistically.
- **Mode button + flip**: the Nebius `ModeButton` is disabled until the key is set. Confirming the flip replaces role/global assignments while keeping per-agent pins and complexity overrides; the payload passes `mode: "nebius"` plus the picked model as `default_model` only when one is selected. The mode note states the delivery-roles-only scope (intake/secretary stay on Anthropic) and that agents run on the opencode CLI against Nebius Token Factory.
- **Model picker states** (rendered only in `nebius` mode): with no key the input is disabled behind an amber "Save your Nebius API key above…" notice; a failing search renders the status-specific copy below; zero results render `No models found for "…"`. There is no preload - the query fires only for a non-empty debounced search while the key is set.

The picker surfaces the cause of a failure through `nebiusSearchErrorMessage` (mirroring `openRouterSearchErrorMessage`):

| Failure | Copy |
| --- | --- |
| HTTP 400 | "Nebius API key not set - save your key above first." |
| HTTP 401 | "Nebius auth failure - your API key may be invalid or expired." |
| HTTP 429 | "Nebius rate limit - too many requests, try again in a moment." |
| `ECONNABORTED` / `ETIMEDOUT` | "Nebius search timed out - try again in a moment." |
| Anything else | "Nebius API is unavailable - try again in a moment." |

Pricing rendering reuses `formatPricePerMillion` with the same null-safe rules as the OpenRouter picker; on Token Factory most models have no list price, so "-" is the common rendering, and a null `context_length` omits the context line.

## Mix mode / per-agent pins

The Mix-mode per-agent select lists only backend catalog models. Nebius models are stored via `provider_type_override` outside the catalog, so there is no catalog entry to offer a select row against - **per-agent Nebius pins are out of scope, mirroring the OpenRouter decision**. Do not extend the select to synthesize Nebius entries.

## Testing

```bash
cd panel
pnpm test ai-routing-card
```

Focused coverage lives in `panel/src/components/settings/__tests__/ai-routing-card.test.tsx`:

- `Nebius model picker` - mode-application payload (`mode: "nebius"`, with and without a picked default model), the debounced search (nothing fetched while the query is empty; one fetch per debounced keystroke), the status-specific error copy for a failing models query, the empty "no models found" state, and the key-not-set notice.
- `Nebius key row` (via `NebiusProviderKeyRow`) - the "Saved" badge appears only after a successful mutation and clears on any input change; never "Saved" on failure.
- `Nebius mode button` - applies `mode: "nebius"` on confirm when the key is set; disabled when it is not.
