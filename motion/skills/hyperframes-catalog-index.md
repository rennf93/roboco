# HyperFrames catalog index

RoboCo-authored reference index of the public HyperFrames catalog (`https://hyperframes.heygen.com`, machine index `llms.txt`, fetched 2026-07-17) — 109 blocks + 24 components, 133 entries total, grouped below by kind. This is design vocabulary to study and EMULATE with the RoboCo kit (`motion/kit/`), not a list of installable dependencies: none of this is vendored code, `npx hyperframes add` is not part of this repo's toolchain, and the offline-render constraint (no CDN, no npm runtime deps — see `motion/kit/README.md`) still applies to every composition regardless of what inspired it. Read this file on demand when planning a beat that needs more visual range than the current `pk-*` pieces cover; it is not injected into any agent prompt.

## How to use this index

- Skim the category you need, find a slug whose description matches the beat you're planning, then ask: does an existing `pk-*` piece already cover this look, or does it need a new one?
- **Maps onto existing kit pieces.** Most caption styles, lower-thirds, and social overlays describe a LOOK a `pk-card`/`pk-pill`/`pk-toast`/`pk-outro` variant could already achieve with a new modifier class or animation curve. Before writing new CSS, check `motion/kit/kit.css` and the "Visual design bar" section in `motion/README.md` for the closest existing pattern to extend rather than a one-off composition-local hack.
- **Maps onto the choreography engines.** Camera pushes/pulls (`pk-camera` + `data-shots`) and cursor travel (`pk-cursor` + `data-waypoints`) already cover most "reads as filmed" and "cursor behaves like a hand" needs the shader/transition entries below gesture at; a CSS crossfade or hard cut between `.clip` layers is usually enough for a scene change. Reach for a shader transition only when a composition genuinely needs a GPU effect no CSS transform can fake — and note the shader packages themselves are not vendored here, only the idea of what they look like.
- **Needs a new kit piece.** Code blocks, charts/flowcharts, maps, VFX, and liquid-glass/3D-device blocks have no RoboCo kit equivalent today. If a brief genuinely needs one — a code-diff beat for a dev-tooling release clip, say — that's a normal dev task: build a new `pk-*` piece under `motion/kit/kit.css` following the design bar in `motion/README.md`, not a composition-local hack. Don't reach for one of these categories to decorate a beat a `pk-card`/`pk-chip` variant would serve just as well — see "Anti-generic tells" in `motion/README.md`'s Visual design bar section.
- Full craft rules behind any of this (palette, density, motion, frame composition, rhythm planning) live in the vendored references at `references/` — `house-style.md`, `video-composition.md`, `beat-direction.md`, `motion-principles.md` — read those before choosing colors or timing for anything below.

## Catalog

### Code blocks (33) — VS Code / Apple Terminal theme skins + animated code effects

