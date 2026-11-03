# Z.ai and Hummin Routing UI

The AI Routing card (`panel/src/components/settings/ai-routing-card.tsx`) offers one-click, key-gated fleet-wide mode buttons for every apply-capable provider added to the roster: OpenRouter, Nebius, Hummin (GLM) and Z.ai (GLM). This note covers the Z.ai button added with the roster completion, the copy cleanup that shipped with it, and the round-trip tests.

## Z.ai mode button

`flipToZai` mirrors `flipToHummin` and the other key-gated flips:

- The key state comes from `useZaiKey()` (`panel/src/hooks/use-providers.ts`); `hasZaiKey` is `!!zaiKeyStatus?.has_key`.
- The "Z.ai (GLM)" `ModeButton` is disabled while a mode apply is pending or the Z.ai key is not saved. Its description reads "Save the Z.ai key first." until the key exists, then "Every agent uses Z.ai GLM."
- Clicking without a key toasts "Save the Z.ai API key first". With a key it asks for confirmation (per-agent pins and complexity overrides are kept; other role/global assignments are replaced), then calls `applyMode.mutateAsync({ mode: "zai" })`.
- The button is highlighted when `currentMode === "zai"`, where `currentMode` is the mode the backend's `derive_mode` reports in the snapshot.

Hummin and Z.ai use separate keys. The Hummin button is gated by `useHumminKey()`, the Z.ai button by `useZaiKey()`. Saving one never enables the other. The Z.ai button routes through Z.ai's Anthropic-compatible GLM endpoint; the Hummin button runs the GLM-native hummin CLI with its key injected as `ZAI_API_KEY`.

`RoutingMode` (`panel/src/lib/api/providers.ts`) and `ModelProvider` (`panel/src/types/index.ts`) already carried `zai`, `hummin`, `nebius` and `openrouter`, so every `derive_mode` value renders as a labeled button.

## Copy cleanup

All "V1: delivery roles only, not Intake/Secretary" text was removed from the card: the OpenRouter and Nebius confirm dialogs, the card description, the Codex/Gemini/Kimi/OpenRouter/Nebius/Hummin button hints, and the per-mode explanatory paragraphs. The backend's `INTERACTIVE_UNSUPPORTED_PROVIDERS` (`roboco/services/llm.py`) is now empty, so the panel keeps no exclusion list and shows no scope caveat.

## Testing

```bash
cd panel
pnpm test ai-routing-card
```

`panel/src/components/settings/__tests__/ai-routing-card.test.tsx` has a `Hummin and Z.ai mode round-trips` block, with `getHumminKey`/`setHumminKey` added to the providers API mock:

- Applying the mode POSTs `{ mode }`. The mocked snapshot GET then returns the stored mode, as `derive_mode` does server-side. The test asserts the matching button shows the "active" badge after the refetch and the other GLM button does not.
- Each button is disabled while its own key is not saved.

Out of scope: the full panel gate showed unrelated timeout flakiness in the dev's runs, and no non-panel files were changed.
