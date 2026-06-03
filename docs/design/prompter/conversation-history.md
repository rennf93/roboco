# Conversation History Panel — Design Specification

**Last Updated:** 2026-06-03  
**Spec Version:** 1.0  
**Owner:** ux-dev-1  
**References:** `README.md` for shared tokens

---

## 1. Panel Overview

The conversation history panel is a fixed-width sidebar that lists past conversations. It supports collapse/expand, grouping by recency, inline rename, and inline delete confirmation.

```
┌──────────────────────────────────────┐
│  [≡ RoboCo logo]    [New Chat  +]   │  ← Header (48px)
├──────────────────────────────────────┤
│  [Search conversations...]           │  ← Search input (optional)
├──────────────────────────────────────┤
│                                      │
│  Today                               │  ← Group heading
│  ─────────────────────────────────   │
│  ▌ Current convo title     14:32  ●  │  ← Active item (left border)
│  Another convo              Yesterday│  ← Default item
│  Third convo                Mon      │
│                                      │
│  Previous 7 Days                     │  ← Group heading
│  ─────────────────────────────────   │
│  Old convo title            Tue      │
│  Another old convo          Mon      │
│                                      │
│  Older                               │  ← Group heading
│  ─────────────────────────────────   │
│  Much older convo           Jun 1    │
│                                      │
└──────────────────────────────────────┘
  260px wide
```

---

## 2. Panel Container

| Property | Value |
|---|---|
| Width | 260 px (expanded), 0 px (collapsed) |
| Height | Full viewport height minus app nav (calc(100vh - 48px)) |
| Background | `surface-50` |
| Border-right | `1 px solid surface-300` |
| Overflow-y | `auto` |
| Overflow-x | `hidden` |
| Z-index | 50 |
| Flex-shrink | 0 (does not compress when chat area narrows) |
| Transition | `width 300 ms cubic-bezier(0.4, 0, 0.2, 1)` |

### 2.1 Collapse / Expand

| Property | Value |
|---|---|
| Toggle trigger | Icon button in app nav bar (hamburger / close icon, 24 px) |
| Collapsed width | 0 px (panel content hidden with `overflow: hidden`) |
| Expanded width | 260 px |
| Animation | `width 300 ms ease-in-out` |
| ARIA on toggle button | `aria-expanded="true/false"`, `aria-controls="history-panel"` |
| Panel ID | `id="history-panel"` |
| When collapsed | Toggle button remains visible in nav bar |

---

## 3. Panel Header

Fixed at the top of the panel (does not scroll with list).

```
┌──────────────────────────────────────┐
│  [RoboCo logo mark 20px]  [+ New]    │
└──────────────────────────────────────┘
```

| Property | Value |
|---|---|
| Height | 48 px |
| Padding | `0 12 px` |
| Display | Flex, align-items center, justify-content space-between |
| Background | `surface-50` |
| Border-bottom | `1 px solid surface-300` |

### 3.1 Logo Mark (panel header)

| Property | Value |
|---|---|
| Size | 20 × 20 px |
| Style | RoboCo logo mark, `brand-500` fill |

### 3.2 New Chat Button

| Property | Value |
|---|---|
| Label | `"New chat"` |
| Icon | Plus `+` 16 px, left of label |
| Height | 32 px |
| Padding | `0 10 px` |
| Border radius | `radius-md` (8 px) |
| Font size | 13 px |
| Font weight | 500 |
| Default bg | `surface-200` |
| Default text | `neutral-700` |
| Hover bg | `surface-300` |
| Focus ring | `2 px solid focus-ring`, `outline-offset: 2 px` |
| Active bg | `brand-100` |
| Transition | `background-color 150 ms ease-default` |
| aria-label | `"Start a new chat conversation"` |
| On click | Create new conversation, navigate to it, focus textarea |

---

## 4. Search Input (optional)

If implemented, the search input sits below the header, above the list.

| Property | Value |
|---|---|
| Placeholder | `"Search conversations..."` |
| Icon | Magnify 14 px, `neutral-400`, left-aligned inside input |
| Height | 32 px |
| Margin | `8 px 12 px` |
| Font size | 13 px |
| Background | `surface-0` |
| Border | `1 px solid surface-300` |
| Border radius | `radius-md` |
| Focus border | `brand-500` |
| Focus ring | `2 px solid focus-ring`, `outline-offset: 2 px` |
| Filter behaviour | Client-side, filters list in real-time |
| No-results state | Show `"No conversations match"` in `neutral-400`, centered, 13 px |

