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

## Visual design bar (demo/kit register)

The design bar above governs the text-card register; `kit/` pieces answer to the same restraint, but the tells are different — a demo clip fails by looking like a slide deck wearing the panel's colors, not by looking like a marketing card. Ground every rule below in the actual `pk-*` classes (`kit/kit.css`) and the shipped release compositions before inventing a new pattern; the vendored craft references now living in `skills/references/` (see the Cinematography section below) back every rule here with the underlying design theory.

- **Spacing and hierarchy** — `pk-column`'s 56px left inset is fixed, and the kit's card padding (32px/36px) and title size (34px) are the vertical-cut baseline — square cuts legitimately tighten them per orientation (`release-0.25.0/square.html` runs 18px/24px padding and a 28px title), but that's a per-orientation override, never a per-beat one. Don't crowd a second full card into the same beat just because there's vertical room: `release-0.25.0` gives each card its own ~5s scene, and `release-recap` caps its visible stack at three compact cards with tight margins. A card's own internal rhythm (title, a gap, the chip row, another gap, the status pill) is load-bearing — don't add a second meta row or a second pill just to fill space.
- **Beat density and pacing variation** — identical entrance intervals across many beats (every 5.0s via `animation-delay`, as `release-0.25.0`'s cards do — beats ride delayed CSS animations, never `data-start` clip windows; see the clip-window rule below) are fine for a receipt-style listing where the viewer is meant to learn the rhythm, but the entrance itself must still vary: swap `--pk-ease` for a springy overshoot (`cubic-bezier(0.34, 1.4, 0.64, 1)`, as `release-0.25.0` does on its cards) on at least one beat type so the library doesn't share byte-identical timing everywhere. Past ~4 evenly-spaced beats the interval itself starts to read metronomic — vary it, or break the pattern with a different-shaped beat (a receipt, a stat overlay) before the count gets there.
- **Chip and pill color-variant discipline** — `pk-pill` variants are STATUS-semantic (`progress`/`review`/`approved`/`completed` map onto real lifecycle states); never pick one for how it looks against the frame. `pk-chip` variants are CATEGORY-semantic — reuse the same variant for the same real-world category across a whole clip (and across the library), rather than rotating colors per beat for visual variety. A frame carrying four-plus chip colors with no categorical reason reads decorative, not designed.
- **Camera + cursor + beat rhythm reading as filmed** — treat each card's build (entrance), breathe (pill hold while the camera settles or pushes), and resolve (pill swap, cursor click) as its own three-phase unit, and point `pk-camera`'s `data-shots` at whichever card is actually completing rather than a fixed frame the beats happen to pass through — `release-0.25.0`'s shot list re-centers on each card in turn. Chain `pk-cursor` waypoints with room for the idle-hand sway (`kit.js`) to run between legs; a cursor that jumps beat to beat with zero rest reads like a pointer teleporting, not a hand moving.
- **Anti-generic tells** — a uniform grid of same-size cards with no hierarchy difference (reach for a one-card-per-scene sequence like `release-0.25.0`, or `release-recap`'s compact three-card stack, instead); the same ease and duration on every entrance across a whole clip (`skills/references/motion-principles.md`'s "same ease on every tween" guardrail is a CSS problem too, not just a GSAP one); and decorative motion with no product meaning — `pk-frame__statusdot`'s pulse earns its place because "Live" really is pulsing, but a second unrelated glow added only to fill a frame doesn't.

For visual vocabulary beyond what `kit/` covers today, see `skills/hyperframes-catalog-index.md` — it names which effects map onto an existing `pk-*` piece and which would need a new one.

## Cinematography & rhythm (demo register)

A panel-demo clip is a FILM of software being used, not a screenshot with captions. Before authoring, write a shot list: for every beat, where is the camera, where is the cursor, what changes on screen, and what caused it. Then build to it. The specific tells that get a cut bounced:

- **A locked-off camera.** Wrap the scene in `pk-camera` and drive it with `data-shots` (see `kit/README.md`): open slightly tight, push toward each beat's focal point (`scale <= 1.08`, translate <= ~160px — the audience should feel it, not see it), pull wide for a reveal, settle to end. A static frame for more than ~8s is dead air.
- **A cursor that doesn't behave like a hand.** Drive it with `data-waypoints`: it fades in, travels with eased legs, rests with an idle sway (kit adds this), clicks with a visible cause→effect (the click precedes the thing it triggers), and leaves the frame — it never pops in, freezes pixel-still, or blinks out mid-scene.
- **A metronome.** Identical beat lengths with identical flat entrances read as a slideshow. Vary entrance energy (the kit cards take a springy overshoot well), let a beat breathe after a click, and give the climax (stats/receipt) a different rhythm than the build.
- **Verify motion, not stills.** After `request_render`, sample PAIRS of frames ~0.5s apart around each cursor/camera beat and compare positions — a single frame proves presence, only a pair proves movement.
- **The vendor's authoring doctrine is vendored in `skills/`.** Read `skills/hyperframes-core.md` (composition contract), `skills/hyperframes-keyframes.md` (seek-safe keyframes across runtimes), and `skills/hyperframes-creative.md` (beat planning) before authoring — they are the official HyperFrames agent skills, vendored at a pinned upstream commit (Apache-2.0, header in each file; re-vendor when bumping `@hyperframes/producer`). `skills/hyperframes-creative.md` in turn points at `skills/references/house-style.md`, `video-composition.md`, `beat-direction.md`, and `motion-principles.md` — four of its own reference docs, vendored the same way, that cover palette/lazy-defaults, video-medium scale and density, per-beat rhythm planning, and ease/speed/direction variance in more depth than the summary above. Note the vendor's primary seek-safe animation primitive is GSAP tweens registered on `window.__timelines` — this kit's CSS-animation register is a house pattern, and the clip-window rule below is its empirically-derived seek-safety companion.
- **Clip windows are for structural layers only.** The renderer's per-clip visibility scheduler drifts badly behind the encoded timeline on long compositions (measured live: a 40s cut whose per-beat clips only reached the ~19s mark by the final frame — the entire tail silently missing from the MP4). Give `class="clip"` + `data-start`/`data-duration` only to full-length structural layers (the cold-open, the panel frame), and drive every BEAT inside them with the kit's pattern instead: base-hidden styles (`opacity: 0`) plus a delayed CSS animation (`animation-delay` + `forwards`/`both` fill) — those run on the correct clock. If a beat must also disappear, give it an exit animation, not a clip window.

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

## Release-specific example: `release-0.25.0`

`compositions/release-0.25.0/` is a panel-demo kit clip for the RoboCo v0.25.0 release. It builds on the `kit/` register instead of the text-card style, so it has no `theme.css` of its own.

The current revision runs **40 seconds** total (up from an earlier 14s cut) so every feature card is fully visible before the next one enters. The story is still "governance gets a better UI": the CEO types "Governance gets a better UI" into the panel intake at 3.6s, then four shipped feature cards enter the kanban column one per scene and flip from `in progress` to `completed`:

1. **Env ladder** — enters at 5.0s, completes at 6.6s ("Dev to prod, one rung at a time.").
2. **Collision map** — enters at 10.0s, completes at 11.6s ("See who touched what before you review.").
3. **Metrics donut** — enters at 15.0s, completes at 16.6s ("90 days of real task flow.").
4. **Notification bell** — enters at 20.0s, completes at 21.6s ("Real read/ack actions on every alert.").

Each card gets roughly five seconds of fully visible time before the next card enters. A cursor clicks the column at 22.0s, the stats overlay shows "1 release / 25 agents / 1 human" from 24.0s to 32.0s, the toast "v0.25.0 shipped / I approved once. 25 agents did the rest." runs from 30.0s to 38.0s, and the "roboco.tech" outro lands at 36.0s and holds through the end.

The composition reuses the same `pk-frame` chrome, `pk-column`/`pk-card`, `pk-pill`, `pk-cursor`, `pk-toast`, and `pk-outro` pieces from `kit/`, plus the typing reveal wired through `props.js`. Each feature card uses the `pk-pill--swap-out` / `pk-pill--swap-in` pattern from `panel-demo` and `release-recap` to replace the `in progress` pill with `completed` on the same beat.

### Preview / test this composition

```bash
pnpm preview:release-0.25.0
pnpm test   # release-0.25.0.test.js is picked up by vitest
```

### `props.js` shape

```js
{
  introText: string,   // text that types into the panel intake field
  toastTitle: string, // headline inside the shipping toast
  toastBody: string,   // sub-line inside the shipping toast
}
```

`window.__ORIENTATION__` is set for local preview only; the sidecar overwrites both globals at render time.

### `captions.json`

Same schema as `release-recap`: one `captions.json` next to the HTML with self-verified X and TikTok captions. The X caption was updated to include the Notification bell feature and now totals **216 characters**:

```json
{
  "composition_id": "release-0.25.0",
  "occasion": "release: RoboCo v0.25.0",
  "platforms": {
    "x":      { "caption": "...", "char_count": 216, "limit": 280,  "within_limit": true },
    "tiktok": { "caption": "...", "char_count": 347, "limit": 2200, "within_limit": true }
  }
}
```

### Smoke-test invariants

`release-0.25.0.test.js` extends the panel-demo register checks: both `vertical.html` (1080×1920) and `square.html` (1080×1080) parse with `data-duration="40"` and the HyperFrames params, the kit CSS/JS wiring is present, **four** feature cards each carry a progress-to-completed pill swap and include the "Notification bell" text, the cursor and toast appear, the outro shows "roboco.tech", no external scripts are loaded, and no em dashes slip into on-screen copy or captions.

## Release-specific example: `release-0.26.0`

`compositions/release-0.26.0/` is a panel-demo kit clip for the RoboCo v0.26.0 release, mirroring the structure and pacing of release-0.25.0. It builds on the `kit/` register instead of the text-card style, so it has no `theme.css` of its own.

The composition runs **40 seconds** total. The story is "the control plane gets a lock" (the hero tagline): the CEO types "The control plane gets a lock" into the panel intake at 3.6s, then four shipped security/feature cards enter the kanban column one per scene and flip from `in progress` to `completed`:

1. **Off the public net** — enters at 5.0s, completes at 6.6s ("127.0.0.1 only, nginx does the rest."). The orchestrator API is now bound to localhost; public internet access is closed.
2. **3 forges, 1 API** — enters at 10.0s, completes at 11.6s ("GitHub, Gitea, GitLab. Pick your forge."). Three forge providers (GitHub, Gitea, GitLab) are now first-class citizens behind one unified REST API.
3. **Telegram cockpit** — enters at 15.0s, completes at 16.6s ("Today brief, approvals, chat. One socket."). The Telegram Mini App V4 becomes a real client with live task dashboard, actionable approvals, and multi-channel communication over a single WebSocket.
4. **Guard mode: active** — enters at 20.0s, completes at 21.6s ("fastapi-guard stops watching, starts blocking."). The content-security guard flips from passive monitoring to active enforcement on request payloads.

Each card gets roughly five seconds of fully visible time before the next card enters. A cursor clicks the intake at 4.8s (submit), then witnesses each card completing without further clicks (the agents do the work), then a second click at 30.8s (acknowledge the toast). The stats overlay shows "1 release / 3 forges / 0 leaks" from 24.0s to 32.0s, the toast "v0.26.0 shipped / I approved once. 25 agents shipped it." runs from 30.0s to 38.0s, and the "roboco.tech" outro lands at 36.0s and holds through the end.

The composition reuses the same `pk-frame` chrome, `pk-column`/`pk-card`, `pk-pill`, `pk-cursor`, `pk-toast`, and `pk-outro` pieces from `kit/`, plus the typing reveal wired through `props.js`. Each feature card uses the `pk-pill--swap-out` / `pk-pill--swap-in` pattern to replace the `in progress` pill with `completed` on the same beat. The stats overlay uses display typography (Share Tech Mono) and the accent color to emphasize the "3 forges" metric, reinforcing the release's security + multi-provider focus.

### Preview / test this composition

```bash
pnpm preview
pnpm test   # release-0.26.0.test.js is picked up by vitest
```

### `props.js` shape

```js
{
  introText: string,   // text that types into the panel intake field
  toastTitle: string, // headline inside the shipping toast
  toastBody: string,   // sub-line inside the shipping toast
}
```

`window.__ORIENTATION__` is set for local preview only; the sidecar overwrites both globals at render time.

### `captions.json`

Same schema as prior releases: one `captions.json` next to the HTML with self-verified X and TikTok captions. The X caption totals **182 characters** (within the 280 limit); the TikTok caption **419 characters** (within the 2200 limit):

```json
{
  "composition_id": "release-0.26.0",
  "occasion": "release: RoboCo v0.26.0",
  "platforms": {
    "x":      { "caption": "v0.26.0 is out.\nOrchestrator API is off the public internet now.\nGitHub, Gitea, GitLab: one API.\nTelegram cockpit is a real client.\nI approved once. 25 agents shipped it.\nroboco.tech", "char_count": 182, "limit": 280,  "within_limit": true },
    "tiktok": { "caption": "v0.26.0 is out.\n\nThe orchestrator API is off the public internet. No more raw access to the control plane.\n\nThree forges are first class now: GitHub, Gitea, GitLab. One API, one review flow, pick your forge.\n\nThe Telegram Mini App is a real client now: today brief, approvals, chat, all live off one socket.\n\nfastapi-guard is active enforcement. Not passive anymore.\n\nI approved once. 25 agents shipped it.\n\nroboco.tech", "char_count": 419, "limit": 2200, "within_limit": true }
  }
}
```

### Smoke-test invariants

`release-0.26.0.test.js` extends the panel-demo register checks: both `vertical.html` (1080×1920) and `square.html` (1080×1080) parse with `data-duration="40"` and the HyperFrames params, the kit CSS/JS wiring is present, **four** feature cards each carry a progress-to-completed pill swap and include the exact titles ("Off the public net", "3 forges, 1 API", "Telegram cockpit", "Guard mode: active"), the stats overlay renders the three-line receipt (1 release / 3 forges / 0 leaks), the cursor and toast appear, the outro shows "roboco.tech", no external scripts are loaded, and no em dashes slip into on-screen copy or captions.
