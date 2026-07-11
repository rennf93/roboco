# Content Readability Helper (`content-readability.ts`)

A shared utility that defines thresholds and a checker for auto-collapsing long content in the task-detail view. It prevents tasks with long histories (many progress updates, checkpoints, or acceptance criteria) from forcing continuous scrolling through fully-expanded sections.

## Rationale

When a task accumulates 30+ progress entries, 20+ checkpoints, or a long acceptance-criteria list, rendering all entries open by default fills the viewport. Users must scroll through every item to reach later sections. This helper establishes a readability-driven threshold so long content defaults collapsed while short content stays visible.

## Thresholds

| Name | Value | Purpose |
|------|-------|---------|
| `READABILITY_LINE_THRESHOLD` | 10 | Content exceeding this many lines is considered "long" |
| `READABILITY_CHAR_THRESHOLD` | 640 | Content exceeding this character count is considered "long" |

A section defaults **collapsed** if either threshold is exceeded (OR logic). A section defaults **open** if both are satisfied (short by both measures).

## API

### `exceedsReadabilityThreshold(content: string): boolean`

Returns `true` if the content exceeds readability thresholds, indicating it should default collapsed.

```tsx
import { exceedsReadabilityThreshold } from "@/lib/content-readability";

const listText = items.map(i => i.name).join("\n");
const shouldCollapse = exceedsReadabilityThreshold(listText);
```

**Parameters:**
- `content` (string): Plain-text representation of the section body. Line breaks are respected; a multi-line string counts as multiple lines.

**Returns:**
- `true` if lineCount > 10 OR content.length > 640
- `false` otherwise

**Edge case:** empty or falsy content always returns `false` (empty content is always "readable" and defaults open).

## Usage in CollapsibleSection

`CollapsibleSection` integrates this checker via its optional `content` prop:

```tsx
<CollapsibleSection
  title="Acceptance Criteria"
  content={criteriaText}  // Plain-text list of all criteria, newline-joined
>
  <ul>
    {criteria.map(c => (
      <li key={c.id}>{c.text}</li>
    ))}
  </ul>
</CollapsibleSection>
```

If `content` is provided without an explicit `defaultOpen`, the component calls `exceedsReadabilityThreshold(content)` and defaults:
- **Collapsed** if `exceedsReadabilityThreshold` returns `true`
- **Open** if it returns `false`

An explicit `defaultOpen` prop always takes precedence, bypassing the content check.

## Usage in Tab Progress / Checkpoints

`tab-progress.tsx` wraps each progress update and checkpoint entry in a Radix Collapsible (not CollapsibleSection). A `defaultEntryOpen(idx, content)` function keeps only the **2 most recent entries open** (regardless of content length) and collapses all older entries. This applies even to short entries: older updates are collapsed to keep the list scannable.

```tsx
// Pseudo-code: each update is wrapped
<Collapsible
  defaultOpen={defaultEntryOpen(idx, updateContent)}
  // ... which evaluates to:
  // - true if idx < 2 AND content is short
  // - false if idx >= 2 OR content is long
>
  {/* update body */}
</Collapsible>
```

Test coverage: `tab-progress-collapse.test.tsx` verifies that a 30-entry task has entries 0–1 open and entries 2–29 closed, even if all entries are short.

## Rationale for thresholds

- **10 lines**: Enough to show a short paragraph or a 5-6 item list without scrolling the section itself. Most prose descriptions fit. A checkbox-list item typically takes 1 line; 10 items fits comfortably.
- **640 characters**: ~4–5 sentences of prose, or ~20 short list items (32 chars/item). Roughly equivalent to 10 lines of average content. The threshold allows either metric to trigger collapse independently (a single very-long line counts; a tall list of short lines counts).

Both must be satisfied to keep content open. This prevents edge cases where a small number of extremely long lines OR a tall list of 1-char items each individually pass but together are unreadable.

## Testing

`collapsible-section.test.tsx` covers the content-driven defaultOpen behavior:
- Short content stays open by default
- Long content (exceeding either threshold) defaults closed
- Explicit `defaultOpen` prop overrides content-driven behavior
- Controlled `open` prop is unaffected by content length

Example:

```tsx
it("defaults collapsed when content exceeds the readability thresholds", () => {
  render(
    <CollapsibleSection
      title="Section"
      content={"a".repeat(READABILITY_CHAR_THRESHOLD + 1)}
    >
      <p>body</p>
    </CollapsibleSection>,
  );
  expect(screen.getByRole("button", { name: "Section" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
});
```

## Related work

- **CollapsibleSection component** (`collapsible-section.tsx`): integrates this helper via the `content` prop
- **AcceptanceCriteria** (`acceptance-criteria.tsx`): wraps the full criteria list in CollapsibleSection with `content={criteriaText}`
- **TabProgress** (`tab-progress.tsx`): wraps each update/checkpoint entry in a Radix Collapsible with `defaultEntryOpen(idx, content)`

## Backward compatibility

Existing consumers of `CollapsibleSection` (task-description, tab-notes, tab-plan) all pass an explicit `open` prop (controlled mode), so the content-readability check does not apply to them. This change is additive: no existing behavior changes; new consumers can opt into content-driven collapse by omitting `defaultOpen` and providing `content`.
