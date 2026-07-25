"use client";

import { useQuery } from "@tanstack/react-query";
import { sentinelApi } from "@/lib/api";
import type { QualityReport } from "@/lib/api/sentinel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";

// ---------------------------------------------------------------------------
// Read-only skeleton placeholder shaped like a report card
// ---------------------------------------------------------------------------

function ReportCardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-64" />
        <Skeleton className="h-4 w-40 mt-1" />
      </CardHeader>
      <CardContent className="space-y-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// One report, read-only — headline/items/overall assessment. No approve/
// reject UI: a quality report is a report, not a queue item.
// ---------------------------------------------------------------------------

function ReportCard({ report }: { report: QualityReport }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">{report.headline}</CardTitle>
        {report.completed_at && (
          <p className="text-xs text-muted-foreground">
            Filed {new Date(report.completed_at).toLocaleString()}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {report.items.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Drift items
            </p>
            <ul className="space-y-3">
              {report.items.map((item) => (
                <li key={item.id} className="text-sm space-y-1">
                  <Badge variant="secondary" className="uppercase text-xs">
                    {item.area}
                  </Badge>
                  <p className="whitespace-pre-wrap">{item.observation}</p>
                  <p className="text-xs text-muted-foreground whitespace-pre-wrap">
                    Evidence: {item.evidence}
                  </p>
                  <p className="text-xs text-muted-foreground whitespace-pre-wrap">
                    Suggested action: {item.suggested_action}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}
        {report.overall_assessment && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Overall assessment
            </p>
            <p className="text-sm whitespace-pre-wrap">
              {report.overall_assessment}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function QualityReportsTab() {
  const {
    data: reports = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["sentinel", "reports"],
    queryFn: () => sentinelApi.listReports(),
    refetchInterval: 30000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Quality Reports</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-4">
            <ReportCardSkeleton />
            <ReportCardSkeleton />
          </div>
        ) : isError ? (
          <OfflineState
            title="Failed to load quality reports"
            description="Could not reach the orchestrator API. Check the backend is running."
            onRetry={() => void refetch()}
          />
        ) : reports.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No quality reports filed yet. The Auditor files one weekly, once
            Sentinel is enabled — they appear here as reports, not a queue to
            act on.
          </p>
        ) : (
          <div className="space-y-4">
            {reports.map((r) => (
              <ReportCard key={r.task_id} report={r} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
