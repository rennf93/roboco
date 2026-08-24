# OpenRouter AI routing card and provider key card UI

OpenRouter joined the panel's AI routing settings as a first-class provider: a key card row for saving/clearing the API key, a routing-mode button gated on that key, and a search-as-you-type model picker with per-million-token pricing and explicit error/empty states. The implementation spans two files — `provider-key-card.tsx` (new) and `ai-routing-card.tsx` (extended) — and mirrors the existing Grok and self-hosted patterns.

## API contract

The component talks to three backend endpoints through the typed API client (`lib/api/client.ts`). The panel's default identity headers (`X-Agent-ID`/`X-Agent-Role` defaulting to the CEO) already carry the necessary auth, so no extra auth wiring lives in these modules.

```
GET    /api/providers/openrouter-key              -> OpenRouterKeyStatus
PUT    /api/providers/openrouter-key              body: { api_key: string }
                                                  -> OpenRouterKeyStatus
GET    /api/providers/openrouter/models?q=<query> -> OpenRouterModel[]
```

```ts
interface OpenRouterKeyStatus {
  key_set: boolean; // never returns the key itself — server-side Fernet-encrypted
}

interface OpenRouterModel {
  model_name: string; // OpenRouter's internal id, e.g. "deepseek/deepseek-chat-v3"
  display_name: string; // human-readable label
  context_length: number; // token window; 0 = unknown/unset
  pricing: {
    prompt: string; // per-token float as a string (OpenRouter's native format)
    completion: string; // per-token float as a string
  };
}
```