---

## 5. Conversation List

### 5.1 Scrollable List Container

| Property | Value |
|---|---|
| Overflow-y | `auto` |
| Flex-grow | 1 |
| Padding-bottom | `16 px` (breathing room at bottom of list) |

### 5.2 Group Headings

Conversations are grouped into three recency buckets. Group headings are sticky within the scrollable list.

| Group | Display Label | Condition |
|---|---|---|
| Today | `"Today"` | Conversations started today |
| Previous 7 Days | `"Previous 7 days"` | Started in last 7 days, not today |
| Older | `"Older"` | Older than 7 days |

#### Heading Style

| Property | Value |
|---|---|
| Text | See table above |
| Font size | 11 px |
| Font weight | 600 |
| Text transform | Uppercase |
| Letter spacing | `0.05 em` |
| Colour | `neutral-400` |
| Padding | `12 px 12 px 4 px` |
| Position | `sticky`, `top: 0`, `z-index: 1` |
| Background | `surface-50` (matches panel, so sticky heading masks list beneath) |
| Border-bottom | `1 px solid surface-300`, `margin: 0 12 px` |

---

## 6. Conversation List Item — Default State

```
┌──────────────────────────────────────┐
│  Conversation title (truncated)  12:30│
└──────────────────────────────────────┘
```

| Property | Value |
|---|---|
| Height | 40 px |
| Padding | `0 12 px` |
| Display | Flex, align-items center, justify-content space-between |
| Font size | 14 px |
| Font weight | 400 |
| Colour | `neutral-700` |
| Background | Transparent |
| Border-left | `2 px solid transparent` (reserve space for active state) |
| Cursor | `pointer` |
| Text overflow | `overflow: hidden`, `white-space: nowrap`, `text-overflow: ellipsis` |
| Title max-width | Flex-grow 1, min-width 0 (required for ellipsis in flex child) |

### 6.1 Timestamp

| Property | Value |
|---|---|
| Format | `HH:MM` (today) / `"Yesterday"` / Day name (`"Mon"`) / `"Jun 1"` (older) |
| Font size | 12 px |
| Colour | `neutral-400` |
| White-space | `nowrap` |
| Margin-left | `8 px` |
| Flex-shrink | 0 |

---

## 7. Conversation List Item — States

### 7.1 Active (current conversation)

The item corresponding to the currently open conversation.

| Property | Value |
|---|---|
| Background | `brand-100` |
| Border-left | `2 px solid brand-600` |
| Text colour | `neutral-900` |
| Font weight | 500 |
| Timestamp colour | `neutral-500` |

### 7.2 Hover State

On hover of a non-active item:

| Property | Value |
|---|---|
| Background | `surface-200` |
| Transition | `background-color 150 ms ease-default` |
| Action row | Revealed (see §8) |
| Timestamp | Hidden when action row is shown (to avoid overlap) |

On hover of the active item:

| Property | Value |
|---|---|
| Background | `brand-100` (unchanged) |
| Action row | Revealed |

### 7.3 Focus State (keyboard)

| Property | Value |
|---|---|
| Outline | `2 px solid focus-ring`, `outline-offset: -2 px` (inset) |
| Background | `surface-200` |

---

## 8. Hover Action Row

When a list item is hovered, the timestamp is replaced by a row of two icon buttons: Rename and Delete. These appear with a `150 ms` fade-in.

```
│  Conversation title          [✏][🗑]  │
```

| Property | Value |
|---|---|
| Container | Flex row, gap `4 px`, align-items center |
| Transition | `opacity 150 ms ease-default` |
| Opacity at rest | 0 |
| Opacity on hover | 1 |

### 8.1 Rename Button

| Property | Value |
|---|---|
| Icon | Pencil 16 px, `neutral-500` |
| Size | 28 × 28 px |
| Shape | `radius-md` |
| Default bg | Transparent |
| Hover bg | `surface-300` |
| Icon hover colour | `neutral-700` |
| Focus ring | `2 px solid focus-ring`, `outline-offset: 2 px` |
| aria-label | `"Rename conversation"` |
| On click | Show inline rename flow (see §9) |

### 8.2 Delete Button

| Property | Value |
|---|---|
| Icon | Trash 16 px, `danger-500` |
| Size | 28 × 28 px |
| Shape | `radius-md` |
| Default bg | Transparent |
| Hover bg | `danger-100` |
| Icon hover colour | `danger-500` |
| Focus ring | `2 px solid danger-500`, `outline-offset: 2 px` |
| aria-label | `"Delete conversation"` |
| On click | Show inline delete confirmation (see §10) |

