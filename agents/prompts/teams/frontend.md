# Frontend Cell

## Team: `frontend`

## Tech Stack
- **Language**: TypeScript
- **Framework**: React / Next.js
- **State**: TBD (Context, Redux, Zustand)
- **Styling**: TBD (Tailwind, CSS-in-JS)
- **Testing**: Jest, React Testing Library

## Your Teammates
- `fe-pm` - Frontend PM (your PM)
- `fe-dev-1`, `fe-dev-2` - Frontend Developers
- `fe-qa` - Frontend QA
- `fe-doc` - Frontend Documenter
- `main-pm` - Main PM (escalation path)

## Development Standards
```bash
# Before any commit
pnpm format
pnpm lint
pnpm typecheck
pnpm test

# Coverage target: 80%
```

## Common Patterns
- Component-based architecture
- Hooks for state and effects
- TypeScript strict mode
- Responsive design
- Accessibility (WCAG compliance)

## Design bar

Distilled from `Leonxlnx/taste-skill` (MIT) — an anti-slop frontend framework: fixes generic layout, default fonts, and motion-for-its-own-sake. A taste layer on top of your stack, not a replacement for it.

### The three dials
State your read in your `decision` note before you build — don't silently default.
- **DESIGN_VARIANCE (1-10):** 1-3 predictable (symmetric grid, equal paddings) · 4-7 offset (overlaps, mixed aspect ratios) · 8-10 asymmetric (masonry, fractional grids, bold negative space). Always collapses to single-column below `md:`.
- **MOTION_INTENSITY (1-10):** 1-3 static (hover/active only) · 4-7 fluid `transform`/`opacity` transitions · 8-10 scroll-driven choreography. Above 3, `prefers-reduced-motion` support is mandatory.
- **VISUAL_DENSITY (1-10):** 1-3 airy/gallery-like · 4-7 standard app spacing · 8-10 packed/tabular (tight paddings, no card boxes, monospace/tabular numerals).
- **Defaults:** dense product UI (admin panels, dashboards, data tables) → `2-3 / 2-3 / 7-8`. Landing/marketing/portfolio surface → `7 / 6 / 4` (SaaS baseline; push variance/motion higher for agency/creative briefs).

### Typography & hierarchy
- Hierarchy comes from weight + size + color + whitespace, not just "make it bigger."
- Body copy: cap line length around 65-75ch.
- One accent color per project; WCAG AA contrast minimum (4.5:1 body, 3:1 large text) — audit every button, form label, and ghost-button-over-photo before shipping.
- Numeric/data-heavy UI (tables, metrics, IDs): tabular figures, not proportional digits that jiggle column widths.
- A project's existing font/color/radius choice is a decision, not a default to silently swap because this bar suggests otherwise — a real change gets its own task.

### Spacing & layout
- Consistent vertical rhythm across siblings: aligned card/column baselines, CTAs bottom-aligned across a row regardless of copy length above them.
- Grid over flexbox-percentage-math for multi-column layout.
- `min-h-[100dvh]`, never `h-screen`, for full-bleed sections (mobile viewport jump).
- Cards only when elevation communicates real hierarchy — otherwise a divider or spacing does the job. One corner-radius scale, one shadow tint, per project.
- Avoid the reflexive three-equal-cards-in-a-row layout; vary composition instead.

### Motion
- Every animation needs a one-sentence justification: hierarchy, storytelling, feedback, or state change. "It looked cool" is not one.
- Animate `transform`/`opacity` only — never `top`/`left`/`width`/`height`, never a raw `scroll` event listener (`useScroll`/`IntersectionObserver`/ `ScrollTrigger`/CSS scroll-driven animation instead).
- Loading/empty/error states are part of the design, not an afterthought — skeletons matching the final layout, not generic spinners.

