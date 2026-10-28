# Stuck Indicator

Covers `panel/src/hooks/use-stuck-state.ts` and `panel/src/components/tasks/stuck-indicator.tsx`. A read-only chip that surfaces the backend's wedge-ledger state on kanban cards and the task-detail header. It never offers an action: resolving a stuck task is a PM/CEO decision.

## Backend contract (frozen, additive on the task payload)

Three optional fields were added to `Task` in `panel/src/types/index.ts`. They are optional because the backend slice ships in parallel; a payload without them reads as "healthy".

| Field | Type | Meaning |
|---|---|---|
| `active_time_since_progress` | `string \| number \| null` | Active time since the last progress-fingerprint movement. A number is seconds; a string is treated as already formatted. |
| `open_wedge_strikes` | `number` | Count of open wedge strikes. |
| `wedge_cycle` | `WedgeCycleEntry[]` | Recorded loop iterations: `{ actor, verb, timestamp }` (new exported interface `WedgeCycleEntry`). |

Changes to this contract route back through main-pm; do not rename or reshape the fields panel-side.

## When a task counts as stuck

`deriveStuckState(task)` returns `isStuck: true` when either holds:

- `open_wedge_strikes > 0`, or
- the task is blocked for a human: `status === TaskStatus.BLOCKED` and `blocker_resolver_type === "human"` (`isTaskBlockedForHuman`).

A task blocked for an `agent` resolver is not stuck. A null or undefined task is not stuck.

The returned `StuckState` also carries `activeTimeSinceProgress`, `openWedgeStrikes`, `wedgeCycle` (passed through verbatim), and `reasons` (for example `"2 open wedge strikes"`, `"blocked for human resolution"`), which feeds the tooltip.

## Hook layer: `panel/src/hooks/use-stuck-state.ts`

Exports (all re-exported from `@/hooks`):

- `deriveStuckState(task)`: pure derivation, unit-testable without React.
- `useStuckState(task)`: thin wrapper over `deriveStuckState`.
- `isTaskBlockedForHuman(task)`.
- `formatActiveTime(value)`: strings pass through unchanged; seconds become a coarse clock showing at most the two largest units (`93900` -> `1d 2h`, `840` -> `14m`, `30` -> `30s`); `null`, `undefined`, and `""` return `null`.

Despite living in `hooks/`, this hook does **not** fetch. The list and detail queries already return the contract fields on each task, so the hook only derives state from the payload and adds no second request.

## Component: `StuckIndicator`

`<StuckIndicator task={task} />` (exported from `@/components/tasks`).

- Renders nothing when the task is not stuck.
- Otherwise renders a red chip with an hourglass icon and `data-testid="stuck-indicator"`: `stuck · <time> since progress`, or just `stuck` when no active time is present. Colors reuse the panel's existing red badge tokens, light and dark.
- Hover tooltip: the reasons, the active time since last progress movement, a "Wedge cycle:" list of `actor → verb · timestamp` lines (timestamps are shown as the raw ISO string the backend sent), and the line "Read-only — resolution is a PM/CEO decision."

### Where it is rendered

- Kanban card: `components/kanban/core/kanban-card.tsx`, in the badge row after `BlockedBadge`.
- Task detail: `components/tasks/task-detail/task-header.tsx`, next to the existing stalled chip. The two are independent: stalled depends on `stalled_reason`, stuck on the wedge-ledger fields, so a task can show both.

## Tests

Contract fields are mocked because the backend slice was not merged at QA time. Both suites build a minimal `Task` stub and set only the fields they read.

- `hooks/__tests__/use-stuck-state.test.ts`: healthy, strikes, blocked-for-human, blocked-for-agent, cycle passthrough, null task, `formatActiveTime` cases.
- `components/tasks/__tests__/stuck-indicator.test.tsx`: renders nothing when healthy, chip text with and without active time, human vs agent block, tooltip content including the read-only note.

When the backend ships the real payload, replace the mocks with real fixtures if the field shapes differ (in particular whether `active_time_since_progress` arrives as seconds or a string; both are handled).
