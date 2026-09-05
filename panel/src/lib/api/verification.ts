// Pure client-side derivations for the task-detail Verification receipt and
// the release-proposal rollup. No new backend endpoints or capture: every
// export here reshapes data GET /tasks/{id}, GET /tasks/{id}/findings, and
// GET /projects/{id}/conventions/findings already return.
import type { TaskFinding } from "./tasks";

// One acceptance criterion's QA verification state, parsed from the
// deterministic "[AC] <criterion> — verified: <evidence>" lines
// `pass_review` stamps into `qa_notes`
// (roboco/services/gateway/choreographer/qa.py `_render_criteria_verified`).
export interface AcVerificationStamp {
  criterion: string;
  verified: boolean;
  evidence: string | null;
}

const AC_STAMP_RE = /^\[AC\]\s+(.+?)\s+—\s+verified:\s+(.+)$/;

// ponytail: matches by exact acceptance-criteria TEXT only. `criteria_verified`
// accepts a criterion by its stable id (`acceptance_criteria_ids`) too, but
// that column isn't on TaskResponse/GET /tasks/{id} — an id-stamped
// criterion reads as unverified here even when QA verified it. Widening this
// needs `acceptance_criteria_ids` added to the wire response, a real
// backend change out of this task's scope.
export function parseAcVerificationStamps(
  acceptanceCriteria: string[],
  qaNotes: string | null,
): AcVerificationStamp[] {
  const evidenceByCriterion = new Map<string, string>();
  for (const line of (qaNotes ?? "").split("\n")) {
    const match = AC_STAMP_RE.exec(line.trim());
    if (match) evidenceByCriterion.set(match[1].trim(), match[2].trim());
  }
  return acceptanceCriteria.map((criterion) => {
    const evidence = evidenceByCriterion.get(criterion) ?? null;
    return { criterion, verified: evidence !== null, evidence };
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

// One reviewer-stage entry in a task's review history, derived from the
// revision-findings ledger — the only reachable "review record" for this
// (no endpoint exposes agent_spawn_sessions per task). Covers only rounds
// that produced a finding (a bounce); a round that passed clean leaves no
// ledger row and so has no entry here.
export interface ReviewerChainEntry {
  round: number;
  origin: TaskFinding["origin"];
  author_slug: string | null;
  // Always null: no endpoint exposes which model an agent ran on for a
  // given round (AgentResponse carries no model field, and
  // agent_spawn_sessions has no REST route at all). Escalated for a
  // dedicated endpoint rather than invented — see PR_CI_VERDICT_UNAVAILABLE
  // below for the same class of gap.
  model: null;
}

export function buildReviewerChain(
  findings: TaskFinding[],
): ReviewerChainEntry[] {
  const byRound = new Map<number, ReviewerChainEntry>();
  for (const f of findings) {
    if (!byRound.has(f.round)) {
      byRound.set(f.round, {
        round: f.round,
        origin: f.origin,
        author_slug: f.author_slug,
        model: null,
      });
    }
  }
  return [...byRound.values()].sort((a, b) => a.round - b.round);
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
  reason: string;
}

export const PR_CI_VERDICT_UNAVAILABLE: PrCiVerdict = {
  available: false,
  reason:
    "No backend endpoint exposes PR CI status for a task " +
    "(GitService.get_pr_ci_status has no REST route) — needs a new " +
    "endpoint; escalate rather than invent.",
};

// The release proposal's member-task set — NOT reachable from any existing
// endpoint. ReleaseReport.change_summary (roboco/services/release_readiness.py)
// is free-text commit strings only; no task_id/pr_number is exposed anywhere
// on the release-manager surface (release.py schema, ReleaseReportModel).
// Per this task's intake facts ("no new backend data capture"), that gap is
// reported here for escalation instead of invented — the release-proposal
// verification rollup (@/components/verification/release-rollup) is
// parametrized over an explicit task-id list instead, so it renders the real
// aggregation the day a real endpoint exposes the member set.
export interface ReleaseMemberTaskIdsUnavailable {
  available: false;
  reason: string;
}

export const RELEASE_MEMBER_TASK_IDS_UNAVAILABLE: ReleaseMemberTaskIdsUnavailable =
  {
    available: false,
    reason:
      "No backend endpoint exposes this release's member task set " +
      "(ReleaseReport.change_summary is free-text commit strings only, no " +
      "task_id/pr_number) — needs a new endpoint; escalate rather than " +
      "invent.",
  };