- `code-3d-extrude` (Code 3D Extrude) — Syntax-highlighted code on a lit, beveled 3D slab that rotates through real space and settles to a readable rest — true WebGL depth and lighting, not a 2D transform.
- `code-diff` (Code Diff) — An edit shown as a colored diff — removed lines collapse in red, added lines expand in green.
- `code-highlight` (Code Highlight Sweep) — A highlight band sweeps across a target line while the surrounding context dims — draws the eye to one line. `line` is 0-based: `line: 1` targets the second displayed line (unlike code-scroll, whose target is 1-based).
- `code-morph` (Code Morph) — One snippet transforms into another — tokens glide between positions, leavers fade out, enterers fade in. Shiki Magic Move re-driven as a paused GSAP timeline.
- `code-particle-assemble` (Code Particle Assemble) — Thousands of GPU points scatter through space and fly to the exact glyph pixels of the code, resolving into readable syntax-highlighted text — a particle system, not a token tween.
- `code-scroll` (Code Scroll To Line) — The camera scrolls a long file to bring a target line to center and spotlights it — for walking through real modules.
- `code-shader-dissolve` (Code Shader Dissolve) — The code compiles into existence: a GPU fragment shader resolves it out of seeded noise with a chromatic dissolve front and edge glow, then holds crisp.
- `code-snippet-flight` (Code Snippet Flight) — Discrete code snippets fly in from the side and assemble into a stacked program, staggered. Block-level FLIP.
- `code-typing` (Code Typing) — Token-streamed typing reveal with a caret that tracks the frontier — deterministic, no CSS animation.
- `code-snippet-dark-2026` (Dark 2026) — The newest VS Code dark theme with refined token scopes and updated palette.
- `code-snippet-dark-modern` (Dark Modern) — The default dark theme — clean and contemporary with comfortable contrast.
- `code-snippet-dark-plus` (Dark+) — Classic dark theme with enhanced syntax highlighting for popular languages.
- `code-snippet-high-contrast` (High Contrast) — Maximum contrast dark theme for accessibility.
- `code-snippet-high-contrast-light` (High Contrast Light) — Maximum contrast light theme for accessibility.
- `code-snippet-light-2026` (Light 2026) — The newest VS Code light theme with refined token scopes and updated palette.
- `code-snippet-light-modern` (Light Modern) — The default light theme — a fresh, modern take on the classic VS light experience.
- `code-snippet-light-plus` (Light+) — Classic light theme with enhanced syntax highlighting for popular languages.
- `code-snippet-monokai` (Monokai) — The iconic warm-toned dark theme beloved by developers worldwide.
- `code-snippet-solarized-light` (Solarized Light) — Ethan Schoonover's precision-engineered light color scheme.
- `code-snippet-visual-studio-dark` (Visual Studio Dark) — The traditional Visual Studio dark color scheme.
- `code-snippet-visual-studio-light` (Visual Studio Light) — The traditional Visual Studio light color scheme.
- `code-snippet-apple-terminal-basic` (Apple Terminal Basic) — Apple Terminal Basic profile with white background and black text, per-character typing animation.
- `code-snippet-apple-terminal-clear-dark` (Apple Terminal Clear Dark) — Apple Terminal Clear Dark profile with semi-transparent dark background and white text, per-character typing animation.
- `code-snippet-apple-terminal-clear-light` (Apple Terminal Clear Light) — Apple Terminal Clear Light profile with semi-transparent white background and black text, per-character typing animation.
- `code-snippet-apple-terminal-grass` (Apple Terminal Grass) — Apple Terminal Grass profile with black background and bright green text, per-character typing animation.
- `code-snippet-apple-terminal-homebrew` (Apple Terminal Homebrew) — Apple Terminal Homebrew profile with black background, bright green text and lime cursor, per-character typing animation.
- `code-snippet-apple-terminal-man-page` (Apple Terminal Man Page) — Apple Terminal Man Page profile with pale yellow background and black text, per-character typing animation.
- `code-snippet-apple-terminal-novel` (Apple Terminal Novel) — Apple Terminal Novel profile with warm parchment background and dark brown text, per-character typing animation.
- `code-snippet-apple-terminal-ocean` (Apple Terminal Ocean) — Apple Terminal Ocean profile with deep blue background and white text, per-character typing animation.
- `code-snippet-apple-terminal-pro` (Apple Terminal Pro) — Apple Terminal Pro profile with black background, grey text and lime green cursor, per-character typing animation.
- `code-snippet-apple-terminal-red-sands` (Apple Terminal Red Sands) — Apple Terminal Red Sands profile with deep red background and sandy text, per-character typing animation.
- `code-snippet-apple-terminal-silver-aerogel` (Apple Terminal Silver Aerogel) — Apple Terminal Silver Aerogel profile with dark grey background and white text, per-character typing animation.
- `code-snippet-apple-terminal-solid-colors` (Apple Terminal Solid Colors) — Apple Terminal Solid Colors profile with deep purple background and white text, per-character typing animation.

### Shader transitions (14) — WebGL GPU scene-to-scene effects

- `chromatic-radial-split` (Chromatic Radial Split) — Shader transition with chromatic aberration radial split
- `cinematic-zoom` (Cinematic Zoom) — Shader transition with dramatic zoom blur
- `cross-warp-morph` (Cross Warp Morph) — Shader transition with cross-warped morphing
- `domain-warp-dissolve` (Domain Warp Dissolve) — Shader transition with fractal noise domain warping
- `flash-through-white` (Flash Through White) — Shader transition with white flash crossfade
- `glitch` (Glitch) — Shader transition with digital glitch artifacts
- `gravitational-lens` (Gravitational Lens) — Shader transition with gravitational lensing distortion
- `light-leak` (Light Leak) — Shader transition with cinematic light leak overlay
- `ridged-burn` (Ridged Burn) — Shader transition with ridged turbulence burn effect
- `ripple-waves` (Ripple Waves) — Shader transition with concentric ripple wave distortion
- `sdf-iris` (SDF Iris) — Shader transition with signed distance field iris reveal
- `swirl-vortex` (Swirl Vortex) — Shader transition with swirling vortex distortion
- `thermal-distortion` (Thermal Distortion) — Shader transition with heat haze thermal distortion
- `whip-pan` (Whip Pan) — Shader transition simulating a fast camera whip pan

