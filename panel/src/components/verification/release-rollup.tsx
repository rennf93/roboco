"use client";

// The release-proposal verification rollup: the same per-task receipt (AC
// state, findings by round, CI verdict, conventions findings, reviewer
// chain) the task-detail Verification tab renders, composed once per member
// task and laid out together so the CEO sees the whole release trust story
// in one place instead of opening every task individually. Every member's
// receipt arrives in ONE request and every CI verdict in one more
// (`useReleaseVerification` / `useReleaseCiVerdicts`): never a request per
// member. This file only arranges the shared presentational pieces.
import {
  useReleaseCiVerdicts,
  useReleaseVerification,
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  memberCiVerdict,
  RELEASE_NO_MEMBER_TASKS_MESSAGE,
  RELEASE_ROLLUP_UNREADABLE_MESSAGE,
  type MemberVerification,
  type PrCiVerdict,
} from "@/lib/api/verification";

interface ReleaseVerificationRollupProps {
  /** The open release proposal's task id: keys the rollup's two reads. */
  proposalTaskId: string;
  /**
   * How many member tasks GET /release/proposal reported
   * (`member_task_ids.length`). Zero means this release genuinely carries no
   * member tasks (RELEASE_NO_MEMBER_TASKS_MESSAGE) and nothing is fetched.
   */
  memberCount: number;
}

export function ReleaseVerificationRollup({
  proposalTaskId,
  memberCount,
}: ReleaseVerificationRollupProps) {
  const hasMembers = memberCount > 0;
  const receipts = useReleaseVerification(proposalTaskId, hasMembers);
  const ci = useReleaseCiVerdicts(proposalTaskId, hasMembers);

  if (!hasMembers) {
    return (
      <p className="text-sm text-muted-foreground">
        {RELEASE_NO_MEMBER_TASKS_MESSAGE}
      </p>
    );
  }
  if (receipts.isLoading) return <Skeleton className="h-24 w-full" />;
  if (receipts.isError || !receipts.data) {
    return (
      <p className="text-sm text-muted-foreground">
        {RELEASE_ROLLUP_UNREADABLE_MESSAGE}
      </p>
    );
  }

  return (
    <div className="divide-y divide-border">
      {receipts.data.map((member) => (
        <MemberTaskVerification
          key={member.taskId}
          member={member}
          ci={memberCiVerdict(ci.data, ci.isError, member.taskId)}
          ciLoading={ci.isLoading}
        />
      ))}
    </div>
  );
}

function MemberTaskVerification({
  member,
  ci,
  ciLoading,
}: {
  member: MemberVerification;
  ci: PrCiVerdict;
  ciLoading: boolean;
}) {
  const verifiedCount = member.ac.filter((s) => s.verified).length;
  const totalCount = member.ac.length;
  const openFindingsCount = member.findings.reduce(
    (n, round) => n + round.findings.filter((f) => f.status === "open").length,
    0,
  );

  return (
    <CollapsibleSection
      variant="button"
      className="py-1"
      contentClassName="space-y-4 pb-3 pt-1"
      title={
        <span className="flex min-w-0 flex-wrap items-center gap-2 tabular-nums">
          <code className="shrink-0 text-xs text-muted-foreground">
            {member.taskId.slice(0, 8)}
          </code>
          <span className="truncate text-sm">{member.title}</span>
          <Badge variant="outline">
            {verifiedCount}/{totalCount} AC verified
          </Badge>
          {openFindingsCount > 0 && (
            <Badge className="bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300">
              {openFindingsCount} open
            </Badge>
          )}
          <CiVerdictBadge data={ci} isLoading={ciLoading} />
        </span>
      }
    >
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Acceptance criteria
        </h4>
        <div className="mt-1">
          <AcVerificationList data={member.ac} isLoading={false} />
        </div>
      </section>
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Findings by review round
        </h4>
        <div className="mt-1">
          <FindingsByRoundList data={member.findings} isLoading={false} />
        </div>
      </section>
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Conventions findings
        </h4>
        <div className="mt-1">
          <ConventionsFindingsList
            data={member.conventions}
            isLoading={false}
          />
        </div>
      </section>
      <section>
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Reviewer chain
        </h4>
        <div className="mt-1">
          <ReviewerChainList data={member.reviewers} isLoading={false} />
        </div>
      </section>
    </CollapsibleSection>
  );
}
