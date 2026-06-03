# Model Selection Component — Design Specification

**Last Updated:** 2026-06-03  
**Spec Version:** 1.0  
**Owner:** ux-dev-1  
**References:** `README.md` for shared tokens

---

## 1. Overview

The model selector is a compact dropdown component in the chat header bar that lets the user choose which AI model powers their conversation. It consists of a **trigger button** (always visible) and a **dropdown panel** (appears on click).

```
Chat Header Bar (48px):
┌─────────────────────────────────────────────────────────────┐
│  [← Back]   Prompter               [Claude 3.5 Sonnet ▼]   │
└─────────────────────────────────────────────────────────────┘
                                      ↑
                               Model selector trigger
```

---

## 2. Placement

| Property | Value |
|---|---|
| Position in header | Right-aligned, flex-end |
| Vertical alignment | Center (align-self: center) |
| Margin-right | `16 px` from right edge of header |
| Z-index of trigger | Auto (inherits header stacking context) |
| Z-index of dropdown | `200` (above all chat content, below modals) |

---

## 3. Trigger Button — Closed State

The trigger button is always visible and shows the currently selected model.

```
┌────────────────────────────────┐
│  Claude 3.5 Sonnet  ▼          │
└────────────────────────────────┘
```

| Property | Value |
|---|---|
| Height | 32 px |
| Min width | 120 px |
| Max width | 200 px |
| Padding | `0 10 px` |
| Display | Flex, align-items center, gap `6 px` |
| Background | `surface-50` |
| Border | `1 px solid neutral-200` |
| Border radius | `radius-md` (8 px) |
| Font size | 13 px |
| Font weight | 500 |
| Text colour | `neutral-700` |
| Cursor | `pointer` |
| Chevron icon | Down-chevron 14 px, `neutral-400`, right of label |
| Text overflow | Truncate at max-width with ellipsis |
| aria-haspopup | `"listbox"` |
| aria-expanded | `"false"` (closed) |
| aria-label | `"Select AI model, current: {model name}"` |

### 3.1 Trigger Hover State

| Property | Value |
|---|---|
| Background | `surface-100` |
| Border | `1 px solid neutral-300` |
| Transition | `background-color 150 ms ease-default`, `border-color 150 ms ease-default` |

### 3.2 Trigger Focus State

| Property | Value |
|---|---|
| Outline | `2 px solid focus-ring`, `outline-offset: 2 px` |
| Background | `surface-100` |

### 3.3 Trigger Active (pressed) State

| Property | Value |
|---|---|
| Background | `surface-200` |
| Transform | `scale(0.98)` |
| Transition | `transform 100 ms ease-in` |
| Chevron rotation | 180° (points up when open) |

---

## 4. Trigger Button — Open State

While the dropdown is open, the trigger reflects the open state.

| Property | Value |
|---|---|
| Background | `surface-100` |
| Border | `1.5 px solid brand-500` |
| Chevron | Rotated 180° (up-chevron) |
| Chevron colour | `brand-500` |
| Text colour | `neutral-900` |
| Chevron rotation transition | `transform 200 ms ease-default` |
| aria-expanded | `"true"` |

---

## 5. Dropdown Panel

Opens below the trigger button, right-aligned.

```
          [Claude 3.5 Sonnet ▲]
          ┌────────────────────────────────────────┐
          │                                        │
          │  ✓  Claude 3.5 Sonnet       Latest    │  ← selected
          │     Claude 3.0                         │
          │     Claude 2.1              Fast       │
          │  ─────────────────────────────────     │  ← divider
          │     GPT-4o                             │
          │     GPT-4o mini             Fast       │
          │                                        │
          └────────────────────────────────────────┘
```

| Property | Value |
|---|---|
| Width | 240 px |
| Min height | 36 px (single item) |
| Max height | 320 px |
| Overflow-y | `auto` when content exceeds max height |
| Background | `surface-0` |
| Border | `1 px solid surface-300` |
| Border radius | `radius-md` (8 px) |
| Box shadow | `elevation-3` (`0 8px 24px rgba(0,0,0,0.12)`) |
| Padding | `4 px 0` |
| Position | Absolute, anchored below-right of trigger |
| Anchor | `top: 100% + 4 px`, `right: 0` |
| Z-index | 200 |
| Animation | Fade in + slide down `8 px` over `200 ms ease-out` |

### 5.1 Dropdown Open Animation

