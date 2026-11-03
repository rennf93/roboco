# Z.ai and Hummin routing modes

The AI Providers card (`panel/src/components/settings/ai-routing-card.tsx`) offers one-click fleet-wide mode buttons for every apply-capable provider. This note covers the two GLM buttons, `Hummin (GLM)` and `Z.ai (GLM)`, and the copy cleanup that shipped with the Z.ai button.

## Z.ai mode button

`flipToZai` mirrors the Hummin, Nebius and OpenRouter flips:

1. The key is read through `useZaiKey()` (`hasZaiKey = !!zaiKeyStatus?.has_key`).
2. The `Z.ai (GLM)` `ModeButton` is disabled while `applyMode` is pending or no Z.ai key is saved. Its description reads "Save the Z.ai key first." until a key exists, then "Every agent uses Z.ai GLM."
3. If the handler is somehow reached without a key it toasts "Save the Z.ai API key first" and returns.
4. Otherwise it asks for confirmation ("Switch every agent to Z.ai GLM? Per-agent pins and complexity overrides are kept; other role/global assignments are replaced."), then calls `applyMode.mutateAsync({ mode: "zai" })`.
5. The button is `active` when `currentMode === "zai"`, where `currentMode` is the mode the backend snapshot reports (the backend's `derive_mode`).

The Z.ai key is separate from the Hummin key. Z.ai uses its Anthropic-compatible GLM endpoint; Hummin runs the GLM-native hummin CLI.

`RoutingMode` (`panel/src/lib/api/providers.ts`) and `ModelProvider` (`panel/src/types/index.ts`) include `zai`, `hummin`, `nebius` and `openrouter`, so every mode `derive_mode` can return renders a labeled button.

## Interactive roles: no exclusion copy

The backend's `INTERACTIVE_UNSUPPORTED_PROVIDERS` is empty, so every provider can power Intake, Secretary and PR Review. The panel therefore carries no "V1: delivery roles only, not Intake/Secretary" text: it was removed from confirm dialogs, hints, the card description and the mode paragraphs. Do not reintroduce a panel-side exclusion list; if the backend ever restricts a provider again, mirror that restriction from the API response rather than hard-coding it.

## Tests

```bash
cd panel
pnpm test ai-routing-card
```

`panel/src/components/settings/__tests__/ai-routing-card.test.tsx` has a `Hummin and Z.ai mode round-trips` block, parameterized over both modes:

- Applying a mode POSTs `applyMode({ mode })`; the mocked snapshot then returns the stored mode, as `derive_mode` does server-side, and the test asserts that provider's button shows the `active` badge after the refetch and the other GLM button does not.
- Each button is disabled while its key is not saved.

The test file also mocks `getHumminKey` / `setHumminKey` next to the existing Z.ai key mocks.
