# motion

In-repo [Remotion](https://www.remotion.dev/) (v4) project. UX/UI devs author bespoke marketing-video compositions here on a normal delivery branch; once a composition merges, the orchestrator's render loop tars this directory and POSTs it to the `remotion-renderer` sidecar, which bundles + renders it to MP4. This package never renders itself — its own gate (`pnpm typecheck` + `pnpm test`) is static: type-correctness and a jsdom component-mount smoke. The render-truth check happens on the sidecar, against the merged code.

## Adding a composition

1. Add `src/compositions/YourClip.tsx`, exporting the component, its props type, and a `calculateMetadata` function (see `ReleaseAnnouncement.tsx`).
2. Register it in `src/Root.tsx` with a unique `id` — that `id` is the `composition_id` the authoring dev passes to `propose_video`.
3. Add a same-shape test file (`YourClip.test.tsx`) asserting `calculateMetadata`'s dimensions and a `<Thumbnail>` mount smoke — copy `ReleaseAnnouncement.test.tsx` as a template.
4. `pnpm dev` opens Remotion Studio (`src/index.ts` as the entry) to preview locally. `pnpm typecheck` / `pnpm test` are the same checks CI runs.

## `ReleaseAnnouncement` — inputProps shape

```ts
type ReleaseAnnouncementProps = {
  script: string; // one or two sentences — the voiceover-style hook
  version: string; // e.g. "0.19.0" — rendered as "v0.19.0"
  highlights: string[]; // shipped-feature bullets; only the first 4 render
  orientation: "vertical" | "square";
};
```

`orientation` is what `calculateMetadata` reads to pick the frame: **1080×1920** for `"vertical"` (TikTok + X mobile) or **1080×1080** for `"square"` (X timeline). Width is always 1080 — only the height (and so the available vertical canvas) changes. There is no width/height render parameter in Remotion; the orchestrator's `RemotionRenderer` sends the identical `inputProps` object to both `selectComposition` and `renderMedia`, and `calculateMetadata` branches on `inputProps.orientation` to return the right frame for whichever cut is being rendered (the sidecar calls `/render` twice, once per orientation).

## Design bar for future compositions

This composition is the library's reference point — match its restraint, don't reinvent the palette per clip. Dials (see the org's design-bar doctrine): **variance ~6, motion ~6, density ~4** — an energetic landing/marketing register, not a dense dashboard.

- **Color** — `src/theme.ts` is the single source: a near-black ink field (never pure `#000`), warm off-white text (never pure `#fff`), and **one** accent color used with intent (a label, a rule, a marker — not washed across the frame). Reuse `theme.ts`'s tokens; don't hardcode new hex values per composition.
- **Type** — **Share Tech Mono** (a single static weight, 400 Regular — no bold/italic exists for this family) for the one big headline moment, paired with **Inter** as the clean workhorse body face for everything else. Both are vendored under `public/fonts/*.woff2` and loaded via `@remotion/fonts`' `loadFont()` (see `src/theme.ts`) rather than fetched from Google Fonts at render time — rendering never depends on network access or on whatever fonts happen to be installed on the render host. At least two weights of the body face, so hierarchy comes from more than just size.
- **Motion** — entrances are `spring()`-driven (a little overshoot, quick settle — see `springConfig` in `ReleaseAnnouncement.tsx`), staggered across elements rather than all firing on frame 0. Keep one continuous ambient motion (here: the scanning accent line) so the frame is never fully static once entrances land. Every animation should earn its place — hierarchy, or a beat of pacing, not motion for its own sake.
- **Layout** — anchor content asymmetrically (this clip sits in the lower two-thirds, left-aligned); avoid a perfectly centered card, which reads as a generic template rather than a designed frame.
- **AI tells to avoid** — no default AI-purple gradient wash, no centered-everything, no emoji as design elements, no one-font-one-size, no em dash in on-screen copy (voiceover script, highlight bullets, kicker text) or filler verbs ("Elevate", "Seamless", "Unleash", "Next-Gen").
