"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { scalesApi } from "@/lib/api";
import type { RebalanceCycle, RebalanceItem } from "@/lib/api/scales";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { Scale, CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { HelpTip } from "@/components/ui/help-tip";

const _MIN_REASON_CHARS = 4;

interface RejectTarget {
  taskId: string;
  item: RebalanceItem;
}

function itemStatusBadge(item: RebalanceItem) {
  if (item.status === "approved") {
    return (
      <HelpTip label={item.executed_detail ?? "Executed against the live task"}>
        <Badge variant="secondary" className="bg-green-600/10 text-green-700">
          Approved
        </Badge>
      </HelpTip>
    );
  }
  if (item.status === "rejected") {
    return (
      <HelpTip label="Recorded, the target task is untouched">
        <Badge variant="outline">Rejected</Badge>
      </HelpTip>
    );
  }
  return null;
}

function actionBadge(item: RebalanceItem) {
  if (item.action === "cancel") {
    return (
      <HelpTip label="Approving cancels the live task">
        <Badge variant="destructive">Cancel</Badge>
      </HelpTip>
    );
  }
  return (
    <HelpTip label={`Approving sets priority to P${item.new_priority ?? "?"}`}>
      <Badge variant="secondary">Reprioritize → P{item.new_priority ?? "?"}</Badge>
    </HelpTip>
  );
}

// One rebalance item row: details + per-item approve/reject (proposed only).
function RebalanceItemRow({
  taskId,
  item,
  onApprove,
  onReject,
  approving,
}: {
  taskId: string;
  item: RebalanceItem;
  onApprove: (taskId: string, itemId: string) => void;
  onReject: (target: RejectTarget) => void;
  approving: boolean;
}) {
  const isProposed = item.status === "proposed";

  return (
    <div className="rounded-lg border p-4 transition-colors hover:bg-muted/50">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-medium">{item.target_task_title}</span>
        {actionBadge(item)}
        {itemStatusBadge(item)}
      </div>
      <p className="text-sm text-muted-foreground">
        <span className="font-semibold">Rationale:</span> {item.rationale}
      </p>
      {item.status === "rejected" && item.reject_reason && (
        <p className="mt-2 text-sm text-destructive">
          Rejected: {item.reject_reason}
        </p>
      )}
      {isProposed && (
        <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
          <HelpTip label="Records your reason — the target task is left untouched">
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => onReject({ taskId, item })}
            >
              <XCircle className="mr-1 h-4 w-4" />
              Reject
            </Button>
          </HelpTip>
          <HelpTip label="Executes the action against the live task right now">
            <Button
              size="sm"
              className="bg-green-600 hover:bg-green-700"
              disabled={approving}
              onClick={() => onApprove(taskId, item.id)}
            >
              <CheckCircle2 className="mr-1 h-4 w-4" />
              Approve
            </Button>
          </HelpTip>
        </div>
      )}
    </div>
  );
}

function RebalanceCycleCard({
  cycle,
  onApprove,
  onReject,
  approvingItemId,
}: {
  cycle: RebalanceCycle;
  onApprove: (taskId: string, itemId: string) => void;
  onReject: (target: RejectTarget) => void;
  approvingItemId: string | null;
}) {
  const pending = cycle.items.filter((i) => i.status === "proposed").length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Scale className="h-5 w-5" />
          Scales
          <HelpTip label="Items still awaiting your approve/reject decision">
            <Badge variant="secondary">{pending} pending</Badge>
          </HelpTip>
        </CardTitle>
        <CardDescription>
          Portfolio rebalance the Product Owner proposed against the live
          backlog — each item reviewed individually.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {cycle.items.map((item) => (
          <RebalanceItemRow
            key={item.id}
            taskId={cycle.task_id}
            item={item}
            onApprove={onApprove}
            onReject={onReject}
            approving={approvingItemId === item.id}
          />
        ))}
      </CardContent>
    </Card>
  );
}

// CEO queue for the Product Owner's held Scales cycles. Hidden when no cycle
// has been authored yet (mirrors PestControlReviewQueue).
export function ScalesReviewQueue({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<RejectTarget | null>(null);
  const [reason, setReason] = useState("");
  const [approvingItemId, setApprovingItemId] = useState<string | null>(null);

  const { data: cycles, isLoading } = useQuery({
    queryKey: ["scales", "cycles"],
    queryFn: () => scalesApi.listCycles(),
    refetchInterval: 30000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["scales", "cycles"] });

  const approveMutation = useMutation({
    mutationFn: ({ taskId, itemId }: { taskId: string; itemId: string }) =>
      scalesApi.approveItem(taskId, itemId),
    onSuccess: (result) => {
      invalidate();
      if (result.status === "approved" || result.status === "already_approved") {
        toast.success(result.detail || "Item approved");
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
    }) => scalesApi.rejectItem(taskId, itemId, reason),
    onSuccess: () => {
      invalidate();
      toast.success("Item rejected");
      closeReject();
    },
    onError: (e) =>
      toast.error(`Reject failed: ${e instanceof Error ? e.message : "error"}`),
  });

  const closeReject = () => {
    setRejecting(null);
    setReason("");
  };

  const confirmReject = () => {
    if (!rejecting) return;
    if (reason.trim().length < _MIN_REASON_CHARS) {
      toast.error("Give a brief reason for rejecting");
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

  if (isLoading || !cycles || cycles.length === 0) return null;

  return (
    <>
      <div className={`space-y-4 ${className ?? ""}`}>
        {cycles.map((cycle) => (
          <RebalanceCycleCard
            key={cycle.task_id}
            cycle={cycle}
            onApprove={handleApprove}
            onReject={setRejecting}
            approvingItemId={approvingItemId}
          />
        ))}
      </div>

      <Dialog open={!!rejecting} onOpenChange={() => closeReject()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject rebalance item</DialogTitle>
            <DialogDescription>
              This records your reason — the target task is left untouched.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="scales-reject-reason">Reason</Label>
            <Textarea
              id="scales-reject-reason"
              placeholder="e.g. still on the roadmap this quarter..."
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
              {rejectMutation.isPending ? "Rejecting..." : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
