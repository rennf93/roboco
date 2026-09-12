// Pure client-side derivations for the task-detail Verification receipt and
// the release-proposal rollup. No new backend endpoints or capture: every
// export here reshapes data GET /tasks/{id}, GET /tasks/{id}/findings, and
// GET /projects/{id}/conventions/findings already return.
import type { TaskFinding } from "./tasks";
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

// The PR CI verdict bound to the task's PR head commit — NOT reachable from
// any existing endpoint. `GitService.get_pr_ci_status` exists server-side
// (roboco/services/git.py) but is only called internally by the in-path PR
// gate and the self-heal loop; no REST route wraps it, so the panel has no
// way to fetch it without a new backend endpoint. Per this task's intake
// facts ("no new backend data capture"), that gap is reported here for
// escalation instead of invented — the shape below always reads as
// unavailable until a real route (e.g. GET /tasks/{id}/pr-ci-status) ships.
export interface PrCiVerdict {
  available: false;
  /** Short sentence safe to render directly to the approver. */
  reason: string;
  /**
   * The backend-facing escalation detail (which endpoint is missing, why) —
   * for a HelpTip, code comment, or the docs. Never render this as the
   * primary visible sentence; it names internals the approver has no use for.
   */
  technicalDetail: string;
}

export const PR_CI_VERDICT_UNAVAILABLE: PrCiVerdict = {
  available: false,
  reason: "Per-task PR CI verification isn't available yet.",
  technicalDetail:
    "No backend endpoint exposes PR CI status for a task " +
    "(GitService.get_pr_ci_status has no REST route) — needs a new " +
    "endpoint; escalate rather than invent.",
};

// The release proposal's member-task set. GET /release/proposal's
// `member_task_ids` (roboco/api/schemas/release.py ReleaseProposalResponse)
// is now always present — an empty list is a real, legitimate state ("this
// release genuinely carries no member tasks", e.g. a hotfix/env-sync
// release), not the old "no endpoint exposes this" escalation gap. This
// message is the release-proposal rollup's empty-state copy for that case.
export const RELEASE_NO_MEMBER_TASKS_MESSAGE =
  "This release has no member tasks. There's nothing to verify per-task.";