```css
@keyframes dropdown-enter {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

| Property | Value |
|---|---|
| Animation | `dropdown-enter 200 ms cubic-bezier(0, 0, 0.2, 1)` |
| Fill mode | `forwards` |

### 5.2 Dropdown Close Animation

| Property | Value |
|---|---|
| Animation | Reverse: opacity 1 → 0, translateY 0 → -4 px |
| Duration | `150 ms ease-in` |

### 5.3 Dismiss Behaviour

| Trigger | Result |
|---|---|
| Click outside dropdown | Close dropdown |
| Press `Escape` | Close dropdown, return focus to trigger |
| Press `Tab` | Close dropdown, move focus to next element |
| Select an option | Close dropdown, update trigger label |

---

## 6. Option Item — Default State

```
│     Claude 3.5 Sonnet       Latest   │
│  ↑                            ↑      │
│  label (14px)                 tag    │
```

| Property | Value |
|---|---|
| Height | 36 px |
| Padding | `0 12 px` |
| Display | Flex, align-items center, gap `8 px` |
| Background | Transparent |
| Font size | 14 px |
| Font weight | 400 |
| Text colour | `neutral-700` |
| Cursor | `pointer` |
| role | `"option"` |
| Checkmark area | 20 px wide (always reserved; empty when not selected) |

### 6.1 Option Item — Hover State

| Property | Value |
|---|---|
| Background | `surface-100` |
| Transition | `background-color 150 ms ease-default` |

### 6.2 Option Item — Focus State (keyboard)

| Property | Value |
|---|---|
| Outline | `2 px solid focus-ring`, `outline-offset: -2 px` (inset) |
| Background | `surface-100` |

### 6.3 Option Item — Selected State

The currently selected model.

| Property | Value |
|---|---|
| Background | Transparent (not highlighted at rest; hover still highlights) |
| Checkmark | Checkmark icon 16 px, `brand-500`, at left (within the 20 px area) |
| Font weight | 600 |
| Text colour | `neutral-900` |
| aria-selected | `"true"` |

### 6.4 Option Item — Disabled State

A model option that is unavailable (e.g. subscription tier mismatch, temporarily down).

| Property | Value |
|---|---|
| Background | Transparent |
| Text colour | `neutral-400` |
| Tag colour | `neutral-300` (if tag present) |
| Cursor | `not-allowed` |
| Pointer events | `none` |
| aria-disabled | `"true"` |

---

## 7. Option Tag / Badge

Some models may carry a tag (e.g. `"Latest"`, `"Fast"`, `"Beta"`). The tag appears right-aligned within the option row.

| Property | Value |
|---|---|
| Background | `surface-200` |
| Text colour | `neutral-500` |
| Font size | 11 px |
| Font weight | 500 |
| Padding | `2 px 6 px` |
| Border radius | `radius-full` |
| Margin-left | `auto` (pushes to right edge) |

#### Special Tag Colours

| Tag | Background | Text |
|---|---|---|
| `"Latest"` | `brand-50` | `brand-600` |
| `"Fast"` | `#ECFDF5` (green-50) | `#059669` (green-600) |
| `"Beta"` | `#FFF7ED` (orange-50) | `#D97706` (amber-600) |
| `"Preview"` | `#F5F3FF` (violet-50) | `#7C3AED` (violet-600) |

---

## 8. Group Divider

If models are grouped (e.g. "Anthropic" vs "OpenAI"), a thin divider separates groups.

| Property | Value |
|---|---|
| Element | `<hr>` or `border-top` on a group heading item |
| Height | 1 px |
| Colour | `surface-300` |
| Margin | `4 px 0` |

### 8.1 Group Label (optional)

| Property | Value |
|---|---|
| Text | Provider name (e.g. `"Anthropic"`, `"OpenAI"`) |
| Font size | 11 px |
| Font weight | 600 |
| Text transform | Uppercase |
| Colour | `neutral-400` |
| Padding | `8 px 12 px 4 px` |
| Pointer events | `none` (not selectable) |

---

## 9. Trigger Button — Disabled State

The entire model selector is disabled (e.g. during streaming, or when the feature is unavailable).

| Property | Value |
|---|---|
| Background | `surface-100` |
| Border | `1 px solid surface-300` |
| Text colour | `neutral-400` |
| Chevron colour | `neutral-300` |
| Cursor | `not-allowed` |
| Pointer events | `none` |
| Opacity | `0.6` |
| Tooltip | `"Model selection unavailable"` (shown on hover, `150 ms` delay) |
| aria-disabled | `"true"` |

### 9.1 Tooltip Design

| Property | Value |
|---|---|
| Background | `neutral-900` |
| Text colour | `#FFFFFF` |
| Font size | 12 px |
| Padding | `4 px 8 px` |
| Border radius | `radius-sm` (4 px) |
| Position | Below trigger, centered, `top: calc(100% + 6px)` |
| Delay | `150 ms` before showing |
| Z-index | 201 |

---

## 10. Keyboard Interaction

| Key | Behaviour |
|---|---|
| `Enter` / `Space` | Open dropdown (when trigger focused) |
| `↑` / `↓` | Move focus between options |
| `Enter` | Select focused option |
| `Escape` | Close dropdown, return focus to trigger |
| `Tab` | Close dropdown, move focus to next element in page |
| `Home` | Focus first option |
| `End` | Focus last option |

ARIA pattern: `combobox` or `button` + `listbox`. Use `role="listbox"` on dropdown container and `role="option"` on each item.

---

## 11. Selected-Item Display in Trigger

When a model is selected, the trigger label shows:

| Scenario | Trigger label |
|---|---|
| Model name ≤ 18 chars | Full model name |
| Model name > 18 chars | Truncated with ellipsis |
| No model selected (loading) | `"Select model"` in `neutral-400` |

> **Do not** show provider name in the trigger — only the model name. The full provider + model name appears in the dropdown option row.
