"use client";

// The release-proposal verification rollup: the same per-task receipt (AC
// state, findings by round, CI verdict, conventions findings, reviewer
// chain) the task-detail Verification tab renders, composed once per member
// task and laid out together so the CEO sees the whole release trust story
// in one place instead of opening every task individually. This file only
// arranges repeated instances of the shared hooks + presentational pieces —
// nothing here re-implements or forks them.
import {
  useAcVerificationStamps,
  useFindingsByRound,
  usePrCiVerdict,
  useReviewerChain,
  useTaskConventionFindings,
} from "@/hooks/use-verification";
import {
  AcVerificationList,
  CiVerdictBadge,
  ConventionsFindingsList,
  FindingsByRoundList,
  ReviewerChainList,
} from "@/components/verification/receipt";
import { CollapsibleSection } from "@/components/ui/collapsible-section";
import { Badge } from "@/components/ui/badge";
import { RELEASE_NO_MEMBER_TASKS_MESSAGE } from "@/lib/api/verification";

interface ReleaseVerificationRollupProps {
  /**
   * The release proposal's member task ids (GET /release/proposal's
   * `member_task_ids`, always present — an empty list means this release
   * genuinely carries no member tasks, see RELEASE_NO_MEMBER_TASKS_MESSAGE).
   */
  taskIds: string[];
  /** Optional display title per task id — falls back to the bare id. */
  taskTitles?: Record<string, string>;
}

export function ReleaseVerificationRollup({
  taskIds,
  taskTitles,
}: ReleaseVerificationRollupProps) {
  if (taskIds.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {RELEASE_NO_MEMBER_TASKS_MESSAGE}
      </p>
    );
  }

  return (
    <div className="divide-y divide-border">
      {taskIds.map((taskId) => (
        <MemberTaskVerification
          key={taskId}
          taskId={taskId}
          title={taskTitles?.[taskId]}
        />
      ))}
    </div>
  );
}

function MemberTaskVerification({
  taskId,
  title,
}: {
  taskId: string;
  title?: string;
}) {
  const ac = useAcVerificationStamps(taskId);
  const findings = useFindingsByRound(taskId);
  const ci = usePrCiVerdict(taskId);
  const conventions = useTaskConventionFindings(taskId);
  const reviewers = useReviewerChain(taskId);

  const verifiedCount = ac.data?.filter((s) => s.verified).length ?? 0;
  const totalCount = ac.data?.length ?? 0;
  const openFindingsCount =
    findings.data?.reduce(
      (n, round) =>
        n + round.findings.filter((f) => f.status === "open").length,
      0,
    ) ?? 0;

  return (
    <CollapsibleSection
      variant="button"
      className="py-1"
      contentClassName="space-y-4 pb-3 pt-1"
      title={
        <span className="flex min-w-0 flex-wrap items-center gap-2 tabular-nums">
          <code className="shrink-0 text-xs text-muted-foreground">
            {taskId.slice(0, 8)}
          </code>
          <span className="truncate text-sm">{title ?? taskId}</span>
          <Badge variant="outline">
            {verifiedCount}/{totalCount} AC verified
          </Badge>
          {openFindingsCount > 0 && (
            <Badge className="bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">
              {openFindingsCount} open
            </Badge>
          )}
          <CiVerdictBadge data={ci.data} isLoading={ci.isLoading} />
        </span>
      }
    >
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Acceptance criteria
        </h4>
        <div className="mt-1">
          <AcVerificationList data={ac.data} isLoading={ac.isLoading} />
        </div>
      </section>
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Findings by review round
        </h4>
        <div className="mt-1">
          <FindingsByRoundList
            data={findings.data}
            isLoading={findings.isLoading}
          />
        </div>
      </section>
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Conventions findings
        </h4>
        <div className="mt-1">
          <ConventionsFindingsList
            data={conventions.data}
            isLoading={conventions.isLoading}
          />
        </div>
      </section>
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Reviewer chain
        </h4>
        <div className="mt-1">
          <ReviewerChainList
            data={reviewers.data}
            isLoading={reviewers.isLoading}
          />
        </div>
      </section>
    </CollapsibleSection>
  );
}
