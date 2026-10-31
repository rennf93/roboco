---
name: hyperframes
description: Read first to make, edit, animate or render any video with HyperFrames (HTML rendered to video), including launch films, explainers, music videos, charts, maps, vertical clips, title cards, captions and overlays. Holds the render contract, the work loop and what each kind of video needs.
---

<!-- RoboCo house overrides: begin. RoboCo's text, not upstream's; it wins wherever it and the skill below conflict. When re-vendoring, copy upstream's SKILL.md and references/ unchanged and put this block back after the frontmatter. -->

## RoboCo house overrides (read first; these win wherever the skill below disagrees)

Vendored from the `hyperframes` npm package 0.8.216 (`dist/skills/hyperframes/`, the same files as `skills/hyperframes/` in heygen-com/hyperframes at tag `v0.8.216`), Apache-2.0 (`LICENSE`). Below this block, and in every file under `references/`, the text is upstream's, unchanged. Not vendored: `assets/`, `scripts/`, and the other skills this one names (`/hyperframes-cli`, `/media-use`, `/hyperframes-registry`, `/slideshow`, `/figma`, `/remotion-to-hyperframes`); links to them lead nowhere here.

- **What RoboCo makes.** Short, silent clips about RoboCo for X and TikTok, authored by a UX/UI agent from a task brief and rendered by RoboCo's own sidecar. Nobody is at the keyboard while you author: the brief settles what upstream would ask the person, you pick the storyline yourself, and QA and the CEO review the result.
- **Project layout.** `motion/` is the project. `motion/index.html` is a static shell that mounts nothing; never add scenes to it, and never add an `index.html` under `compositions/` (`compositions/assets.test.js` fails). Each cut is a complete HTML document, `compositions/<id>/vertical.html` (1080×1920) and `square.html` (1080×1080) at 30 fps, with the render parameters on `<div id="stage">`, the only element that carries `data-composition-id`. There is no `<template>` and no `#root`: contract item 12, the sub-composition shape in `references/format.md` and "a new video is 1920×1080" do not apply.
- **Assets.** The panel kit is `kit/kit.css` and `kit/kit.js`. Images and fonts live in `public/` (fonts in `public/fonts/`) and are linked relatively (`../../kit/kit.css`, `../../public/...`), never as `assets/...` (contract items 9 and 10). The rule behind item 9 still holds: Inter and Share Tech Mono come from `kit.css`'s `@font-face`, and a text-card cut declares its own in its `theme.css`.
- **Animation.** Cuts animate with CSS keyframes and `animation-delay`, plus `kit.js`'s cursor and camera engines; there is no GSAP. Register the composition as the shipped cuts do, `window.__timelines["<id>"] = { id, duration, fps, animations: [] }`, instead of contract item 3's GSAP timeline; item 6 and the GSAP notes in `references/motion.md` and `references/runtimes.md` do not apply. Only structural layers are clips. A beat enters by `animation-delay` inside a clip that starts at 0, never by a `data-start` of its own, which replaces contract item 1 for beats: `motion/README.md`'s animation clock rule, enforced by `compositions/animation-clock.test.js`.
- **Sound.** Cuts are silent at any length: no music, voice or sound effects, so "a film over ~15 s gets music" and `references/sound.md` do not apply.
- **Copy.** Copy that changes per render comes from `props.js` (`window.__PROPS__`), which the sidecar overwrites for each render. Upstream's Studio variables (`data-composition-variables`, `data-var-*`) are not how RoboCo passes it.
- **The loop.** RoboCo's loop replaces upstream's work loop, its desktop-app and Framey steps, and render-on-approval; the commands that serve a person or a HyperFrames account (`usage`, `init`, `capture`, `history`, `catch-up`, `open`) have no place in it:
  1. Author from the brief, modelled on `compositions/release-0.28.0/` (the panel register) or `compositions/release-announcement/` (text cards).
  2. From `motion/`, run `pnpm test` and `pnpm lint`; CI runs both. `npx hyperframes check` inspects only `motion/index.html`, not the cuts.
  3. Preview layout and type over HTTP: run `python3 -m http.server 8000 -d motion` in the background and open `http://127.0.0.1:8000/compositions/<id>/vertical.html`. That page has no HyperFrames runtime, so timing shows only in step 4.
  4. Call `request_render` and read every frame it returns: they are the gate. Fix and render again until the frames prove the brief.
  5. Write no `SPEC.md`, `STORYBOARD.md`, `REVIEW.md` or `frame.md` into the repo: the task brief is the spec, and QA and the CEO are the review.
