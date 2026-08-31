# OpenRouter Routing UI

The AI Routing card (`panel/src/components/settings/ai-routing-card.tsx`) and the
OpenRouter key row (`panel/src/components/settings/provider-key-card.tsx`) expose
OpenRouter as a fleet-wide routing mode: save a key, hit the OpenRouter
mode button, and pick a default model from OpenRouter's catalog via the
debounced search picker.

## Shared types + hooks

The OpenRouter API contract lives in `panel/src/lib/api/providers.ts`:

- `OpenRouterModel` — one entry from `GET /providers/openrouter/models?q=`.
  OpenRouter's native wire format (per-token prices as strings); `pricing`
  and its `prompt`/`completion` fields, plus `context_length`, are all
  nullable because OpenRouter's catalog genuinely contains unpriced models.
- `OpenRouterKeyStatus` — `{ key_set: boolean }` from
  `GET/PUT /providers/openrouter-key`.
- `providersApi.getOpenRouterKey` / `setOpenRouterKey` / `searchOpenRouterModels`.

`panel/src/hooks/use-providers.ts` exposes `useOpenRouterKey`,
`useSetOpenRouterKey`, and `useSearchOpenRouterModels` keyed off
`providerKeys.openRouterKey()` / `providerKeys.openRouterModels(query)`. Both
consumers ("OpenRouter API key" row and the mode card) go through these —
there are no hand-rolled query keys or inline duplicate contract types.

`RoutingMode` includes `"openrouter"`, so `applyMode` payloads pass
`mode: "openrouter"` directly with no casts, and the card's `currentMode` is
plainly typed `RoutingMode`.

## Pricing rendering

- `formatPricePerMillion` converts OpenRouter's per-token string prices to
  per-million figures ("0.000003" → "$3.00/1M"). Any value `<= 0` renders as
  "—" — OpenRouter's "-1" string is the unknown-price sentinel and must never
  render as a negative dollar figure. Sub-cent per-million prices render with
  a third decimal ("$0.004/1M") so they don't look free.
- A model row guards every render through the nullable shape: `pricing` null
  or null prompt/completion renders "—" instead of throwing, and a null
  `context_length` simply omits the context line.

## UI behavior

- **Key row** (`OpenRouterProviderKeyRow`): password input with Save/Clear;
  the green "Saved" badge appears only after the mutation's `onSuccess` and
  clears on any input change, never optimistically.
- **Mode button + flip**: the OpenRouter `ModeButton` (Globe icon) is disabled
  until the key is set. Confirming the flip replaces role/global assignments
  while keeping per-agent pins and complexity overrides; the payload passes
  `mode: "openrouter"` plus the picked model as `default_model` only when one
  is selected.
- **Model picker states** (rendered only in `openrouter` mode): with no key
  the input is disabled behind an amber "Save your OpenRouter API key above…"
  notice; a failing search renders the status-specific copy below; zero
  results render `No models found for "…"`. There is no preload — the query
  fires only for a non-empty debounced search while the key is set.

The picker surfaces the cause of a failure through `openRouterSearchErrorMessage`
(the panel-side counterpart to the backend's park-reason surfacing for exit 75/78):

| Failure | Copy |
| --- | --- |
| HTTP 400 | "OpenRouter API key not set — save your key above first." |
| HTTP 401 | "OpenRouter auth failure — your API key may be invalid or expired." |
| HTTP 429 | "OpenRouter rate limit — too many requests, try again in a moment." |
| `ECONNABORTED` / `ETIMEDOUT` | "OpenRouter search timed out — try again in a moment." |
| Anything else | "OpenRouter API is unavailable — try again in a moment." |

The `ProviderBadge` gains an `openrouter` variant (indigo, "OR") — a new entry
in the existing per-provider color map, no new visual language.

## Mix mode / per-agent pins

The Mix-mode per-agent select lists only backend catalog models
(`roboco/services/llm.py`'s catalog). OpenRouter models are stored via
`provider_type_override` outside the catalog, so there is no catalog entry to
offer a select row against — **per-agent OpenRouter pins are out of V1 scope
by decision** (recorded in the cell PM's journal). Do not extend the select
to synthesize OpenRouter entries; the exclusions ship intentionally.

## Testing

```bash
cd panel
pnpm test ai-routing-card
```

Focused coverage lives in
`panel/src/components/settings/__tests__/ai-routing-card.test.tsx`:

- `OpenRouter model picker` — mode-application payload (`mode: "openrouter"`,
  with and without a picked default model), the debounced search (nothing
  fetched while the query is empty; one fetch per debounced keystroke),
  null-safe pricing rows ("—" for a missing/unknown price), sub-cent prices
  rendered with a third decimal, the status-specific error copy for a failing
  models query, the empty "no models found" state, and the key-not-set notice.
- `OpenRouter key row` (via `OpenRouterProviderKeyRow`) — the "Saved" badge
  appears only after a successful mutation and clears on any input change.
