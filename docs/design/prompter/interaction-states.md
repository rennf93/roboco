# Interaction States — Design Specification

**Last Updated:** 2026-06-03  
**Spec Version:** 1.0  
**Owner:** ux-dev-1  
**References:** `README.md` for shared tokens

---

## Purpose

This document is the canonical reference for every interactive element state across the Prompter chat surface. For each element, all five interaction states are specified: **default**, **hover**, **focus**, **active**, and **disabled**.

Use this alongside `chat-interface.md`, `conversation-history.md`, and `model-selection.md` which describe component shape, layout, and content.

---

## Global Focus Ring Specification

All keyboard-focusable elements in the chat surface use the same focus ring style, unless explicitly overridden.

| Property | Value |
|---|---|
| Style | `2 px solid var(--focus-ring)` |
| Colour | `brand-500` (`#5B6CF6`) |
| Offset | `outline-offset: 2 px` |
| Application | CSS `outline` (do NOT use `box-shadow` for focus — it conflicts with elevation shadows) |
| When visible | Only when navigating by keyboard (use `:focus-visible` pseudo-class) |
| When hidden | Mouse click interactions should not show the ring |

```css
/* Global rule */
*:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 2px;
}
/* Override for inset ring contexts (list items) */
.list-item:focus-visible {
  outline-offset: -2px;
}
```

---

## Global Transition Defaults

Unless otherwise specified, state transitions use:

| Property | Value |
|---|---|
| Background colour changes | `150 ms cubic-bezier(0.4, 0, 0.2, 1)` |
| Border colour changes | `150 ms cubic-bezier(0.4, 0, 0.2, 1)` |
| Opacity changes | `150 ms cubic-bezier(0.4, 0, 0.2, 1)` |
| Transform (scale/translate) | `100 ms cubic-bezier(0.4, 0, 1, 1)` (ease-in for snap) |
| Height / width changes | `200 ms cubic-bezier(0.4, 0, 0.2, 1)` |

---

## 1. Send Button

> See `chat-interface.md §3.3` for size, shape, and placement.

| State | Background | Icon colour | Border | Transform | Cursor | Transition |
|---|---|---|---|---|---|---|
| Default | `brand-500` | `#FFFFFF` | none | none | `pointer` | — |
| Hover | `brand-600` | `#FFFFFF` | none | none | `pointer` | bg `150 ms` |
| Focus | `brand-500` | `#FFFFFF` | focus ring `2 px brand-500, offset 2 px` | none | `pointer` | — |
| Active | `brand-700`¹ | `#FFFFFF` | none | `scale(0.95)` | `pointer` | transform `100 ms ease-in` |
| Disabled | `surface-200` | `neutral-400` | none | none | `not-allowed` | — |
| Loading | `brand-400` | spinner `#FFFFFF` | none | none | `default` | — |

¹ `brand-700` = `#3B4DC8` (darken brand-500 by ~10%).

**Disabled condition:** textarea is empty, whitespace-only, or stream is in progress.  
**Loading:** streaming in progress — icon replaced by spinner (16 px, `#FFFFFF`).

---

## 2. Textarea (Input Composer)

> See `chat-interface.md §3.2` for dimensions and placeholder.

| State | Background | Border | Outline | Text colour | Cursor |
|---|---|---|---|---|---|
| Default | `surface-0` | `1.5 px solid surface-300` | none | `neutral-900` | `text` |
| Hover | `surface-0` | `1.5 px solid neutral-300` | none | `neutral-900` | `text` |
| Focus | `surface-0` | `1.5 px solid brand-500` | `2 px solid focus-ring, offset 2 px` | `neutral-900` | `text` |
| Active | Same as focus | — | — | — | `text` |
| Disabled | `surface-100` | `1.5 px solid surface-300` | none | `neutral-400` | `not-allowed` |
| Error | `surface-0` | `1.5 px solid danger-500` | `2 px solid danger-500` | `neutral-900` | `text` |

**Placeholder colour:** `neutral-400` in all enabled states.  
**Transition:** border-color `150 ms ease-default`.

---

## 3. Suggested-Prompt Chips

> See `chat-interface.md §5.6` for layout and copy.

