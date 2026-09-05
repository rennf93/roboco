# Task-Detail Verification Tab & Receipt Components

## Overview

The task-detail page's **Verification** tab assembles the approver's trust story — every acceptance criterion's verified state, findings by review round, the PR CI verdict, conventions findings, and the reviewer chain — in one place instead of scattering it across the Notes, Findings, and Commits tabs. It is the 9th tab on task detail (`panel/src/components/tasks/task-detail/task-tabs.tsx`), positioned right after Overview, and shows an unverified-acceptance-criteria count badge on the tab trigger when any criterion is not yet verified.

This is a **presentational-only** surface: every data read goes through the shared hooks documented in `hooks.md` under "Verification receipt data layer" (`useAcVerificationStamps`, `useFindingsByRound`, `usePrCiVerdict`, `useTaskConventionFindings`, `useReviewerChain`). No fetching logic lives in any component covered by this doc.

## Files

- `panel/src/components/tasks/task-detail/tab-verification.tsx` — the tab itself. Composes the five receipt pieces below via the shared hooks, for one task.
- `panel/src/components/verification/receipt.tsx` — five separately-exported, composable presentational pieces. This file is the reusable surface: the release-proposal rollup leaf (task `c348bb11`) reuses these exact exports rather than forking new components.

## The five receipt exports

Each export takes only the `{data, isLoading}` shape a shared hook already returns — none of them know about task-detail, tabs, or any specific caller.

| Export | Renders | Backing hook |
|---|---|---|
| `AcVerificationList` | Every acceptance criterion with a check/x icon for verified/unverified, plus the QA evidence line underneath when present | `useAcVerificationStamps` |
| `FindingsByRoundList` | Findings grouped by review round (round number + origin badge), each finding showing severity + open/addressed/verified/waived status badges and its `file:line` | `useFindingsByRound` |
| `CiVerdictBadge` | The PR CI verdict; today always renders "CI verdict unavailable" with a `HelpTip` naming the reason — see "Known ceiling: CI verdict" below | `usePrCiVerdict` |
| `ConventionsFindingsList` | Conventions-validator findings for the task's diff (level, `file:line`, rule) | `useTaskConventionFindings` |
| `ReviewerChainList` | The reviewer chain, one row per recorded round: round number, origin badge, author slug, and a `model: unknown` HelpTip — see "Known ceiling: reviewer model" below | `useReviewerChain` |

Every list export has its own empty state ("No acceptance criteria recorded.", "No revision findings recorded yet.", etc.) and its own `Skeleton` loading state — a caller never needs to handle loading/empty itself.

## Design treatment (per the PM's design-dial decision)

This is a dense admin data surface, not a landing page: `DESIGN_VARIANCE 2-3`, `MOTION_INTENSITY 2-3` (hover/active only — no scroll choreography or entrance animation), `VISUAL_DENSITY 7-8`. Concretely:

- Dividers (`divide-y`) between rows instead of card boxes.
- `tabular-nums` on the findings-by-round and reviewer-chain lists, since these are count/round-number-heavy.
- Tight paddings (`py-1.5`/`py-2`), no card elevation anywhere in the receipt.
- The tab reuses the existing task-detail tab chrome (`Tabs`/`TabsContent` from `@/components/ui/tabs`) rather than introducing a new layout system.

## Known ceilings (surfaced in the UI, not hidden)

Two data-layer limits (documented in `hooks.md`'s "Escalated gaps" table) show up as explicit UI states rather than blank space:

- **CI verdict**: `CiVerdictBadge` always renders "CI verdict unavailable" today, because no REST route wraps `GitService.get_pr_ci_status`. The `HelpTip` attached to the badge names the reason string the hook returns, so an approver sees *why* it's unavailable instead of a silently missing widget.
- **Reviewer model**: `ReviewerChainList`'s `model: unknown` HelpTip explains no endpoint exposes which model an agent ran on for a review round. A round that passed clean (no ledger finding) also has no chain entry at all — this is the ledger's structural limit, not a bug in the component.

Both ceilings are also called out in the shared parent task's dev risk notes as candidates for a follow-up backend task; this UI leaf renders them honestly rather than guessing.

## Testing

`panel/src/components/tasks/task-detail/__tests__/tab-verification.test.tsx` mocks `@/hooks/use-verification` (never the network) and covers the three states the acceptance criteria call out explicitly:

1. **All-verified** — every acceptance criterion renders with its evidence line.
2. **Open findings** — a finding renders its severity and `open` status badge plus its `file:line`.
3. **No-CI-repo** — `usePrCiVerdict` returning `{available: false}` renders the "CI verdict unavailable" text.

`panel/src/components/tasks/task-detail/__tests__/task-tabs.test.tsx` was updated alongside this: the `@/hooks/use-tasks` mock gained `useTask` (now pulled in transitively via `useAcVerificationStamps`'s `useTask(taskId)` cache reuse), and the active-tab-highlight assertion moved from 7 to 8 non-Overview tabs to account for the new Verification tab.

## Integration point: `task-tabs.tsx`

- The tab grid moved from `grid-cols-8` to `grid-cols-9`.
- The Verification tab's trigger reads `unverifiedCount` from `useAcVerificationStamps(task.id)` and shows it as the tab's count badge only when greater than zero (an all-verified task shows no badge, not a `0`).
- `<TabsContent value="verification">` renders `<TabVerification task={task} />` immediately after the Overview content, matching the tab's position in the trigger list.

## For the next consumer (release-proposal rollup)

The release-proposal rollup leaf (task `c348bb11`) is expected to import directly from `@/components/verification/receipt` — the same five named exports this doc describes — rather than re-implementing rendering logic. If that leaf needs a layout different from the task-detail tab's stacked `<section>`s, it should still reuse these exports and only change the surrounding composition, keeping the presentational pieces single-sourced.
