# Content-Readability Thresholds

**File**: `panel/src/lib/content-readability.ts`

**Problem**: A task with a long progress history (30+ updates), many checkpoints, or detailed acceptance criteria would render fully expanded, forcing continuous scrolling through both old and new content. This significantly degrades UX on tasks with verbose or lengthy histories.

**Solution**: Auto-collapse sections and entries that exceed readability thresholds, keeping only the most recent and concise content visible by default.

## Thresholds

- **Line threshold**: `10 lines`
- **Character threshold**: `640 characters`

Content exceeding **either** threshold defaults to collapsed.

### How they were chosen

- **10 lines** roughly fits a typical commit message or moderate progress update on a standard mobile viewport (300–400px width)
- **640 characters** is approximately 70–80 words, a readable paragraph of context without requiring scrolling within a single entry
- Derived from typography best practices (line length for legibility) and task-detail UX surveys

## Components using the thresholds

### 1. **CollapsibleSection** (`panel/src/components/tasks/task-detail/collapsible-section.tsx`)

A reusable collapsible section component that auto-applies the thresholds.

**Props**:
- `content: string` (optional) – Plain-text representation of the section body
- `defaultOpen: boolean` (optional) – Explicit override (always respected)

**Logic**:
```typescript
// resolved default = explicit prop > content check > default to true
const resolvedDefaultOpen =
  defaultOpen ??
  (content !== undefined ? !exceedsReadabilityThreshold(content) : true);
```

**Usage examples**:
```tsx
// Acceptance criteria: auto-collapse if criteria list is long
<CollapsibleSection
  title="Acceptance Criteria"
  content={criteriaText}  // passed to decide defaultOpen
>
  {/* render criteria */}
</CollapsibleSection>

// Force open during edit (e.g., user is actively adding a criterion)
<CollapsibleSection
  title="Acceptance Criteria"
  content={criteriaText}
  defaultOpen={isEditing}  // explicit override
>
  {/* render criteria */}
</CollapsibleSection>
```

### 2. **TabProgress: ProgressUpdatesSection** (`panel/src/components/tasks/task-detail/tab-progress.tsx`)

Shows task progress entries (timestamped messages, percentage checkpoints) in reverse chronological order (newest first).

**Dual-threshold logic**:
```typescript
const RECENT_OPEN_COUNT = 2;

function defaultEntryOpen(idx: number, content: string): boolean {
  if (idx >= RECENT_OPEN_COUNT) return false;  // idx 2+ always collapsed
  return !exceedsReadabilityThreshold(content);  // idx 0–1: check content length
}
```

**Behavior**:
- **2 most recent entries**: Start open if their individual content fits under thresholds; collapse if verbose
- **Entries 3+**: Always start collapsed (user can expand any individual entry)

**Rationale**: A task with 32 progress updates would fill 1+ screenfulls if all expanded. Showing the 2 most recent (usually the most relevant) keeps the page scrollable.

### 3. **TabProgress: CheckpointsSection** (`panel/src/components/tasks/task-detail/tab-progress.tsx`)

Shows saved checkpoints (state summaries, remaining work) using the same dual-threshold logic as ProgressUpdatesSection.

### 4. **AcceptanceCriteria** (`panel/src/components/tasks/task-detail/acceptance-criteria.tsx`)

Lists all acceptance criteria (checkbox format).

**Usage**:
```typescript
const criteriaText = criteria.map((c) => parseCriterion(c).text).join("\n");

<CollapsibleSection
  title="Acceptance Criteria"
  content={criteriaText}  // all criteria joined; triggers auto-collapse if list is long
>
  {/* render criteria list */}
</CollapsibleSection>
```

**Behavior**:
- A short list (e.g., 2–5 criteria, <640 chars total) starts expanded
- A long list (e.g., 20+ criteria, >640 chars total) starts collapsed
- User can always click the section header to toggle

## Testing

**File**: `panel/src/components/tasks/task-detail/__tests__/task-detail-readability.test.tsx`

Regression test suite verifying the thresholds work end-to-end:

```typescript
// AC4: 32 progress entries, only 2 open by default
it("keeps a 30+ entry progress history navigable — only the 2 most recent default open", () => {
  const task = buildTask({ progress_updates: makeUpdates(32) });
  const { container } = render(<TabProgress task={task} />);

  const openCount = triggers.filter(
    (t) => t.getAttribute("data-state") === "open",
  ).length;
  expect(openCount).toBe(2);
});

// Long criteria list: section starts collapsed
it("collapses a long acceptance-criteria list by default", () => {
  const task = buildTask({ acceptance_criteria: makeLongCriteria(20) });
  render(<AcceptanceCriteria task={task} />);

  expect(
    screen.getByRole("button", { name: /acceptance criteria/i }),
  ).toHaveAttribute("aria-expanded", "false");
});

// Short criteria list: section starts expanded (no regression)
it("keeps a short acceptance-criteria list expanded", () => {
  const task = buildTask({ acceptance_criteria: makeLongCriteria(2) });
  render(<AcceptanceCriteria task={task} />);

  expect(
    screen.getByRole("button", { name: /acceptance criteria/i }),
  ).toHaveAttribute("aria-expanded", "true");
});
```

Run tests:
```bash
pnpm test task-detail-readability.test.tsx
```

## Implementation notes

- **Threshold check is content-only**: No rendering or layout inspection. All decisions are based on text length (lines + characters), not visual dimensions, so the logic is stable across screen sizes and fonts.
- **Explicit `defaultOpen` always wins**: A parent can force a section open (e.g., while editing) by passing `defaultOpen={true}`, overriding the content check.
- **User action overrides defaults**: Once a user clicks to expand/collapse, local state persists for that session. The thresholds only set the initial state.
- **Fade-slide animation**: Open/close transitions use CSS fade + slide (opacity/transform only, never height/width), so the browser never needs to recalculate layout mid-animation. `prefers-reduced-motion` is respected globally in `globals.css`.

## Future improvements

1. **Tunable thresholds per section**: Allow different thresholds for progress updates vs. acceptance criteria (e.g., progress updates default to 1 open, criteria list threshold to 20 lines).
2. **Smart recent-count**: Use task metadata (e.g., if no updates in 7 days, show more recent ones) to decide how many to keep open.
3. **User preference**: Let users set their preferred thresholds (Settings → Content Readability).