| State | Background | Text colour | Border | Transform | Cursor |
|---|---|---|---|---|---|
| Default | `surface-200` | `neutral-700` | none | none | `pointer` |
| Hover | `surface-300` | `neutral-700` | none | none | `pointer` |
| Focus | `surface-200` | `neutral-700` | focus ring `2 px brand-500, offset 2 px` | none | `pointer` |
| Active | `brand-100` | `brand-600` | none | `scale(0.97)` | `pointer` |
| Disabled | `surface-100` | `neutral-400` | none | none | `not-allowed` |

**Transition:** background-color `150 ms ease-default`.

---

## 4. Message Action Buttons (Copy / Regenerate / Delete)

> See `chat-interface.md §2.5` for placement and appearance conditions.

### 4.1 Copy Button

| State | Background | Icon colour | Outline | Cursor |
|---|---|---|---|---|
| Default | Transparent | `neutral-400` | none | `pointer` |
| Hover | `surface-200` | `neutral-700` | none | `pointer` |
| Focus | `surface-100` | `neutral-700` | focus ring `2 px brand-500, offset 2 px` | `pointer` |
| Active | `surface-300` | `neutral-900` | none | `pointer` |
| Disabled | — | `neutral-300` | none | `not-allowed` |

### 4.2 Regenerate Button (AI messages only)

Same as Copy button above.

### 4.3 Delete Message Button

| State | Background | Icon colour | Outline | Cursor |
|---|---|---|---|---|
| Default | Transparent | `danger-500` | none | `pointer` |
| Hover | `danger-100` | `danger-500` | none | `pointer` |
| Focus | `danger-100` | `danger-500` | `2 px solid danger-500, offset 2 px` | `pointer` |
| Active | `#FCA5A5` (danger-300) | `danger-500` | none | `pointer` |
| Disabled | — | `neutral-300` | none | `not-allowed` |

---

## 5. New Chat Button (History Panel Header)

> See `conversation-history.md §3.2`.

| State | Background | Text colour | Icon colour | Border | Cursor |
|---|---|---|---|---|---|
| Default | `surface-200` | `neutral-700` | `neutral-500` | none | `pointer` |
| Hover | `surface-300` | `neutral-700` | `neutral-700` | none | `pointer` |
| Focus | `surface-200` | `neutral-700` | `neutral-700` | focus ring `2 px brand-500, offset 2 px` | `pointer` |
| Active | `brand-100` | `brand-600` | `brand-600` | none | `pointer` |
| Disabled | `surface-100` | `neutral-400` | `neutral-300` | none | `not-allowed` |

---

## 6. Conversation History List Item

> See `conversation-history.md §6` and `§7`.

| State | Background | Border-left | Text colour | Timestamp colour | Cursor |
|---|---|---|---|---|---|
| Default | Transparent | `2 px solid transparent` | `neutral-700` | `neutral-400` | `pointer` |
| Hover | `surface-200` | `2 px solid transparent` | `neutral-700` | hidden (action row shown) | `pointer` |
| Focus | `surface-200` | focus ring inset | `neutral-700` | `neutral-400` | `pointer` |
| Active (selected) | `brand-100` | `2 px solid brand-600` | `neutral-900` | `neutral-500` | `default` |
| Active + Hover | `brand-100` | `2 px solid brand-600` | `neutral-900` | hidden (action row shown) | `pointer` |

---

## 7. History Item Rename Button

> See `conversation-history.md §8.1`.

| State | Background | Icon colour | Outline | Cursor |
|---|---|---|---|---|
| Default (hidden at rest) | Transparent | `neutral-500` | none | `pointer` |
| Hover | `surface-300` | `neutral-700` | none | `pointer` |
| Focus | `surface-200` | `neutral-700` | focus ring `2 px brand-500, offset 2 px` | `pointer` |
| Active | `surface-300` | `neutral-900` | none | `pointer` |
| Disabled | Transparent | `neutral-300` | none | `not-allowed` |

---

## 8. History Item Delete Button

> See `conversation-history.md §8.2`.

| State | Background | Icon colour | Outline | Cursor |
|---|---|---|---|---|
| Default (hidden at rest) | Transparent | `danger-500` | none | `pointer` |
| Hover | `danger-100` | `danger-500` | none | `pointer` |
| Focus | `danger-100` | `danger-500` | `2 px solid danger-500, offset 2 px` | `pointer` |
| Active | `#FCA5A5` | `danger-500` | none | `pointer` |
| Disabled | Transparent | `neutral-300` | none | `not-allowed` |

---

## 9. Delete Confirmation — Cancel Button

> See `conversation-history.md §10.3`.

