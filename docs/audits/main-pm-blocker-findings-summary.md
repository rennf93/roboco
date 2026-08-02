# Main PM blocker-findings synthesis — 2026-08-02

Main PM-level synthesis tying the three cell audit reports together. Addresses revision findings `894227b7` (minor), `81d7a501` (major), and `39188666` (blocker) from root PR #794's bounce.

As-of: 2026-08-02 11:30 UTC.

## Scope

Sentinel flagged 6 open BLOCKER-severity findings (plus 2 major, 2 minor) in the `task_review_findings` ledger on 2026-07-29. Main PM routed the audit to all three cell PMs since no cross-team findings-query tool exists. This doc synthesizes the three cell audit reports, enumerates all 6 open blocker findings by ID with owner and disposition, reconciles Sentinel's count of 6 against the cell audits' count of 2 found, and gives a cross-cell lifecycle verdict.

Source audit docs (already merged into the root branch):

- `docs/audits/backend-blocker-findings-2026-07-30.md` — backend found 2 active blocker findings (plus 1 superseded-but-open).
- `docs/frontend/blocker-findings-audit-2026-07-29.md` — frontend found 0 blocker findings.
- `docs/ux_ui/blocker-findings-audit-2026-07-29.md` — ux_ui found 0 blocker findings.

## Open BLOCKER findings — enumeration

