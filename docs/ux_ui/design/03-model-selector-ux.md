# Prompter — Model Selector UX Specification

## Principle

Most users do not know which model drafts better tasks. The model selector should be available for power users but invisible for everyone else. The default must be safe, fast, and context-aware.

---

## Placement

**Inside the Advanced Options drawer** of the confirmation interstitial.

- `Collapsible` trigger label: "Advanced Options"
- When expanded, the first item in the drawer is the model selector.

This follows the existing `CreateTaskDialog` pattern where advanced fields (parent task, assignee, git config) are tucked behind a `Collapsible` with `ChevronRight` / `ChevronDown` icons.

Rationale:
- Reduces cognitive load on the primary review screen.
- Prevents choice paralysis for users who don’t care about the model.
- Aligns with the Head of Marketing directive: "most users don’t know which model drafts better tasks."

---

## Component

`Select` from `panel/src/components/ui/select.tsx`:

```tsx
<Select value={model} onValueChange={setModel}>
  <SelectTrigger className="w-full">
    <SelectValue />
  </SelectTrigger>
  <SelectContent>
    <SelectItem value="recommended">
      ⭐ Recommended — {dynamicLabel}
    </SelectItem>
    <SelectItem value="claude-sonnet-4">
      Claude Sonnet 4 — Balanced
    </SelectItem>
    <SelectItem value="gpt-4o">
      GPT-4o — Fast
    </SelectItem>
    <SelectItem value="claude-opus-4">
      Claude Opus 4 — Deep reasoning
    </SelectItem>
  </SelectContent>
</Select>
```

---

## Defaults

### Default Selection: "Recommended"

The `recommended` value is not a real model ID; it is a frontend alias that resolves to a model based on the draft's estimated complexity:

| Draft Complexity | Resolved Model | Rationale |
|----------------|----------------|-----------|
| `LOW` | `gpt-4o` or lightweight equivalent | Fast, cheap, good enough for simple tasks |
| `MEDIUM` | `claude-sonnet-4` | Balanced quality and speed |
| `HIGH` | `claude-opus-4` | Deep reasoning for complex specs |

**Dynamic label**: the Select trigger should display the resolved model name in the description:

> ⭐ Recommended — Claude Sonnet 4

This gives transparency without requiring the user to make a manual choice.

### How Complexity Is Determined

1. **LLM suggestion**: the drafting LLM outputs an `estimated_complexity` field as part of the structured draft.
2. **User override**: if the user changes the Complexity `Select` in the review modal, the recommended model re-evaluates automatically.
3. **No user model preference persistence in Phase 1** — each session starts fresh with `recommended`.

---

## Option Descriptions

Each `SelectItem` should have a one-line subtitle explaining when to choose it:

```
⭐ Recommended — Claude Sonnet 4
  (Best balance for this task's complexity)

Claude Sonnet 4 — Balanced
  (Good for most tasks)

GPT-4o — Fast
  (Quick drafts, simpler specs)

Claude Opus 4 — Deep reasoning
  (Complex architecture or security tasks)
```

Implementation note: if `SelectItem` does not natively support subtitles, append the subtitle as muted text inside the item using a nested `span` with `text-muted-foreground text-xs`.

---

## Visual Hierarchy

```
┌─ Advanced Options ──────────────────────┐
│                                         │
│  Model                                  │
│  [⭐ Recommended — Claude Sonnet 4 ▼]  │
│    ├─ ⭐ Recommended — Claude Sonnet 4 │
│    ├─ Claude Sonnet 4 — Balanced        │
│    ├─ GPT-4o — Fast                    │
│    └─ Claude Opus 4 — Deep reasoning   │
│                                         │
│  Assign To …                            │
│  Parent Task …                          │
│  …                                      │
└─────────────────────────────────────────┘
```

---

## Accessibility

- `Label` with `htmlFor` tied to the `SelectTrigger` id.
- `aria-describedby` on the trigger pointing to a helper paragraph: "The model used to draft this task. 'Recommended' picks the best fit automatically."
- `SelectContent` should trap focus while open; `Esc` closes the dropdown.

---

## Out of Scope (Phase 2)

- Model comparison side-by-side
- User-level default model preference
- Cost/usage indicators per model
- Temperature / max-tokens sliders
- Custom system prompt editing

---

## Cross-Cell Note

The frontend sends the resolved model ID (not the alias) to the backend chat endpoint. The backend endpoint in `roboco/services/llm.py` already handles multi-provider routing; the frontend only needs to pass the model string in the payload.
