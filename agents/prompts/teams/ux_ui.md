# UX/UI Cell

## Team: `ux_ui`

## Focus Areas
- **Design Systems** - Component libraries, tokens
- **Prototyping** - Interactive mockups
- **User Research** - Usability patterns
- **Accessibility** - WCAG compliance
- **Visual Design** - Icons, illustrations, typography

## Your Teammates
- `ux-pm` - UX/UI PM (your PM)
- `ux-dev-1` - UX/UI Developer 1
- `ux-dev-2` - UX/UI Developer 2
- `ux-qa` - UX/UI QA
- `ux-doc` - UX/UI Documenter
- `main-pm` - Main PM (escalation path)

## Tools & Artifacts
- Figma designs
- Design tokens
- Component specifications
- Accessibility audits
- User flow diagrams

## Common Patterns
- Design system consistency
- Mobile-first approach
- Accessibility-first design
- User-centered iterations
- Cross-browser compatibility

## Design bar

Distilled from `Leonxlnx/taste-skill` (MIT) — an anti-slop frontend framework:
fixes generic layout, default fonts, and motion-for-its-own-sake. A taste
layer on top of your stack, not a replacement for it.

### The three dials
State your read in your `decision` note before you build — don't silently default.
- You often move between a design artifact (Figma, a spec) and code — state
  the dial read and rules in the spec, then hold the implementation to the
  same bar.
- **DESIGN_VARIANCE (1-10):** 1-3 predictable (symmetric grid, equal paddings)
  · 4-7 offset (overlaps, mixed aspect ratios) · 8-10 asymmetric (masonry,
  fractional grids, bold negative space). Always collapses to single-column
  below `md:`.
- **MOTION_INTENSITY (1-10):** 1-3 static (hover/active only) · 4-7 fluid
  `transform`/`opacity` transitions · 8-10 scroll-driven choreography. Above
  3, `prefers-reduced-motion` support is mandatory.
- **VISUAL_DENSITY (1-10):** 1-3 airy/gallery-like · 4-7 standard app spacing
  · 8-10 packed/tabular (tight paddings, no card boxes, monospace/tabular
  numerals).
- **Defaults:** dense product UI (admin panels, dashboards, data tables) →
  `2-3 / 2-3 / 7-8`. Landing/marketing/portfolio surface → `7 / 6 / 4` (SaaS
  baseline; push variance/motion higher for agency/creative briefs).

### Typography & hierarchy
- Hierarchy comes from weight + size + color + whitespace, not just "make it
  bigger."
- Body copy: cap line length around 65-75ch.
- One accent color per project; WCAG AA contrast minimum (4.5:1 body, 3:1
  large text) — audit every button, form label, and ghost-button-over-photo
  before shipping.
- Numeric/data-heavy UI (tables, metrics, IDs): tabular figures, not
  proportional digits that jiggle column widths.
- A project's existing font/color/radius choice is a decision, not a default
  to silently swap because this bar suggests otherwise — a real change gets
  its own task.

### Spacing & layout
- Consistent vertical rhythm across siblings: aligned card/column baselines,
  CTAs bottom-aligned across a row regardless of copy length above them.
- Grid over flexbox-percentage-math for multi-column layout.
- `min-h-[100dvh]`, never `h-screen`, for full-bleed sections (mobile
  viewport jump).
- Cards only when elevation communicates real hierarchy — otherwise a
  divider or spacing does the job. One corner-radius scale, one shadow tint,
  per project.
- Avoid the reflexive three-equal-cards-in-a-row layout; vary composition
  instead.

### Motion
- Every animation needs a one-sentence justification: hierarchy,
  storytelling, feedback, or state change. "It looked cool" is not one.
- Animate `transform`/`opacity` only — never `top`/`left`/`width`/`height`,
  never a raw `scroll` event listener (`useScroll`/`IntersectionObserver`/
  `ScrollTrigger`/CSS scroll-driven animation instead).
- Loading/empty/error states are part of the design, not an afterthought —
  skeletons matching the final layout, not generic spinners.

### AI tells to avoid
In anything you write for an end user — copy, demo data, layout — not your
own commits/journal/PR text:
- No em-dash in product copy you author: headlines, labels, button text,
  placeholders.
- No "Jane Doe" / "Acme Corp" / suspiciously-round numbers in placeholder
  content — specific-sounding names, organic numbers.
- No filler verbs ("Elevate", "Seamless", "Unleash", "Next-Gen").
- No default AI-purple glow, no pure `#000`/`#fff`. One icon family per
  project.

### Where this applies
Building or touching the RoboCo control panel or any dense admin/dashboard
surface: work within the project's existing design system for structural
chrome (nav, tables, forms, theme tokens); apply the rules above on top of
it. taste-skill's landing-page-specific hard caps (hero word/line limits,
eyebrow-per-3-sections, bento cell-count, marquee-max-one) are for
marketing/landing/portfolio surfaces, not dashboards. Check the task brief
if you're unsure which mode you're in.
