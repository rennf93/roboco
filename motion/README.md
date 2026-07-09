# motion

In-repo [HyperFrames](https://github.com/heygen-com/hyperframes) composition package. UX/UI devs author bespoke marketing-video compositions here on a normal delivery branch; once a composition merges, the orchestrator's render loop tars this directory and POSTs it to the `video-renderer` sidecar (T1 rewrote its render core to `@hyperframes/producer`), which renders the HTML to MP4 with Chromium. This package never renders itself — its own gate (`pnpm test`) is static: a vitest HTML-structure smoke against the authored files. The render-truth check happens on the sidecar, against the merged files.

## Adding a composition

Compositions live under `compositions/<composition_id>/`. Each composition is one directory containing the HTML/CSS/JS for that clip; one HTML file per orientation (the sidecar points `inputPath` at a single HTML file, so per-orientation branching belongs in its own file, not inside one file).

1. Create `compositions/<your_clip>/vertical.html` and `square.html` carrying the HyperFrames render params on `<html>`: `data-width`, `data-height`, `data-duration` (seconds), `data-fps`. Timed visible elements get `class="clip"` plus `data-start`, `data-duration`, and `data-track-index`.
2. Add a shared `theme.css` with `@font-face` declarations (fonts are vendored under `public/fonts/*.woff2` — never load from a CDN, the render is offline) and the color/type tokens for the clip. (Skip this if you're building in the panel-demo register below — `kit/kit.css` already owns the reset + fonts.)
3. Ship a `props.js` with default `window.__PROPS__` + `window.__ORIENTATION__` values for local preview. The sidecar OVERWRITES this file at render time with the real per-release values — your HTML loads it via `<script src="props.js"></script>` before any inline script that reads the globals.
4. Add `<your_clip>.test.js` (vitest) asserting the HTML-structure invariants — dimensions, props.js wiring, theme link, at least one `class="clip"` element, no CDN scripts. See `release-announcement.test.js` as a template.
5. `pnpm preview` (alias for `hyperframes preview <file>`) opens the local preview server; `pnpm lint` runs `hyperframes lint` over both orientations; `pnpm test` runs the smoke gate that CI runs.

## `release-announcement` — props shape

The composition reads `window.__PROPS__` (written into `props.js` by the sidecar at render time):

```js
{
  script: string,      // one or two sentences — the voiceover-style hook
  version: string,     // e.g. "0.19.0" — rendered as "v0.19.0"
  highlights: string[],// shipped-feature bullets; only the first 4 render
}
```

`window.__ORIENTATION__` is also set by the sidecar, but each HTML file is for ONE orientation (the sidecar points `inputPath` directly at `compositions/release-announcement/vertical.html` or `square.html`), so the orientation is known at author time and there is no runtime branch. Vertical is **1080×1920** (TikTok + X mobile), square is **1080×1080** (X timeline). Width is always 1080; only the height (and so the available vertical canvas) changes. Square uses a tighter bottom pad (72) and scan-line top (72) than vertical (148 / 104) — those differences are baked into the respective HTML files.

## Design bar for future compositions

This composition is the library's reference point — match its restraint, don't reinvent the palette per clip. Dials (see the org's design-bar doctrine): **variance ~6, motion ~6, density ~4** — an energetic landing/marketing register, not a dense dashboard.

- **Color** — `compositions/<id>/theme.css` is the single source: a near-black ink field (never pure `#000`), warm off-white text (never pure `#fff`), and **one** accent color used with intent (a label, a rule, a marker — not washed across the frame). Reuse the theme tokens; don't hardcode new hex values per composition.
- **Type** — **Share Tech Mono** (a single static weight, 400 Regular — no bold/italic exists for this family) for the one big headline moment, paired with **Inter** as the clean workhorse body face for everything else. Both are vendored under `public/fonts/*.woff2` and loaded via `@font-face` in `theme.css` rather than fetched from a CDN — rendering never depends on network access or on whatever fonts happen to be installed on the render host. At least two weights of the body face, so hierarchy comes from more than just size.
- **Motion** — entrances are CSS keyframe animations with a restrained easing (`cubic-bezier(0.22, 1, 0.36, 1)` — smooth settle, no overshoot, approximating Remotion's `spring({damping:17, mass:0.7, stiffness:140})` without the bounce), staggered across elements via `animation-delay` rather than all firing on frame 0. Keep one continuous ambient motion (here: the scanning accent line) so the frame is never fully static once entrances land. Every animation should earn its place — hierarchy, or a beat of pacing, not motion for its own sake.
- **Layout** — anchor content asymmetrically (this clip sits in the lower two-thirds, left-aligned); avoid a perfectly centered card, which reads as a generic template rather than a designed frame.
- **AI tells to avoid** — no default AI-purple gradient wash, no centered-everything, no emoji as design elements, no one-font-one-size, no em dash in on-screen copy (voiceover script, highlight bullets, kicker text) or filler verbs ("Elevate", "Seamless", "Unleash", "Next-Gen").

## Panel-demo kit (`kit/`)

`kit/` is a second register alongside the release-announcement's text-card style: reusable `pk-`-namespaced CSS/HTML that recreates the control panel's look (dark chrome, task cards, status pills, toasts, a typing reveal, a cursor) so a composition can simulate the product actually being used, instead of announcing it over a headline. Use the **text-card register** (release-announcement's pattern) for version/feature announcements with no product visuals; use the **demo register** (`kit/`) whenever the story is "watch this happen in the app" — a task moving through the panel, a feature being triggered, an agent doing something visible.

`compositions/panel-demo/` is the reference composition: a task title types into an intake field, a card materializes in a column, a cursor clicks it done, a toast confirms, out on "roboco.tech". Start a new demo-register composition from its structure and `kit.css`'s classes rather than reinventing the panel's chrome per clip. See `kit/README.md` for the full piece-by-piece reference.

## Release-specific example: `release-recap` (0.18.0 - 0.20.0)

`compositions/release-recap/` is a demo-register clip built on `kit/`, not the release-announcement text-card style — the CEO rejected an earlier text-card cut of this same occasion ("Build this in the panel-demo register... the video must show the product moving... Do not invent a new visual language"). It ships the same two orientations as every other composition — `vertical.html` (1080×1920) and `square.html` (1080×1080) — sharing `props.js` and the offline-render constraints, but no `theme.css` of its own since `kit/kit.css` owns the look.

The story is "three releases shipped in six days": a single intake types "3 releases in 6 days", then three release cards (v0.18.0, v0.19.0, v0.20.0) cycle through **the same kanban slot** — each card is absolutely positioned at the same spot inside the column and painted after the previous, so the later card's solid background fully covers the one before it, a beat swap that reuses `panel-demo`'s exact single-card geometry per orientation instead of stacking three cards' worth of height (which would collide with the toast in the square cut). Each beat gets its own status-pill flip (`in progress` -> `completed`) and its own cursor click at the same parked position (only the first click glides in; the other two are click-only, `x0==x1`/`y0==y1`), then one toast and the "roboco.tech" outro once all three land.

### Preview / test this composition

```bash
npx hyperframes preview compositions/release-recap/vertical.html
npx hyperframes preview compositions/release-recap/square.html
pnpm test   # runs vitest on all *.test.js under motion/
```

### `captions.json`

Like every release composition, this one ships a tracked `captions.json` next to the HTML holding the X and TikTok captions the render pipeline proposes alongside the MP4, self-verifying character counts against each platform's limit:

```json
{
  "composition_id": "release-recap",
  "platforms": {
    "x":      { "caption": "...", "char_count": 136, "limit": 280,  "within_limit": true },
    "tiktok": { "caption": "...", "char_count": 365, "limit": 2200, "within_limit": true }
  }
}
```

The smoke test (`release-recap.test.js`) asserts this schema, checks the counts, and regression-guards no em dashes in on-screen copy or captions (the design-bar violation QA caught on the prior text-card cut of this task).