| State | Background | Text colour | Border | Cursor |
|---|---|---|---|---|
| Default | `surface-200` | `neutral-700` | none | `pointer` |
| Hover | `surface-300` | `neutral-700` | none | `pointer` |
| Focus | `surface-200` | `neutral-700` | focus ring `2 px brand-500, offset 2 px` | `pointer` |
| Active | `surface-300` | `neutral-900` | none | `pointer` |
| Disabled | `surface-100` | `neutral-400` | none | `not-allowed` |

---

## 10. Delete Confirmation — Confirm (Delete) Button

> See `conversation-history.md §10.3`.

| State | Background | Text colour | Border | Cursor |
|---|---|---|---|---|
| Default | `danger-500` | `#FFFFFF` | none | `pointer` |
| Hover | `#DC2626` (danger-600) | `#FFFFFF` | none | `pointer` |
| Focus | `danger-500` | `#FFFFFF` | `2 px solid danger-500, offset 2 px` | `pointer` |
| Active | `#B91C1C` (danger-700) | `#FFFFFF` | none | `pointer` |
| Disabled | `surface-200` | `neutral-400` | none | `not-allowed` |

---

## 11. Rename Input (Inline)

> See `conversation-history.md §9`.

| State | Background | Border | Outline | Text colour | Cursor |
|---|---|---|---|---|---|
| Default | `surface-0` | `1.5 px solid brand-500` | none | `neutral-900` | `text` |
| Hover | `surface-0` | `1.5 px solid brand-500` | none | `neutral-900` | `text` |
| Focus | `surface-0` | `1.5 px solid brand-500` | `2 px solid focus-ring, offset 2 px` | `neutral-900` | `text` |
| Error (empty submit) | `surface-0` | `1.5 px solid danger-500` | `2 px solid danger-500` | `neutral-900` | `text` |
| Disabled | `surface-100` | `1 px solid surface-300` | none | `neutral-400` | `not-allowed` |

---

## 12. Rename Confirm Button (✓)

| State | Background | Icon colour | Outline | Cursor |
|---|---|---|---|---|
| Default | Transparent | `brand-500` | none | `pointer` |
| Hover | `brand-50` | `brand-600` | none | `pointer` |
| Focus | `brand-50` | `brand-600` | focus ring `2 px brand-500, offset 2 px` | `pointer` |
| Active | `brand-100` | `brand-600` | none | `pointer` |
| Disabled | Transparent | `neutral-300` | none | `not-allowed` |

---

## 13. Rename Cancel Button (✕)

| State | Background | Icon colour | Outline | Cursor |
|---|---|---|---|---|
| Default | Transparent | `neutral-500` | none | `pointer` |
| Hover | `surface-200` | `neutral-700` | none | `pointer` |
| Focus | `surface-100` | `neutral-700` | focus ring `2 px brand-500, offset 2 px` | `pointer` |
| Active | `surface-300` | `neutral-900` | none | `pointer` |
| Disabled | Transparent | `neutral-300` | none | `not-allowed` |

---

## 14. Model Selector — Trigger Button

> See `model-selection.md §3` and `§4`.

| State | Background | Border | Text colour | Chevron colour | Cursor |
|---|---|---|---|---|---|
| Default (closed) | `surface-50` | `1 px solid neutral-200` | `neutral-700` | `neutral-400` | `pointer` |
| Hover (closed) | `surface-100` | `1 px solid neutral-300` | `neutral-700` | `neutral-500` | `pointer` |
| Focus | `surface-100` | `1 px solid neutral-300` | `neutral-700` | `neutral-400` | `pointer` |
| Active / Open | `surface-100` | `1.5 px solid brand-500` | `neutral-900` | `brand-500` (rotated 180°) | `pointer` |
| Disabled | `surface-100` | `1 px solid surface-300` | `neutral-400` | `neutral-300` | `not-allowed` |

Focus ring: `2 px solid focus-ring, offset 2 px` when focused via keyboard.

---

## 15. Model Selector — Option Item

> See `model-selection.md §6`.

| State | Background | Text colour | Tag colour | Checkmark | Cursor |
|---|---|---|---|---|---|
| Default | Transparent | `neutral-700` | per tag spec | hidden (space reserved) | `pointer` |
| Hover | `surface-100` | `neutral-700` | per tag spec | visible if selected | `pointer` |
| Focus | `surface-100` | `neutral-700` | per tag spec | visible if selected | `pointer` |
| Selected | Transparent | `neutral-900` (bold) | per tag spec | `brand-500` checkmark 16 px | `pointer` |
| Selected + Hover | `surface-100` | `neutral-900` | per tag spec | `brand-500` checkmark | `pointer` |
| Disabled | Transparent | `neutral-400` | `neutral-300` | — | `not-allowed` |

