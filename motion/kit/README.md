# kit

Reusable plain-CSS/HTML building blocks that recreate the RoboCo control panel's look, for compositions that want to simulate the product rather than run a text card. Hand-rolled (no React, no Tailwind, no bundler) — this is a recreation of the panel's design language, not a port of its components. Load `kit.css` (and `kit.js` if you use the typing helper); no composition-level `theme.css` is needed, kit.css owns the reset + fonts.

```html
<link rel="stylesheet" href="../../kit/kit.css" />
<script src="props.js"></script>
<script src="../../kit/kit.js"></script>
```

Every class is namespaced `pk-`. See `compositions/panel-demo/` for a full worked example (task types in, a card appears, a cursor clicks it done, a toast confirms it).

## Pieces

- **`pk-frame`** — the app chrome: a slim sidebar (`pk-frame__sidebar`, `pk-frame__navitem[--active]`) and a top bar (`pk-frame__topbar`, `pk-frame__search`, `pk-frame__status` for the pulsing "Live" dot) around a `pk-frame__content` area. Fixed sidebar/topbar sizing works at both 1080x1920 and 1080x1080 — only the content area's height changes.
- **`pk-column`** / **`pk-card`** — a kanban-ish list container (`pk-column__header` + children) hosting task cards. A card is `pk-card__title`, `pk-card__meta` (chips + `pk-card__assignee`), and `pk-card__status` (a pill).
- **`pk-chip`** — a small team/priority tag: `<span class="pk-chip pk-chip--purple">Frontend</span>`. Variants: `--purple`, `--blue`, `--green`, `--orange`, `--pink`, `--gray`.
- **`pk-pill`** — a status pill: `<span class="pk-pill pk-pill--progress">in progress</span>`. Variants: `--progress`, `--review`, `--approved`, `--completed`. To swap one pill for another (e.g. progress -> completed), stack two pills at the same spot inside a `position: relative` `pk-card__status` and give the outgoing one `pk-pill--swap-out` (fades in then out) and the incoming one `pk-pill--swap-in` (fades in and stays), each with its own `--pk-pill-enter-delay` / `--pk-pill-exit-delay` (composition-absolute seconds — the same convention `data-start` uses).
- **`pk-toast`** — a notification card that slides in from a corner: `pk-toast__icon` + `pk-toast__body` (`pk-toast__title`, `pk-toast__text`). Position via `--pk-toast-bottom` (right-anchored).
- **`pk-type`** / **`pk-caret`** — character-by-character typing reveal. Write the target element's final text directly in the HTML, then call `window.PanelKit.typeText(el, { delay, stagger })` (from `kit.js`) once, synchronously, before rendering starts — it splits the text into `pk-type__char` spans with staggered `animation-delay`s (works with any font, no per-character width math). `delay` is the composition-absolute second the first character appears; `stagger` defaults to 0.045s/char. Add a `<span class="pk-caret"></span>` next to it for the blinking caret.
- **`pk-cursor`** — a small CSS-shape cursor (`pk-cursor__glyph`). Preferred: give it `data-waypoints="t x y [click]; t x y; ..."` (composition-absolute seconds, content-box px) and call `PanelKit.choreographAllCursors()` in the composition's inline script — kit.js generates a multi-leg eased path, a fade-in/out (never pop in or blink out), an idle-hand sway between legs, and a click ring + glyph press dip at every waypoint flagged `click`. The click should land ~0.2s BEFORE the thing it visually triggers. Legacy single-glide via `--pk-cursor-x0/y0/x1/y1` + `--pk-cursor-delay`/`--pk-click-delay` still works for a one-move cameo.
- **`pk-camera`** — a full-stage wrapper driven by `data-shots="t x y scale; ..."` + `PanelKit.choreographAllCameras()`: eased camera moves (push-ins toward the beat's focal point, pull-backs for reveals). Wrap the whole `pk-frame` in one; everything inside, cursor included, rides the move. Keep it subtle — `scale <= 1.08`, translate <= ~160px — and end settled at identity.

## Reaching for a catalog-grade beat

`kit/kit.css` already has the piece for most beats: a `pk-chip` for a one-word category tag, a `pk-pill` for a lifecycle status (never the other way around — see "Chip and pill color-variant discipline" in `motion/README.md`'s Visual design bar section), a `pk-card` when the beat is about one real thing gaining detail over time, and a `pk-toast` for a one-shot confirmation that shouldn't compete with the column. When a brief wants a look nothing here covers — a code diff, a chart, a device 3D showcase — check `motion/skills/hyperframes-catalog-index.md` for the closest public HyperFrames reference before improvising a one-off composition-local hack; it also says which effects already map onto `pk-*` and which would need a genuinely new kit piece (a normal dev task, not a composition workaround).

## Offline constraints (same as every composition)

No CDN, no npm runtime deps, no bundler — plain files only, loaded via relative paths (`../../kit/...` from a composition two levels down). Fonts are the vendored `motion/public/fonts/*.woff2`, loaded via `@font-face` in `kit.css`. Motion is CSS keyframes timed with `animation-delay` (seconds from frame 0 — no `requestAnimationFrame`, no `Date.now()`), so a render is frame-deterministic.
