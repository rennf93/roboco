"use client";

// Composable presentational pieces for the verification receipt — reused by
// both the task-detail Verification tab and the release-proposal rollup.
// Every export here is pure presentation: data flows in as the {data,
// isLoading} shape the shared hooks in @/hooks/use-verification already
// return, no fetching logic lives here.
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { CheckCircle2, XCircle } from "lucide-react";
import type {
  AcVerificationStamp,
  PrCiVerdict,
  ReviewerChainEntry,
  ReviewRoundFindings,
} from "@/lib/api/verification";
import type { ConventionFinding } from "@/lib/api/conventions";

const ORIGIN_LABEL: Record<string, string> = {
  qa: "QA",
  pr_gate: "PR Review",
  pm: "PM",
  ceo: "CEO",
};

const SEVERITY_CLASS: Record<string, string> = {
  blocker: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  major:
    "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
  minor:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
  nit: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
};

const STATUS_CLASS: Record<string, string> = {
  open: "border-red-300 text-red-700 dark:border-red-800 dark:text-red-300",
  addressed:
    "border-blue-300 text-blue-700 dark:border-blue-800 dark:text-blue-300",
  verified:
    "border-green-300 text-green-700 dark:border-green-800 dark:text-green-300",
  waived: "border-border text-muted-foreground",
};

interface LoadableProps<T> {
  data: T;
  isLoading: boolean;
}

// (a) Per-acceptance-criterion verification state + the QA evidence line.
export function AcVerificationList({
  data,
  isLoading,
}: LoadableProps<AcVerificationStamp[] | undefined>) {
  if (isLoading) return <Skeleton className="h-20 w-full" />;
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No acceptance criteria recorded.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border text-sm">
      {data.map((stamp, i) => (
        <li key={i} className="flex items-start gap-2 py-2">
          {stamp.verified ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600 dark:text-green-400" />
          ) : (
            <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
          )}
          <div className="min-w-0">
            <p>{stamp.criterion}</p>
            {stamp.evidence && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {stamp.evidence}
              </p>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

// (b) Findings grouped by review round, severity + status per finding.
export function FindingsByRoundList({
  data,
  isLoading,
}: LoadableProps<ReviewRoundFindings[] | undefined>) {
  if (isLoading) return <Skeleton className="h-20 w-full" />;
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No revision findings recorded yet.
      </p>
    );
  }
  return (
    <div className="space-y-3 tabular-nums">
      {data.map((round, idx) => (
        <div
          key={`${round.round}-${idx}`}
          className="border-t pt-3 first:border-t-0 first:pt-0"
        >
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>Round {round.round}</span>
            <Badge variant="outline">
              {ORIGIN_LABEL[round.origin] ?? round.origin}
            </Badge>
          </div>
          <ul className="mt-1.5 divide-y divide-border text-sm">
            {round.findings.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center gap-2 py-1.5">
                <Badge
                  className={SEVERITY_CLASS[f.severity] ?? SEVERITY_CLASS.nit}
                >
                  {f.severity}
                </Badge>
                <Badge variant="outline" className={STATUS_CLASS[f.status]}>
                  {f.status}
                </Badge>
                {f.file && (
                  <code className="text-xs text-muted-foreground">
                    {f.file}
                    {f.line != null ? `:${f.line}` : ""}
                  </code>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

// (c) The PR CI verdict bound to the head commit — always renders the
// documented-unavailable state today (no backend route exists yet); shaped
// as its own component so the day a real endpoint ships, only this render
// branch changes.
export function CiVerdictBadge({
  data,
  isLoading,
}: LoadableProps<PrCiVerdict>) {
  if (isLoading) return <Skeleton className="h-6 w-36" />;
  return (
    <HelpTip label={data.reason}>
      <Badge variant="outline" className="text-muted-foreground">
        CI verdict unavailable
      </Badge>
    </HelpTip>
  );
}

// (d) Conventions-validator findings for this task's diff.
export function ConventionsFindingsList({
  data,
  isLoading,
}: LoadableProps<ConventionFinding[]>) {
  if (isLoading) return <Skeleton className="h-16 w-full" />;
  if (data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No conventions findings for this diff.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border text-sm">
      {data.map((f, i) => (
        <li key={i} className="flex flex-wrap items-center gap-2 py-1.5">
          <Badge variant="outline">{f.level}</Badge>
          <code className="text-xs text-muted-foreground">
            {f.file}:{f.line}
          </code>
          <span className="text-xs text-muted-foreground">{f.rule}</span>
        </li>
      ))}
    </ul>
  );
}

// (e) The reviewer chain — role + round, model where reachable (always
// "unknown" today — see `buildReviewerChain` for why).
export function ReviewerChainList({
  data,
  isLoading,
}: LoadableProps<ReviewerChainEntry[] | undefined>) {
  if (isLoading) return <Skeleton className="h-16 w-full" />;
  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No review rounds recorded yet.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border text-sm tabular-nums">
      {data.map((entry) => (
        <li key={entry.round} className="flex flex-wrap items-center gap-2 py-1.5">
          <span className="text-xs text-muted-foreground">
            Round {entry.round}
          </span>
          <Badge variant="outline">
            {ORIGIN_LABEL[entry.origin] ?? entry.origin}
          </Badge>
          {entry.author_slug && <span>{entry.author_slug}</span>}
          <HelpTip label="No endpoint exposes which model an agent ran on for a given review round">
            <span className="text-xs text-muted-foreground">
              model: {entry.model ?? "unknown"}
            </span>
          </HelpTip>
        </li>
      ))}
    </ul>
  );
}