### Transition showcase groups (13) — CSS transition families (non-shader)

- `transitions-3d` (3D Transitions) — Showcase of 3D perspective flip and rotate transitions
- `transitions-blur` (Blur Transitions) — Showcase of blur-based transitions between scenes
- `transitions-cover` (Cover Transitions) — Showcase of cover/uncover slide transitions
- `transitions-destruction` (Destruction Transitions) — Showcase of destructive break-apart transitions
- `transitions-dissolve` (Dissolve Transitions) — Showcase of dissolve and fade transitions
- `transitions-distortion` (Distortion Transitions) — Showcase of warp and distortion transitions
- `transitions-grid` (Grid Transitions) — Showcase of grid-based tile transitions
- `transitions-light` (Light Transitions) — Showcase of light-based glow and flash transitions
- `transitions-mechanical` (Mechanical Transitions) — Showcase of mechanical shutter and iris transitions
- `transitions-other` (Other Transitions) — Showcase of miscellaneous creative transitions
- `transitions-push` (Push Transitions) — Showcase of push and slide transitions
- `transitions-radial` (Radial Transitions) — Showcase of radial wipe and reveal transitions
- `transitions-scale` (Scale Transitions) — Showcase of scale and zoom transitions

### Charts / flowchart (3)

- `data-chart` (Data Chart) — Animated bar + line chart with staggered reveal, NYT-style typography, and value labels
- `flowchart` (Flowchart) — Animated decision tree with SVG connectors, sticky-note nodes, cursor interaction, and typing correction
- `flowchart-vertical` (Flowchart Vertical) — Portrait animated decision tree with SVG connectors, sticky-note nodes, cursor interaction, and typing correction

### Maps (6) — data-driven D3 map visualizations

- `spain-map` (Spain Map) — Animated Spain choropleth by autonomous community with staggered reveals and gradient legend — D3 conic conformal projection
- `us-map` (US Map) — Animated US choropleth map with staggered state reveals, value labels, and gradient legend — pure inline SVG with GSAP
- `us-map-bubble` (US Bubble Map) — Animated US bubble map with proportional city markers, value callouts, and connection lines — composable with us-map
- `us-map-flow` (US Flow Map) — Animated connection arcs between US cities over a base map — composable origin-destination flow visualization
- `us-map-hex` (US Hex Grid Map) — Animated hexagonal tile grid map — each state as an equal-weight hex with data fill and abbreviation label
- `world-map` (World Map) — Animated world choropleth with country-by-country reveal, tooltip labels, and rotating globe inset — D3 Natural Earth projection

### Liquid-glass / 3D-device blocks (8)

- `ios26-liquid-glass` (iOS 26 Liquid Glass Home Screen) — 3D iPhone with a normal iOS 26 home screen, liquid glass app icons, shader wallpaper, dock, and fluid glass notifications that drop from the status area onto a GLTF device model.
- `macos-tahoe-liquid-glass` (macOS Tahoe Liquid Glass Desktop) — 3D MacBook with a macOS Tahoe-style desktop, glass menu bar, Finder window, dock, and cinematic device camera move.
- `liquid-glass-context-menu` (Liquid Glass Context Menu) — Frosted glass context menu panel drifting over an aurora shader background
- `liquid-glass-media-controls` (Liquid Glass Media Controls) — Frosted glass media control panels spreading over an aurora shader background
- `liquid-glass-notification` (Liquid Glass Notification) — Frosted glass notification cards floating over an aurora shader background
- `liquid-glass-widgets` (Liquid Glass Widgets) — Frosted glass stat cards, showcase panel and pill chips over an aurora shader background
- `vfx-liquid-glass` (Liquid Glass) — VFX composition block
- `vfx-iphone-device` (iPhone & MacBook 3D Showcase) — Real GLTF iPhone 15 Pro Max and MacBook Pro models with live HTML-in-Canvas screen content, morphing glass lens, product review camera choreography, and 360° turntable.

