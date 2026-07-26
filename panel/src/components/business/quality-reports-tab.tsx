"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { sentinelApi } from "@/lib/api";
import type { QualityReport, QualityReportItem } from "@/lib/api/sentinel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { HelpTip } from "@/components/ui/help-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";

const _MIN_REASON_CHARS = 4;

interface RejectTarget {
  taskId: string;
  item: QualityReportItem;
}

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

function itemStatusBadge(item: QualityReportItem) {
  if (item.status === "approved") {
    return (
      <HelpTip label="Materialized as a Main-PM-owned task">
        <Badge variant="secondary" className="bg-green-600/10 text-green-700">
          Approved
        </Badge>
      </HelpTip>
    );
  }
  if (item.status === "rejected") {
    return (
      <HelpTip label="Dismissed — not added to the backlog">
        <Badge variant="outline">Dismissed</Badge>
      </HelpTip>
    );
  }
  return null;
}

// ---------------------------------------------------------------------------
// One drift item — area/observation/evidence/suggested action, plus a
// per-item approve/dismiss action.
// ---------------------------------------------------------------------------

function DriftItemRow({
  taskId,
  item,
  onApprove,
  onReject,
  approving,
}: {
  taskId: string;
  item: QualityReportItem;
  onApprove: (taskId: string, itemId: string) => void;
  onReject: (target: RejectTarget) => void;
  approving: boolean;
}) {
  const isProposed = item.status === "proposed";

  return (
    <li className="space-y-1 border-b pb-3 last:border-b-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="uppercase text-xs">
          {item.area}
        </Badge>
        {itemStatusBadge(item)}
      </div>
      <p className="text-sm whitespace-pre-wrap">{item.observation}</p>
      <p className="text-xs text-muted-foreground whitespace-pre-wrap">
        Evidence: {item.evidence}
      </p>
      <p className="text-xs text-muted-foreground whitespace-pre-wrap">
        Suggested action: {item.suggested_action}
      </p>
      {item.status === "rejected" && item.reject_reason && (
        <p className="text-xs text-destructive">
          Dismissed: {item.reject_reason}
        </p>
      )}
      {isProposed && (
        <div className="flex justify-end gap-2 pt-1">
          <HelpTip label="Records your reason — not added to the backlog">
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => onReject({ taskId, item })}
            >
              <XCircle className="mr-1 h-3.5 w-3.5" />
              Dismiss
            </Button>
          </HelpTip>
          <HelpTip label="Materializes this item as a Main-PM-owned task — needs normal PM activation to start">
            <Button
              size="sm"
              className="bg-green-600 hover:bg-green-700"
              disabled={approving}
              onClick={() => onApprove(taskId, item.id)}
            >
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
              Approve
            </Button>
          </HelpTip>
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// One report — headline/items/overall assessment, each item with its own
// approve/dismiss action.
// ---------------------------------------------------------------------------

function ReportCard({
  report,
  onApprove,
  onReject,
  approvingItemId,
}: {
  report: QualityReport;
  onApprove: (taskId: string, itemId: string) => void;
  onReject: (target: RejectTarget) => void;
  approvingItemId: string | null;
}) {
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
                <DriftItemRow
                  key={item.id}
                  taskId={report.task_id}
                  item={item}
                  onApprove={onApprove}
                  onReject={onReject}
                  approving={approvingItemId === item.id}
                />
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
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<RejectTarget | null>(null);
  const [reason, setReason] = useState("");
  const [approvingItemId, setApprovingItemId] = useState<string | null>(null);

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

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["sentinel", "reports"] });

  const approveMutation = useMutation({
    mutationFn: ({ taskId, itemId }: { taskId: string; itemId: string }) =>
      sentinelApi.approveItem(taskId, itemId),
    onSuccess: (result) => {
      invalidate();
      if (
        result.status === "approved" ||
        result.status === "already_approved"
      ) {
        toast.success("Item approved — materialized as a task");
      } else {
        toast.warning(result.detail);
      }
    },
    onError: (e) =>
      toast.error(
        `Approve failed: ${e instanceof Error ? e.message : "error"}`,
      ),
    onSettled: () => setApprovingItemId(null),
  });

  const rejectMutation = useMutation({
    mutationFn: ({
      taskId,
      itemId,
      reason,
    }: {
      taskId: string;
      itemId: string;
      reason: string;
    }) => sentinelApi.rejectItem(taskId, itemId, reason),
    onSuccess: () => {
      invalidate();
      toast.success("Item dismissed");
      closeReject();
    },
    onError: (e) =>
      toast.error(
        `Dismiss failed: ${e instanceof Error ? e.message : "error"}`,
      ),
  });

  const closeReject = () => {
    setRejecting(null);
    setReason("");
  };

  const confirmReject = () => {
    if (!rejecting) return;
    if (reason.trim().length < _MIN_REASON_CHARS) {
      toast.error("Give a brief reason for dismissing");
      return;
    }
    rejectMutation.mutate({
      taskId: rejecting.taskId,
      itemId: rejecting.item.id,
      reason: reason.trim(),
    });
  };

  const handleApprove = (taskId: string, itemId: string) => {
    setApprovingItemId(itemId);
    approveMutation.mutate({ taskId, itemId });
  };

  return (
    <>
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
              Sentinel is enabled — each drift item can be approved or dismissed
              individually once one lands.
            </p>
          ) : (
            <div className="space-y-4">
              {reports.map((r) => (
                <ReportCard
                  key={r.task_id}
                  report={r}
                  onApprove={handleApprove}
                  onReject={setRejecting}
                  approvingItemId={approvingItemId}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!rejecting} onOpenChange={() => closeReject()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Dismiss quality-report item</DialogTitle>
            <DialogDescription>
              This records your reason and feeds the next cycle&apos;s prompt —
              it is not added to the backlog.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="sentinel-reject-reason">Reason</Label>
            <Textarea
              id="sentinel-reject-reason"
              placeholder="e.g. already tracked elsewhere; not worth a task this cycle..."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeReject}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmReject}
              disabled={rejectMutation.isPending}
            >
              {rejectMutation.isPending ? "Dismissing..." : "Dismiss"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
