// Pure client-side derivations for the task-detail Verification receipt and
// the release-proposal rollup. No new backend endpoints or capture: every
// export here reshapes data GET /tasks/{id}, GET /tasks/{id}/findings,
// GET /tasks/{id}/attestation, and GET /projects/{id}/conventions/findings
// already return.
import type { AttestationCiVerdict, TaskFinding } from "./tasks";
import type { TaskMetricsReviewerChainEntry } from "./dashboard";

// One acceptance criterion's QA verification state, parsed from the
// deterministic "[AC] <criterion> — verified: <evidence>" lines
// `pass_review` stamps into `qa_notes`
// (roboco/services/gateway/choreographer/qa.py `_render_criteria_verified`).
export interface AcVerificationStamp {
  criterion: string;
  // True only when a qa_notes stamp's label exactly matched this criterion's
  // text — the one thing this function can actually determine client-side.
  matched: boolean;
  verified: boolean;
  // True when `matched` is false AND qa_notes carries at least one [AC]
  // stamp line whose label matched no criterion's text at all — a strong
  // signal QA stamped that line by the criterion's stable id instead (see
  // the note below). Lets the UI show "can't confirm" instead of a
  // confident "not verified" for that case.
  unresolved: boolean;
  evidence: string | null;
}

const AC_STAMP_RE = /^\[AC\]\s+(.+?)\s+—\s+verified:\s+(.+)$/;

// ponytail: matches by exact acceptance-criteria TEXT only. `criteria_verified`
// accepts a criterion by its stable id (`acceptance_criteria_ids`) too, but
// that column isn't on TaskResponse/GET /tasks/{id} — an id-stamped
// criterion's [AC] line carries the raw id as its label, which matches no
// criterion text here. Rather than misreport that as a confident "not
// verified" (see `unresolved` above), this scopes the false-negative down to
// "can't confirm" whenever an orphan stamp exists. Real id matching needs
// `acceptance_criteria_ids` added to the wire response, a backend change out
// of this task's scope.
export function parseAcVerificationStamps(
  acceptanceCriteria: string[],
  qaNotes: string | null,
): AcVerificationStamp[] {
  const evidenceByCriterion = new Map<string, string>();
  let hasOrphanStamp = false;
  for (const line of (qaNotes ?? "").split("\n")) {
    const match = AC_STAMP_RE.exec(line.trim());
    if (!match) continue;
    const label = match[1].trim();
    if (acceptanceCriteria.includes(label)) {
      evidenceByCriterion.set(label, match[2].trim());
    } else {
      hasOrphanStamp = true;
    }
  }
  return acceptanceCriteria.map((criterion) => {
    const evidence = evidenceByCriterion.get(criterion) ?? null;
    const matched = evidence !== null;
    return {
      criterion,
      matched,
      verified: matched,
      unresolved: !matched && hasOrphanStamp,
      evidence,
    };
  });
}

// One review round's findings, grouped from the revision-findings ledger.
// Mirrors the exact consecutive-run grouping `tab-findings.tsx` already
// performs inline (findings arrive newest-round-first, pre-sorted, with
// same-round rows contiguous) — extracted here so the task-detail
// Verification tab and the release-proposal rollup can reuse the same
// grouping without re-deriving it or forking the order it renders in.
// Each finding already carries its own `severity` and `status`
// (open/addressed/verified/waived); grouping only buckets by round.
export interface ReviewRoundFindings {
  round: number;
  origin: TaskFinding["origin"];
  findings: TaskFinding[];
}

export function groupFindingsByRound(
  findings: TaskFinding[],
): ReviewRoundFindings[] {
  const rounds: ReviewRoundFindings[] = [];
  for (const f of findings) {
    const last = rounds[rounds.length - 1];
    if (last && last.round === f.round) last.findings.push(f);
    else rounds.push({ round: f.round, origin: f.origin, findings: [f] });
  }
  return rounds;
}

// One reviewer-stage entry in a task's review history — one per review-role
// (qa/pr_reviewer/cell_pm/main_pm) spawn session against the task, sourced
// from GET /dashboard/metrics/task/{task_id}'s `reviewer_chain`
// (roboco/services/metrics.py `_reviewer_chain_for_task`), `round` numbered
// identically to the revision-findings ledger. Replaces the earlier
// findings-ledger derivation, which could only ever report `model: null`
// (no endpoint exposed agent_spawn_sessions per task/round) and covered only
// rounds that produced a finding — this covers every review round,
// including a round that passed clean.
export type ReviewerChainEntry = TaskMetricsReviewerChainEntry;

export function buildReviewerChain(
  entries: TaskMetricsReviewerChainEntry[],
): ReviewerChainEntry[] {
  return [...entries].sort((a, b) => a.round - b.round);
}

// The PR CI verdict bound to the task's PR head commit, read off
// GET /tasks/{id}/attestation's `ci` (roboco/services/attestation.py
// `_ci_verdict`, which wraps `GitService.get_pr_ci_status`: the same
// check-runs signal the in-path PR gate reads). `PrCiState` is
// get_pr_ci_status's own vocabulary; the attestation reports
// `not_available` instead when the task has no PR yet or no CI signal
// could be resolved, which maps to the unavailable branch below.
export type PrCiState =
  | "success"
  | "failure"
  | "pending"
  | "pending_not_scheduled"
  | "no_ci_configured"
  | "error";

const PR_CI_STATES: readonly PrCiState[] = [
  "success",
  "failure",
  "pending",
  "pending_not_scheduled",
  "no_ci_configured",
  "error",
];

export type PrCiVerdict =
  | {
      available: true;
      state: PrCiState;
      headSha: string | null;
      failingChecks: string[];
    }
  | {
      available: false;
      /** Short sentence safe to render directly to the approver. */
      reason: string;
    };

export const PR_CI_VERDICT_NOT_AVAILABLE: PrCiVerdict = {
  available: false,
  reason:
    "No CI verdict yet: the task has no pull request, or its CI can't be read.",
};

export const PR_CI_VERDICT_UNREADABLE: PrCiVerdict = {
  available: false,
  reason: "The task's verification record couldn't be loaded.",
};

export function prCiVerdictFromAttestation(
  ci: AttestationCiVerdict,
): PrCiVerdict {
  const state = PR_CI_STATES.find((s) => s === ci.state);
  if (state === undefined) return PR_CI_VERDICT_NOT_AVAILABLE;
  return {
    available: true,
    state,
    headSha: ci.head_sha ?? null,
    failingChecks: ci.failing_checks,
  };
}

// The release proposal's member-task set. GET /release/proposal's
// `member_task_ids` (roboco/api/schemas/release.py ReleaseProposalResponse)
// is now always present — an empty list is a real, legitimate state ("this
// release genuinely carries no member tasks", e.g. a hotfix/env-sync
// release), not the old "no endpoint exposes this" escalation gap. This
// message is the release-proposal rollup's empty-state copy for that case.
export const RELEASE_NO_MEMBER_TASKS_MESSAGE =
  "This release has no member tasks. There's nothing to verify per-task.";