---

## 9. Inline Rename Flow

When the user clicks the Rename button, the title text is replaced by an editable text input in-place.

```
│  [________________________________________]  [✓] [✕]  │
│   ^— text input, pre-filled with current title         │
```

| Property | Value |
|---|---|
| Input height | 28 px |
| Input padding | `0 6 px` |
| Input font size | 14 px |
| Input border | `1.5 px solid brand-500` |
| Input border-radius | `radius-sm` (4 px) |
| Input background | `surface-0` |
| Input initial value | Current conversation title |
| Input focus | Auto-focus on appear; select-all text |
| Confirm button | Checkmark icon 16 px, 28 × 28 px, `brand-500` icon |
| Cancel button | X icon 16 px, 28 × 28 px, `neutral-500` icon |
| Submit | `Enter` key or Confirm button click |
| Cancel | `Escape` key or Cancel button click |
| On submit | Update title, revert to default list item display |
| On cancel | Revert to default list item display without change |
| Empty validation | If input is empty on submit, do not save; show `border: danger-500`; do not close |

---

## 10. Inline Delete Confirmation Flow

When the user clicks the Delete button, the list item row is replaced by an inline confirmation. This prevents accidental deletion without a disruptive modal.

### 10.1 Transition

The item row morphs into a confirmation row using a height animation:

| Property | Value |
|---|---|
| Initial height | 40 px (normal item) |
| Final height | 60 px (confirmation row — two sub-rows) |
| Transition | `height 200 ms cubic-bezier(0.4, 0, 0.2, 1)` |
| Overflow during transition | `hidden` |

### 10.2 Confirmation Row Layout

```
┌──────────────────────────────────────────────┐
│  "Delete this conversation?"                 │
│  [Cancel]                        [Delete]    │
└──────────────────────────────────────────────┘
```

| Property | Value |
|---|---|
| Question text | `"Delete this conversation?"` |
| Question font size | 13 px |
| Question colour | `neutral-700` |
| Question font weight | 500 |
| Question padding | `8 px 12 px 4 px` |

### 10.3 Confirmation Buttons

| Property | Cancel | Delete (Confirm) |
|---|---|---|
| Label | `"Cancel"` | `"Delete"` |
| Height | 28 px | 28 px |
| Padding | `0 10 px` | `0 10 px` |
| Font size | 12 px | 12 px |
| Font weight | 500 | 500 |
| Background | `surface-200` | `danger-500` |
| Text colour | `neutral-700` | `#FFFFFF` |
| Border radius | `radius-md` (8 px) | `radius-md` (8 px) |
| Hover bg | `surface-300` | `#DC2626` (darken danger-500) |
| Focus ring | `2 px solid focus-ring` | `2 px solid danger-500` |
| Transition | `background-color 150 ms ease-default` | same |
| Margin | `0 12 px 8 px` | same row |
| On click | Dismiss confirmation, restore item | Delete conversation, animate item out |

### 10.4 Delete Animation

After the user confirms deletion:

| Step | Behaviour |
|---|---|
| 1 | Item height animates to 0 px over `200 ms ease-in` |
| 2 | Opacity fades from 1 to 0 over `150 ms` (concurrent with height) |
| 3 | Item is removed from DOM after animation completes |
| 4 | If deleted convo was active | Navigate to most recent remaining conversation, or empty state if none |

---

## 11. Keyboard Navigation

| Key | Behaviour |
|---|---|
| `↑` / `↓` | Move focus between list items |
| `Enter` | Open focused conversation |
| `F2` | Start rename on focused item |
| `Delete` | Show delete confirmation for focused item |
| `Escape` | Cancel rename or dismiss delete confirmation |
| `Tab` | Move to next interactive element in focus order |

---

## 12. Empty History State

When there are no saved conversations:

```
┌──────────────────────────────────────┐
│  [New Chat +]                        │
│                                      │
│         (empty area)                 │
│                                      │
│   No conversations yet.              │
│   Start a new chat above.            │
│                                      │
└──────────────────────────────────────┘
```

| Property | Value |
|---|---|
| Text line 1 | `"No conversations yet."` |
| Text line 2 | `"Start a new chat above."` |
| Font size | 13 px |
| Colour | `neutral-400` |
| Text align | Center |
| Padding | `24 px 16 px` |