Focus ring on options: `2 px solid focus-ring, offset -2 px` (inset).

---

## 16. Code Block Copy Button

> See `chat-interface.md §2.4`.

| State | Background | Icon colour | Cursor |
|---|---|---|---|
| Hidden (default, no hover) | — | — | — |
| Default (on bubble hover) | `surface-0` | `neutral-400` | `pointer` |
| Hover | `surface-200` | `neutral-700` | `pointer` |
| Focus | `surface-100` | `neutral-700` | `pointer` |
| Active | `surface-300` | `neutral-900` | `pointer` |
| Copied (confirmation) | `surface-0` | `#059669` (green-600) | `default` |

Copied state: icon changes to checkmark for `2000 ms`, then reverts to copy icon.

---

## Motion Reference Table

Complete listing of all animation durations and easings used across the chat surface.

| Element / Interaction | Duration | Easing | Property |
|---|---|---|---|
| Button hover colour change | 150 ms | `ease-default` | background-color |
| Button active transform | 100 ms | `ease-in` | transform |
| Action row fade in (message hover) | 150 ms | `ease-default` | opacity |
| Typing indicator — per dot | 1200 ms cycle | `ease-default` | transform, opacity |
| Typing indicator — dot stagger | 160 ms per dot | — | animation-delay |
| Typing indicator fade in | 200 ms | `ease-out` | opacity |
| Typing indicator fade out | 150 ms | `ease-in` | opacity |
| Empty-state chip hover | 150 ms | `ease-default` | background-color |
| History panel collapse/expand | 300 ms | `ease-in-out` | width |
| History item hover | 150 ms | `ease-default` | background-color |
| History action row reveal | 150 ms | `ease-default` | opacity |
| Delete confirmation expand | 200 ms | `ease-default` | height |
| Delete item collapse (confirmed) | 200 ms | `ease-in` | height, opacity |
| Model selector dropdown open | 200 ms | `ease-out` | opacity, transform |
| Model selector dropdown close | 150 ms | `ease-in` | opacity, transform |
| Model selector chevron rotate | 200 ms | `ease-default` | transform |
| New message appear | 200 ms | `ease-out` | opacity, translateY +8 px |
| Scroll to new message | smooth | browser default | scroll-behavior |
| Chip active press | 100 ms | `ease-in` | transform scale |
| Code block copy confirmation | 0 ms (instant) + 2000 ms hold | — | icon swap |
| Input textarea height grow | 0 ms (instant) | — | height (JS-driven) |
| Rename input appear | 150 ms | `ease-out` | opacity |
| Tooltip appear | 150 ms delay + 100 ms fade | `ease-out` | opacity |

---

## WCAG Compliance Reference

All colour pairings in this spec must meet WCAG 2.1 AA standards.

| Pairing | Ratio | Standard |
|---|---|---|
| `neutral-900` on `surface-0` | 16.1:1 | ✅ AA |
| `neutral-700` on `surface-0` | 8.0:1 | ✅ AA |
| `neutral-500` on `surface-0` | 4.6:1 | ✅ AA |
| `#FFFFFF` on `brand-500` (user bubble) | 3.6:1 | ✅ AA large text / UI |
| `neutral-700` on `surface-100` (AI bubble) | 6.1:1 | ✅ AA |
| `neutral-700` on `surface-200` (chips, hover) | 5.7:1 | ✅ AA |
| `#FFFFFF` on `danger-500` (delete confirm) | 4.6:1 | ✅ AA |
| `neutral-400` on `surface-0` (placeholder) | 2.9:1 | ⚠️ Use only for placeholder / decorative — not for meaningful content |
| `neutral-400` on `surface-50` (timestamps) | 2.7:1 | ⚠️ Timestamps are supplementary; body text must use neutral-700+ |

> **Note on `neutral-400`:** This token is intentionally low-contrast for decorative text (placeholders, timestamps, keyboard hints). No critical information is communicated in this colour alone. If the design system uses WCAG AAA targets, replace placeholder colour with `neutral-500` (4.6:1).