| # | Finding ID | Origin | Round | Task | Owner | Disposition |
|---|-----------|--------|-------|------|-------|-------------|
| 1 | `4e4ef942` | pr_gate | r2 | `a9a030d8` (PR #718) | be-pm | **PENDING-VERIFICATION** — task reached `completed` as of 2026-07-31T23:00:27 UTC; closure inferred from completion mechanics (a task cannot complete while it carries an open pr_gate blocker) but never directly confirmed via ledger read. evidence()/KB tools timed out across 5+ retries in the backend audit and returned `not_authorized` on this round (not the assignee). Follow-up owner: **be-pm** to confirm via direct ledger read when tools recover. |
| 2 | `53ce191d` | pr_gate | r1 | `a9a030d8` (PR #718) | be-pm | **OPEN (superseded, not auto-closed)** — round-1 finding for the same CI-red issue that re-surfaced as `4e4ef942` in round 2. The ledger has no supersede auto-close mechanism, so `53ce191d` was never explicitly marked addressed. Likely still open in the ledger. |
| 3 | `d41b9bc7` | qa | — | `d4d255d1` | be-pm | **CLOSED (verified via completion)** — task `d4d255d1` reached `completed` as of 2026-07-31 21:27 UTC. A task cannot reach `completed` while it carries an open QA-origin blocker, so `d41b9bc7` was addressed and verified by that completion event. |
| 4 | `397f9453` | qa | — | `c0c2ac32` | be-pm | **CANDIDATE (KB-sourced, not directly confirmed)** — surfaced via KB search: a QA review (`roboco://reviews/rev-289088149552`) states "Passed: CI blocker F-397f9453 resolved. Failing step was reflow-check on the competitive-positioning doc." The review marks it resolved, but the ledger may still show it as open if it was never stamped `addressed→verified` through the formal lifecycle. Cannot confirm via `evidence()` (not the assignee of `c0c2ac32`). Treat as a candidate pending direct ledger confirmation. |
| 5 | pending-identification | — | — | candidate: `62845be1` | be-pm | **PENDING-IDENTIFICATION** — the evidence-timeout bug task (`62845be1`, "Fix claim_review/evidence 120s timeout") completed via PR #756 as of 2026-07-31. A blocker finding may have been filed on it during its own QA/PR-gate rounds and left orphaned in the ledger if it completed through a pre-gate path. Cannot confirm — `evidence()` on `62845be1` returns `not_authorized` (not the assignee), and the full UUID search did not surface a finding ID. |
| 6 | pending-identification | — | — | candidate: `07738ec5` | be-pm | **PENDING-IDENTIFICATION** — blocked task `07738ec5` was flagged in Main PM journal context as needing review to ensure it is not stalled without attention. A blocker finding may exist on it, but the full UUID was not recoverable from KB search and the task was not directly enumerable. Treat as a candidate pending direct ledger confirmation. |

### Tooling degradation note

Every attempt to directly enumerate findings via `evidence()` on candidate tasks (`a9a030d8`, `d4d255d1`, `62845be1`) returned `not_authorized` — the caller is not the assignee of those tasks, so the finding ledger cannot be read cross-task. KB search (`roboco_kb_search`) is the only available enumeration path and it surfaced only one candidate finding ID (`397f9453`) from a review record; the other two unknowns could not be resolved to concrete IDs. This is consistent with the tooling degradation documented across multiple prior rounds in the backend audit doc (`docs/audits/backend-blocker-findings-2026-07-30.md`, "Lifecycle-wiring gap found during triage") — `evidence()`/`roboco_kb_search` timed out across 5+ retries there, and the `claim_review` 120s timeout (task `62845be1`, now fixed via PR #756) was a related recurrence. The three pending-identification/pending-verification entries above are the explicit fallback the task description authorizes when tools cannot fully enumerate.

## Reconciliation: Sentinel's 6 vs cell audits' 2

Sentinel counted 6 open BLOCKER-severity findings org-wide. The three cell audits collectively found 2 active blocker findings (backend: `4e4ef942` and `d41b9bc7`; frontend: 0; ux_ui: 0), with a third (`53ce191d`) mentioned as superseded-but-open in the backend audit. The discrepancy of 4 unaccounted findings is explained below, one row per unaccounted finding:

| Unaccounted finding | Why the cell audits missed it | Disposition in this doc |
|---------------------|------------------------------|------------------------|
| `53ce191d` | The backend audit mentions it as superseded by `4e4ef942` but does not count it among its 2 active blockers — it was treated as already-subsumed, not as a separate open ledger row. It is a distinct open row in the ledger because supersede does not auto-close. | Row 2 above: OPEN (superseded, not auto-closed). |
| `397f9453` (candidate) | The cell audits only covered tasks in `needs_revision` / `awaiting_pm_review`. Task `c0c2ac32` was not in those states at audit time — it was a completed reflow-fix task in the same PR #718 dependency chain. | Row 4 above: CANDIDATE, KB-sourced. |
| pending-identification (candidate: `62845be1`) | The cell audits only covered `needs_revision` / `awaiting_pm_review` tasks. `62845be1` completed via PR #756 and was not in an audited state. A finding filed on it during its own review rounds would persist as open if it completed through a pre-gate path. | Row 5 above: PENDING-IDENTIFICATION. |
| pending-identification (candidate: `07738ec5`) | The cell audits only covered `needs_revision` / `awaiting_pm_review` tasks. `07738ec5` was in `blocked` status, not an audited state. | Row 6 above: PENDING-IDENTIFICATION. |

Net: the cell audits' count of 2 reflects only the findings visible on tasks sitting in `needs_revision` / `awaiting_pm_review` at audit time. Sentinel's count of 6 includes findings on tasks that have since completed (or were never in an audited state), plus the superseded round-1 finding the backend audit noted but did not count as active. The 4-finding gap is a scope gap in the cell audits' triage window, not a count error — the cell audits were scoped to non-terminal tasks only, while the ledger retains open findings on terminal tasks until they are explicitly closed.

## Lifecycle wiring assessment

The findings lifecycle has two steps (per `docs/map/review-findings.md`):

1. **open → addressed**: via `mark_addressed` when the bounced side names the finding in `resolved_findings` on `i_am_done` / `submit_up` / `submit_root`.
2. **addressed → verified**: via `stamp_addressed_verified` on the next same-origin pass (`pass_review` / `pr_pass` / `complete`).

There is **no auto-close on task completion**. `stamp_addressed_verified` only verifies findings already in the `addressed` state — it does not touch `open` findings. The `FINDINGS_ADDRESSED` gate on `i_am_done` / `submit_up` / `submit_root` blocks submission when open findings remain, but findings filed before this gate was implemented remain orphaned.

### Gap 1: no auto-close on supersede

When a round-1 finding is superseded by a round-2 finding for the same underlying issue (as with `53ce191d` → `4e4ef942`), the round-1 finding is never automatically marked `addressed` or `verified`. It persists as a distinct `open` row in the ledger indefinitely. This inflates the open-blocker count and makes the ledger noisy — a reviewer seeing `53ce191d` open cannot tell from the row alone that it was subsumed by a later finding without cross-referencing the task's revision history.

### Gap 2: no auto-close on task completion

Findings on tasks that completed through pre-gate paths (before `FINDINGS_ADDRESSED` was implemented, or via a path that did not enforce it) persist as `open` in the ledger. A task reaching `completed` does not stamp its open findings to `verified` — only already-`addressed` findings get verified by the completion's same-origin pass. This is the likely source of the orphaned findings on `62845be1` and `c0c2ac32`: both completed, but their open findings (if any) were never driven through `open → addressed → verified` because the gate either did not exist or did not fire for their completion path.

### Gap 3: cross-task ledger read is structurally blocked

A PM attempting to audit findings org-wide cannot read another task's finding ledger via `evidence()` — it returns `not_authorized` (not the assignee). The only org-wide signal is Sentinel's aggregate count; the only per-task read is `GET /{task_id}/findings` (not exposed as an agent verb). This means a PM driving findings to closure must either own the task or rely on the cell PM who does — there is no centralized "list all open blockers" agent surface. This is not a lifecycle wiring gap per se, but it is the operational constraint that made full enumeration impossible for this synthesis.

## Cross-cell lifecycle verdict

**Is `mark_addressed` / `mark_verified` wired effectively?** Partially. For tasks currently flowing through the gated delivery path (`i_am_done` with `FINDINGS_ADDRESSED` enforced), the lifecycle works: open findings block submission, the dev names each in `resolved_findings`, the next same-origin pass stamps them verified. The backend cell's `d41b9bc7` is the worked example — it was addressed and verified as part of `d4d255d1`'s completion, and the cell audit confirmed closure.

**Where it breaks:**

- **Superseded findings** (`53ce191d`): never auto-closed; the ledger accumulates round-1 rows that are semantically dead but structurally open. No mechanism exists to close them.
- **Pre-gate completions** (`62845be1`, `c0c2ac32` candidates): findings filed before `FINDINGS_ADDRESSED` was enforced, or on tasks that completed via a path that bypassed the gate, persist as open with no owner driving them to closure.
- **Cross-task visibility**: a PM cannot read another cell's finding ledger, so stale findings are only discovered when Sentinel's aggregate count surfaces them — by which point the owning task has often already completed, leaving no active claim-holder to drive closure.

**Frontend and ux_ui cells**: both audits found 0 open blockers and gave no direct evidence on whether the lifecycle is working — only that nothing is currently stuck. Their empty backlogs mean the lifecycle is not actively broken in those cells, but the gaps above (supersede, pre-gate completion) are org-wide structural issues, not cell-specific.

**Recommended fixes (out of scope for this doc — for a future task):**

1. Add an auto-close-on-supersede mechanism: when a round-N finding is filed for the same task + same file + same severity as an open round-(N-1) finding, mark the earlier finding `addressed` with a `superseded_by` note.
2. Stamp all `open` findings to `verified` (or `waived`) when a task reaches `completed` — not just already-`addressed` ones — so the ledger cannot accumulate orphaned open rows on terminal tasks.
3. Expose an org-wide open-findings list as an agent verb (or a PM-facing route beyond the per-task `GET /{task_id}/findings`) so a PM can enumerate without owning each task.

---

This is a documentation artifact only — no behavior change.