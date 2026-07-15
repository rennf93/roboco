"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { roadmapApi } from "@/lib/api";
import type { RoadmapCycle, RoadmapItem } from "@/lib/api/roadmap";
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
import { CheckCircle2, Map, XCircle } from "lucide-react";
import { toast } from "sonner";
import { HelpTip } from "@/components/ui/help-tip";

const _MIN_REASON_CHARS = 4;

interface RejectTarget {
  taskId: string;
  item: RoadmapItem;
}

function itemStatusBadge(item: RoadmapItem) {
  if (item.status === "approved") {
    return (
      <Badge variant="secondary" className="bg-green-600/10 text-green-700">
        Approved
      </Badge>
    );
  }
  if (item.status === "rejected") {
    return <Badge variant="outline">Rejected</Badge>;
  }
  return null;
}

// One roadmap item row: details + per-item approve/reject (proposed only).
function RoadmapItemRow({
  taskId,
  item,
  onApprove,
  onReject,
  approving,
}: {
  taskId: string;
  item: RoadmapItem;
  onApprove: (taskId: string, itemId: string) => void;
  onReject: (target: RejectTarget) => void;
  approving: boolean;
}) {
  const isProposed = item.status === "proposed";

  return (
    <div className="rounded-lg border p-4 transition-colors hover:bg-muted/50">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-medium">{item.title}</span>
        <Badge variant="outline">{item.team}</Badge>
        <HelpTip label="The project (repository) this roadmap item targets">
          <Badge variant="outline">{item.project_slug}</Badge>
        </HelpTip>
        <HelpTip label={`Priority P${item.priority} — ${item.priority === 0 ? "critical" : item.priority === 1 ? "high" : item.priority === 2 ? "medium" : "low"}`}>
          <Badge variant="secondary">P{item.priority}</Badge>
        </HelpTip>
        {itemStatusBadge(item)}
      </div>
      <p className="text-sm text-muted-foreground">{item.description}</p>
      <p className="mt-1 text-sm text-muted-foreground">
        <span className="font-semibold">Why:</span> {item.rationale}
      </p>
      {item.acceptance_criteria.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-muted-foreground">
          {item.acceptance_criteria.map((ac) => (
            <li key={ac}>{ac}</li>
          ))}
        </ul>
      )}
      {item.status === "rejected" && item.reject_reason && (
        <p className="mt-2 text-sm text-destructive">
          Rejected: {item.reject_reason}
        </p>
      )}
      {isProposed && (
        <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
          <Button
            variant="outline"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => onReject({ taskId, item })}
          >
            <XCircle className="mr-1 h-4 w-4" />
            Reject
          </Button>
          <Button
            size="sm"
            className="bg-green-600 hover:bg-green-700"
            disabled={approving}
            onClick={() => onApprove(taskId, item.id)}
          >
            <CheckCircle2 className="mr-1 h-4 w-4" />
            Approve
          </Button>
        </div>
      )}
    </div>
  );
}

function RoadmapCycleCard({
  cycle,
  onApprove,
  onReject,
  approvingItemId,
}: {
  cycle: RoadmapCycle;
  onApprove: (taskId: string, itemId: string) => void;
  onReject: (target: RejectTarget) => void;
  approvingItemId: string | null;
}) {
  const pending = cycle.items.filter((i) => i.status === "proposed").length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Map className="h-5 w-5" />
          Roadmap Cycle
          <Badge variant="secondary">{pending} pending</Badge>
        </CardTitle>
        <CardDescription>{cycle.goal}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {cycle.items.map((item) => (
          <RoadmapItemRow
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

// CEO queue for the Product Owner's held roadmap cycles. Hidden when no
// cycle has been authored yet (mirrors the playbook + X post queues).
export function RoadmapReviewQueue({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<RejectTarget | null>(null);
  const [reason, setReason] = useState("");
  const [approvingItemId, setApprovingItemId] = useState<string | null>(null);

  const { data: cycles, isLoading } = useQuery({
    queryKey: ["roadmap", "cycles"],
    queryFn: () => roadmapApi.listCycles(),
    refetchInterval: 30000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["roadmap", "cycles"] });

  const approveMutation = useMutation({
    mutationFn: ({ taskId, itemId }: { taskId: string; itemId: string }) =>
      roadmapApi.approveItem(taskId, itemId),
    onSuccess: (result) => {
      invalidate();
      if (
        result.status === "approved" ||
        result.status === "already_approved"
      ) {
        toast.success("Item approved — added to the backlog");
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
    }) => roadmapApi.rejectItem(taskId, itemId, reason),
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
          <RoadmapCycleCard
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
            <DialogTitle>Reject roadmap item</DialogTitle>
            <DialogDescription>
              This records your reason and feeds the next cycle&apos;s prompt —
              it is not added to the backlog.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="roadmap-reject-reason">Reason</Label>
            <Textarea
              id="roadmap-reject-reason"
              placeholder="e.g. not a priority this quarter; overlaps an existing task..."
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