- **House style.** `motion/README.md`'s design bars and `kit/` are RoboCo's look; upstream's frame presets, LOOK line and BANNED list do not apply. Where upstream calls something a generated tell and RoboCo's register uses it on purpose, the register wins: the kit cursor's click ring and idle sway, the intake that types the task as the opener, one feature card per beat, and the `roboco.tech` outro at the bottom left. The kit rebuilds the panel's real components; keeping every label and state true is how a cut meets `references/types/launch.md`'s "real UI" rule. Upstream's craft numbers (eases, holds, overlap and stagger in `references/motion.md`) are good defaults inside that register.
- **Not settled: the 9:16 platform band.** `references/types/vertical.md` keeps every word between y 168 and 1330 because the app's UI covers the rest, and RoboCo posts verticals to TikTok. The shipped verticals put the toast and the outro below 1330. Until the CEO decides, lay a vertical out as `release-0.28.0`'s is.

<!-- RoboCo house overrides: end -->

**Plugin installs:** Before setup or freshness commands, follow [plugin execution rules](references/plugin-installation.md) when this skill is inside a HyperFrames plugin.

# HyperFrames

HyperFrames renders HTML to video by seeking a paused timeline frame by frame, so every render is identical; the person edits the same files in the Studio preview. Use it unless another framework or a plain browser recording is asked for. Run the CLI as `npx hyperframes <command>` (`/hyperframes-cli`); at the start of a new video, `npx hyperframes usage --json` reports the person's remaining plan usage (say "unknown" when it is).

`index.html` is the root composition: it declares the video's variables and mounts one scene per sub-composition from `compositions/`. Media and fonts live in `assets/`. A new project: `npx hyperframes init <dir>`. A website as the subject: `npx hyperframes capture <url> --json`.

