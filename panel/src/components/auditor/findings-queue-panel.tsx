"use client";

import { AuditorFinding } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { HelpTip } from "@/components/ui/help-tip";
import { ListChecks } from "lucide-react";
import Link from "next/link";

interface FindingsQueuePanelProps {
  findings: AuditorFinding[] | undefined;
  isLoading: boolean;
}

// Severity stored as the finding's lowercase value (blocker/major/minor/nit).
const severityColors: Record<string, string> = {
  blocker: "bg-red-100 text-red-700",
  major: "bg-orange-100 text-orange-700",
  minor: "bg-yellow-100 text-yellow-700",
  nit: "bg-blue-100 text-blue-700",
};

const severityOrder: Record<string, number> = {
  blocker: 0,
  major: 1,
  minor: 2,
  nit: 3,
};

export function FindingsQueuePanel({
  findings,
  isLoading,
}: FindingsQueuePanelProps) {
  // The API already returns blocking-first, but keep the sort stable client-side
  // in case a later re-fetch reorders.
  const sorted = [...(findings ?? [])].sort((a, b) => {
    const diff = (severityOrder[a.severity] ?? 9) - (severityOrder[b.severity] ?? 9);
    if (diff !== 0) return diff;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <ListChecks className="h-5 w-5" />
              Open Findings
            </CardTitle>
            {sorted.length > 0 && (
              <Badge variant="destructive">{sorted.length}</Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
        ) : sorted.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-sm">
            <ListChecks className="h-8 w-8 mx-auto mb-2 opacity-50" />
            No open review findings
          </div>
        ) : (
          <ScrollArea className="h-[400px] pr-4">
            <div className="space-y-3">
              {sorted.map((finding) => (
                <div
                  key={finding.id}
                  className="p-4 rounded-lg border bg-muted/50"
                >
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <Badge
                      className={
                        (severityColors[finding.severity] ?? "") + " text-xs"
                      }
                    >
                      {finding.severity}
                    </Badge>
                    <HelpTip label="Where this finding was raised — QA review, PR gate, PM review, or CEO approval">
                      <Badge variant="outline" className="text-xs">
                        {finding.origin}
                      </Badge>
                    </HelpTip>
                    <HelpTip label="Revision round this finding was raised in — round 1 is the first pass">
                      <span className="text-xs text-muted-foreground">
                        round {finding.round}
                      </span>
                    </HelpTip>
                  </div>
                  <p className="text-sm text-muted-foreground mb-2">
                    {finding.actual ?? finding.expected ?? finding.criterion ?? "—"}
                  </p>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    {finding.file && (
                      <span className="font-mono">
                        {finding.file}
                        {finding.line ? `:${finding.line}` : ""}
                      </span>
                    )}
                    <Link
                      href={"/tasks/" + finding.task_id}
                      prefetch={false}
                    >
                      <HelpTip label="Short task ID — first 8 characters of the full task identifier">
                        <span className="text-primary hover:underline">
                          Task #{finding.task_id.slice(0, 8)}
                        </span>
                      </HelpTip>
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}