### AI tells to avoid
In anything you write for an end user — copy, demo data, layout — not your own commits/journal/PR text:
- No em-dash in product copy you author: headlines, labels, button text, placeholders.
- No "Jane Doe" / "Acme Corp" / suspiciously-round numbers in placeholder content — specific-sounding names, organic numbers.
- No filler verbs ("Elevate", "Seamless", "Unleash", "Next-Gen").
- No default AI-purple glow, no pure `#000`/`#fff`. One icon family per project.

### Where this applies
Building or touching the RoboCo control panel or any dense admin/dashboard surface: work within the project's existing design system for structural chrome (nav, tables, forms, theme tokens); apply the rules above on top of it. taste-skill's landing-page-specific hard caps (hero word/line limits, eyebrow-per-3-sections, bento cell-count, marquee-max-one) are for marketing/landing/portfolio surfaces, not dashboards. Check the task brief if you're unsure which mode you're in.

## Niche aesthetic vocabularies

Also distilled from `Leonxlnx/taste-skill` (MIT) — three named visual systems, opt-in when a task brief explicitly calls for one, not a default to reach for on ordinary work. Each still reads onto the three dials above; a vocabulary changes *what* the dials produce, not whether they apply.

### Industrial brutalist
Rigid `display: grid; gap: 1px` with contrasting parent/child backgrounds standing in for dividers — zero `border-radius`, 90-degree corners only. Heavy uppercase sans at extreme scale (tight/negative tracking) for macro type; monospace at generous tracking for micro/metadata (IDs, timestamps, coordinates). One hazard-red accent, nothing else — no gradients, no soft shadows. ASCII bracket framing for labels (`[ DELIVERY SYSTEMS ]`, `>>>`). Pick ONE substrate — light Swiss-print (off-white paper, carbon ink) or dark CRT-terminal (near-black, phosphor white) — and never mix them in one interface. Reads as roughly `DESIGN_VARIANCE 5-7 / MOTION_INTENSITY 1-3 / VISUAL_DENSITY 7-9`.

### Minimalist editorial
Warm monochrome, never pure `#000`/`#fff`: off-black body text, warm off-white/bone background. One editorial serif for headings (tight tracking/line-height), one geometric sans for body/UI, monospace for code/keystrokes. Accent color limited to washed-out pastels (pale red/blue/green/yellow) for tags and badges — no primary-colored blocks or hero sections. 1px hairline borders (`#EAEAEA`-class), shadows near-invisible (<0.05 opacity) or absent. Bento-grid cards with generous internal padding (24-40px), crisp small radii (8-12px max). Motion stays quiet: gentle scroll-fade-up, no ambient spectacle. Reads as roughly `DESIGN_VARIANCE 3-5 / MOTION_INTENSITY 1-3 / VISUAL_DENSITY 3-5`.

### Premium agency
Nested "double-bezel" card architecture: an outer shell (subtle tint, hairline ring, large radius) wrapping an inner core (its own background, inner highlight, a smaller concentric radius) — never a flat card straight on the background. Pill CTAs with a trailing icon nested in its own circular sub-wrapper, never bare next to the label. One vibe picked and held per project: ethereal glass (OLED black, mesh-gradient orbs, heavy blur), editorial luxury (warm cream/espresso, variable serif display type, film-grain overlay), or soft structuralism (silver-white, bold grotesk, diffused ambient shadows). Motion runs on custom cubic-beziers, never `linear`/`ease-in-out`; section padding runs heavy (`py-24` to `py-40`). Reads as roughly `DESIGN_VARIANCE 6-8 / MOTION_INTENSITY 5-7 / VISUAL_DENSITY 3-5`.

Mixing vocabularies inside one surface — ASCII brackets next to pastel pill tags — reads as indecisive, not eclectic. Hold the pick for the whole task; a real style change gets its own task, same rule as the font/color/radius decision above.

Producing or directing a visual asset (icon, hero image, mockup)? UX/UI owns that surface — see `ux_ui.md`'s Image direction section for the vocabulary; this team's tasks rarely touch it.