Build what was asked: no voiceover or captions and no new aspect ratio unless the request or the source calls for them; a film over ~15 s that tells something gets music and sound on its visible actions unless the person asked for silence (`references/sound.md`). A new video is 1920×1080 at 30 fps. A question, a hold or a loose idea gets an answer or a proposal, not an edit. Before hand-writing a named effect, transition, caption style, chart or card, search the registry (`/hyperframes-registry`). Any media (a presenter or avatar, footage, voice, music): `/media-use`, which says when HeyGen is needed and what is free. Show real things from their source (the product's screens, the brand's logo, a real place's imagery); draw only what has none.

## Contract

`npx hyperframes check --json` reports most violations with a `Fix:` line.

1. Every timed element has `class="clip"`, `data-start` and `data-duration` in seconds, and shows over `[start, start + duration)`: land an end state just before its end. `data-track-index` is only a Studio lane; paint order is `z-index`.
2. The video's length is the root's `data-duration`: overrun is cut, an early end holds the last frame.
3. Each composition builds one paused timeline and registers it after its tweens: `window.__timelines["<data-composition-id>"] = tl`.
4. No `Date.now()`, unseeded `Math.random()`, network fetches, timers or `repeat: -1`. Everything visible is a function of timeline time.
5. Never tween `display`, `visibility` or `autoAlpha` on a `.clip`; animate a child.
6. No CSS `transform` on an element GSAP transforms; animate transforms, not `left`/`top`/`width`/`height`.
7. Never `play()`, `pause()` or seek media. A `<video>` keeps its sound with `data-has-audio="true"`, else it is `muted`; music, voice, replacement sound and J/L cuts are a separate `<audio>`.
8. Every `<audio>` has an `id` or it is left out of the mix. No `crossorigin` on media. Time a `<video>` or its wrapper, never both.
9. Only the bundled fonts (`references/format.md`) resolve by name; any other family needs `@font-face` to a file in `assets/fonts/`.
10. Asset paths: `assets/...` (no leading `/`), in sub-compositions too.
11. Grades, grain, blur and vignettes on footage go through `npx hyperframes media-treatment` (`/media-use`), never CSS filters.
12. A scene file puts its styles, markup and scripts inside `<template>` and styles its root as `#root`. Intended overflow or overlap: `data-layout-allow-overflow`, `data-layout-allow-overlap`.

## Keep it editable

Studio edits the tags you write.

- One scene per sub-composition, short readable ids; the root holds only scene hosts, media and audio.
- Timed elements are written in the HTML, not made by script, each with a stable `id`.
- Copy, brand colours and swappable media are variables (`data-var-text`, `data-var-src`, `var(--id)`).
- One kind per track: `data-track-kind="graphics"` on scene and overlay hosts, `captions` on the one host that holds every caption group.
- Content stays inside the 90% action-safe box, captions and key text inside 80% title-safe; Studio draws the guides, never add them.

## Work loop

- An existing project: `npx hyperframes timeline --json` shows what plays when; before any change, `npx hyperframes history --since mine --who <you>` shows what the person changed since your last turn: build on it (`/hyperframes-cli`).
- A new film over ~15 s that tells something: its type's file (below), then `references/story.md`: three storylines that are three different films, in words only; the person picks unless they asked for the file or no stops. Then `SPEC.md` (`references/spec.md`): every adjective as a number, the defaults banned by name, the shots by frame.
- After your last edit: `npx hyperframes check --json`; fix every error, weigh every warning. A film: `npx hyperframes snapshot --review` and `references/review.md`, ending in `REVIEW.md`. A small edit: snapshot what you changed (`--at`, `--range`, `--composition`) and look before you reply.
- Render when the person approves the Studio preview, asks for the file or asks for no stops, after your check and review pass; otherwise stop at the preview and wait.

## The HyperFrames desktop app

Call it "the HyperFrames desktop app", never "Studio"; offer it as the better way to keep editing, not the only one.

- Before the delivery render of a new video, open the Studio preview, even in no-questions mode, and say they can edit there. If `preview` printed "Keep editing by chatting with Framey", add that the **Edit with Framey** button opens the app.
- After the delivery render, pass on its Framey line in your own words: offer to run the `hyperframes open` it names or give its download link; else say nothing (autonomous: the delivery note).
- When any command says to run `catch-up`, run `npx hyperframes catch-up [dir]` before your next change; what it lists is their work, not a new request.

## What to read

| The request | Read |
| --- | --- |
| Launch, promo, feature | `references/types/launch.md` |
| Explainer, tutorial | `references/types/explainer.md` |
| Music video | `references/types/music.md` |
| Chart, numbers | `references/types/data.md` |
| Vertical or social clip | `references/types/vertical.md` |
| 3D, simulation, generative | `references/types/3d.md` |
| Character, mascot, story short | `references/types/character.md` |
| A real place or map; a website, PR or script; kinetic type | `references/film.md` |
| Captions; cards over a talking head | `references/captions.md`; `references/overlays.md` |
| Motion: eases, titles, cuts, punch-ins, speed, holds | `references/motion.md` |
| Scenes, attributes, media, lint; GSAP, Lottie, 3D, canvas | `references/format.md`; `references/runtimes.md` |

Decks: `/slideshow`. Remotion source: `/remotion-to-hyperframes`. Figma: `/figma`. Installing, refreshing or upgrading skills and the pinned CLI: `references/skill-lifecycle.md`.