### Lower-thirds (12)

- `lower-third-bild` (Lower Third — BILD Style) — News-style lower third with tight-fit text boxes: white headline bar with red drop-shadow, red sub-line with white drop-shadow.
- `lt-accent-underline` (Lower Third — Accent Underline) — Cardless lower third for footage overlay: name rises, an accent rule draws left-to-right, role fades in; text-shadowed for legibility
- `lt-bold-block` (Lower Third — Bold Block) — High-energy podcast lower third: solid dark block wipes in, uppercase name slams up, accent tag pops
- `lt-clean-bar` (Lower Third — Clean Bar) — Minimal white-card lower third for podcasts/interviews: accent tab, name + role, clip-wipe entrance
- `lt-color-block` (Lower Third — Color Block) — High-energy lower third: bold accent-color block slides in with overshoot, condensed name + mono role
- `lt-dark-card` (Lower Third — Dark Card) — Charcoal card lower third for bright footage: name, drawn accent underline, role; slide-up entrance
- `lt-kicker-name` (Lower Third — Kicker Name) — Cardless lower third with an accent eyebrow/kicker tag, heavy name, and a drawn baseline; for footage
- `lt-mask-reveal` (Lower Third — Mask Reveal) — Cardless lower third: an accent sweep crosses and clip-path-reveals a heavy name, role fades up; for footage
- `lt-side-rule` (Lower Third — Side Rule) — Cardless lower third with a vertical accent bar; condensed display name + mono role, text-shadowed for footage
- `lt-soft-pill` (Lower Third — Soft Pill) — Rounded white pill lower third for podcasts/interviews: status dot, name + role, scale-pop entrance
- `lt-stack-bars` (Lower Third — Stack Bars) — Two stacked bars: a dark name bar wipes from the left, an accent role bar wipes from the right
- `yt-lower-third` (YouTube Lower Third) — Animated YouTube subscribe lower third with avatar and channel info

### Social / device overlays (8)

- `instagram-follow` (Instagram Follow) — Animated Instagram follow overlay with profile card and follow button
- `tiktok-follow` (TikTok Follow) — Animated TikTok follow overlay with profile card and follow button
- `x-post` (X Post Card) — Animated X/Twitter post card overlay with engagement metrics
- `reddit-post` (Reddit Post Card) — Animated Reddit post card overlay with upvotes and comments
- `spotify-card` (Spotify Now Playing) — Animated Spotify now-playing card with album art and progress bar
- `macos-notification` (macOS Notification) — Animated macOS-style notification banner with app icon and message
- `news-ticker` (News Ticker) — Premium broadcast-style lower-third ticker with live label, headline ribbon, and scrolling news crawl.
- `ui-3d-reveal` (3D UI Reveal) — Perspective 3D reveal animation for UI elements

### VFX (5)

- `vfx-liquid-background` (Liquid Background) — Organic liquid simulation with vertex displacement on a subdivided plane. HTML content floats above rippling fluid surface with real-time wave dynamics.
- `vfx-magnetic` (Magnetic) — VFX composition block
- `vfx-portal` (Portal) — VFX composition block
- `vfx-shatter` (Shatter) — VFX composition block
- `vfx-text-cursor` (VFX Text Cursor) — Dramatic text reveal with cursor glow, chromatic shadow rays, and directional lighting on a black stage. Canvas-based shader post-processing with spectral color edges.

### Full demo compositions (7) — multi-beat narrative pieces, often with sound