The key endpoints are shared across two consumers (the `OpenRouterProviderKeyRow` and the `AIRoutingCard`'s own key-status read for ModeButton gating), so both use the same react-query key `["providers", "openrouter-key"]` (exported as `openRouterKeyQueryKey` from `provider-key-card.tsx`) to deduplicate. The model-search endpoint is queried directly via `api.get` with a `params: { q: debouncedSearch }` query parameter — there is no preload; the query fires only when the debounced search string is non-empty and the key is set.

## Files

| File | Role |
| --- | --- |
| `panel/src/components/settings/provider-key-card.tsx` | **New.** Exports `OpenRouterProviderKeyRow` (password input + Save/Clear buttons), `useOpenRouterKeyStatus` (react-query GET, 60s stale), `useSetOpenRouterKey` (mutation PUT, invalidates key-status + mode queries on success), and the shared `openRouterKeyQueryKey`. |
| `panel/src/components/settings/ai-routing-card.tsx` | Imports `OpenRouterProviderKeyRow` + `useOpenRouterKeyStatus`. Adds the OpenRouter `ModeButton` (Globe icon, gated on `hasOpenRouterKey`), `flipToOpenRouter` handler, debounced search model picker with error/empty/loading states, `formatPricePerMillion` helper, `openRouterSearchErrorMessage` helper, `ProviderBadge` openrouter variant (indigo, "OR"), help text, and the card-description mention. |
| `panel/src/components/settings/__tests__/ai-routing-card.test.tsx` | Updated: the "not set" badge count assertion goes from 2 to 3 (Grok + Ollama + OpenRouter), and `@/lib/api/client` is mocked for the OpenRouter key endpoint. |

## UI behavior

### Provider key card (`OpenRouterProviderKeyRow`)

- **Key status badge**: emerald "key set" or amber "not set", each wrapped in a `HelpTip` explaining what it means. The "not set" hint names the prerequisite ("Required before any agent can route to an OpenRouter model").
- **Password input + Save**: the input is `type="password"`, placeholder `sk-or-…` when no key is set or `•••••••••••• (leave blank to keep)` when one is. Save is disabled while a mutation is pending (button reads "Saving…"). An empty key with the clear checkbox unchecked is rejected with a toast ("Enter a key first").
- **Clear flow**: when a key is already set, a "Clear the stored key" checkbox appears. Checking it empties the input field and disables it; Save then sends an empty string to the PUT endpoint, which clears the key server-side. A successful clear shows "OpenRouter key cleared" and the badge reverts to "not set".
- **False-Saved bug fix** (PR #170 review issue #5): the green "Saved" badge appears **only** after the mutation succeeds (`onSuccess`), never optimistically. It also clears on any input change or clear-checkbox toggle, so a stale "Saved" can't linger after the operator edits the field. This is the specific regression the salvaged PR #170 had — the badge used to flip to "Saved" before the server confirmed, giving false confidence on a failed save.
- **Hint branch**: when no key is set, a muted paragraph explains the value proposition ("One key unlocks hundreds of models on OpenRouter — GLM, DeepSeek, Qwen, Claude, GPT and more. Stored Fernet-encrypted server-side; never returned by the API."). When a key is set, the clear checkbox replaces the hint.

### AI routing card (`AIRoutingCard`)

- **ModeButton**: a Globe-icon "OpenRouter" button sits in the routing-mode grid (grid expanded from `lg:grid-cols-9` to `lg:grid-cols-10` to fit). It is disabled until the key is set; its description reads "Save the OpenRouter key first." when gated, or "Every agent uses OpenRouter (pick a model below)." when ready. A `labelHint` on the button explains the one-key-many-models value and the V1 delivery-roles-only scope.
- **flipToOpenRouter**: mirrors `flipToGrok`. Refuses if no key is set ("Save the OpenRouter API key first"). Confirms with a dialog explaining that per-agent pins and complexity overrides survive the switch but other role/global assignments are replaced. On success, calls `applyMode` with `mode: "openrouter"` and the selected model (if any) as `default_model`. Toast: "Role/global routing now on OpenRouter — per-agent pins and complexity overrides kept".
- **Model picker**: shown only when `currentMode === "openrouter"`. A text input with a 300ms debounce (`useEffect` + `setTimeout`) drives a `useQuery` against `/providers/openrouter/models?q=`. The query is enabled only when the debounced string is non-empty **and** the key is set — there is no preload, so opening OpenRouter mode without typing does not fire a request. Results render as a scrollable (`max-h-64`) bordered list of buttons, each showing `display_name`, `model_name` (monospace), `context_length` (if > 0), and per-million-token pricing (in/out). Clicking a row sets the selected model; the selected row gets a `bg-primary/5` highlight and the model id appears below the list in a "Selected:" line.
- **Error states** (not a blank list): the picker renders an amber-bordered message with an `AlertTriangle` icon for every failure mode, using `openRouterSearchErrorMessage` to surface the specific cause:
  - HTTP 400 → "OpenRouter API key not set — save your key above first."
  - HTTP 401 → "OpenRouter auth failure — your API key may be invalid or expired."
  - HTTP 429 → "OpenRouter rate limit — too many requests, try again in a moment."
  - `ECONNABORTED`/`ETIMEDOUT` → "OpenRouter search timed out — try again in a moment."
  - Anything else → "OpenRouter API is unavailable — try again in a moment."
- **Empty state**: when the query returns zero results and the debounced search is non-empty and not loading, a bordered muted message reads `No models found for "{debouncedSearch}". Try a different search.`
- **No-key state**: when no key is set, the search input is disabled and an amber message reads "Save your OpenRouter API key above to search and pick models."
- **Pricing**: `formatPricePerMillion` converts OpenRouter's per-token float strings to human-readable per-million-token. `0.000003` → `$3.00/1M`. Zero → `Free` (OpenRouter has free models). Applied to both `pricing.prompt` (labeled "in") and `pricing.completion` (labeled "out").
- **ProviderBadge**: the `openrouter` variant uses indigo (`bg-indigo-500/20 text-indigo-700 dark:text-indigo-400`) with the label "OR". Added to the badge union type and the styles/labels maps.
- **Help text**: the card description now names OpenRouter alongside Grok, Ollama Cloud, and the CLI-authenticated providers. The routing-mode `HelpTip` lists OpenRouter in the provider enumeration. A contextual paragraph appears when `currentMode === "openrouter"` or `"mix"`, explaining that OpenRouter agents run on the opencode CLI, one key unlocks hundreds of models, and the same guards (command/secret-exfiltration, prompt-injection, per-agent cost cap) apply. V1 scope: delivery roles only, not Intake/Secretary.

### Park-reason clarity (HoM UX requirement c)

The error messaging in the model picker is the panel-side counterpart to the backend's park-reason surfacing: when a 429 parks (exit 75) or a 401/missing-key parks (exit 78), the operator sees "OpenRouter rate limit" or "OpenRouter auth failure" in the picker, not a generic "Provider unavailable". The `openRouterSearchErrorMessage` helper maps the HTTP status codes to these specific messages at the search layer; the backend's own park-reason surfacing handles the agent-spawn layer.

## Design bar

The OpenRouter UI mirrors the existing Grok and self-hosted patterns rather than inventing a new visual language: same `ModeButton` composition, same `Badge`/`HelpTip`/`Separator`/`Input` primitives, same amber-on-amber-border error/empty-state idiom used elsewhere in the card. The `ProviderBadge` openrouter variant adds one indigo entry to the existing per-provider color map — no new color scale, no new radius, no new shadow. The model picker's scrollable bordered list matches the self-hosted picker's structure but uses buttons instead of a `Select` (search-as-you-type vs. static list). Motion stays static (hover/active only); no decorative animation. `ponytail:` the inline `OpenRouterModel` type and `formatPricePerMillion`/`openRouterSearchErrorMessage` helpers live in `ai-routing-card.tsx` rather than `lib/api/providers` because fe-dev-1's parallel types/hooks were not on this branch — they should be refactored to the shared client after that PR merges.

## Self-contained implementation note

The dev's journal flags this: the `OpenRouterKeyStatus` and `OpenRouterModel` types are defined inline in the two component files, and the API calls go through `api.get`/`api.put` directly rather than through `providersApi`. This is because fe-dev-1's parallel work (task `0a581a92` — "OpenRouter types, API client, and hooks") was not on this branch at build time. After that PR merges, the inline types and direct API calls should be refactored to use the shared `useOpenRouterKey`/`useSetOpenRouterKey`/`useSearchOpenRouterModels` hooks and the `OpenRouterKeyStatus` type from `lib/api/providers`. The `RoutingMode` type also does not yet include `"openrouter"` — the component uses a local `RoutingMode | "openrouter"` union and an `as unknown as RoutingMode` cast for `applyMode` calls, which should be removed once the shared type is updated.

## Testing

```bash
cd panel
pnpm test ai-routing-card
```

The test file mocks `@/lib/api/client` to return a default no-key response (`{ key_set: false }`) for `GET /providers/openrouter-key` and an empty array for model search. The "not set" badge count assertion was updated from 2 to 3 to account for the new OpenRouter key row. The OpenRouter-specific picker states (error, empty, loading, results, no-key) are exercised through the component's conditional render paths.