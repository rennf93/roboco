# Verification Receipt

Covers `panel/src/lib/api/verification.ts` and `panel/src/components/verification/receipt.tsx` (plus `release-rollup.tsx`, the release-level aggregation of the same primitives). This is the one-screen surface the CEO merge decision reads: per-criterion QA verification stamps, the reviewer-round history, and the PR CI verdict. Every export here is a pure client-side derivation from data `GET /tasks/{id}`, `GET /tasks/{id}/findings`, and `GET /projects/{id}/conventions/findings` already return — no new backend endpoints.

## `AcVerificationStamp` — tri-state AC rendering

`parseAcVerificationStamps(acceptanceCriteria, qaNotes)` reads the deterministic `"[AC] <criterion> — verified: <evidence>"` lines `pass_review` stamps into `qa_notes` and returns one `AcVerificationStamp` per criterion:

```ts
interface AcVerificationStamp {
  criterion: string;
  matched: boolean;    // an [AC] line's label exactly matched this criterion's text
  verified: boolean;   // matches `matched` today
  unresolved: boolean; // matched is false, but qa_notes carries an orphan [AC] stamp
  evidence: string | null;
}
```

Why `unresolved` exists: `pass_review`'s `criteria_verified` accepts a criterion by its stable id as well as its exact text, but `acceptance_criteria_ids` isn't on the wire response (`TaskResponse` / `GET /tasks/{id}`) yet — a separate backend gap. When QA stamps a criterion by id, its `[AC]` line's label is the raw id, which matches no criterion text here. Before this fix, that criterion silently rendered as a confident "not verified" X — a false negative for a criterion QA actually verified. Now: `parseAcVerificationStamps` detects any `[AC]` line whose label matches no known criterion (an "orphan stamp") and marks every *unmatched* criterion `unresolved: true` in that case, instead of a confident false-negative.

**Rendering** (`AcVerificationList` in `receipt.tsx`):
- `verified` → green `CheckCircle2`.
- `unresolved` → yellow `HelpCircle` (`role="img"`, `aria-label="Possibly verified by id — unresolved"`), wrapped in a `HelpTip` explaining the id/text-matching gap.
- neither → gray `XCircle` (confidently not verified — no orphan stamp exists, so this criterion really has no matching QA stamp).

This is a **heuristic**, not real id resolution: when multiple criteria are unmatched and only one orphan stamp exists, it can't tell which specific criterion the id-stamp belongs to — it only avoids asserting confident-unverified across all of them in that ambiguous case. Real id matching needs `acceptance_criteria_ids` added to the wire response (tracked separately, out of scope here).

## `ReviewerChainList` — ledger-only scope

`buildReviewerChain(findings)` derives one entry per review round from the **findings ledger only** (`task_review_findings`) — there's no endpoint exposing `agent_spawn_sessions` per task, so this is the only reachable "review record." A round that passed clean (zero findings) never produces a ledger row and so never appears here.

Before this fix, both the empty state ("No review rounds recorded yet.") and the populated list read as "this is the complete review history" — an approver could misread a clean-passing round as "nobody reviewed this." Both states now say so explicitly:
- **Empty state**: "No review rounds recorded yet. Only rounds that recorded findings appear here — a round that passed clean has no entry."
- **Populated state**: the same caption renders above the list (not just in a tooltip), so an approver scanning the populated list sees the scope note without opening a `HelpTip`.

## `PrCiVerdict` / `ReleaseMemberTaskIdsUnavailable` — reader-facing vs. technical copy

Both `PR_CI_VERDICT_UNAVAILABLE` and `RELEASE_MEMBER_TASK_IDS_UNAVAILABLE` represent a genuine backend gap (no REST route exposes `GitService.get_pr_ci_status` per task; no endpoint exposes a release's member-task-id set) that's deliberately reported rather than invented. Each now splits into two fields instead of one:

```ts
interface PrCiVerdict {
  available: false;
  reason: string;           // short sentence safe to render directly to the approver
  technicalDetail: string;  // internal escalation detail — HelpTip/comment/docs only
}
```

Before this fix, `reason` carried the internal escalation string verbatim (`"...needs a new endpoint; escalate rather than invent."`) and it rendered as the visible sentence in `release-rollup.tsx`'s `<p>` and the CI tooltip on task detail — naming internals (`GitService.get_pr_ci_status`, `ReleaseReport.change_summary`) the approver has no use for. Now:
- `reason` is a short reader-facing sentence, e.g. `"Per-task PR CI verification isn't available yet."` / `"Per-task verification isn't available for this release yet."` — this is what renders in the UI.
- `technicalDetail` carries the endpoint-level escalation detail — never rendered as the primary visible sentence; consumers put it in a `HelpTip`, code comment, or this doc.

## Consumers

- `panel/src/hooks/use-verification.ts` — `useAcVerificationStamps`, `usePrCiVerdict` wrap the above for the task-detail Verification tab.
- `panel/src/components/tasks/task-detail/tab-verification.tsx` — renders `AcVerificationList`, `ReviewerChainList`, and the PR CI verdict for one task.
- `panel/src/components/verification/release-rollup.tsx` (`ReleaseVerificationRollup`) — aggregates the same per-task primitives across a release's member tasks; when the member-task-id set is itself unavailable (`RELEASE_MEMBER_TASK_IDS_UNAVAILABLE`), it renders that reason and skips calling `useAcVerificationStamps` entirely.

## Tests

- `panel/src/lib/api/__tests__/verification.test.ts` — `parseAcVerificationStamps` matched/unresolved/unverified cases, `PR_CI_VERDICT_UNAVAILABLE`/`RELEASE_MEMBER_TASK_IDS_UNAVAILABLE` reason-vs-technicalDetail split.
- `panel/src/components/verification/__tests__/receipt.test.tsx` — `AcVerificationList`'s 3 rendered states, `ReviewerChainList`'s empty and populated scope captions.
- `panel/src/components/verification/__tests__/release-rollup.test.tsx`, `panel/src/components/tasks/task-detail/__tests__/tab-verification.test.tsx`, `panel/src/components/dashboard/__tests__/release-proposal-card.test.tsx` — fixture shapes updated for the new fields; stale verbatim-technical-string assertions replaced with reader-facing-copy assertions.