- `app-showcase` (App Showcase) — Fitness app product showcase with three floating smartphone screens
- `apple-money-count` (Apple Money Count) — Apple-style finance counter that counts from $0 to $10,000, flashes green, and bursts money icons with sound.
- `blue-sweater-intro-video` (Blue Sweater Intro Video) — Warm AI creator intro sequence that resolves into an X follow card for @_blue_sweater_.
- `logo-outro` (Logo Outro) — Cinematic logo reveal with piece-by-piece assembly, glow bloom, tagline fade-in, and URL pill
- `north-korea-locked-down` (North Korea Locked Down) — Realistic map zoom into North Korea with a red scribble circle, locked-down pop-up label, and reddish editorial wash.
- `nyc-paris-flight` (NYC Paris Flight) — Apple-style realistic map animation with a plane flying from New York to Paris, marker circle, landing pop, and sound effects.
- `vpn-youtube-spot` (VPN YouTube Spot) — Snappy Apple-style YouTube insert showing a phone finding and installing a friendly VPN app with sound effects.

### Caption styles (16, components)

- `caption-blend-difference` (Blend Difference) — Auto-inverting text using mix-blend-mode: difference — flips between white and black per-pixel against the background
- `caption-clip-wipe` (Clip Wipe) — Left-to-right clip-path wipe reveal per word
- `caption-editorial-emphasis` (Editorial Emphasis) — Dual-font system with dramatic size contrast for emphasis words
- `caption-emoji-pop` (Emoji Pop) — Emoji integration with stroked text and horizontal squeeze entrance
- `caption-glitch-rgb` (Glitch RGB) — RGB chromatic aberration with CRT scanline overlay
- `caption-gradient-fill` (Gradient Fill) — Gradient-clipped text with elastic bounce entrance
- `caption-highlight` (Highlight) — Red background sweep behind each active word, TikTok-style
- `caption-kinetic-slam` (Kinetic Slam) — Full-screen single-word display with alternating entrance directions
- `caption-matrix-decode` (Matrix Decode) — Character scramble animation before text reveal
- `caption-neon-accent` (Neon Accent) — Multi-color neon glow accents with wiggle drift animation
- `caption-neon-glow` (Neon Glow) — Cyan and magenta neon glow with keyword accent colors
- `caption-parallax-layers` (Parallax Layers) — Behind-subject 3D text layering with vertical stretch effect
- `caption-particle-burst` (Particle Burst) — Keyword words trigger colored particle explosions
- `caption-pill-karaoke` (Pill Karaoke) — Pill-shaped container with per-word karaoke color highlight
- `caption-texture` (Texture) — Flowing texture mask over large uppercase text — ships with 6 textures (lava, marble, metal, wood, concrete, rock), configurable via the texture variable
- `caption-weight-shift` (Weight Shift) — Elegant font-weight transition between caption lines

### Text/overlay effect components (8)

- `grain-overlay` (Grain Overlay) — Animated film grain texture overlay using CSS keyframes — adds warmth and analog character to any composition
- `grid-pixelate-wipe` (Grid Pixelate Wipe) — Transition effect where the screen dissolves into a grid of squares that fade out with staggered timing — use between scenes
- `morph-text` (Morph Text) — Gooey text morph — cycles through an editable word list using SVG threshold + GSAP-driven blur for a fluid, satisfying transition effect
- `parallax-unzoom` (Parallax Unzoom) — Reveal transition — focus card scales down from full frame as siblings parallax in to form a grid (reverse of parallax-zoom)
- `parallax-zoom` (Parallax Zoom) — Center card scales up to fill the frame while siblings parallax outward — inspired by the eBay Playbook hero transition
- `shimmer-sweep` (Shimmer Sweep) — Animated light sweep across text or elements using a CSS gradient mask — ideal for AI accents and premium reveals
- `texture-mask-text` (Texture Mask Text) — Large display text filled with a swappable material mask (brick, rock, ground, wood, metal, lava); the general-purpose sibling of the caption-only Texture component.
- `vignette` (Vignette) — Cinematic radial vignette overlay using a pure-CSS gradient — darkens the edges to pull focus toward the center

## Coverage note

33 code + 14 shader transitions + 13 transition showcase groups + 3 charts/flowchart + 6 maps + 8 liquid-glass/3D-device + 12 lower-thirds + 8 social/device overlays + 5 VFX + 7 full demo compositions = 109 blocks. 16 caption styles + 8 text/overlay effect components = 24 components. 109 + 24 = 133, matching the verified catalog count. Category boundaries beyond the code/shader/transition/lower-third counts (which the upstream naming makes unambiguous) are this file's own judgment call, not an upstream taxonomy — re-derive from `https://hyperframes.heygen.com/llms.txt` if the catalog grows and this file goes stale.
