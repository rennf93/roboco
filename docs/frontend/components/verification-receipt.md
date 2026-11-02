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

## `ReviewerChainList` — every review round

`buildReviewerChain(entries)` sorts the task-metrics `reviewer_chain` (`GET /dashboard/metrics/task/{task_id}`, sourced from `agent_spawn_sessions` by `roboco.services.metrics.reviewer_chains_for_tasks`) by round: one entry per review-layer spawn session (qa / pr_reviewer / cell_pm / main_pm), with the real model, including a round that passed clean. The empty state reads "No review rounds recorded yet for this task."

## `PrCiVerdict` — the PR head's CI state

`prCiVerdictFromAttestation(ci)` maps an attestation CI verdict (`roboco.services.attestation.pr_ci_verdict`, the same check-runs signal the in-path PR gate reads) onto `PrCiVerdict`: `available: true` with the state, head sha and failing checks for a known state, otherwise `PR_CI_VERDICT_NOT_AVAILABLE`. `PR_CI_VERDICT_UNREADABLE` is the copy when the read itself failed. Both `reason` strings are reader-facing and name no internals.

## Release rollup — two requests, never one per member

`ReleaseVerificationRollup` (`release-rollup.tsx`) lays the receipt out once per member task of the open release proposal. It reads every member through two bulk endpoints and nothing else:

- `useReleaseVerification` → `GET /api/release/proposal/verification`: every member's acceptance criteria and `qa_notes`, findings ledger, conventions findings and reviewer chain, read server-side with a fixed number of queries (`roboco.services.release_verification.read_release_member_receipts`). `memberVerificationFromReceipt` applies the same derivations the task-detail tab uses.
- `useReleaseCiVerdicts` → `GET /api/release/proposal/ci-verdicts`: every member PR's CI verdict, read from the forge four at a time on background-pool sessions; `memberCiVerdict` picks one member's.

Until 2026-10-28 the rollup fired four requests per member (task, findings, task metrics, attestation). The 74-member release on the Overview card meant about 300 requests per render, repeated on every refetch of the `["tasks"]` prefix, and it exhausted the orchestrator's DB pool and the guard's Redis pool. The rollup's query keys live under `["release", …]` so a task mutation's `["tasks"]` invalidation no longer refetches them. A proposal with no member tasks renders `RELEASE_NO_MEMBER_TASKS_MESSAGE` and fetches nothing; a failed receipts read renders `RELEASE_ROLLUP_UNREADABLE_MESSAGE`.

The app's query client (`app-providers.tsx`) retries through `shouldRetryQuery` (`panel/src/lib/query-retry.ts`): only a request that got no HTTP response is replayed, once. A 4xx or 5xx answer is final for the query client; a 429 is replayed after its `Retry-After` by the API client's own interceptor.

## Consumers

- `panel/src/hooks/use-verification.ts` — per-task hooks (`useAcVerificationStamps`, `useFindingsByRound`, `useReviewerChain`, `useTaskConventionFindings`, `usePrCiVerdict`) for the task-detail Verification tab; bulk hooks (`useReleaseVerification`, `useReleaseCiVerdicts`) for the release rollup.
- `panel/src/components/tasks/task-detail/tab-verification.tsx` — renders the receipt for one task.
- `panel/src/components/verification/release-rollup.tsx` (`ReleaseVerificationRollup`) — the release-level aggregation, mounted by `release-proposal-card.tsx` with the proposal's task id and member count.

## Tests

- `panel/src/lib/api/__tests__/verification.test.ts` — `parseAcVerificationStamps` matched/unresolved/unverified cases, round grouping, reviewer-chain order, CI verdict mapping, `memberVerificationFromReceipt`, `ciVerdictsByTask` / `memberCiVerdict`.
- `panel/src/components/verification/__tests__/receipt.test.tsx` — the presentational pieces' rendered states.
- `panel/src/components/verification/__tests__/release-rollup.test.tsx` — empty state (nothing fetched), mixed member state from the bulk read, unreadable receipts, unreadable CI.
- `panel/src/components/dashboard/__tests__/release-proposal-card-fanout.test.tsx` — a 12-member release reads every member through exactly one `/verification` and one `/ci-verdicts` request and no per-member request.
- `panel/src/components/__tests__/app-providers.test.tsx`, `panel/src/lib/__tests__/query-retry.test.ts` — the query client never replays an answered 429/5xx.
- `tests/integration/test_release_verification_route.py` — the bulk receipts' content, a database statement count that does not grow with the member count, bounded CI lookup concurrency, the per-task findings cap.
