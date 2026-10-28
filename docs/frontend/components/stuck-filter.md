# Tasks-page Stuck Filter

Covers the Stuck toggle on the Tasks page: `panel/src/app/(dashboard)/tasks/page.tsx`, `panel/src/components/tasks/task-filters.tsx`, and the `stuck` option on `TaskFilters` in `panel/src/lib/api/tasks.ts`. It is read-only: it narrows the list and offers no remediation action, because resolving a stuck task is a PM/CEO decision. The per-task chip is documented separately in [`stuck-indicator.md`](./stuck-indicator.md).

## Contract: the backend decides what is stuck

The filter sends a query parameter to the tasks list endpoint and renders exactly the set that comes back. The backend contract is "exactly the non-terminal tasks carrying open wedge strikes or a blocked-for-human status". The panel never re-derives stuckness for this list: it does not filter `tasks` client-side, and it does not call `deriveStuckState` here.

| Layer | Behavior |
|---|---|
| URL | `?stuck=1` turns the filter on; absence means off. Read with `searchParams.get("stuck") === "1"`, same pattern as the existing `?stalled=1`. |
| Page | When on, adds `stuck: true` to the filters passed to `useTasks`. When off, the key is omitted entirely (not `stuck: false`). |
| API client | `tasksApi.list` appends `stuck=true` to `GET /tasks/summary` only when `filters.stuck` is truthy. |
| Render | The page hands the returned `tasks` straight to `TaskTable`. |

**Assumed parameter name.** The cross-cell contract left the exact parameter name to the backend cell. The panel assumes `stuck=true`. If the backend ships a different name, the change is one line in `tasksApi.list` (`params.append("stuck", "true")`); the URL state and component props stay as they are. Confirm against the merged backend slice.

## Component: `TaskFilters`

Two new optional props:

| Prop | Type | Purpose |
|---|---|---|
| `stuckFilter` | `boolean` (default `false`) | Whether the filter is active. |
| `onStuckChange` | `(value: boolean) => void` | Toggle handler. **When omitted, the toggle is not rendered at all**, so other pages that reuse `TaskFilters` are unaffected. |

When `onStuckChange` is provided:

- A "Stuck" button with an `AlertTriangle` icon sits next to the Stalled toggle. It uses the `default` variant when active and `outline` when not, sets `aria-pressed`, and has a HelpTip: "Open tasks with wedge strikes or blocked for a human decision".
- While active, a removable "Stuck" badge appears in the active-filters row (the remove icon has `aria-label="Remove Stuck filter"`).
- "Clear all" also calls `onStuckChange(false)`, and the active-filters row and the Clear all button both show when only the Stuck filter is on.

The page's `handleStuckChange` writes `stuck=1` to the URL, or removes the key when turned off, via the page's existing `updateParams`.

## Where the data fetching lives

No new hook. Fetching stays in the existing `useTasks` hook (`panel/src/hooks`), which receives the extended filters object. The toggle and badge are presentational and live in `panel/src/components/tasks`, consistent with the architectural standard.

## Shared surface

`panel/src/lib/api/tasks.ts` is also touched by the stuck-indicator slice (card/detail chip). The two changes are independent: the chip reads wedge-ledger fields on each task, this filter adds a request parameter.

## Stuck vs Stalled

Both toggles can be on at once and are independent. Stalled uses the stalled-tasks query and `stalled_reason` (see [`../stalled-needs-you.md`](../stalled-needs-you.md)); Stuck uses the backend `stuck` parameter and the wedge ledger.

## Tests

The backend slice was not merged at QA time, so the contract is mocked.

- `app/(dashboard)/tasks/__tests__/stuck-filter.test.tsx`: mocks `tasksApi.list`. With `?stuck=1`, asserts the call carries `stuck: true` and that `TaskTable` receives exactly the mocked response; with no param, asserts the `stuck` key is absent.
- `components/tasks/__tests__/task-filters-stuck.test.tsx`: toggle hidden without `onStuckChange`; click calls `onStuckChange(true)` with `aria-pressed="false"` beforehand; active badge removal and Clear all both call `onStuckChange(false)`.

Once the backend ships, re-check the parameter name and swap in real fixtures if the response shape differs.
