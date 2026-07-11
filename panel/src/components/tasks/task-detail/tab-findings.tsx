"use client";

import { Task } from "@/types";
import { useTaskFindings } from "@/hooks/use-tasks";
import type { TaskFinding } from "@/lib/api/tasks";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ListChecks } from "lucide-react";

interface TabFindingsProps {
  task: Task;
}

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

const ORIGIN_LABEL: Record<string, string> = {
  qa: "QA",
  pr_gate: "PR Review",
  pm: "PM",
  ceo: "CEO",
};

function FindingCard({ finding }: { finding: TaskFinding }) {
  return (
    <Card>
      <CardContent className="pt-4 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge className={SEVERITY_CLASS[finding.severity] ?? SEVERITY_CLASS.nit}>
            {finding.severity}
          </Badge>
          <Badge variant="outline" className={STATUS_CLASS[finding.status]}>
            {finding.status}
          </Badge>
          {finding.file && (
            <code className="text-xs text-muted-foreground">
              {finding.file}
              {finding.line != null ? `:${finding.line}` : ""}
            </code>
          )}
          {finding.criterion && (
            <span className="text-xs text-muted-foreground">
              · {finding.criterion}
            </span>
          )}
          {finding.addressed_by_commit && (
            <code className="ml-auto text-xs text-muted-foreground">
              {finding.addressed_by_commit.slice(0, 7)}
            </code>
          )}
        </div>
        <div className="space-y-1 text-sm">
          <p>
            <span className="text-muted-foreground">Expected:</span>{" "}
            {finding.expected}
          </p>
          <p>
            <span className="text-muted-foreground">Actual:</span>{" "}
            {finding.actual}
          </p>
          {finding.fix && (
            <p>
              <span className="text-muted-foreground">Fix:</span> {finding.fix}
            </p>
          )}
        </div>
        {finding.evidence && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none">Evidence</summary>
            <pre className="mt-1 whitespace-pre-wrap rounded bg-muted/50 p-2">
              {finding.evidence}
            </pre>
          </details>
        )}
        {finding.resolution_note && (
          <p className="text-xs text-muted-foreground">
            Resolution: {finding.resolution_note}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function TabFindings({ task }: TabFindingsProps) {
  const { data, isLoading } = useTaskFindings(task.id);
  const findings = data?.findings ?? [];

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (findings.length === 0) {
    return (
      <div className="py-12 text-center text-muted-foreground">
        <ListChecks className="mx-auto mb-4 h-12 w-12 opacity-50" />
        <p>No revision findings recorded yet.</p>
        <p className="mt-2 text-sm">
          Findings appear here after the first QA / PR-review / PM / CEO
          bounce.
        </p>
      </div>
    );
  }

  // Findings arrive newest-round-first; group while preserving that order.
  const rounds: { round: number; origin: string; items: TaskFinding[] }[] = [];
  for (const f of findings) {
    const last = rounds[rounds.length - 1];
    if (last && last.round === f.round) last.items.push(f);
    else rounds.push({ round: f.round, origin: f.origin, items: [f] });
  }

  return (
    <div className="space-y-6">
      {(data?.summary.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-2">
          {data!.summary.map((s) => (
            <Badge key={s.origin} variant="outline">
              {ORIGIN_LABEL[s.origin] ?? s.origin}: {s.open} open ·{" "}
              {s.addressed + s.verified + s.waived} closed
            </Badge>
          ))}
        </div>
      )}
      {rounds.map((group) => (
        <div key={group.round} className="space-y-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">Round {group.round}</h3>
            <Badge variant="outline">
              {ORIGIN_LABEL[group.origin] ?? group.origin}
            </Badge>
          </div>
          <div className="space-y-3">
            {group.items.map((f) => (
              <FindingCard key={f.id} finding={f} />
            ))}
          </div>
        </div>
      ))}
      {data?.truncated && (
        <p className="text-xs text-muted-foreground">
          … {data.total - findings.length} more not shown ({data.total} total)
        </p>
      )}
    </div>
  );
}
