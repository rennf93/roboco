# Release-Proposal Verification Rollup

## Overview

The release-proposal card (`release-proposal-card.tsx`) surfaces a **Verification rollup** section that aggregates the same per-task trust story the task-detail Verification tab shows — every acceptance criterion's verified state, findings by review round, PR CI verdict, conventions findings, and reviewer chain — across every member task the release carries, so the CEO can read the whole release's trust story in one place instead of opening each task individually.

This is the third of three consumption-ordered leaves built on the shared verification data layer: leaf 1 (`hooks.md`'s "Verification receipt data layer") built the typed client + hooks, leaf 2 (`verification-tab.md`) built the composable receipt components and the single-task tab, and this leaf composes both **without forking or re-implementing either**.

## Files

- `panel/src/components/verification/release-rollup.tsx` — `ReleaseVerificationRollup`, the new component. Renders one collapsible section per member task id.
- `panel/src/components/dashboard/release-proposal-card.tsx` — wires the rollup into the release-proposal card, right below the release report, behind a `HelpTip` labeled "Verification rollup".
- `panel/src/lib/api/release.ts` — `ReleaseProposal.member_task_ids?: string[] | null`, a new optional field for the (not-yet-existing) member-task-id source.
- `panel/src/lib/api/verification.ts` — `RELEASE_MEMBER_TASK_IDS_UNAVAILABLE`, the disclosed-gap constant described below.

## Composition, not a fork

`ReleaseVerificationRollup` imports the exact five receipt exports from `@/components/verification/receipt` (`AcVerificationList`, `FindingsByRoundList`, `CiVerdictBadge`, `ConventionsFindingsList`, `ReviewerChainList`) and the exact five hooks from `@/hooks/use-verification` (`useAcVerificationStamps`, `useFindingsByRound`, `usePrCiVerdict`, `useTaskConventionFindings`, `useReviewerChain`) that `verification-tab.md` documents. Nothing in `release-rollup.tsx` re-implements their rendering or fetching logic — it only calls each hook once per member task id and lays the five sections out inside a `CollapsibleSection` (`variant="button"`, from `@/components/ui/collapsible-section`) per task, so N member tasks render as N independently-collapsible rows instead of one page-long dump.

Each row's always-visible header (the collapsed state) already summarizes the member task without expanding it: a `{verified}/{total} AC verified` badge, an `{n} open` badge when any finding is still open, and the `CiVerdictBadge`. Expanding a row reveals the same four detail sections the task-detail tab shows (acceptance criteria, findings by round, conventions findings, reviewer chain).

## Known gap: no member-task-id source yet

`ReleaseVerificationRollup` takes `taskIds: string[]` as a prop rather than deriving it from the release proposal itself, because **no existing backend endpoint exposes a release proposal's member task set**. `ReleaseReport.change_summary` (`roboco/services/release_readiness.py`) is free-text commit strings only — no `task_id` or `pr_number` is exposed anywhere on the release-manager surface (`release.py` schema, `ReleaseReportModel`). This was investigated and confirmed by fe-pm before the leaf was built (see the dev's decision-log journal entry), following the same precedent already accepted for the PR CI verdict gap in leaf 1: report the gap for escalation via a disclosed `available: false` constant rather than inventing a derivation (e.g. parsing commit-message strings to guess task ids).

`RELEASE_MEMBER_TASK_IDS_UNAVAILABLE` (`panel/src/lib/api/verification.ts`) is that constant:

```typescript
{
  available: false,
  reason:
    "No backend endpoint exposes this release's member task set " +
    "(ReleaseReport.change_summary is free-text commit strings only, no " +
    "task_id/pr_number) — needs a new endpoint; escalate rather than invent.",
}
```

Concretely, this means:

- `ReleaseProposal.member_task_ids` is optional and always absent in production today — no existing endpoint populates it.
- `ReleaseVerificationRollup` renders `RELEASE_MEMBER_TASK_IDS_UNAVAILABLE.reason` as plain text (and calls none of the five hooks) whenever it receives an empty `taskIds` array — which is always, in production, until a follow-up backend task adds the member-task-id source.
- The component is still built against the real aggregation shape (parametrized over an explicit id list) rather than stubbed out, so it renders the real per-member-task rollup the day a real endpoint ships one — component tests inject task ids directly to exercise that path (see Testing below).

This is a disclosed, intentional gap, not an oversight — flagged in the dev's PR risk notes as a candidate follow-up backend task (adding a real member-task-id source to the release-proposal API).

## Design treatment

Same design dials as the task-detail Verification tab this leaf reuses (`DESIGN_VARIANCE 2-3`, `MOTION_INTENSITY 2-3`, `VISUAL_DENSITY 7-8` — a dense data surface): `divide-y` dividers between member-task rows instead of card boxes, `tabular-nums` on the badge counts, tight paddings, and the existing release-proposal card chrome — no new layout system introduced for this leaf.

## Testing

`panel/src/components/verification/__tests__/release-rollup.test.tsx` mocks `@/hooks/use-verification` (never the network, per the acceptance criteria) and covers:

1. **No member-task ids** — renders the unavailable reason text and calls none of the shared hooks.
2. **Mixed verified/open across member tasks** — two injected task ids, one fully verified with a clean CI verdict, one partially verified with an open finding; asserts both AC-count badges, the open-finding badge, and the detail sections render the mixed state correctly.
3. **No-CI-repo member case** — both member tasks return `{available: false}` from `usePrCiVerdict`, each with its own distinct `reason` string, asserting the CI-unavailable badge renders once per member task (not a single shared/global verdict).
4. **Title fallback** — a member task with no `taskTitles` entry falls back to rendering the bare task id.

`panel/src/components/dashboard/__tests__/release-proposal-card.test.tsx` gained one test confirming the card renders the "Verification rollup" section header and the unavailable-reason text when `member_task_ids` is absent from the proposal (today's only production state).

## Related work

- **Task-detail Verification tab & receipt components** (`verification-tab.md`) — the upstream leaf this rollup reuses verbatim.
- **Verification receipt data layer** (`hooks.md`, "Verification receipt data layer" section) — the shared hooks/typed client both UI leaves consume.
